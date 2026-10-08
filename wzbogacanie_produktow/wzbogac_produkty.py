#!/usr/bin/env python3
"""
Wzbogacanie bazy produktów B2B (armatura, zasuwy) pod import do IdoSell.

Dla każdego wiersza pliku CSV (eksport z Google Sheets):
  1. generuje opis SEO w czystym HTML przez OpenAI (gpt-4o-mini) -> kolumna "Opis_HTML",
  2. wyszukuje URL zdjęcia produktu ("Producent kod_producenta", fallback: EAN)
     -> kolumna "Zdjecie_URL".

Postęp jest zapisywany co CHECKPOINT_EVERY wierszy do pliku wyjściowego.
Po restarcie skrypt liczy wiersze już zapisane w pliku wyjściowym i zaczyna
od następnego, więc nic nie jest generowane (ani opłacane) dwa razy.

Uruchomienie (szczegóły w README.md w tym katalogu):
    python wzbogac_produkty.py --input produkty.csv --output produkty_wzbogacone.csv
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")          # wymagany, np. "sk-proj-..."

# Wyszukiwarka zdjęć — wybierz JEDNĄ (parametr --image-source lub poniżej):
#   "ddg"     — DuckDuckGo, darmowe, bez klucza (ale potrafi blokować przy dużej liczbie zapytań)
#   "serpapi" — SerpApi (Google Images), płatne, najlepsza trafność
#   "google"  — Google Custom Search JSON API (100 zapytań/dzień za darmo)
IMAGE_SOURCE = os.getenv("IMAGE_SOURCE", "ddg")

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")        # tylko dla "serpapi"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")          # tylko dla "google"
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")            # tylko dla "google" (identyfikator "cx")

# =============================================================================
# USTAWIENIA PRZETWARZANIA
# =============================================================================
OPENAI_MODEL = "gpt-4o-mini"
CHECKPOINT_EVERY = 10        # co ile wierszy zapisywać postęp na dysk
RETRY_WAIT_SECONDS = 5       # ile czekać przed ponowieniem po błędzie
MAX_RETRIES = 6              # ile razy ponawiać jedno zapytanie, zanim wiersz zostanie pominięty
REQUEST_TIMEOUT = 60         # timeout pojedynczego zapytania HTTP (sekundy)
DEFAULT_WORKERS = 3          # ile wierszy przetwarzać równolegle w ramach jednej paczki

# Nazwy kolumn wejściowych (dokładnie jak w eksporcie z IdoSell / Google Sheets)
COL_ID = "@id"
COL_CODE = "@code_producer"
COL_PRODUCER = "/producer@name"
COL_EAN = "/sizes/size@code_producer"
COL_CATEGORY = "/navigation/site/menu/item@textid[pol]"
COL_NAME = "/description/name[pol]"

COL_DESC = "Opis_HTML"
COL_IMAGE = "Zdjecie_URL"

SYSTEM_PROMPT = "Jesteś ekspertem SEO w branży instalacyjnej i B2B."

USER_PROMPT_TEMPLATE = """Napisz opis produktu do sklepu internetowego B2B.

Dane produktu:
- Nazwa techniczna: {name}
- Producent: {producer}
- Kategoria: {category}

Wymagania:
1. Długość: około 1000 znaków (ze spacjami, nie licząc znaczników HTML).
2. Rozwiń wszystkie skróty techniczne z nazwy i wyjaśnij je klientowi, np.
   DN80 = średnica nominalna 80 mm, PN16 = ciśnienie nominalne 16 bar,
   KOŁN. = kołnierzowa (połączenie kołnierzowe), KR. = kółko ręczne,
   F4/F5 = długość zabudowy wg normy EN 558, ŻEL. = żeliwna,
   RK/RR = łącznik rurowo-kołnierzowy / rurowo-rurowy, D225 lub OD63 = średnica
   zewnętrzna rury w mm, PE/PVC = do rur z polietylenu i PVC, Z PE = z końcówkami PE itp.
3. Wyjaśnij zastosowanie produktu (gdzie i do czego się go montuje) i jego zalety.
4. Nie wymyślaj parametrów, których nie da się wywnioskować z nazwy (np. masy,
   certyfikatów, ceny). Pisz rzeczowo, językiem branżowym, bez przesadnych superlatywów.
5. Formatowanie: WYŁĄCZNIE czysty HTML z użyciem tagów <h2>, <p>, <ul>, <li>, <strong>.
   Bez <html>, <body>, <head>, bez stylów CSS, bez Markdown i bez bloków ```.
   Zacznij od <h2> z czytelną nazwą produktu."""

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

log = logging.getLogger("wzbogacanie")


# =============================================================================
# PONAWIANIE PRÓB
# =============================================================================
class FatalError(Exception):
    """Błąd, którego ponawianie nie ma sensu (np. zły klucz API) — zatrzymuje skrypt."""


def with_retry(func, *args, what: str = "zapytanie", **kwargs):
    """Wywołuje func; przy błędzie czeka RETRY_WAIT_SECONDS i ponawia (do MAX_RETRIES razy).

    Zwraca wynik func albo None, jeśli wszystkie próby się nie powiodły
    (wiersz zostanie wtedy zapisany z pustą wartością, a skrypt jedzie dalej).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except FatalError:
            raise
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise FatalError(f"OpenAI odrzuciło klucz API: {exc}") from exc
        except openai.BadRequestError as exc:
            # Błędne zapytanie nie naprawi się samo — nie ma sensu czekać.
            log.error("%s: błędne zapytanie, pomijam: %s", what, exc)
            return None
        except Exception as exc:  # timeout, rate limit, błąd sieci, 5xx...
            msg = str(exc)
            if "insufficient_quota" in msg:
                raise FatalError("Brak środków na koncie OpenAI (insufficient_quota).") from exc
            log.warning("%s: błąd (próba %d/%d): %s: %s",
                        what, attempt, MAX_RETRIES, type(exc).__name__, msg[:200])
            if attempt < MAX_RETRIES:
                # Przy kolejnych błędach z rzędu czekamy trochę dłużej (5 s, 10 s, 15 s...),
                # co pomaga przy limitach zapytań (rate limit).
                time.sleep(RETRY_WAIT_SECONDS * attempt)
    log.error("%s: wszystkie %d próby nieudane — zapisuję pustą wartość.", what, MAX_RETRIES)
    return None


# =============================================================================
# KROK 1: OPIS HTML (OpenAI)
# =============================================================================
_openai_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        # max_retries=0 — ponawianiem zajmuje się with_retry (5 s przerwy, logowanie).
        _openai_client = OpenAI(api_key=OPENAI_API_KEY, timeout=REQUEST_TIMEOUT, max_retries=0)
    return _openai_client


def clean_html(text: str) -> str:
    """Usuwa ewentualne bloki ```html ... ``` i zbędne białe znaki z odpowiedzi modelu."""
    text = text.strip()
    text = re.sub(r"^```(?:html)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    # Jedna linia na tag — CSV jest wtedy czytelniejszy, a HTML działa tak samo.
    text = re.sub(r">\s*\n\s*<", "><", text)
    return text.strip()


def _call_openai(name: str, producer: str, category: str) -> str:
    # Ścieżka kategorii z IdoSell ("A\\B\\C") jest czytelniejsza dla modelu jako "A > B > C".
    category = " > ".join(part.strip() for part in category.split("\\") if part.strip())
    response = get_openai_client().chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.5,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                name=name, producer=producer or "brak danych", category=category or "brak danych")},
        ],
    )
    content = response.choices[0].message.content or ""
    html = clean_html(content)
    if not html:
        raise ValueError("Model zwrócił pustą odpowiedź")
    return html


def generate_description(name: str, producer: str, category: str, row_label: str) -> str:
    if not name:
        log.warning("%s: brak nazwy produktu — pomijam opis.", row_label)
        return ""
    return with_retry(_call_openai, name, producer, category,
                      what=f"{row_label} OpenAI") or ""


# =============================================================================
# KROK 2: URL ZDJĘCIA
# =============================================================================
def is_direct_image_url(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    return urlparse(url).path.lower().endswith(IMAGE_EXTENSIONS)


def _search_ddg(query: str) -> list[str]:
    try:
        from ddgs import DDGS  # nowa nazwa paczki
    except ImportError:
        from duckduckgo_search import DDGS  # starsza nazwa paczki
    results = DDGS(timeout=REQUEST_TIMEOUT).images(query, max_results=20)
    return [r.get("image", "") for r in results or []]


def _search_serpapi(query: str) -> list[str]:
    resp = requests.get(
        "https://serpapi.com/search.json",
        params={"engine": "google_images", "q": query, "api_key": SERPAPI_API_KEY, "hl": "pl", "gl": "pl"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code in (401, 403):
        raise FatalError(f"SerpApi odrzuciło klucz API (HTTP {resp.status_code}).")
    resp.raise_for_status()
    data = resp.json()
    if "error" in data and "hasn't returned any results" not in data["error"]:
        raise RuntimeError(f"SerpApi: {data['error']}")
    return [r.get("original", "") for r in data.get("images_results", [])]


def _search_google(query: str) -> list[str]:
    resp = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "searchType": "image", "num": 10},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code in (400, 401, 403) and "quota" not in resp.text.lower():
        raise FatalError(f"Google Custom Search odrzucił zapytanie (HTTP {resp.status_code}): {resp.text[:200]}")
    resp.raise_for_status()  # 429 / limit dzienny -> zwykły błąd, ponawiamy
    return [item.get("link", "") for item in resp.json().get("items", [])]


SEARCHERS = {"ddg": _search_ddg, "serpapi": _search_serpapi, "google": _search_google}


def _first_image(query: str, source: str) -> str:
    """Zwraca pierwszy bezpośredni URL .jpg/.png albo "" (brak wyników to nie błąd)."""
    for url in SEARCHERS[source](query):
        if is_direct_image_url(url):
            return url
    return ""


def find_image(producer: str, code: str, ean: str, source: str, row_label: str) -> str:
    queries = []
    if code:
        queries.append(f"{producer} {code}".strip())
    if ean:
        queries.append(ean)  # fallback: kod EAN
    for query in queries:
        url = with_retry(_first_image, query, source, what=f"{row_label} zdjęcie '{query}'")
        if url:
            return url
    return ""


# =============================================================================
# PRZETWARZANIE WIERSZY I CHECKPOINTY
# =============================================================================
def process_row(row: dict, position: int, source: str, skip_images: bool) -> dict:
    def val(col: str) -> str:
        return str(row.get(col, "") or "").strip()

    row_label = f"[wiersz {position + 1}, id={val(COL_ID)}]"
    out = dict(row)
    out[COL_DESC] = generate_description(val(COL_NAME), val(COL_PRODUCER), val(COL_CATEGORY), row_label)
    out[COL_IMAGE] = "" if skip_images else find_image(
        val(COL_PRODUCER), val(COL_CODE), val(COL_EAN), source, row_label)
    log.info("%s opis: %s, zdjęcie: %s", row_label,
             f"{len(out[COL_DESC])} zn." if out[COL_DESC] else "BRAK",
             out[COL_IMAGE] or "BRAK")
    return out


def count_done_rows(output_path: str, df_in: pd.DataFrame) -> int:
    """Ile wierszy jest już w pliku wyjściowym (= od którego wiersza wznowić)."""
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return 0
    df_out = pd.read_csv(output_path, dtype=str, keep_default_na=False, encoding="utf-8")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Wzbogacanie produktów B2B o opisy HTML i zdjęcia.")
    parser.add_argument("--input", "-i", required=True,
                        help="Plik CSV z Google Sheets (ścieżka lub link .../export?format=csv)")
    parser.add_argument("--output", "-o", default="produkty_wzbogacone.csv", help="Plik wynikowy CSV")
    parser.add_argument("--image-source", choices=sorted(SEARCHERS), default=IMAGE_SOURCE,
                        help="Wyszukiwarka zdjęć (domyślnie: %(default)s)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="Ile wierszy przetwarzać równolegle (domyślnie: %(default)s)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Przetwórz tylko N kolejnych wierszy (do testów, np. --limit 5)")
    parser.add_argument("--no-images", action="store_true", help="Pomiń wyszukiwanie zdjęć")
    parser.add_argument("--sep", default=",", help="Separator kolumn w pliku wejściowym (domyślnie przecinek)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("wzbogacanie.log", encoding="utf-8")],
    )

    if not OPENAI_API_KEY:
        sys.exit("Brak klucza OpenAI. Ustaw zmienną środowiskową OPENAI_API_KEY "
                 "albo wpisz klucz w sekcji KONFIGURACJA na górze skryptu.")
    if "TWÓJ" in OPENAI_API_KEY.upper() or "..." in OPENAI_API_KEY:
        sys.exit("OPENAI_API_KEY zawiera przykładowy tekst zamiast prawdziwego klucza. "
                 "Wklej swój klucz z https://platform.openai.com/api-keys.")
    if not args.no_images:
        if args.image_source == "serpapi" and not SERPAPI_API_KEY:
            sys.exit("Wybrano SerpApi, ale brak SERPAPI_API_KEY.")
        if args.image_source == "google" and not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
            sys.exit("Wybrano Google Custom Search, ale brak GOOGLE_API_KEY lub GOOGLE_CSE_ID.")

    # dtype=str + keep_default_na=False: EAN-y i kody zostają tekstem (bez "5.9e+12" i "nan").
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

    columns = [c for c in df_in.columns if c not in (COL_DESC, COL_IMAGE)] + [COL_DESC, COL_IMAGE]
    total = len(df_in)
    start = count_done_rows(args.output, df_in)
    end = total if args.limit is None else min(total, start + args.limit)

    if start >= total:
        log.info("Wszystkie %d wiersze są już przetworzone w %s. Nic do zrobienia.", total, args.output)
        return
    log.info("Wierszy w pliku: %d. Start od wiersza %d%s. Wyszukiwarka zdjęć: %s. Wątki: %d.",
             total, start + 1, " (wznowienie)" if start else "",
             "wyłączona" if args.no_images else args.image_source, args.workers)

    records = df_in.to_dict("records")
    started_at = time.time()
    done_now = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for batch_start in range(start, end, CHECKPOINT_EVERY):
                batch_end = min(batch_start + CHECKPOINT_EVERY, end)
                positions = range(batch_start, batch_end)
                # pool.map zachowuje kolejność wierszy, więc plik wyjściowy ma ten sam porządek co wejściowy.
                results = list(pool.map(
                    lambda p: process_row(records[p], p, args.image_source, args.no_images), positions))
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
        sys.exit(1)
    except KeyboardInterrupt:
        log.warning("Przerwano (Ctrl+C). Zapisane paczki zostają w %s; niedokończona paczka "
                    "zostanie przetworzona ponownie przy następnym uruchomieniu.", args.output)
        sys.exit(130)

    log.info("Gotowe. Przetworzono %d wierszy w tym uruchomieniu. Wynik: %s", done_now, args.output)


if __name__ == "__main__":
    main()
