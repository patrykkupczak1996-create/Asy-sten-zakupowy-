#!/usr/bin/env python3
"""
Wzbogacanie bazy produktów B2B (armatura, zasuwy) pod import do IdoSell.

Dla każdego wiersza pliku CSV (eksport z Google Sheets):
  1. szuka w internecie strony produktu po kodzie producenta i EAN i sprawdza,
     czy ten kod / EAN faktycznie występuje na stronie (weryfikacja),
  2. generuje opis SEO w czystym HTML przez Gemini albo OpenAI WYŁĄCZNIE na podstawie
     nazwy i tekstu tej strony; model dodatkowo ocenia, czy strona opisuje ten sam produkt,
  3. bierze zdjęcie z potwierdzonej strony (albo z wyszukiwarki obrazów, jeśli jego źródło
     też zawiera kod / EAN),
  4. pobiera zdjęcie do folderu zdjecia/ i sprawdza, czy to prawdziwy plik obrazu,
  5. nadaje status:
       PEWNY          — wszystko potwierdzone kodem/EAN -> plik *_pewne.csv (do importu),
       DO_AKCEPTACJI  — cokolwiek niepotwierdzone (z podanym powodem) -> plik *_do_akceptacji.csv.

Po przejrzeniu pliku do akceptacji (kolumna "Akceptacja" = TAK) komenda
    py wzbogac_produkty.py --output produkty_wzbogacone.csv --zatwierdz zaakceptowane.csv
tworzy plik *_do_importu.csv = produkty pewne + zaakceptowane ręcznie.

Postęp jest zapisywany co CHECKPOINT_EVERY wierszy. Po restarcie skrypt zaczyna od
pierwszego niezapisanego wiersza, więc nic nie jest generowane (ani opłacane) dwa razy.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from openai import OpenAI
import openai

# =============================================================================
# KONFIGURACJA — TUTAJ WPISUJESZ KLUCZE API
# =============================================================================
# Najbezpieczniej ustawić je jako zmienne środowiskowe (patrz README.md).
# Możesz też wpisać klucz bezpośrednio między cudzysłowy zamiast "" —
# wtedy nie wysyłaj tego pliku nikomu i nie wrzucaj go do repozytorium.

# Model AI do pisania opisów — parametr --ai lub poniżej: "gemini" albo "openai".
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")          # dla "gemini", np. "AIza..."
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")          # dla "openai", np. "sk-proj-..."

# Wyszukiwarka (strony produktów + zdjęcia) — parametr --search lub poniżej:
#   "ddg"     — DuckDuckGo, darmowe, bez klucza (przy dużej liczbie zapytań potrafi blokować)
#   "serpapi" — SerpApi (Google), płatne, najlepsza trafność i stabilność
#   "google"  — Google Custom Search JSON API (100 zapytań/dzień za darmo)
SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "ddg")

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")        # tylko dla "serpapi"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")          # tylko dla "google"
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")            # tylko dla "google" (identyfikator "cx")

# Strony producentów — przeszukiwane NAJPIERW (najlepsze zdjęcia, bez znaków wodnych, pełne dane).
# Klucz: nazwa producenta dokładnie jak w kolumnie /producer@name (wielkość liter bez znaczenia).
# Dopisz kolejnych producentów z bazy, np. "HAWLE": ["hawle.pl"].
PRODUCER_SITES = {
    # AEON: aeon-sale.com nie pokazuje kodów producenta (AG0828) ani EAN i nie ma go w indeksie
    # wyszukiwarek, więc nie da się na nim potwierdzić produktu — produkty AEON potwierdzają hurtownie
    # z TRUSTED_SITES. Tu wpisuj tylko strony producentów, które pokazują kod lub EAN produktu.
}
# Wyszukiwarki sklepów producentów, w których kodów nie ma, ale są dobre zdjęcia serii. Skrypt szuka tam
# po nazwie serii (z karty hurtowni), a model AI wybiera z wyników pozycję zgodną z produktem.
# {q} = zapytanie. Strona nie musi być w indeksie DuckDuckGo — skrypt używa jej własnej wyszukiwarki.
PRODUCER_SEARCH = {
    "AEON": "https://aeon-sale.com/?s={q}&post_type=product",
}
# Bezpośrednie wyszukiwanie po kodzie we własnej wyszukiwarce hurtowni — bez DuckDuckGo, więc wynik jest
# powtarzalny i szybszy. "url": adres strony wyników ({q} = kod), "link": fragment adresu karty produktu.
# Skrypt bierze tylko karty, w których adresie jest kod produktu (warianty z innymi kodami odpadają).
DIRECT_SEARCH = {
    # Onninen (https://onninen.pl/szukaj-produktow?query=/szukaj:{q}) blokuje automatyczne pobieranie
    # (HTTP 403), więc go tu nie ma. Dopisuj hurtownie, których wyszukiwarka odpowiada skryptowi —
    # sprawdzisz to komendą:  py wzbogac_produkty.py --test-wyszukiwarki AG0828
}
# Hurtownie z rzetelnymi kartami produktów (kod producenta + EAN) — przeszukiwane zaraz po stronach producenta.
TRUSTED_SITES = ["cetel-hurtownia.pl", "mateomarket.pl"]  # Onninen odpada — blokuje skrypty (HTTP 403)
# Strony, które nakładają znak wodny na zdjęcia — skrypt bierze z nich tylko tekst (potwierdzenie kodu/EAN,
# dane do opisu), a zdjęcie szuka gdzie indziej. Dotyczy też ich serwerów ze zdjęciami (np. img.onninen…).
WATERMARK_SITES = ["onninen.pl"]

# =============================================================================
# USTAWIENIA PRZETWARZANIA
# =============================================================================
# Gemini udostępnia interfejs zgodny z OpenAI, więc oba działają przez tę samą bibliotekę `openai`.
AI_PROVIDERS = {
    # Gdy model jest przeciążony (503) albo niedostępny (404), skrypt bierze kolejny z listy.
    "gemini": {"name": "Gemini",
               "models": os.getenv("GEMINI_MODEL", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash").split(","),
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": "GEMINI_API_KEY", "key_url": "https://aistudio.google.com/apikey",
               # Modele Gemini 3.x "myślą" przed odpowiedzią i liczą to do limitu tokenów —
               # mały limit uciąłby opis w połowie.
               "max_tokens": 8000},
    "openai": {"name": "OpenAI", "models": os.getenv("OPENAI_MODEL", "gpt-4o-mini").split(","),
               "base_url": None,
               "key_env": "OPENAI_API_KEY", "key_url": "https://platform.openai.com/api-keys",
               "max_tokens": 3000},  # opis HTML po polsku + pola JSON (nazwa, zapytanie)
    # Ollama — model uruchomiony lokalnie na Twoim komputerze (darmowy, bez klucza, potrzebna dobra karta graficzna).
    "ollama": {"name": "Ollama", "models": os.getenv("OLLAMA_MODEL", "gemma3:12b").split(","),
               "base_url": os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/v1",
               "key_env": None, "key_url": "https://ollama.com/download",
               "max_tokens": 3000,  # opis HTML po polsku + pola JSON — 1500 ucinało odpowiedź
               "timeout": 600},  # lokalny model bywa wolny, szczególnie bez karty graficznej
}
AI = dict(AI_PROVIDERS["gemini"], key="")  # ustawiane w main() przez configure_ai()
CHECKPOINT_EVERY = 10        # co ile wierszy zapisywać postęp na dysk
RETRY_WAIT_SECONDS = 5       # ile czekać przed ponowieniem po błędzie
MAX_RETRIES = 6              # ile razy ponawiać jedno zapytanie, zanim wiersz zostanie pominięty
REQUEST_TIMEOUT = 60         # timeout zapytań do API (sekundy)
OLLAMA_NUM_CTX = 8192        # kontekst lokalnego modelu (tokeny) — mieści polecenie + tekst strony
AI_TIMEOUT = 180             # timeout odpowiedzi modelu AI — przy przeciążeniu Gemini odpowiada wolno
PAGE_TIMEOUT = 20            # timeout pobierania pojedynczej strony produktu (sekundy)
MAX_PAGES_PER_PRODUCT = 12   # ile stron z wyników wyszukiwania sprawdzić łącznie na produkt
MAX_PAGES_PER_QUERY = 3      # …i ile z wyników jednego zapytania (żeby jedno złe zapytanie nie zużyło limitu)
SOURCE_EXCERPT_CHARS = 5000  # ile znaków tekstu strony przekazać modelowi
IMAGES_DIR = "zdjecia"       # folder na pobrane zdjęcia (obok pliku wynikowego)
MAX_IMAGE_BYTES = 15_000_000
MIN_IMAGE_BYTES = 2_000      # mniejsze pliki to zwykle ikonki/piksele śledzące, nie zdjęcia produktu
DEFAULT_WORKERS = 3          # ile wierszy przetwarzać równolegle w ramach jednej paczki

# Nazwy kolumn wejściowych (dokładnie jak w eksporcie z IdoSell / Google Sheets)
COL_ID = "@id"
COL_CODE = "@code_producer"
COL_PRODUCER = "/producer@name"
COL_EAN = "/sizes/size@code_producer"
COL_CATEGORY = "/navigation/site/menu/item@textid[pol]"
COL_NAME = "/description/name[pol]"

# Kolumny dopisywane przez skrypt
COL_DESC = "Opis_HTML"
COL_IMAGE = "Zdjecie_URL"
COL_IMAGE_FILE = "Zdjecie_plik"
COL_SOURCE = "Zrodlo_URL"
COL_STATUS = "Status"
COL_REASON = "Powod"
COL_ACCEPT = "Akceptacja"
NEW_COLUMNS = [COL_DESC, COL_IMAGE, COL_IMAGE_FILE, COL_SOURCE, COL_STATUS, COL_REASON]

STATUS_OK = "PEWNY"
STATUS_REVIEW = "DO_AKCEPTACJI"
ACCEPT_VALUES = {"TAK", "T", "OK", "X", "1", "YES", "Y"}

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")  # .webp: m.in. sklepy na WordPressie (np. AEON)
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.5",
}

SYSTEM_PROMPT = "Jesteś ekspertem SEO w branży instalacyjnej i B2B."

ABBREVIATIONS = """DN80 = średnica nominalna 80 mm, PN16 = ciśnienie nominalne 16 bar,
KOŁN. = kołnierzowa (połączenie kołnierzowe), KR. = krótka (krótka długość zabudowy, np. KR. F4),
F4/F5 = długość zabudowy wg normy EN 558 (F4 krótka, F5 długa), ŻEL. = żeliwna,
RK/RR = łącznik rurowo-kołnierzowy / rurowo-rurowy, D225 lub OD63 = średnica
zewnętrzna rury w mm, PE/PVC = do rur z polietylenu i PVC, Z PE = z końcówkami PE"""

PROMPT_WITH_SOURCE = """Napisz opis produktu do sklepu internetowego B2B.

DANE Z KARTOTEKI:
- Nazwa techniczna: {name}
- Producent: {producer}
- Kod producenta: {code}
- EAN: {ean}
- Kategoria: {category}

TEKST ZE STRONY ŹRÓDŁOWEJ ({url}):
\"\"\"
{source}
\"\"\"

Zasady:
1. Najpierw sprawdź, czy tekst ze strony dotyczy DOKŁADNIE tego produktu: ten sam kod lub EAN
   i parametry zgodne z nazwą (np. DN, PN, średnice). Jeśli cokolwiek się nie zgadza,
   ustaw "ten_sam_produkt": false i w "uwagi" napisz krótko, co.
2. Opis: około 1000 znaków (bez znaczników HTML). Rozwiń skróty techniczne z nazwy, np.
   {abbreviations}. Wyjaśnij zastosowanie produktu.
3. Używaj WYŁĄCZNIE faktów z nazwy i z tekstu źródłowego. Jeśli czegoś tam nie ma
   (materiał, norma, masa, wymiary, certyfikaty), POMIŃ to — nie zgaduj. Pisz rzeczowo.
4. Formatowanie opisu: wyłącznie czysty HTML z tagami <h2>, <p>, <ul>, <li>, <strong>.
   Bez <html>, <body>, stylów i Markdown. Zacznij od <h2> z czytelną nazwą produktu.
5. Nie wspominaj w opisie o stronie źródłowej ani o innych sklepach.
6. "pelna_nazwa": pełna nazwa handlowa produktu ze strony źródłowej (bez ceny i kodów sklepu).
7. "zapytanie_producent": 2–5 słów do znalezienia TEJ SERII w sklepie producenta: nazwa serii/linii
   (np. OptiValve), rodzaj produktu i najważniejsza cecha odróżniająca (np. "zasuwa gaz kołnierzowa
   OptiValve typ A"). Bez średnicy DN, ciśnienia PN i kodów. Puste, jeśli nie da się ustalić.

Odpowiedz WYŁĄCZNIE obiektem JSON:
{{"ten_sam_produkt": true lub false, "uwagi": "...", "opis_html": "...",
  "pelna_nazwa": "...", "zapytanie_producent": "..."}}"""

PROMPT_PICK_PRODUCER_ITEM = """Szukamy w sklepie producenta {producer} zdjęcia produktu:
- Nazwa w kartotece: {name}
- Pełna nazwa z hurtowni: {full_name}

Wyniki wyszukiwania w sklepie producenta:
{items}

Wskaż numer pozycji, która jest DOKŁADNIE tym samym rodzajem i serią produktu. Muszą się zgadzać
(jeśli są podane): rodzaj (np. zasuwa, łącznik), przeznaczenie (gaz / woda), sposób połączenia
(kołnierzowa, z króćcami PE, kielichowa, gwintowana), typ i seria (np. OptiValve typ A vs OptiValve Plus),
długość zabudowy (F4 / F5), materiał korpusu. Pozycja może dotyczyć całej serii bez podanej średnicy;
jeśli jednak ma podaną INNĄ średnicę DN niż szukany produkt — nie pasuje. Gdy nie masz pewności, zwróć 0.

Odpowiedz WYŁĄCZNIE obiektem JSON: {{"numer": liczba, "uzasadnienie": "krótko"}}"""

PROMPT_NAME_ONLY = """Napisz opis produktu do sklepu internetowego B2B.

DANE Z KARTOTEKI:
- Nazwa techniczna: {name}
- Producent: {producer}
- Kod producenta: {code}
- Kategoria: {category}

Nie mamy karty katalogowej tego produktu, więc:
1. Opis: około 800–1000 znaków (bez znaczników HTML). Rozwiń skróty techniczne z nazwy, np.
   {abbreviations}. Wyjaśnij typowe zastosowanie tego rodzaju produktu.
2. Używaj WYŁĄCZNIE informacji wynikających z nazwy i kategorii. NIE podawaj materiałów,
   norm, masy, wymiarów ani certyfikatów, których nie ma w nazwie.
3. Formatowanie opisu: wyłącznie czysty HTML z tagami <h2>, <p>, <ul>, <li>, <strong>.
   Bez <html>, <body>, stylów i Markdown. Zacznij od <h2> z czytelną nazwą produktu.

Odpowiedz WYŁĄCZNIE obiektem JSON:
{{"opis_html": "..."}}"""

log = logging.getLogger("wzbogacanie")


# =============================================================================
# PONAWIANIE PRÓB
# =============================================================================
class FatalError(Exception):
    """Błąd, którego ponawianie nie ma sensu (np. zły klucz API) — zatrzymuje skrypt."""


def with_retry(func, *args, what: str = "zapytanie", **kwargs):
    """Wywołuje func; przy błędzie czeka RETRY_WAIT_SECONDS i ponawia (do MAX_RETRIES razy).

    Zwraca wynik func albo None, jeśli wszystkie próby się nie powiodły
    (wiersz trafi wtedy do akceptacji, a skrypt jedzie dalej).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except FatalError:
            raise
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise FatalError(f"{AI['name']} odrzuciło klucz API: {exc}") from exc
        except openai.BadRequestError as exc:
            # Błędne zapytanie nie naprawi się samo — nie ma sensu czekać.
            log.error("%s: błędne zapytanie, pomijam: %s", what, exc)
            return None
        except Exception as exc:  # timeout, rate limit, błąd sieci, 5xx...
            msg = str(exc)
            if "insufficient_quota" in msg:
                raise FatalError(f"Brak środków na koncie {AI['name']} (insufficient_quota).") from exc
            log.warning("%s: błąd (próba %d/%d): %s: %s",
                        what, attempt, MAX_RETRIES, type(exc).__name__, msg[:200])
            if attempt < MAX_RETRIES:
                # Przy kolejnych błędach z rzędu czekamy dłużej (5 s, 10 s, 15 s...),
                # co pomaga przy limitach zapytań (rate limit).
                time.sleep(RETRY_WAIT_SECONDS * attempt)
    log.error("%s: wszystkie %d próby nieudane.", what, MAX_RETRIES)
    return None


# =============================================================================
# WYSZUKIWARKI
# =============================================================================
def _ddgs():
    try:
        from ddgs import DDGS  # nowa nazwa paczki
    except ImportError:
        from duckduckgo_search import DDGS  # starsza nazwa paczki
    return DDGS(timeout=REQUEST_TIMEOUT)


def _serpapi(params: dict) -> dict:
    resp = requests.get("https://serpapi.com/search.json",
                        params={**params, "api_key": SERPAPI_API_KEY, "hl": "pl", "gl": "pl"},
                        timeout=REQUEST_TIMEOUT)
    if resp.status_code in (401, 403):
        raise FatalError(f"SerpApi odrzuciło klucz API (HTTP {resp.status_code}).")
    resp.raise_for_status()
    data = resp.json()
    if "error" in data and "hasn't returned any results" not in data["error"]:
        raise RuntimeError(f"SerpApi: {data['error']}")
    return data


def _google(params: dict) -> dict:
    resp = requests.get("https://www.googleapis.com/customsearch/v1",
                        params={**params, "key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "num": 10},
                        timeout=REQUEST_TIMEOUT)
    if resp.status_code in (400, 401, 403) and "quota" not in resp.text.lower():
        raise FatalError(f"Google Custom Search odrzucił zapytanie (HTTP {resp.status_code}): {resp.text[:200]}")
    resp.raise_for_status()  # 429 / limit dzienny -> zwykły błąd, ponawiamy
    return resp.json()


def _ddg_call(method: str, query: str, max_results: int) -> list[dict]:
    try:
        return getattr(_ddgs(), method)(query, region="pl-pl", max_results=max_results) or []
    except Exception as exc:
        # "No results found" to odpowiedź, nie awaria — nie ma sensu ponawiać tego samego zapytania.
        # "malformed headers" — DuckDuckGo odrzuca tę postać zapytania; ponowienie da ten sam wynik.
        msg = str(exc).lower()
        if "no results" in msg or "malformed headers" in msg:
            log.debug("DuckDuckGo (%s) '%s': %s", method, query, exc)
            return []
        raise


def search_pages(query: str, engine: str) -> list[str]:
    """Zwraca listę adresów stron z wyników wyszukiwania."""
    if engine == "ddg":
        return [r.get("href", "") for r in _ddg_call("text", query, 10)]
    if engine == "serpapi":
        return [r.get("link", "") for r in _serpapi({"engine": "google", "q": query}).get("organic_results", [])]
    return [i.get("link", "") for i in _google({"q": query}).get("items", [])]


def search_images(query: str, engine: str) -> list[dict]:
    """Zwraca listę {"image": url_obrazka, "page": url_strony, "title": tytuł}."""
    if engine == "ddg":
        return [{"image": r.get("image", ""), "page": r.get("url", ""), "title": r.get("title", "")}
                for r in _ddg_call("images", query, 20)]
    if engine == "serpapi":
        data = _serpapi({"engine": "google_images", "q": query})
        return [{"image": r.get("original", ""), "page": r.get("link", ""), "title": r.get("title", "")}
                for r in data.get("images_results", [])]
    data = _google({"q": query, "searchType": "image"})
    return [{"image": i.get("link", ""), "page": (i.get("image") or {}).get("contextLink", ""),
             "title": i.get("title", "")} for i in data.get("items", [])]


# =============================================================================
# WERYFIKACJA: CZY STRONA / ZDJĘCIE DOTYCZY TEGO PRODUKTU
# =============================================================================
def code_regex(code: str) -> re.Pattern | None:
    """Wzorzec dopasowujący kod jako osobny ciąg, z tolerancją na spacje/myślniki (AG-0828, AG 0828)."""
    chars = [re.escape(c) for c in code if c.isalnum()]
    if len(chars) < 4:  # zbyt krótki kod dałby przypadkowe trafienia
        return None
    return re.compile(r"(?<![0-9A-Za-z])" + r"[\s\-./]?".join(chars) + r"(?![0-9A-Za-z])", re.IGNORECASE)


class ProductMatcher:
    """Sprawdza, czy tekst zawiera EAN albo kod producenta (+ nazwę producenta)."""

    def __init__(self, code: str, ean: str, producer: str):
        self.ean_re = code_regex(ean) if len(re.sub(r"\D", "", ean)) >= 8 else None
        self.code_re = code_regex(code)
        self.producer = producer.lower()

    def find(self, text: str) -> int | None:
        """Pozycja potwierdzającego dopasowania w tekście albo None."""
        if self.ean_re and (m := self.ean_re.search(text)):
            return m.start()
        if self.code_re and (m := self.code_re.search(text)):
            # Sam kod (np. AG0828) może się powtórzyć u innego producenta — wymagamy też nazwy producenta.
            if not self.producer or self.producer in text.lower():
                return m.start()
        return None

    def in_short_text(self, *texts: str) -> bool:
        """Dopasowanie w krótkich tekstach (tytuł, adres URL) — wystarczy sam kod lub EAN."""
        joined = " ".join(texts)
        return bool((self.ean_re and self.ean_re.search(joined)) or (self.code_re and self.code_re.search(joined)))


def is_direct_image_url(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    return urlparse(url).path.lower().endswith(IMAGE_EXTENSIONS)


def html_doc(resp: requests.Response):
    """Drzewo HTML z poprawnym kodowaniem (polskie znaki) także dla stron, które go nie deklarują."""
    import lxml.html

    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"  # requests domyślnie zakłada latin-1
    text = resp.text[:3_000_000]
    try:
        return lxml.html.fromstring(text)
    except ValueError:  # deklaracja <?xml encoding=...?> w napisie — lxml chce wtedy bajtów
        return lxml.html.fromstring(text.encode("utf-8"))


def largest_from_srcset(srcset: str) -> str:
    """Największy wariant z atrybutu srcset ("a.jpg 300w, b.jpg 1200w")."""
    best, best_w = "", -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        width = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
        if width > best_w:
            best, best_w = bits[0], width
    return best


BLOCK_AFTER = 3              # po tylu odmowach (403) z rzędu domena jest pomijana do końca przebiegu
_refusals: dict[str, int] = {}
_refusals_lock = threading.Lock()


def domain_blocked(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    with _refusals_lock:
        return _refusals.get(host, 0) >= BLOCK_AFTER


def note_response(url: str, status: int) -> None:
    """Liczy odmowy dostępu per domena — strony blokujące skrypty nie zabierają czasu przy kolejnych produktach."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    with _refusals_lock:
        if status in (401, 403):
            _refusals[host] = _refusals.get(host, 0) + 1
            if _refusals[host] == BLOCK_AFTER:
                log.warning("Strona %s blokuje automatyczne pobieranie (HTTP %d) — pomijam ją do końca przebiegu.",
                            host, status)
        elif status == 200:
            _refusals[host] = 0


def fetch_page(url: str) -> tuple[str, list[str], list[tuple[str, str]], str] | None:
    """Pobiera stronę HTML. Zwraca (tekst, obrazki og:image, [(src, alt) wszystkich <img>], tytuł) albo None."""
    if domain_blocked(url):
        return None
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=PAGE_TIMEOUT)
        note_response(url, resp.status_code)
        if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "").lower():
            return None
        doc = html_doc(resp)
    except Exception as exc:
        log.debug("Nie udało się pobrać %s: %s", url, exc)
        return None

    titles = doc.xpath('//meta[@property="og:title"]/@content') or doc.xpath("//h1//text()") or doc.xpath("//title/text()")
    title = re.sub(r"\s+", " ", " ".join(t.strip() for t in titles[:1])).strip()
    og_images = [urljoin(url, u) for u in doc.xpath(
        '//meta[@property="og:image" or @name="og:image" or @name="twitter:image"]/@content')]
    imgs = []
    for img in doc.xpath("//img"):
        # Pełny rozmiar: WooCommerce trzyma go w data-large_image, inne sklepy w data-zoom-image / srcset.
        src = (img.get("data-large_image") or img.get("data-zoom-image") or img.get("data-large")
               or largest_from_srcset(img.get("data-srcset") or img.get("srcset") or "")
               or img.get("data-src") or img.get("src") or "")
        if src and not src.startswith("data:"):
            imgs.append((urljoin(url, src), img.get("alt", "") or img.get("title", "")))
    for bad in doc.xpath("//script|//style|//noscript|//svg"):
        bad.drop_tree()
    # itertext + spacja: sąsiednie znaczniki (<h1>…</h1><p>Kod…) nie sklejają się w jedno słowo.
    text = re.sub(r"\s+", " ", " ".join(doc.itertext())).strip()
    return text, og_images, imgs, title


def producer_sites(producer: str) -> list[str]:
    return PRODUCER_SITES.get(producer.strip().upper(), [])


def is_producer_url(url: str, producer: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in producer_sites(producer))


def direct_search_urls(m: ProductMatcher, code: str) -> list[str]:
    """Karty produktów z wyszukiwarek hurtowni (DIRECT_SEARCH), które mają kod produktu w adresie."""
    from urllib.parse import quote_plus

    urls: list[str] = []
    for domain, cfg in DIRECT_SEARCH.items():
        try:
            resp = requests.get(cfg["url"].format(q=quote_plus(code)), headers=BROWSER_HEADERS, timeout=PAGE_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            log.debug("Wyszukiwarka %s niedostępna: %s", domain, exc)
            continue
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        # Szukamy adresów w całym kodzie strony — działa też, gdy wyniki są w danych JSON dla JavaScriptu.
        link = re.escape(cfg["link"])
        page = resp.text.replace("\\/", "/")  # JSON zapisuje ukośniki jako \/
        for found in re.findall(rf'(?:https?://[^"\'\s<>]*)?{link}[^"\'\s<>\\]+', page):
            url = urljoin(f"https://{domain}/", found)
            if m.in_short_text(url) and url not in urls:
                urls.append(url)
    return urls


def test_direct_search(code: str) -> None:
    """Diagnostyka: co zwracają wyszukiwarki hurtowni dla kodu i czy karta zawiera ten kod."""
    from urllib.parse import quote_plus

    m = ProductMatcher(code, "", "")
    for domain, cfg in DIRECT_SEARCH.items():
        url = cfg["url"].format(q=quote_plus(code))
        print(f"\n{domain}: {url}")
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=PAGE_TIMEOUT)
            print(f"  odpowiedź: HTTP {resp.status_code}, {len(resp.content)} bajtów, "
                  f"'{cfg['link']}' występuje {resp.text.count(cfg['link'])} razy, kod {code}: "
                  f"{'jest' if code.lower() in resp.text.lower() else 'BRAK'} w kodzie strony")
        except Exception as exc:
            print(f"  błąd: {exc}")
            continue
    urls = direct_search_urls(m, code)
    print(f"\nZnalezione karty z kodem {code}: {len(urls)}")
    for url in urls:
        page = fetch_page(url)
        ok = page is not None and m.find(page[0]) is not None
        print(f"  {url}\n    kod na stronie karty: {'TAK' if ok else 'NIE'}")
    if not urls:
        print("  Brak — wyszukiwarka pewnie ładuje wyniki przez JavaScript; skrypt użyje wtedy DuckDuckGo.")


def find_verified_source(m: ProductMatcher, code: str, ean: str, producer: str, engine: str,
                         row_label: str) -> dict | None:
    """Szuka strony, na której występuje kod/EAN produktu — najpierw w wyszukiwarkach hurtowni."""
    # Najpierw zwykłe zapytania (najczęściej trafiają), "site:" tylko gdy one nic nie dadzą —
    # każde dodatkowe zapytanie zwiększa ryzyko, że DuckDuckGo zacznie blokować.
    queries = []
    if code:
        queries.append(f'"{code}" {producer}'.strip())
        queries.append(f"{producer} {code}".strip())  # bez cudzysłowu — część wyszukiwarek źle je obsługuje
    if ean:
        queries.append(ean)
    if code:  # na wybranych stronach tylko po kodzie
        queries += [f"site:{domain} {code}" for domain in producer_sites(producer) + TRUSTED_SITES]

    checked: set[str] = set()
    direct = direct_search_urls(m, code) if code else []
    for query in ([None] if direct else []) + queries:
        if query is None:
            urls = direct  # wyniki z wyszukiwarek hurtowni — sprawdzane przed DuckDuckGo
        else:
            urls = [u for u in (with_retry(search_pages, query, engine, what=f"{row_label} szukanie '{query}'") or [])
                    if u and u not in checked and not urlparse(u).path.lower().endswith(".pdf")]
        if query and query.startswith("site:"):
            # Część wyszukiwarek ignoruje "site:" i zwraca przypadkowe strony — zostawiamy tylko tę domenę.
            domain = query.split()[0].removeprefix("site:")
            urls = [u for u in urls if urlparse(u).netloc.lower().removeprefix("www.").endswith(domain)]
        # Najpierw adresy, w których jest kod/EAN (np. onninen.pl/produkt/…-AG0828) — najczęściej trafione.
        urls.sort(key=lambda u: not m.in_short_text(u))
        for url in urls[:MAX_PAGES_PER_QUERY]:
            if len(checked) >= MAX_PAGES_PER_PRODUCT:
                return None
            checked.add(url)
            page = fetch_page(url)
            if not page:
                continue
            text, og_images, imgs = page[0], page[1], page[2]
            pos = m.find(text)
            if pos is None:
                continue
            start = max(0, pos - SOURCE_EXCERPT_CHARS // 3)
            return {"url": url, "excerpt": text[start:start + SOURCE_EXCERPT_CHARS],
                    "og_images": og_images, "imgs": imgs, "producer_site": is_producer_url(url, producer),
                    "title": page[3] if len(page) > 3 else ""}
    return None


def is_watermark_site(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) or d.split(".")[0] in host for d in WATERMARK_SITES)


def image_candidates_from_source(m: ProductMatcher, source: dict) -> list[str]:
    """Zdjęcia ze zweryfikowanej strony: najpierw <img> z kodem/EAN w nazwie lub opisie, potem og:image."""
    if is_watermark_site(source["url"]):
        return []  # strona ze znakami wodnymi — zdjęcie znajdzie wyszukiwarka obrazów
    with_code = [src for src, alt in source["imgs"] if is_direct_image_url(src) and m.in_short_text(src, alt)]
    og = [src for src in source["og_images"] if is_direct_image_url(src)]
    # Producent często ma jedno zdjęcie na całą serię (DN50–DN300) bez kodu w nazwie pliku —
    # na jego stronie bierzemy też pozostałe zdjęcia (logo/ikonki odpadną w filtrach).
    rest = [src for src, _ in source["imgs"] if is_direct_image_url(src)] if source.get("producer_site") else []
    return list(dict.fromkeys(with_code + og + rest))


_shop_cache: dict[str, list[tuple[str, str]]] = {}
_shop_cache_lock = threading.Lock()


def search_producer_shop(template: str, query: str) -> list[tuple[str, str]]:
    """[(tytuł, adres)] produktów z wyników wewnętrznej wyszukiwarki sklepu producenta (z pamięcią podręczną)."""
    from urllib.parse import quote_plus

    url = template.format(q=quote_plus(query))
    with _shop_cache_lock:
        if url in _shop_cache:
            return _shop_cache[url]
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=PAGE_TIMEOUT)
    resp.raise_for_status()
    doc = html_doc(resp)
    found: dict[str, str] = {}
    for a in doc.xpath("//a[@href]"):
        href = urljoin(url, a.get("href"))
        if "/product/" not in href or "add-to-cart" in href:
            continue
        title = re.sub(r"\s+", " ", a.text_content()).strip()
        if len(title) > len(found.get(href, "")):
            found[href] = title
    items = [(t, h) for h, t in found.items() if len(t) >= 5][:40]
    with _shop_cache_lock:
        _shop_cache[url] = items
    return items


def image_candidates_from_producer_shop(row: dict, source: dict | None, ai_result: dict | None,
                                        row_label: str) -> list[tuple[str, bool, str]]:
    """Zdjęcia z karty serii w sklepie producenta, wybranej przez AI z wyników wyszukiwania po nazwie."""
    template = PRODUCER_SEARCH.get(row["producer"].strip().upper())
    query = str((ai_result or {}).get("zapytanie_producent") or "").strip()
    if not template or not query or not source or (ai_result or {}).get("ten_sam_produkt") is not True:
        return []
    words = query.split()
    items: list[tuple[str, str]] = []
    # Sklep szuka wszystkich słów naraz — przy braku wyników skracamy zapytanie od końca (max 3 próby).
    for n in range(len(words), max(len(words) - 3, 1), -1):
        items = with_retry(search_producer_shop, template, " ".join(words[:n]),
                           what=f"{row_label} sklep producenta '{' '.join(words[:n])}'") or []
        if items:
            break
    if not items:
        return []
    listing = "\n".join(f"{i}. {title}" for i, (title, _) in enumerate(items, 1))
    full_name = str(ai_result.get("pelna_nazwa") or source.get("title") or row["name"])
    answer = with_retry(ai_json, PROMPT_PICK_PRODUCER_ITEM.format(
        producer=row["producer"], name=row["name"], full_name=full_name, items=listing),
        what=f"{row_label} wybór w sklepie producenta") or {}
    try:
        number = int(answer.get("numer") or 0)
    except (TypeError, ValueError):
        number = 0
    if not 1 <= number <= len(items):
        log.info("%s sklep producenta: brak pasującej pozycji (%s)", row_label, answer.get("uzasadnienie", ""))
        return []
    title, url = items[number - 1]
    log.info("%s sklep producenta: %s", row_label, title)
    page = fetch_page(url)
    if not page:
        return []
    shop_source = {"url": url, "og_images": page[1], "imgs": page[2], "producer_site": True}
    return [(u, True, url) for u in image_candidates_from_source(ProductMatcher("", "", ""), shop_source)]


def image_candidates_from_search(m: ProductMatcher, code: str, ean: str, producer: str, engine: str,
                                 row_label: str) -> list[tuple[str, bool, str]]:
    """Zdjęcia z wyszukiwarki obrazów: [(url, czy_potwierdzone_kodem, strona_źródłowa)], potwierdzone najpierw."""
    verified: list[tuple[str, bool, str]] = []
    unverified: list[tuple[str, bool, str]] = []
    # Bez "site:" — wyszukiwarka obrazów DuckDuckGo odrzuca takie zapytania ("malformed headers").
    # Zwykłe zapytanie z kodem i tak zwraca zdjęcia z hurtowni i sklepów, które ten kod mają.
    queries = [q for q in (f"{producer} {code}".strip() if code else "", ean) if q]
    for query in queries:
        results = with_retry(search_images, query, engine, what=f"{row_label} zdjęcie '{query}'") or []
        candidates = [r for r in results if is_direct_image_url(r["image"]) and not image_url_looks_bad(r["image"])
                      and not is_watermark_site(r["image"]) and not is_watermark_site(r["page"] or "")]
        pages_checked = 0
        for r in candidates:
            if m.in_short_text(r["image"], r["title"], r["page"]):
                verified.append((r["image"], True, r["page"]))
            elif r["page"] and pages_checked < 3:
                # Kod nie występuje w tytule/adresie — sprawdzamy stronę, z której pochodzi obrazek.
                pages_checked += 1
                page = fetch_page(r["page"])
                if page and m.find(page[0]) is not None:
                    verified.append((r["image"], True, r["page"]))
                else:
                    unverified.append((r["image"], False, r["page"]))
            else:
                unverified.append((r["image"], False, r["page"]))
        if verified:
            break  # mamy potwierdzone zdjęcia — nie trzeba szukać po EAN
    return verified + unverified[:3]


# =============================================================================
# OPIS HTML (Gemini / OpenAI)
# =============================================================================
_openai_client: OpenAI | None = None


# Gdy OLLAMA_MODEL nie jest ustawione, skrypt bierze pierwszy pobrany model z tej listy (od najlepszego po polsku).
OLLAMA_PREFERRED = ["qwen2.5:14b-instruct", "qwen2.5:14b", "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M",
                    "gemma3:12b", "qwen3:14b", "qwen3:8b", "qwen2.5:7b-instruct", "qwen2.5:7b", "llama3.1:latest"]


def ollama_installed(settings: dict) -> set[str]:
    host = settings["base_url"].removesuffix("/v1")
    try:
        resp = requests.get(host + "/api/tags", timeout=10)
        resp.raise_for_status()
        return {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception:
        sys.exit(f"Ollama nie odpowiada pod adresem {host}. Zainstaluj ją z {settings['key_url']} "
                 "i uruchom (ikona Ollama w zasobniku systemowym), potem spróbuj ponownie.")


def check_ollama(settings: dict) -> None:
    """Sprawdza, czy Ollama działa i czy wybrany model jest pobrany."""
    installed = ollama_installed(settings)
    # "gemma3" bez tagu w Ollamie oznacza "gemma3:latest"
    names = installed | {n.removesuffix(":latest") for n in installed}
    missing = [m for m in settings["models"] if m.strip() not in names]
    if missing:
        sys.exit(f"W Ollamie brakuje modelu {missing[0]}. Pobierz go poleceniem:  ollama pull {missing[0]}\n"
                 f"Pobrane modele: {', '.join(sorted(installed)) or 'brak'}. "
                 "Inny model ustawisz zmienną OLLAMA_MODEL, np. $env:OLLAMA_MODEL=\"qwen3:8b\".")


def configure_ai(provider: str) -> None:
    """Wybiera dostawcę AI i sprawdza klucz. Kończy skrypt czytelnym komunikatem, jeśli klucza brak."""
    settings = AI_PROVIDERS[provider]
    if provider == "ollama":
        if not os.getenv("OLLAMA_MODEL"):
            installed = ollama_installed(settings)
            names = installed | {n.removesuffix(":latest") for n in installed}
            pick = next((m for m in OLLAMA_PREFERRED if m in names), None)
            if pick:
                settings = dict(settings, models=[pick])
        check_ollama(settings)
        AI.update(settings, key="ollama")  # Ollama nie sprawdza klucza, ale biblioteka wymaga jakiegoś
        return
    key = GEMINI_API_KEY if provider == "gemini" else OPENAI_API_KEY
    if not key:
        sys.exit(f"Brak klucza {settings['name']}. Ustaw zmienną środowiskową {settings['key_env']} "
                 f"(klucz z {settings['key_url']}) albo wpisz go w sekcji KONFIGURACJA na górze skryptu.")
    if "TWÓJ" in key.upper() or "..." in key:
        sys.exit(f"{settings['key_env']} zawiera przykładowy tekst zamiast prawdziwego klucza. "
                 f"Wklej swój klucz z {settings['key_url']}.")
    AI.update(settings, key=key)


def get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        # max_retries=0 — ponawianiem zajmuje się with_retry (5 s przerwy, logowanie).
        _openai_client = OpenAI(api_key=AI["key"], base_url=AI["base_url"],
                                timeout=AI.get("timeout", AI_TIMEOUT), max_retries=0)
    return _openai_client


def parse_json(text: str) -> dict:
    """JSON z odpowiedzi modelu — także gdy model owinie go w ```json ... ```."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Model nie zwrócił JSON: {text[:120]!r}")
        return json.loads(match.group(0))


def clean_html(text: str) -> str:
    """Usuwa ewentualne bloki ```html ... ``` i zbędne białe znaki."""
    text = text.strip()
    text = re.sub(r"^```(?:html)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r">\s*\n\s*<", "><", text)
    return text.strip()


def _call_openai(prompt: str) -> dict:
    """Zapytanie do wybranego dostawcy; przy przeciążeniu modelu próbuje kolejnych z listy."""
    last_error: Exception | None = None
    for model in AI["models"]:
        try:
            return _call_model(model.strip(), prompt)
        except (openai.InternalServerError, openai.NotFoundError, openai.APITimeoutError,
                openai.RateLimitError) as exc:
            # 503 "high demand" / model wycofany / brak odpowiedzi / limit (każdy model Gemini ma
            # osobny limit) — od razu kolejny model, bez czekania.
            log.debug("Model %s niedostępny: %s", model, exc)
            last_error = exc
    raise last_error  # wszystkie modele zajęte — with_retry odczeka i spróbuje ponownie


def _call_ollama(model: str, prompt: str) -> dict:
    """Ollama przez jej własne API — pozwala ustawić większy kontekst (num_ctx).

    Domyślny kontekst Ollamy (2–4 tys. tokenów) ucinałby tekst strony źródłowej.
    """
    host = AI["base_url"].removesuffix("/v1")
    resp = requests.post(host + "/api/chat", timeout=AI.get("timeout", AI_TIMEOUT), json={
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "options": {"num_ctx": OLLAMA_NUM_CTX, "temperature": 0.3, "num_predict": AI["max_tokens"]},
    })
    resp.raise_for_status()
    data = resp.json()
    if data.get("done_reason") == "length":
        raise ValueError("Odpowiedź ucięta (limit tokenów)")
    return parse_json((data.get("message") or {}).get("content") or "{}")


def ai_json(prompt: str) -> dict:
    """Dowolne zapytanie do modelu z odpowiedzią JSON (bez wymogu opisu)."""
    last_error: Exception | None = None
    for model in AI["models"]:
        try:
            return _call_model(model.strip(), prompt, require_desc=False)
        except (openai.InternalServerError, openai.NotFoundError, openai.APITimeoutError,
                openai.RateLimitError) as exc:
            last_error = exc
    raise last_error


def _call_model(model: str, prompt: str, require_desc: bool = True) -> dict:
    if AI["name"] == "Ollama":
        data = _call_ollama(model, prompt)
        if not require_desc:
            return data
        data["opis_html"] = clean_html(str(data.get("opis_html") or ""))
        if not data["opis_html"]:
            raise ValueError("Model zwrócił pusty opis")
        return data
    request = dict(
        model=model,
        temperature=0.3,
        max_tokens=AI["max_tokens"],
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": prompt}],
    )
    try:
        response = get_openai_client().chat.completions.create(**request)
    except openai.BadRequestError as exc:
        if "response_format" not in str(exc):
            raise
        # Model bez trybu JSON — prompt i tak każe odpowiedzieć JSON-em, parse_json sobie poradzi.
        del request["response_format"]
        response = get_openai_client().chat.completions.create(**request)
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise ValueError("Odpowiedź ucięta (limit tokenów)")
    data = parse_json(choice.message.content or "{}")
    if not require_desc:
        return data
    data["opis_html"] = clean_html(str(data.get("opis_html") or ""))
    if not data["opis_html"]:
        raise ValueError("Model zwrócił pusty opis")
    return data


def generate_description(row: dict, source: dict | None, row_label: str) -> dict | None:
    # Ścieżka kategorii z IdoSell ("A\B\C") jest czytelniejsza dla modelu jako "A > B > C".
    category = " > ".join(p.strip() for p in row["category"].split("\\") if p.strip()) or "brak danych"
    common = dict(name=row["name"], producer=row["producer"] or "brak danych",
                  code=row["code"] or "brak", ean=row["ean"] or "brak",
                  category=category, abbreviations=ABBREVIATIONS)
    if source:
        prompt = PROMPT_WITH_SOURCE.format(**common, url=source["url"], source=source["excerpt"])
    else:
        prompt = PROMPT_NAME_ONLY.format(**common)
    result = with_retry(_call_openai, prompt, what=f"{row_label} {AI['name']}")
    _track_ai_result(result is not None)
    return result


MAX_AI_FAILURES_IN_ROW = 5
_ai_failures = {"in_row": 0}
_ai_failures_lock = threading.Lock()


def _track_ai_result(ok: bool) -> None:
    """Zatrzymuje skrypt, gdy AI kilka razy z rzędu nie odpowiada (np. wyczerpany limit konta).

    Bez tego skrypt przeszedłby przez tysiące produktów bez opisów, zużywając zapytania wyszukiwarki.
    """
    with _ai_failures_lock:
        _ai_failures["in_row"] = 0 if ok else _ai_failures["in_row"] + 1
        if _ai_failures["in_row"] >= MAX_AI_FAILURES_IN_ROW:
            raise FatalError(
                f"{AI['name']} nie odpowiedziało dla {MAX_AI_FAILURES_IN_ROW} produktów z rzędu. "
                + ("Sprawdź, czy Ollama działa i czy komputerowi starcza pamięci na ten model. "
                   if AI["name"] == "Ollama" else
                   "Najczęstsza przyczyna: wyczerpany limit/brak płatności na koncie "
                   "(błąd 429 'exceeded your current quota'). Sprawdź konto. ")
                + "Po poprawieniu uruchom skrypt ponownie — zacznie od miejsca, w którym przerwał.")


# =============================================================================
# POBIERANIE ZDJĘĆ NA DYSK
# =============================================================================
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF8", ".gif"),
)


def image_type(head: bytes) -> str | None:
    """Rozszerzenie na podstawie nagłówka pliku — odrzuca strony błędów udające obrazek."""
    for signature, ext in IMAGE_SIGNATURES:
        if head.startswith(signature):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def safe_filename(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("_")[:80] or "produkt"


# Fragmenty adresów typowe dla logotypów, banerów i ikon — to nie są zdjęcia produktu.
BAD_IMAGE_WORDS = ("logo", "banner", "baner", "icon", "ikona", "favicon", "sprite", "placeholder",
                   "noimage", "no-image", "no_image", "brak-zdjecia", "brak_zdjecia", "nophoto", "no-photo",
                   "social", "share")
MIN_IMAGE_SIDE = 400         # px — mniejsze to miniaturki słabej jakości albo ikonki
MAX_IMAGE_RATIO = 1.9        # szerokość/wysokość — szersze to zwykle banery i logotypy
MIN_IMAGE_RATIO = 0.5


def image_url_looks_bad(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(word in path for word in BAD_IMAGE_WORDS)


def fetch_image(url: str, referer: str = "") -> tuple[bytes, str, str]:
    """Pobiera obrazek do pamięci. Zwraca (dane, rozszerzenie, "") albo (b"", "", powód_odrzucenia)."""
    if domain_blocked(url):
        return b"", "", "strona blokuje pobieranie"
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer  # część sklepów blokuje obrazki pobierane bez strony źródłowej
    try:
        with requests.get(url, headers=headers, timeout=PAGE_TIMEOUT, stream=True) as resp:
            note_response(url, resp.status_code)
            if resp.status_code != 200:
                return b"", "", f"HTTP {resp.status_code}"
            data = b""
            for chunk in resp.iter_content(64 * 1024):
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    return b"", "", "plik za duży"
    except Exception as exc:
        return b"", "", type(exc).__name__
    ext = image_type(data[:16])
    if not ext:
        return b"", "", "to nie jest plik obrazu"
    if len(data) < MIN_IMAGE_BYTES:
        return b"", "", "obrazek za mały"
    return data, ext, ""


def check_image(data: bytes) -> str:
    """Odrzuca ikonki, banery i logotypy po wymiarach. Zwraca powód odrzucenia albo ""."""
    try:
        from PIL import Image
    except ImportError:
        return ""  # bez Pillow nie sprawdzimy wymiarów — zostaje filtr adresu i sygnatury pliku
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
    except Exception:
        return "uszkodzony plik obrazu"
    if min(width, height) < MIN_IMAGE_SIDE:
        return f"obrazek za mały ({width}x{height})"
    if not MIN_IMAGE_RATIO <= width / height <= MAX_IMAGE_RATIO:
        return f"proporcje banera/logo ({width}x{height})"
    return ""


def save_image(data: bytes, ext: str, images_dir: str, base_name: str) -> str:
    os.makedirs(images_dir, exist_ok=True)
    # Usuwamy plik tego produktu z poprzedniego uruchomienia (mógł mieć inne rozszerzenie albo być błędny).
    for old_ext in (".jpg", ".png", ".webp", ".gif"):
        old = os.path.join(images_dir, base_name + old_ext)
        if os.path.exists(old):
            os.remove(old)
    if ext == ".webp":
        # Lokalna kopia jako JPG — pewniejsza przy ręcznym wgrywaniu do sklepu i w starszych programach.
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            out = io.BytesIO()
            img.convert("RGB").save(out, "JPEG", quality=92)
        data, ext = out.getvalue(), ".jpg"
    path = os.path.join(images_dir, base_name + ext)
    with open(path + ".part", "wb") as fh:
        fh.write(data)
    os.replace(path + ".part", path)
    return path


MAX_IMAGE_TRIES = 6          # ilu kandydatów na zdjęcie sprawdzić na produkt


def pick_image(candidates: list[tuple[str, bool, str]], images_dir: str | None,
               base_name: str, tried: set[str]) -> tuple[str, bool, str, str]:
    """Pierwsze zdjęcie, które przejdzie wszystkie filtry.

    Zwraca (url, czy_potwierdzone, ścieżka_pliku, powód_ostatniego_odrzucenia).
    """
    last_reason = ""
    for url, verified, referer in candidates:
        if url in tried or len(tried) >= MAX_IMAGE_TRIES:
            continue
        tried.add(url)
        if image_url_looks_bad(url):
            last_reason = "logo/baner w adresie"
            continue
        data, ext, reason = fetch_image(url, referer)
        if not reason:
            reason = check_image(data)
        if reason:
            last_reason = reason
            continue
        path = save_image(data, ext, images_dir, base_name) if images_dir else ""
        return url, verified, path, ""
    return "", False, "", last_reason


# =============================================================================
# PRZETWARZANIE WIERSZA
# =============================================================================
def process_row(record: dict, position: int, engine: str, images_dir: str | None) -> dict:
    """images_dir=None wyłącza pobieranie zdjęć na dysk."""
    def val(col: str) -> str:
        return str(record.get(col, "") or "").strip()

    row = {"name": val(COL_NAME), "producer": val(COL_PRODUCER), "code": val(COL_CODE),
           "ean": val(COL_EAN), "category": val(COL_CATEGORY)}
    row_label = f"[wiersz {position + 1}, id={val(COL_ID)}]"
    m = ProductMatcher(row["code"], row["ean"], row["producer"])
    reasons: list[str] = []

    # 1. Strona produktu potwierdzona kodem/EAN
    source = None
    if m.code_re or m.ean_re:
        source = find_verified_source(m, row["code"], row["ean"], row["producer"], engine, row_label)
    if not source:
        reasons.append("nie znaleziono strony z tym kodem/EAN — opis tylko z nazwy")

    # 2. Opis
    desc = ""
    result = None
    if not row["name"]:
        reasons.append("brak nazwy produktu")
    else:
        result = generate_description(row, source, row_label)
        if result:
            desc = result["opis_html"]
            if source and result.get("ten_sam_produkt") is not True:
                reasons.append("AI: strona nie pasuje do produktu"
                               + (f" ({result.get('uwagi')})" if result.get("uwagi") else ""))
        else:
            reasons.append("nie udało się wygenerować opisu")

    # 3. Zdjęcie: kandydaci ze strony źródłowej, potem z wyszukiwarki obrazów. Każdy jest pobierany
    #    i sprawdzany (sygnatura pliku, wymiary, logo/baner) — pierwszy poprawny wygrywa.
    base_name = safe_filename(f"{val(COL_ID)}_{row['code']}")
    tried: set[str] = set()
    image, image_ok, image_file, rejected = "", False, "", ""
    # Najpierw zdjęcie serii ze sklepu producenta (najlepsza jakość, bez znaków wodnych).
    shop = image_candidates_from_producer_shop(row, source, result, row_label) if source else []
    if shop:
        image, image_ok, image_file, rejected = pick_image(shop, images_dir, base_name, tried)
    if source and not image:
        image, image_ok, image_file, rejected = pick_image(
            [(u, True, source["url"]) for u in image_candidates_from_source(m, source)],
            images_dir, base_name, tried)
    if not image:
        found = image_candidates_from_search(m, row["code"], row["ean"], row["producer"], engine, row_label)
        image, image_ok, image_file, reason = pick_image(found, images_dir, base_name, tried)
        rejected = reason or rejected
    if not image:
        log.info("%s zdjęcie: kandydaci — sklep producenta %d, strona źródłowa %d, sprawdzone %d, ostatni powód: %s",
                 row_label, len(shop), len(image_candidates_from_source(m, source)) if source else 0,
                 len(tried), rejected or "brak kandydatów")
        reasons.append(f"brak poprawnego zdjęcia (odrzucone: {rejected})" if rejected else "brak zdjęcia")
    elif not image_ok:
        reasons.append("zdjęcie niepotwierdzone kodem/EAN")

    out = dict(record)
    out[COL_DESC] = desc
    out[COL_IMAGE] = image
    out[COL_IMAGE_FILE] = image_file.replace(os.sep, "/")
    out[COL_SOURCE] = source["url"] if source else ""
    out[COL_STATUS] = STATUS_REVIEW if reasons else STATUS_OK
    out[COL_REASON] = "; ".join(reasons)
    log.info("%s %s%s", row_label, out[COL_STATUS], f" — {out[COL_REASON]}" if reasons else "")
    return out


# =============================================================================
# KONTROLA ZDJĘĆ MODELEM WIZYJNYM (znak wodny, logo, czy to zdjęcie produktu)
# =============================================================================
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
VISION_MAX_SIDE = 1024       # zdjęcie zmniejszane przed wysłaniem do modelu — szybciej, wynik ten sam
# Zamiennik_* — inne zdjęcie znalezione w miejsce odrzuconego (np. ze znakiem wodnym).
CHECK_COLUMNS = [COL_ID, COL_IMAGE, "Wynik", "Uwagi", "Zamiennik_URL", "Zamiennik_plik", "Zamiennik_potwierdzony"]
MAX_REPLACEMENT_CHECKS = 3   # ile zastępczych zdjęć obejrzeć modelem, zanim produkt zostanie bez zdjęcia
CHECK_OK, CHECK_REJECTED = "OK", "ODRZUCONE"

VISION_PROMPT = """To zdjęcie ma być zdjęciem produktu w sklepie internetowym z armaturą instalacyjną.
Produkt: {name}

Oceń zdjęcie i odpowiedz WYŁĄCZNIE obiektem JSON:
{{"zdjecie_produktu": true lub false,
  "znak_wodny": true lub false,
  "uwagi": "krótko po polsku, co jest nie tak (puste, jeśli wszystko w porządku)"}}

- "zdjecie_produktu": true tylko jeśli widać fizyczny produkt (np. zasuwa, zawór, łącznik, kształtka, rura).
  false dla: logo, banera, samego napisu, rysunku technicznego, tabeli, zrzutu strony, zdjęcia innego przedmiotu.
- "znak_wodny": true, jeśli na zdjęcie nałożono znak wodny, logo sklepu lub firmy, adres strony www,
  numer telefonu albo inny napis, który nie jest częścią samego produktu (napisy odlane/nadrukowane
  na produkcie się nie liczą)."""


def check_file_path(output_path: str) -> str:
    return side_path(output_path, "kontrola_zdjec")


def load_image_checks(output_path: str) -> dict[tuple[str, str], dict]:
    """{(id, url_zdjęcia): wiersz kontroli} — wynik dotyczy konkretnego zdjęcia, więc zmiana zdjęcia = nowa kontrola."""
    path = check_file_path(output_path)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    df = read_csv(path)
    missing = [c for c in CHECK_COLUMNS if c not in df.columns]
    if missing:
        # Plik ze starszej wersji (bez kolumn zamiennika) — uzupełniamy nagłówek, wyniki zostają.
        for c in missing:
            df[c] = ""
        df[CHECK_COLUMNS].to_csv(path, index=False, encoding="utf-8")
    return {(r[COL_ID], r[COL_IMAGE]): r for r in df.to_dict("records")}


def _image_for_vision(record: dict) -> bytes:
    """Zdjęcie produktu zmniejszone do JPEG (z pliku na dysku albo z linku)."""
    from PIL import Image

    local = record.get(COL_IMAGE_FILE, "")
    if local and os.path.exists(local):
        with open(local, "rb") as fh:
            data = fh.read()
    else:
        data, _, reason = fetch_image(record[COL_IMAGE])
        if reason:
            raise ValueError(f"nie udało się pobrać zdjęcia ({reason})")
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=90)
        return out.getvalue()


def _ask_vision(image_jpeg: bytes, name: str) -> dict:
    import base64

    host = AI_PROVIDERS["ollama"]["base_url"].removesuffix("/v1")
    resp = requests.post(host + "/api/chat", timeout=300, json={
        "model": VISION_MODEL,
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": VISION_PROMPT.format(name=name),
                      "images": [base64.b64encode(image_jpeg).decode("ascii")]}],
        "options": {"temperature": 0, "num_ctx": 4096},
    })
    resp.raise_for_status()
    return parse_json((resp.json().get("message") or {}).get("content") or "{}")


def _shrink_for_vision(data: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=90)
        return out.getvalue()


def check_one_image(record: dict, data: bytes | None = None) -> tuple[str, str]:
    """(wynik, uwagi) dla zdjęcia produktu — z rekordu albo z podanych bajtów (zdjęcie zastępcze)."""
    try:
        image = _shrink_for_vision(data) if data is not None else _image_for_vision(record)
    except Exception as exc:
        return CHECK_REJECTED, str(exc)
    answer = with_retry(_ask_vision, image, record.get(COL_NAME, ""), what=f"[id={record.get(COL_ID)}] kontrola zdjęcia")
    if answer is None:
        return CHECK_REJECTED, "model nie ocenił zdjęcia"
    problems = []
    if answer.get("zdjecie_produktu") is not True:
        problems.append("to nie jest zdjęcie produktu")
    if answer.get("znak_wodny") is not False:
        problems.append("znak wodny / nałożone logo lub napis")
    if problems:
        note = str(answer.get("uwagi") or "").strip()
        return CHECK_REJECTED, "; ".join(problems) + (f" ({note})" if note else "")
    return CHECK_OK, ""


def find_replacement(record: dict, engine: str, images_dir: str | None) -> tuple[str, bool, str, str]:
    """Szuka innego zdjęcia w miejsce odrzuconego i sprawdza je modelem wizyjnym.

    Zwraca (url, czy_potwierdzone_kodem, plik, uwagi) albo ("", False, "", powód).
    """
    def val(col: str) -> str:
        return str(record.get(col, "") or "").strip()

    m = ProductMatcher(val(COL_CODE), val(COL_EAN), val(COL_PRODUCER))
    label = f"[id={val(COL_ID)}]"
    candidates: list[tuple[str, bool, str]] = []
    if val(COL_SOURCE):
        page = fetch_page(val(COL_SOURCE))
        if page:
            source = {"url": val(COL_SOURCE), "excerpt": "", "og_images": page[1], "imgs": page[2],
                      "producer_site": is_producer_url(val(COL_SOURCE), val(COL_PRODUCER))}
            candidates += [(u, True, source["url"]) for u in image_candidates_from_source(m, source)]
    candidates += image_candidates_from_search(m, val(COL_CODE), val(COL_EAN), val(COL_PRODUCER), engine, label)

    tried = {val(COL_IMAGE)}
    base_name = safe_filename(f"{val(COL_ID)}_{val(COL_CODE)}")
    last_note = "nie znaleziono innego zdjęcia"
    for _ in range(MAX_REPLACEMENT_CHECKS):
        # pick_image pobiera i filtruje (logo w adresie, wymiary) — model ogląda tylko to, co przeszło
        url, verified, path, reason = pick_image(candidates, None, base_name, tried)
        if not url:
            last_note = f"brak innego poprawnego zdjęcia ({reason})" if reason else last_note
            break
        data, ext, err = fetch_image(url)
        if err:
            last_note = err
            continue
        result, note = check_one_image(record, data)
        log.info("%s zdjęcie zastępcze %s%s", label, result, f" — {note}" if note else "")
        if result == CHECK_OK:
            path = save_image(data, ext, images_dir, base_name) if images_dir else ""
            return url, verified, path, ""
        last_note = f"zastępcze też odrzucone: {note}"
    return "", False, "", last_note


def run_image_check(output_path: str, engine: str = "ddg", images_dir: str | None = None) -> None:
    """Etap 2: ogląda modelem wizyjnym każde zdjęcie, którego jeszcze nie sprawdzono.

    Przy odrzuconym zdjęciu (znak wodny, logo, nie produkt) szuka zdjęcia zastępczego.
    """
    if not os.path.exists(output_path):
        sys.exit(f"Nie ma pliku {output_path}. Najpierw uruchom przetwarzanie produktów (--input ...).")
    check_ollama(dict(AI_PROVIDERS["ollama"], models=[VISION_MODEL]))
    df = read_csv(output_path)
    done = load_image_checks(output_path)
    todo = [r for r in df.to_dict("records") if r.get(COL_IMAGE) and (r[COL_ID], r[COL_IMAGE]) not in done]
    log.info("Kontrola zdjęć modelem %s: do sprawdzenia %d (sprawdzone wcześniej: %d).",
             VISION_MODEL, len(todo), len(done))
    path = check_file_path(output_path)
    started, batch, rejected, replaced = time.time(), [], 0, 0
    try:
        for i, record in enumerate(todo, 1):
            result, note = check_one_image(record)
            log.info("[id=%s] zdjęcie %s%s", record[COL_ID], result, f" — {note}" if note else "")
            entry = {COL_ID: record[COL_ID], COL_IMAGE: record[COL_IMAGE], "Wynik": result, "Uwagi": note,
                     "Zamiennik_URL": "", "Zamiennik_plik": "", "Zamiennik_potwierdzony": ""}
            if result != CHECK_OK:
                new_url, new_ok, new_path, new_note = find_replacement(record, engine, images_dir)
                if new_url:
                    replaced += 1
                    entry.update({"Zamiennik_URL": new_url, "Zamiennik_plik": new_path.replace(os.sep, "/"),
                                  "Zamiennik_potwierdzony": "TAK" if new_ok else "NIE"})
                    log.info("[id=%s] zamieniono zdjęcie na: %s", record[COL_ID], new_url)
                else:
                    rejected += 1
                    entry["Uwagi"] = f"{note}; {new_note}"
                    log.info("[id=%s] brak zdjęcia zastępczego — %s", record[COL_ID], new_note)
            batch.append(entry)
            if len(batch) >= CHECKPOINT_EVERY or i == len(todo):
                append_batch(batch, CHECK_COLUMNS, path)
                batch = []
                remaining = (len(todo) - i) * (time.time() - started) / i
                log.info("KONTROLA: %d/%d, zamienione: %d, bez zdjęcia: %d. Pozostało ok. %.1f h.",
                         i, len(todo), replaced, rejected, remaining / 3600)
    except KeyboardInterrupt:
        if batch:
            append_batch(batch, CHECK_COLUMNS, path)
        log.warning("Przerwano — sprawdzone zdjęcia są zapisane, kolejne uruchomienie dokończy resztę.")
    except FatalError as exc:
        if batch:
            append_batch(batch, CHECK_COLUMNS, path)
        log.error("Zatrzymuję kontrolę: %s", exc)
    split_results(output_path)


# =============================================================================
# PLIKI: CHECKPOINTY, PODZIAŁ NA PEWNE / DO AKCEPTACJI, ZATWIERDZANIE
# =============================================================================
def side_path(output_path: str, suffix: str) -> str:
    base, ext = os.path.splitext(output_path)
    return f"{base}_{suffix}{ext or '.csv'}"


def read_csv(path: str) -> pd.DataFrame:
    # dtype=str + keep_default_na=False: EAN-y i kody zostają tekstem (bez "5.9e+12" i "nan").
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def count_done_rows(output_path: str, df_in: pd.DataFrame) -> int:
    """Ile wierszy jest już w pliku wyjściowym (= od którego wiersza wznowić)."""
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return 0
    df_out = read_csv(output_path)
    if any(c not in df_out.columns for c in NEW_COLUMNS):
        sys.exit(f"Plik {output_path} pochodzi ze starszej wersji skryptu (inne kolumny). "
                 "Usuń go albo podaj inną nazwę w --output.")
    done = len(df_out)
    if done > len(df_in):
        sys.exit(f"Plik wyjściowy ma więcej wierszy ({done}) niż wejściowy ({len(df_in)}). "
                 "Czy to na pewno ten sam plik? Usuń/zmień nazwę pliku wyjściowego.")
    # Kontrola, czy plik wejściowy nie zmienił kolejności od poprzedniego uruchomienia.
    if done and COL_ID in df_out.columns and COL_ID in df_in.columns:
        if df_out[COL_ID].iloc[-1] != df_in[COL_ID].iloc[done - 1]:
            sys.exit(f"Ostatni zapisany wiersz (id={df_out[COL_ID].iloc[-1]}) nie zgadza się z wierszem "
                     f"{done} pliku wejściowego (id={df_in[COL_ID].iloc[done - 1]}). "
                     "Plik wejściowy zmienił się od poprzedniego uruchomienia — nie wznawiam, żeby nie "
                     "pomieszać danych.")
    return done


def append_batch(rows: list[dict], columns: list[str], output_path: str) -> None:
    """Dopisuje paczkę wierszy na koniec pliku i wymusza zapis na dysk."""
    write_header = not os.path.exists(output_path) or os.path.getsize(output_path) == 0
    batch_df = pd.DataFrame(rows, columns=columns)
    with open(output_path, "a", encoding="utf-8", newline="") as fh:
        batch_df.to_csv(fh, index=False, header=write_header)
        fh.flush()
        os.fsync(fh.fileno())


def apply_image_checks(df: pd.DataFrame, checks: dict) -> pd.DataFrame:
    """Produkt z niesprawdzonym albo odrzuconym zdjęciem nie może być PEWNY."""
    df = df.copy()
    for i, row in df.iterrows():
        if not row[COL_IMAGE]:
            continue
        check = checks.get((row[COL_ID], row[COL_IMAGE]), {})
        result, note = check.get("Wynik", ""), check.get("Uwagi", "")
        if result == CHECK_OK:
            continue
        if result == CHECK_REJECTED and check.get("Zamiennik_URL"):
            # Odrzucone zdjęcie zastąpione innym, które przeszło kontrolę modelu.
            df.at[i, COL_IMAGE] = check["Zamiennik_URL"]
            df.at[i, COL_IMAGE_FILE] = check.get("Zamiennik_plik", "")
            if check.get("Zamiennik_potwierdzony") == "TAK":
                continue
            reason = "zdjęcie zastępcze niepotwierdzone kodem/EAN"
        elif result == CHECK_REJECTED:
            # Bez zdjęcia — lepiej żadne niż ze znakiem wodnym albo cudzym logo.
            df.at[i, COL_IMAGE] = ""
            df.at[i, COL_IMAGE_FILE] = ""
            reason = f"brak zdjęcia — odrzucone: {note}"
        else:
            reason = "zdjęcie niesprawdzone (uruchom --sprawdz-zdjecia)"
        df.at[i, COL_STATUS] = STATUS_REVIEW
        df.at[i, COL_REASON] = "; ".join(r for r in (row[COL_REASON], reason) if r)
    return df


def split_results(output_path: str) -> None:
    """Dzieli plik roboczy na *_pewne.csv (do importu) i *_do_akceptacji.csv (do przejrzenia)."""
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return
    df = apply_image_checks(read_csv(output_path), load_image_checks(output_path))
    ok_path, review_path = side_path(output_path, "pewne"), side_path(output_path, "do_akceptacji")

    df[df[COL_STATUS] == STATUS_OK].to_csv(ok_path, index=False, encoding="utf-8-sig")

    review = df[df[COL_STATUS] != STATUS_OK].copy()
    review.insert(0, COL_ACCEPT, "")
    # Nie nadpisujemy pliku, w którym ktoś już zaczął akceptować produkty.
    if os.path.exists(review_path):
        try:
            existing = read_csv(review_path)
            if COL_ACCEPT in existing.columns and existing[COL_ACCEPT].str.strip().ne("").any():
                review_path = side_path(output_path, "do_akceptacji_nowe")
                log.warning("Plik do akceptacji ma już Twoje oznaczenia — nowa wersja trafia do %s.", review_path)
        except Exception:
            pass
    review.to_csv(review_path, index=False, encoding="utf-8-sig")
    log.info("PODZIAŁ: %d pewnych -> %s | %d do akceptacji -> %s",
             len(df) - len(review), ok_path, len(review), review_path)
    write_preview(df, side_path(output_path, "podglad").rsplit(".", 1)[0] + ".html")


ALLOWED_TAGS = ("h2", "h3", "p", "ul", "ol", "li", "strong", "b", "em", "br")


def safe_html(fragment: str) -> str:
    """Zostawia tylko proste tagi opisu (bez atrybutów) — reszta jest wyświetlana jako tekst."""
    escaped = html.escape(fragment, quote=False)
    tags = "|".join(ALLOWED_TAGS)
    return re.sub(rf"&lt;(/?)({tags})(?:\s[^&]*)?&gt;", r"<\1\2>", escaped, flags=re.IGNORECASE)


def write_preview(df: pd.DataFrame, path: str) -> None:
    """Strona HTML do przejrzenia wyników w przeglądarce: wyrenderowany opis + miniatura zdjęcia."""
    def col(row, name):
        return str(row.get(name, "") or "")

    cards = []
    for _, row in df.iterrows():
        ok = col(row, COL_STATUS) == STATUS_OK
        img = col(row, COL_IMAGE)
        local = col(row, COL_IMAGE_FILE)
        if local and os.path.exists(local):
            # Lokalna kopia działa w podglądzie także bez internetu i gdy sklep blokuje podlinkowanie.
            img = os.path.relpath(local, os.path.dirname(os.path.abspath(path))).replace(os.sep, "/")
        src = col(row, COL_SOURCE)
        cards.append(
            f'<article class="card {"ok" if ok else "review"}">'
            f'<header><span class="badge">{"PEWNY" if ok else "DO AKCEPTACJI"}</span> '
            f'<b>{html.escape(col(row, COL_NAME))}</b>'
            f'<small>id {html.escape(col(row, COL_ID))} · kod {html.escape(col(row, COL_CODE))} · '
            f'EAN {html.escape(col(row, COL_EAN))}</small></header>'
            + (f'<p class="reason">⚠ {html.escape(col(row, COL_REASON))}</p>' if not ok else "")
            + '<div class="body">'
            + (f'<a href="{html.escape(img)}" target="_blank"><img loading="lazy" src="{html.escape(img)}" '
               f'alt="" referrerpolicy="no-referrer"></a>' if img else '<div class="noimg">brak zdjęcia</div>')
            + f'<div class="desc">{safe_html(col(row, COL_DESC)) or "<i>brak opisu</i>"}'
            + (f'<p class="src">Źródło: <a href="{html.escape(src)}" target="_blank" rel="noreferrer">'
               f'{html.escape(src)}</a></p>' if src else "")
            + "</div></div></article>")
    n_ok = int((df[COL_STATUS] == STATUS_OK).sum())
    page = f"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Podgląd produktów</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1d2433}}
.top{{position:sticky;top:0;background:#fff;padding:12px 16px;border-bottom:1px solid #ddd;z-index:1}}
.top button{{margin-right:6px;padding:6px 12px;border:1px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}}
.top button.on{{background:#1d2433;color:#fff}}
main{{max-width:1100px;margin:0 auto;padding:16px}}
.card{{background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:14px;border-left:6px solid #2e9d5b}}
.card.review{{border-left-color:#e0a100}}
header small{{display:block;color:#667;margin-top:4px}}
.badge{{font-size:12px;padding:2px 8px;border-radius:10px;background:#e3f4ea;color:#1f6f40;margin-right:6px}}
.review .badge{{background:#fff3cf;color:#8a6100}}
.reason{{color:#8a6100;background:#fff8e1;padding:6px 10px;border-radius:6px}}
.body{{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}}
.body img{{width:200px;max-height:200px;object-fit:contain;border:1px solid #eee;border-radius:6px;background:#fff}}
.noimg{{width:200px;height:120px;display:flex;align-items:center;justify-content:center;background:#f0f0f0;color:#888;border-radius:6px}}
.desc{{flex:1;min-width:260px}} .desc h2{{font-size:18px;margin:4px 0 8px}}
.src{{font-size:12px;color:#667;word-break:break-all}}
</style></head><body>
<div class="top"><button class="on" data-f="all">Wszystkie ({len(df)})</button>
<button data-f="ok">Pewne ({n_ok})</button><button data-f="review">Do akceptacji ({len(df) - n_ok})</button></div>
<main>{"".join(cards)}</main>
<script>
document.querySelectorAll('.top button').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('.top button').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('.card').forEach(c=>c.style.display=
    (b.dataset.f==='all'||c.classList.contains(b.dataset.f))?'':'none');
}});
</script></body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    log.info("PODGLĄD w przeglądarce: %s", path)


def approve(output_path: str, accepted_path: str) -> None:
    """Łączy produkty pewne z ręcznie zaakceptowanymi w jeden plik do importu."""
    ok_path = side_path(output_path, "pewne")
    if not os.path.exists(ok_path):
        sys.exit(f"Nie ma pliku {ok_path}. Najpierw uruchom przetwarzanie z tym samym --output.")
    if not os.path.isfile(accepted_path):
        sys.exit(f"Nie znaleziono pliku z akceptacją: {accepted_path}")
    ok = read_csv(ok_path)
    reviewed = read_csv(accepted_path)
    if COL_ACCEPT not in reviewed.columns:
        sys.exit(f"W pliku {accepted_path} brakuje kolumny '{COL_ACCEPT}'.")
    accepted = reviewed[reviewed[COL_ACCEPT].str.strip().str.upper().isin(ACCEPT_VALUES)].drop(columns=[COL_ACCEPT])
    accepted[COL_STATUS] = "ZAAKCEPTOWANY"
    result = pd.concat([ok, accepted], ignore_index=True)
    if COL_ID in result.columns:
        result = result.drop_duplicates(subset=[COL_ID], keep="last")
    import_path = side_path(output_path, "do_importu")
    result.to_csv(import_path, index=False, encoding="utf-8-sig")
    print(f"Pewne: {len(ok)}, zaakceptowane ręcznie: {len(accepted)} "
          f"(odrzucone/pominięte: {len(reviewed) - len(accepted)}). Plik do importu: {import_path}")


# =============================================================================
# AKTUALIZACJA SKRYPTU Z GITHUBA
# =============================================================================
UPDATE_REPO = "patrykkupczak1996-create/Asy-sten-zakupowy-"
UPDATE_BRANCH = "claude/b2b-product-enrichment-script-uve0u0"
UPDATE_DIR = "wzbogacanie_produktow"
UPDATE_BASE_URL = ""  # pusty = ustalany automatycznie (testy mogą go nadpisać)
UPDATE_FILES = ("wzbogac_produkty.py", "requirements.txt", "README.md")


def _update_base_url() -> str:
    """Adres plików z najnowszego commita gałęzi.

    Adres z nazwą gałęzi jest przez kilka minut cache'owany przez GitHub (stara wersja),
    adres z numerem commita — nie. Gdy API GitHuba nie odpowie, używamy nazwy gałęzi.
    """
    if UPDATE_BASE_URL:
        return UPDATE_BASE_URL
    ref = UPDATE_BRANCH
    try:
        resp = requests.get(f"https://api.github.com/repos/{UPDATE_REPO}/commits/{UPDATE_BRANCH}",
                            headers={"Accept": "application/vnd.github.sha"}, timeout=10)
        if resp.status_code == 200 and re.fullmatch(r"[0-9a-f]{40}", resp.text.strip()):
            ref = resp.text.strip()
    except Exception:
        pass
    return f"https://raw.githubusercontent.com/{UPDATE_REPO}/{ref}/{UPDATE_DIR}/"


def _download_text(name: str, timeout: int, base_url: str | None = None) -> bytes:
    resp = requests.get((base_url or _update_base_url()) + name, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def check_for_update() -> None:
    """Przy starcie: jeśli na GitHubie jest inna wersja skryptu, podpowiada --aktualizuj. Błędy ignoruje."""
    try:
        remote = _download_text("wzbogac_produkty.py", timeout=5)
        with open(os.path.abspath(__file__), "rb") as fh:
            local = fh.read()
        if remote.replace(b"\r\n", b"\n") != local.replace(b"\r\n", b"\n"):
            log.warning("Jest nowsza wersja skryptu. Zaktualizuj: py wzbogac_produkty.py --aktualizuj")
    except Exception:
        pass  # brak internetu / GitHub niedostępny — pracujemy na obecnej wersji


def self_update() -> None:
    """Pobiera najnowsze pliki skryptu z GitHuba i podmienia je w folderze skryptu."""
    folder = os.path.dirname(os.path.abspath(__file__))
    downloaded = {}
    base_url = _update_base_url()
    for name in UPDATE_FILES:
        try:
            downloaded[name] = _download_text(name, timeout=30, base_url=base_url)
        except Exception as exc:
            sys.exit(f"Nie udało się pobrać {name}: {exc}. Nic nie zostało zmienione.")
    try:
        # Nie podmieniamy działającego skryptu na uszkodzony plik (np. stronę błędu zamiast kodu).
        compile(downloaded["wzbogac_produkty.py"], "wzbogac_produkty.py", "exec")
    except SyntaxError as exc:
        sys.exit(f"Pobrany skrypt jest uszkodzony ({exc}). Nic nie zostało zmienione.")

    old_requirements = b""
    req_path = os.path.join(folder, "requirements.txt")
    if os.path.exists(req_path):
        with open(req_path, "rb") as fh:
            old_requirements = fh.read()

    changed = []
    for name, content in downloaded.items():
        path = os.path.join(folder, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                if fh.read().replace(b"\r\n", b"\n") == content.replace(b"\r\n", b"\n"):
                    continue
        with open(path + ".nowy", "wb") as fh:
            fh.write(content)
        os.replace(path + ".nowy", path)
        changed.append(name)

    if not changed:
        print("Masz już najnowszą wersję.")
        return
    print("Zaktualizowano: " + ", ".join(changed))
    if downloaded["requirements.txt"].replace(b"\r\n", b"\n") != old_requirements.replace(b"\r\n", b"\n"):
        print("Zmieniły się wymagane biblioteki — uruchom: py -m pip install -r requirements.txt")


# =============================================================================
# MAIN
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Wzbogacanie produktów B2B o opisy HTML i zdjęcia.")
    parser.add_argument("--input", "-i", help="Plik CSV z Google Sheets (ścieżka lub link .../export?format=csv)")
    parser.add_argument("--output", "-o", default="produkty_wzbogacone.csv", help="Plik roboczy z wynikami")
    parser.add_argument("--ai", choices=sorted(AI_PROVIDERS), default=AI_PROVIDER,
                        help="Model do pisania opisów (domyślnie: %(default)s)")
    parser.add_argument("--search", choices=["ddg", "serpapi", "google"], default=SEARCH_ENGINE,
                        help="Wyszukiwarka stron i zdjęć (domyślnie: %(default)s)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="Ile wierszy przetwarzać równolegle (domyślnie: %(default)s)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Przetwórz tylko N kolejnych wierszy (do testów, np. --limit 5)")
    parser.add_argument("--sep", default=",", help="Separator kolumn w pliku wejściowym (domyślnie przecinek)")
    parser.add_argument("--bez-pobierania", action="store_true",
                        help="Nie pobieraj zdjęć na dysk (zapisz tylko linki)")
    parser.add_argument("--sprawdz-zdjecia", action="store_true",
                        help="Etap 2: sprawdź zdjęcia modelem wizyjnym Ollamy (znak wodny, logo, czy to produkt)")
    parser.add_argument("--test-wyszukiwarki", metavar="KOD",
                        help="Pokaż, co skrypt znajduje w wyszukiwarkach hurtowni dla kodu (diagnostyka)")
    parser.add_argument("--aktualizuj", action="store_true",
                        help="Pobierz najnowszą wersję skryptu z GitHuba i zakończ")
    parser.add_argument("--zatwierdz", metavar="PLIK",
                        help="Plik do akceptacji z kolumną Akceptacja=TAK -> tworzy *_do_importu.csv")
    args = parser.parse_args()

    if args.aktualizuj:
        self_update()
        return
    if args.test_wyszukiwarki:
        test_direct_search(args.test_wyszukiwarki)
        return
    if args.zatwierdz:
        approve(args.output, args.zatwierdz)
        return
    if not args.input and not args.sprawdz_zdjecia:
        parser.error("podaj --input (plik CSV z produktami), --sprawdz-zdjecia albo --zatwierdz")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("wzbogacanie.log", encoding="utf-8")],
    )
    # Biblioteki (httpx, ddgs, openai) logują każde zapytanie — to zasłania komunikaty skryptu.
    logging.getLogger().setLevel(logging.WARNING)
    log.setLevel(logging.INFO)
    check_for_update()

    if args.sprawdz_zdjecia:
        images_dir = None if args.bez_pobierania else os.path.relpath(
            os.path.join(os.path.dirname(os.path.abspath(args.output)), IMAGES_DIR))
        run_image_check(args.output, args.search, images_dir)
        return

    configure_ai(args.ai)
    if args.search == "serpapi" and not SERPAPI_API_KEY:
        sys.exit("Wybrano SerpApi, ale brak SERPAPI_API_KEY.")
    if args.search == "google" and not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
        sys.exit("Wybrano Google Custom Search, ale brak GOOGLE_API_KEY lub GOOGLE_CSE_ID.")

    if "://" not in args.input and not os.path.isfile(args.input):
        csv_files = sorted(f for f in os.listdir(".") if f.lower().endswith(".csv"))
        sys.exit(f"Nie znaleziono pliku wejściowego '{args.input}' w folderze {os.getcwd()}.\n"
                 f"Pliki CSV w tym folderze: {', '.join(csv_files) or 'brak'}.\n"
                 "Skopiuj tu plik pobrany z Google Sheets albo podaj pełną ścieżkę w --input "
                 "(w cudzysłowie, jeśli zawiera spacje).")
    df_in = pd.read_csv(args.input, dtype=str, keep_default_na=False, sep=args.sep, encoding="utf-8-sig")
    missing = [c for c in (COL_ID, COL_CODE, COL_PRODUCER, COL_EAN, COL_CATEGORY, COL_NAME)
               if c not in df_in.columns]
    if missing:
        log.warning("Brak kolumn w pliku wejściowym: %s (dostępne: %s)", missing, list(df_in.columns))
    if COL_NAME not in df_in.columns:
        sys.exit(f"Nie znaleziono kolumny z nazwą produktu '{COL_NAME}'. Sprawdź separator (--sep).")

    columns = [c for c in df_in.columns if c not in NEW_COLUMNS + [COL_ACCEPT]] + NEW_COLUMNS
    total = len(df_in)
    start = count_done_rows(args.output, df_in)
    end = total if args.limit is None else min(total, start + args.limit)

    if start >= total:
        log.info("Wszystkie %d wiersze są już przetworzone w %s.", total, args.output)
        split_results(args.output)
        return
    log.info("Wierszy w pliku: %d. Start od wiersza %d%s. AI: %s (%s). Wyszukiwarka: %s. Wątki: %d.",
             total, start + 1, " (wznowienie)" if start else "", AI["name"], ", ".join(AI["models"]),
             args.search, args.workers)

    records = df_in.to_dict("records")
    images_dir = None if args.bez_pobierania else os.path.join(
        os.path.dirname(os.path.abspath(args.output)), IMAGES_DIR)
    if images_dir:
        # Ścieżki w CSV względem bieżącego folderu (zwykle "zdjecia/28846_AG0828.jpg").
        images_dir = os.path.relpath(images_dir)
        log.info("Zdjęcia będą pobierane do folderu: %s", os.path.abspath(images_dir))
    started_at = time.time()
    done_now = 0
    exit_code = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for batch_start in range(start, end, CHECKPOINT_EVERY):
                batch_end = min(batch_start + CHECKPOINT_EVERY, end)
                # pool.map zachowuje kolejność wierszy, więc plik wyjściowy ma ten sam porządek co wejściowy.
                results = list(pool.map(lambda p: process_row(records[p], p, args.search, images_dir),
                                        range(batch_start, batch_end)))
                append_batch(results, columns, args.output)

                done_now += len(results)
                elapsed = time.time() - started_at
                remaining = (total - batch_end) * elapsed / done_now
                log.info("CHECKPOINT: zapisano %d/%d (%.1f%%). Pozostało ok. %.1f h.",
                         batch_end, total, 100 * batch_end / total, remaining / 3600)
    except FatalError as exc:
        log.error("Zatrzymuję skrypt: %s", exc)
        log.error("Dotychczasowy postęp jest zapisany w %s — po poprawieniu problemu uruchom skrypt ponownie.",
                  args.output)
        exit_code = 1
    except KeyboardInterrupt:
        log.warning("Przerwano (Ctrl+C). Zapisane paczki zostają w %s; niedokończona paczka "
                    "zostanie przetworzona ponownie przy następnym uruchomieniu.", args.output)
        exit_code = 130

    split_results(args.output)
    if exit_code:
        sys.exit(exit_code)
    log.info("Gotowe. Przetworzono %d wierszy w tym uruchomieniu.", done_now)


if __name__ == "__main__":
    main()
