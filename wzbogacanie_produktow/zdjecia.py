#!/usr/bin/env python3
"""
Zdjęcia produktów — osobny przebieg po opisach.

Dla produktów PEWNYCH (i zaakceptowanych w akceptacja.py) bierze zdjęcie ze strony źródłowej,
a gdy jej brak albo zdjęcie odpada — z karty produktu w sklepie producenta (AEON), znalezionej po nazwie
(takie zdjęcie serii trafia do akceptacji),
którą skrypt opisów już znalazł i potwierdził kodem/EAN (kolumna Zrodlo_URL) — bez ponownego
wyszukiwania. Każde zdjęcie przechodzi filtry (logo/baner w adresie, rozmiar min. 400 px,
proporcje, prawdziwy plik obrazu) i model wizyjny Ollamy (czy to produkt, czy ten rodzaj,
czy nie ma znaku wodnego). Strony ze znakami wodnymi (onninen.pl) są pomijane. Zdjęcie z napisami
OBOK produktu (wymiary, nazwa, strzałki) jest kadrowane do samego produktu i trafia do akceptacji,
jeśli nie znajdzie się czyste; znaków wodnych sklepów nie wycinamy.

  py zdjecia.py --limit 20          test na 20 produktach
  py zdjecia.py                     cała baza (wznawia od miejsca przerwania, Ctrl+C = przerwa)
  py zdjecia.py --szukaj            na końcu także wyszukiwarka obrazów (inne strony z kodem/EAN są zawsze)
  py zdjecia.py --ponow-brak --szukaj   jeszcze raz produkty, które zostały bez zdjęcia
  py zdjecia.py --pomin eksport_idosell.csv   pomiń produkty, które mają już zdjęcie w sklepie
  py zdjecia.py --podglad           strona z miniaturami: PEWNE do obejrzenia, wątpliwe do akceptacji
  py zdjecia.py --zapisz            plik dla IdoSell: @id + link do zdjęcia (pewne + zaakceptowane)
  py zdjecia.py --zapisz --adres-kadrow https://sklep.pl/data/kadry   wykadrowane z folderu zdjecia_kadry/

Wyniki: opisy_wszystkie_zdjecia.csv, pliki w folderze zdjecia/.
Model wizyjny i model opisów nie mieszczą się razem na karcie 12 GB — uruchamiaj ten skrypt,
gdy przebieg opisów stoi (albo po jego zakończeniu).
"""

from __future__ import annotations

import argparse
import glob
import io
import html
import json
import logging
import os
import re
import shutil
import sys
import time
import unicodedata
import warnings
import webbrowser

import pandas as pd
import requests

try:
    import wzbogac_produkty as w
except ImportError as exc:
    sys.exit(f"Uruchom ten skrypt w folderze z wzbogac_produkty.py ({exc}).")

COL_ID, COL_NAME, COL_CODE = w.COL_ID, w.COL_NAME, w.COL_CODE
COL_EAN, COL_PRODUCER, COL_SOURCE = w.COL_EAN, w.COL_PRODUCER, w.COL_SOURCE
COL_IMG_URL, COL_IMG_FILE, COL_IMG_PAGE = "Zdjecie_URL", "Zdjecie_plik", "Zdjecie_strona"
COL_IMG_STATUS, COL_IMG_REASON = "Status_zdjecia", "Powod_zdjecia"
COLUMNS = [COL_ID, COL_NAME, COL_CODE, COL_PRODUCER, COL_IMG_URL, COL_IMG_FILE, COL_IMG_PAGE,
           COL_IMG_STATUS, COL_IMG_REASON]
OK, REVIEW, NONE = "PEWNE", "DO_AKCEPTACJI", "BRAK"
SHOP_MIN_NAME = 0.85         # min. część słów nazwy produktu obecna w tytule ze sklepu producenta
SHOP_MIN_TITLE = 0.5         # min. część słów tytułu ze sklepu producenta obecna w nazwie produktu
VISION_TRIES = 4             # ile zdjęć jednego produktu obejrzeć modelem, zanim zostanie bez zdjęcia
# Zapasowe zdjęcia (gdy nie ma żadnego PEWNEGO) idą DO_AKCEPTACJI zamiast zostawiać produkt bez zdjęcia:
# mniejsze niż 400 px, ale min. BACKUP_MIN_SIDE, oraz zdjęcia ze strony samego producenta odrzucone tylko
# za jego własne logo (np. ALCA: napis „alca” w rogu każdego zdjęcia, biały przycisk brany za „logo”).
BACKUP_MIN_SIDE = 300
NOT_A_PHOTO_WORDS = ("rysun", "schemat", "tabel", "wymiar", "szkic", "diagram", "tekst", "kolor")
DOWNLOAD_NAME = "zaakceptowane_zdjecia"
IDOSELL_IMAGE_COLUMN = "/images/large/image@url"
log = w.log

# Sklepy, które nakładają znak wodny na zdjęcia — szkoda czasu modelu, od razu szukamy gdzie indziej.
# raleo.de, telematel.com, maan.net.pl, sparepartsboilers.com, kaprys-met.pl: 64 zdjęcia ze znakiem
# wodnym albo napisami usunięte ręcznie z arkusza gotowych — każde z tych źródeł powtarzało się kilka razy.
for site in ("mateomarket.pl", "raleo.de", "telematel.com", "maan.net.pl", "sparepartsboilers.com",
             "kaprys-met.pl"):
    if site not in w.WATERMARK_SITES:
        w.WATERMARK_SITES.append(site)
# Wysokie produkty (hydranty, zasuwy z trzpieniem) mają zdjęcia ok. 1:2 — 0,5 odrzucało je jako „baner”.
w.MIN_IMAGE_RATIO = min(w.MIN_IMAGE_RATIO, 0.4)
# Mniejsze zdjęcia przepuszczamy dalej — pick_checked zrobi z nich tylko zapas do akceptacji (patrz wyżej).
w.MIN_IMAGE_SIDE = min(w.MIN_IMAGE_SIDE, BACKUP_MIN_SIDE)
# …a długie (odpływy liniowe ALCA na sanitino.*: 960x472) ok. 2:1 — 1,9 odrzucało je tak samo. Baner/logo
# o mniej skrajnych proporcjach i tak odrzuci model wizyjny.
w.MAX_IMAGE_RATIO = max(w.MAX_IMAGE_RATIO, 2.5)
# Kilka etapów (źródło, producent, wyszukiwarka) + większe wersje miniatur — 15 prób to za mało.
w.MAX_IMAGE_TRIES = max(w.MAX_IMAGE_TRIES, 30)
# Rysunki techniczne, części zamienne i schematy wymiarowe to nie zdjęcia produktu (np. ALCA: /spareparts/).
w.BAD_IMAGE_WORDS = tuple(dict.fromkeys(w.BAD_IMAGE_WORDS + (
    "sparepart", "spare-part", "spare_part", "drawing", "rysunek", "schemat", "scheme", "wymiar",
    "dimension", "technical", "/cad/", "_cad", "-cad", ".dwg", "diagram", "certyfikat", "certificate", "pictogram",
    "piktogram", "zrzut-ekranu", "zrzut_ekranu", "screenshot", "screen-shot", "screen_shot",
    # alcadrain.com: „A97_koty.png” to rysunek wymiarowy (cz. kóty = wymiary); /category/thumbs/ to miniatury
    # kategorii z menu (VirtueMart), a .avif nie umiemy otworzyć — każdy taki adres zabierał jedną z 30 prób.
    "_koty", "-koty", "/category/", ".avif",
    # afriso.pl: /product-constructions/…-budowa-… to przekrój z opisanymi częściami, a /product-seo-images/
    # to grafiki z napisami — odrzucone ręcznie z arkusza gotowych; zdjęcia produktu są w innych katalogach.
    "budowa", "/product-constructions/", "/product-seo-images/")))
warnings.filterwarnings("ignore", category=UserWarning, module="PIL")  # „Palette images with Transparency…”

# Serwery zdjęć, na których jedna karta pokazuje też warianty serii (sanitino.*: inne długości odpływu to inne
# PRODUCT-…): bierzemy tylko zdjęcia z tym samym numerem co og:image karty.
SAME_ITEM_ID = {"data.sanitino.eu": r"/PRODUCT-(\d+)/"}
# Strony, które przy szybkich zapytaniach odpowiadają HTTP 429 (alcadrain.com: „Too many requests”) — odstęp
# między zapytaniami w sekundach. To nie obchodzi limitu, tylko się w nim mieści.
HOST_DELAY = {"alcadrain.com": 4.0}
RETRY_429_WAIT = 90          # s — jedna ponowna próba po 429; potem domena jest pomijana do końca przebiegu


class PoliteRequests:
    """Zamiast modułu requests w wzbogac_produkty: odstęp między zapytaniami do HOST_DELAY i obsługa HTTP 429.

    Po 429 czeka (Retry-After albo RETRY_429_WAIT) i ponawia raz; drugi 429 = domena pominięta do końca
    przebiegu (jak przy 403), a produkty dostają powód „HTTP 429”, więc --ponow-brak je dokończy.
    """

    def __init__(self, module):
        self._mod = module
        self._last: dict[str, float] = {}
        self.limited: set[str] = set()
        self._page: tuple[str, object] | None = None

    def __getattr__(self, name):  # requests.RequestException, requests.Response itd.
        return getattr(self._mod, name)

    @staticmethod
    def host(url: str) -> str:
        from urllib.parse import urlsplit
        return urlsplit(url).netloc.lower().removeprefix("www.")

    def _pace(self, host: str) -> None:
        delay = next((d for h, d in HOST_DELAY.items() if host == h or host.endswith("." + h)), 0)
        wait = self._last.get(host, 0) + delay - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    def get(self, url, *args, **kwargs):
        # Ta sama karta jest czytana dwa razy z rzędu (fetch_page, potem extra_page_images) — drugi raz z pamięci.
        cacheable = not kwargs.get("stream")
        if cacheable and self._page and self._page[0] == url:
            return self._page[1]
        host = self.host(url)
        self._pace(host)
        resp = self._mod.get(url, *args, **kwargs)
        if cacheable and resp.status_code == 200:
            self._page = (url, resp)
        if resp.status_code != 429 or host in self.limited:
            return resp
        retry = resp.headers.get("Retry-After", "")
        wait = min(int(retry), 300) if retry.isdigit() else RETRY_429_WAIT
        log.warning("%s: HTTP 429 (za dużo zapytań) — czekam %d s i ponawiam.", host, wait)
        resp.close()
        time.sleep(wait)
        self._pace(host)
        resp = self._mod.get(url, *args, **kwargs)
        if resp.status_code == 429:
            self.limited.add(host)
            log.warning("%s nadal odpowiada 429 — pomijam do końca przebiegu (dokończy --ponow-brak).", host)
            for _ in range(w.BLOCK_AFTER):
                w.note_response(url, 403)  # ten sam mechanizm co przy blokadzie 403: domain_blocked() = True
        return resp


http = PoliteRequests(requests)
w.requests = http  # fetch_page / fetch_image w głównym skrypcie też idą przez odstępy i obsługę 429


def side(output: str, suffix: str, ext: str = ".csv") -> str:
    return os.path.splitext(output)[0] + f"_{suffix}{ext}"


def products_to_do(output: str) -> pd.DataFrame:
    df = w.read_csv(output).drop_duplicates(subset=[COL_ID], keep="last")
    accepted_path = side(output, "zaakceptowane", ".txt")
    accepted = set()
    if os.path.isfile(accepted_path):
        with open(accepted_path, encoding="utf-8") as fh:
            accepted = {line.strip() for line in fh if line.strip()}
    keep = (df[w.COL_STATUS] == w.STATUS_OK) | df[COL_ID].isin(accepted)
    return df[keep]


def ids_with_shop_photos(path: str) -> set[str]:
    """Id produktów, które MAJĄ już zdjęcie w sklepie — z eksportu IdoSell (CSV z kolumną @id).

    Jeśli w pliku jest kolumna ze zdjęciem (nazwa zawiera „image”, „zdj” albo „photo”), pomijamy tylko
    produkty z niepustym zdjęciem; bez takiej kolumny — wszystkie id z pliku.
    """
    if not os.path.isfile(path):
        sys.exit(f"Nie ma pliku {path} (--pomin).")
    # Eksport z IdoSell bywa rozdzielany średnikiem — separator wykrywamy z pliku.
    df = pd.read_csv(path, dtype=str, keep_default_na=False, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    if COL_ID not in df.columns:
        sys.exit(f"W pliku {path} brakuje kolumny {COL_ID}. Kolumny: {', '.join(df.columns[:10])}")
    photo_cols = [c for c in df.columns if any(k in c.lower() for k in ("image", "zdj", "photo", "picture"))]
    if photo_cols:
        has = df[photo_cols].apply(lambda r: any(str(v).strip() for v in r), axis=1)
        ids = set(df.loc[has, COL_ID].str.strip())
        log.info("Pomijam %d produktów, które mają już zdjęcie w sklepie (kolumny: %s).", len(ids), ", ".join(photo_cols))
    else:
        ids = set(df[COL_ID].str.strip())
        log.info("Pomijam %d produktów z pliku %s (brak kolumny ze zdjęciem — pomijam wszystkie).", len(ids), path)
    return ids - {""}


def _jsonld_images(node) -> list[str]:
    """Adresy z pól „image” w danych strukturalnych (schema.org Product) — zwykle główne, duże zdjęcie."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, val in node.items():
            if key in ("image", "contentUrl"):
                if isinstance(val, str):
                    out.append(val)
                elif isinstance(val, dict):
                    out += [v for v in (val.get("url"), val.get("contentUrl")) if isinstance(v, str)]
                elif isinstance(val, list):
                    for v in val:
                        out += [v] if isinstance(v, str) else _jsonld_images({"image": v})
            elif isinstance(val, (dict, list)):
                out += _jsonld_images(val)
    elif isinstance(node, list):
        for item in node:
            out += _jsonld_images(item)
    return out


def extra_page_images(url: str, known: list[str]) -> tuple[list[str], list[str]]:
    """Zdjęcia, których fetch_page nie widzi: (z danych strukturalnych JSON-LD, pełne wersje z linków powiększenia).

    Link powiększenia bierzemy tylko wtedy, gdy obejmuje miniaturę, która JUŻ jest kandydatem —
    wtedy to na pewno ta sama fotografia (nie „podobne produkty” ani baner).
    """
    from urllib.parse import urljoin

    if w.domain_blocked(url):
        return [], []
    try:
        resp = http.get(url, headers=w.BROWSER_HEADERS, timeout=w.PAGE_TIMEOUT)
        if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "").lower():
            return [], []
        doc = w.html_doc(resp)
    except Exception:
        return [], []
    ld: list[str] = []
    for block in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        ld += [urljoin(url, u) for u in _jsonld_images(data) if u.startswith(("http", "/"))]
    known_set = set(known)
    big: list[str] = []
    for a in doc.xpath("//a[@href]"):
        href = urljoin(url, a.get("href"))
        if not w.is_direct_image_url(href) or href in known_set:
            continue
        for img in a.xpath(".//img"):
            src = img.get("data-src") or img.get("src") or ""
            if src and urljoin(url, src) in known_set:
                big.append(href)
                break
    for node in doc.xpath("//picture/source[@srcset]"):  # <picture>: największy wariant
        if node.getparent() is not None and any(urljoin(url, i.get("src") or "") in known_set
                                                for i in node.getparent().xpath(".//img")):
            biggest = w.largest_from_srcset(node.get("srcset"))
            if biggest:
                big.append(urljoin(url, biggest))
    return list(dict.fromkeys(ld)), list(dict.fromkeys(big))


def page_candidates(url: str, m, verified: bool, page=None) -> list[tuple[str, bool, str]]:
    """Kandydaci ze strony produktu: JSON-LD → pełne wersje z linków → galeria/og:image (jak dotąd)."""
    page = page or w.fetch_page(url)
    if not page:
        return []
    shop = {"url": url, "og_images": page[1], "imgs": page[2], "gallery": page[4] if len(page) > 4 else [],
            "producer_site": True}
    base = w.image_candidates_from_source(m, shop)
    ld, big = extra_page_images(url, base + [s for s, _ in page[2]])
    return [(u, verified, url) for u in same_item(dict.fromkeys(ld + big + base), page[1])]


def same_item(urls, og_images: list[str]) -> list[str]:
    """Bez zdjęć innych wariantów serii z tej samej karty (SAME_ITEM_ID) — gdy og:image wskazuje numer pozycji."""
    out = list(urls)
    for host, pattern in SAME_ITEM_ID.items():
        ids = {m.group(1) for u in og_images if http.host(u) == host and (m := re.search(pattern, u))}
        if ids:
            out = [u for u in out if http.host(u) != host or (m := re.search(pattern, u)) is None or m.group(1) in ids]
    return out


def source_candidates(record: dict) -> list[tuple[str, bool, str]]:
    """Zdjęcia ze strony źródłowej potwierdzonej kodem/EAN przy opisach."""
    m = w.ProductMatcher(record.get(COL_CODE, ""), record.get(COL_EAN, ""), record.get(COL_PRODUCER, ""))
    src = record.get(COL_SOURCE, "")
    if not src or w.is_watermark_site(src):
        return []
    # strona potwierdzona kodem/EAN — galeria i dane strukturalne to ten produkt
    return page_candidates(src, m, True)


WEB_PAGES_TO_CHECK = 5       # ile stron z wyników wyszukiwania (po kodzie/EAN) otworzyć na produkt


def web_page_candidates(record: dict, engine: str) -> list[tuple[str, bool, str]]:
    """Zdjęcia z INNYCH stron (hurtownie, sklepy) znalezionych w wyszukiwarce po kodzie i EAN.

    Bierzemy tylko strony, na których jest kod albo EAN produktu — jak przy stronie źródłowej. Strona źródłowa
    z opisów to jedna strona; ten sam kod ma zwykle kilka innych sklepów, często ze zdjęciem.
    """
    code, ean = record.get(COL_CODE, "").strip(), record.get(COL_EAN, "").strip()
    producer = record.get(COL_PRODUCER, "").strip()
    m = w.ProductMatcher(code, ean, producer)
    skip = {record.get(COL_SOURCE, "")}
    queries = [q for q in (f"{producer} {code}".strip() if code else "", ean) if q]
    out: list[tuple[str, bool, str]] = []
    checked, with_code, found = 0, 0, 0
    for query in queries:
        urls = w.with_retry(w.search_pages, query, engine, what=f"[id={record[COL_ID]}] strony '{query}'") or []
        found += len(urls)
        for url in urls:
            if checked >= WEB_PAGES_TO_CHECK:
                break
            if not url or url in skip or w.is_watermark_site(url) or w.domain_blocked(url):
                continue
            skip.add(url)
            checked += 1
            page = w.fetch_page(url)
            if page and m.find(page[0]) is not None:  # strona ma kod/EAN — zdjęcia potwierdzone
                with_code += 1
                out += page_candidates(url, m, True, page)
        if out or checked >= WEB_PAGES_TO_CHECK:
            break  # są zdjęcia po kodzie — EAN niepotrzebny
    log.info("[id=%s] inne strony: wyników %d, otwarte %d, z kodem/EAN %d, zdjęć %d",
             record[COL_ID], found, checked, with_code, len(out))
    return out


def producer_code_candidates(record: dict) -> list[tuple[str, bool, str]]:
    """Zdjęcia z karty produktu na stronie producenta znalezionej PO KODZIE (DIRECT_SEARCH w głównym skrypcie).

    Karta musi zawierać kod albo EAN produktu — wtedy zdjęcie jest potwierdzone jak ze strony źródłowej.
    """
    code, ean, producer = record.get(COL_CODE, ""), record.get(COL_EAN, ""), record.get(COL_PRODUCER, "")
    if not code or not hasattr(w, "direct_search_urls"):
        return []
    m = w.ProductMatcher(code, ean, producer)
    found: list[tuple[str, bool, str]] = []
    try:
        urls = w.direct_search_urls(m, code, producer)
    except TypeError:  # starsza wersja głównego skryptu bez parametru producenta
        urls = w.direct_search_urls(m, code)
    for url in urls[:3]:
        if url == record.get(COL_SOURCE) or w.is_watermark_site(url):
            continue
        page = w.fetch_page(url)
        if not page:
            continue
        if m.find(page[0]) is not None or m.in_short_text(url, page[3] if len(page) > 3 else ""):
            found += page_candidates(url, m, True, page)
        elif hasattr(w, "found_only_by_search") and w.found_only_by_search(code, url):
            # Karta serii u producenta dopasowana wzorcem SKU (Bohamet: 21.560.DN.1 dla 21.560.100.1) — kodu
            # na karcie nie ma, ale to ten produkt w innym rozmiarze: zdjęcie do akceptacji, nie PEWNE.
            found += page_candidates(url, m, False, page)
    return found


STOP_WORDS = {"typ", "do", "dla", "z", "ze", "i", "w", "na", "od", "bez", "the", "and", "with"}


def _tokens(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.lower().replace("ł", "l")).encode("ascii", "ignore").decode()
    return [t for t in re.findall(r"[a-z]+\d+|[a-z]+|\d+", plain) if len(t) >= 2 and t not in STOP_WORDS]


def _key_words(tokens: list[str]) -> list[str]:
    """Słowa rozróżniające produkty: litery (też skróty „kr”, „rk”) i oznaczenia typu F4/F5 — bez wymiarów DN80, D225."""
    return [t for t in tokens if t.isalpha() or re.fullmatch(r"[a-z]\d", t)]


def _same_word(a: str, b: str) -> bool:
    """„nadz” = „nadziemny”, „kr” = „krotka” — skróty z nazw w bazie to początki pełnych słów."""
    if a == b:
        return True
    if a.isdigit() or b.isdigit():
        num, other = (a, b) if a.isdigit() else (b, a)
        return re.match(r"\d*", other).group() == num  # „2018” = „2018c”
    if not (a.isalpha() and b.isalpha()):
        return False  # F4 ≠ F5
    short, long_ = sorted((a, b), key=len)
    return long_.startswith(short[:5])


def name_match(name: str, title: str) -> tuple[float, float]:
    """(część słów nazwy obecnych w tytule, część słów tytułu obecnych w nazwie) — obie 0–1.

    Pierwsza liczba pilnuje, żeby „hydrant nadziemny” nie trafił na „hydrant podziemny”, a „KR. F4” na „długa F5”;
    druga — żeby krótka nazwa („łącznik RK”) nie trafiła na inny wariant („łącznik rurowy RR”).
    """
    title_t, name_t = _tokens(title), _tokens(name)
    name_words = _key_words(name_t)
    if not title_t or not name_words:
        return 0.0, 0.0
    name_cov = sum(any(_same_word(n, t) for t in title_t) for n in name_words) / len(name_words)
    title_cov = sum(any(_same_word(t, n) for n in name_t) for t in title_t) / len(title_t)
    return name_cov, title_cov


def producer_shop_candidates(record: dict) -> tuple[list[tuple[str, bool, str]], str]:
    """Zdjęcia z karty produktu w sklepie producenta (AEON…), znalezionej po nazwie. Zwraca (kandydaci, tytuł)."""
    template = w.PRODUCER_SEARCH.get(record.get(COL_PRODUCER, "").strip().upper())
    name = record.get(COL_NAME, "")
    words = [t for t in re.findall(r"[^\W\d_]{4,}", name)]  # słowa z liter — bez DN80, PN16, skrótów
    if not template or not words:
        return [], ""
    items: list[tuple[str, str]] = []
    for n in (3, 2, 1):  # sklep szuka wszystkich słów naraz — przy braku wyników krótsze zapytanie
        query = " ".join(words[:n])
        try:
            items = w.search_producer_shop(template, query)
        except Exception as exc:
            log.info("[id=%s] sklep producenta '%s': %s", record[COL_ID], query, exc)
            items = []
        if items:
            break
    scored = []
    for t, u in items:
        name_cov, title_cov = name_match(name, t)
        if name_cov >= SHOP_MIN_NAME and title_cov >= SHOP_MIN_TITLE:
            scored.append((round(name_cov + title_cov, 3), t, u))
    scored.sort(reverse=True)
    if not scored:
        return [], ""
    if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[1][1] != scored[0][1]:
        log.info("[id=%s] sklep producenta: kilka równie pasujących pozycji (%s / %s) — pomijam",
                 record[COL_ID], scored[0][1], scored[1][1])
        return [], ""
    _, title, url = scored[0]
    page = w.fetch_page(url)
    if not page:
        return [], ""
    shop = {"url": url, "og_images": page[1], "imgs": page[2], "gallery": page[4] if len(page) > 4 else [],
            "producer_site": True}
    return [(u, False, url) for u in w.image_candidates_from_source(w.ProductMatcher("", "", ""), shop)], title


# Sklepy pokazują miniatury, a pełny rozmiar leży pod podobnym adresem:
# WordPress „zdjecie-150x150.jpg” → „zdjecie.jpg”, „/thumb/”, „_small” → „/large/”, „_large”, parametry „?w=100”.
SIZE_WORDS = [("thumbnail", "large"), ("thumbs", "large"), ("thumb", "large"), ("small", "large"),
              ("mini", "large"), ("medium", "large"), ("_min", "_max"), ("/s/", "/l/"), ("/m/", "/l/"),
              ("/middle/", "/big/"), ("/middle/", "/large/"), ("/middle/", "/original/"),  # np. saniland.sk
              ("large_default", "thickbox_default"), ("home_default", "large_default")]  # PrestaShop


def bigger_variants(url: str) -> list[str]:
    """Adresy możliwych większych wersji miniatury (sprawdzane tymi samymi filtrami co oryginał)."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    path = parts.path
    variants = []
    stripped = re.sub(r"-\d{2,4}x\d{2,4}(?=\.[A-Za-z]{3,4}$)", "", path)  # WordPress/WooCommerce
    if stripped != path:
        variants.append(stripped)
    prefixed = re.sub(r"/\d{2,4}_{2,3}(?=[^/]+$)", "/", path)  # rurex.pl i in.: „/f83/500___5.jpg” → „/f83/5.jpg”
    if prefixed != path:
        variants.append(prefixed)
    # „/files/thumbs/2021/07/nazwa_123-4c16efdc.jpg” → oryginał bez katalogu miniatur (i bez skrótu rozmiaru)
    no_thumbs = re.sub(r"/thumbs?/", "/", path, count=1, flags=re.IGNORECASE)
    if no_thumbs != path:
        no_hash = re.sub(r"-[0-9a-f]{6,12}(?=\.[A-Za-z]{3,4}$)", "", no_thumbs)
        variants += [no_hash, no_thumbs] if no_hash != no_thumbs else [no_thumbs]
    for small, big in SIZE_WORDS:
        if small == "thumb" and "thumbs" in path.lower():
            continue  # „thumbs” już obsłużone — „thumb” dałoby „larges”
        if small in path.lower():
            variants.append(re.sub(re.escape(small), big, path, flags=re.IGNORECASE))
    out = [urlunsplit((parts.scheme, parts.netloc, v, parts.query, "")) for v in variants]
    if parts.query and re.search(r"(^|&)(w|h|width|height|size|resize)=", parts.query, re.I):
        out.append(urlunsplit((parts.scheme, parts.netloc, path, "", "")))  # bez parametrów zmniejszania
    return [u for u in dict.fromkeys(out) if u != url]


def with_bigger_variants(cands: list[tuple[str, bool, str]]) -> list[tuple[str, bool, str]]:
    """Najpierw możliwe większe wersje, potem oryginał — większa wersja tego samego zdjęcia ma pierwszeństwo."""
    out: list[tuple[str, bool, str]] = []
    for url, verified, page in cands:
        out += [(v, verified, page) for v in bigger_variants(url)] + [(url, verified, page)]
    return list(dict.fromkeys(out))


def image_size(data: bytes) -> tuple[int, int]:
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return 0, 0


def backup_reason(record: dict, url: str, verified: bool, note: str) -> str:
    """Czy zdjęcie odrzucone przez model nadaje się na zapas do akceptacji — powód albo ""."""
    if not (verified and w.is_producer_url(url, record.get(COL_PRODUCER, ""))):
        return ""  # cudze znaki wodne / niepewne strony — nie
    low = note.lower()
    if note.startswith("znak wodny"):  # produkt i rodzaj OK, przeszkadza tylko napis/logo
        return "zdjęcie producenta z jego logo — model uznał je za znak wodny"
    if note.startswith("to nie jest zdjęcie produktu") and "logo" in low and not any(
            word in low for word in NOT_A_PHOTO_WORDS):
        return "zdjęcie producenta, model widział tylko logo — sprawdź"
    return ""


CROP_REASON = "wykadrowane — odcięto napisy obok produktu, sprawdź kadr"
CROP_PAD = 0.04              # margines wokół produktu (część boku prostokąta)
CROP_SUFFIX = "_kadr"        # pliki wykadrowanych zdjęć: <id>_<kod>_kadr.jpg


def crop_text_away(record: dict, data: bytes) -> bytes:
    """Samo zdjęcie produktu bez napisów obok (wymiary, nazwa, strzałki) — albo b"", gdy się nie da.

    Tylko dla napisów, które model uznał za NIE-znak wodny: znaków wodnych sklepów nie wycinamy, takie
    zdjęcie po prostu odpada i szukamy innego.
    """
    from PIL import Image

    try:
        box = w.with_retry(w.product_box, data, record.get(COL_NAME, ""), what=f"[id={record[COL_ID]}] kadr")
    except Exception:
        box = None
    if not box:
        return b""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        width, height = img.size
        x1, y1, x2, y2 = box
        if (x2 - x1) * (y2 - y1) < 0.1 * width * height or (x2 - x1) * (y2 - y1) > 0.92 * width * height:
            return b""  # model wskazał byle co albo prawie całe zdjęcie — kadr nic nie da
        pad_x, pad_y = (x2 - x1) * CROP_PAD, (y2 - y1) * CROP_PAD
        crop = img.crop((max(0, round(x1 - pad_x)), max(0, round(y1 - pad_y)),
                         min(width, round(x2 + pad_x)), min(height, round(y2 + pad_y))))
        if min(crop.size) < 400:
            return b""
        out = io.BytesIO()
        crop.save(out, "JPEG", quality=92)
    cropped = out.getvalue()
    result, note = w.check_one_image(record, cropped)  # kadr musi przejść tę samą kontrolę od nowa
    if result != w.CHECK_OK or w.LAST_VISION_ANSWER.get("zbedne_napisy") is True:
        log.info("[id=%s] kadr odrzucony — %s", record[COL_ID], note or "nadal widać napisy")
        return b""
    return cropped


LOGGED_SKIPS: set[str] = set()


def pick_checked(record: dict, cands: list[tuple[str, bool, str]], vision: bool,
                 tried: set[str], notes: list[str], backups: list[tuple]) -> tuple[str, bool, bytes, str, str]:
    """Pierwsze zdjęcie, które przejdzie filtry i model wizyjny: (url, potwierdzone, dane, rozszerzenie, strona).

    Zdjęcia „prawie dobre” (małe, logo producenta) trafiają do `backups` jako
    (url, potwierdzone, dane, rozszerzenie, strona, powód) — użyte, gdy nic lepszego się nie znajdzie.
    """
    cands = with_bigger_variants(cands)
    pages = {u: p for u, _, p in cands}
    base = w.safe_filename(f"{record[COL_ID]}_{record.get(COL_CODE, '')}")
    for _ in range(VISION_TRIES):
        rejected: list[str] = []
        url, verified, _, reason = w.pick_image(cands, None, base, tried, rejected)  # pobiera + filtry
        for line in rejected:  # tylko filtr adresu, każdy adres raz na przebieg (logo sklepu jest na każdej karcie)
            if line.startswith("logo/baner") and line not in LOGGED_SKIPS:
                LOGGED_SKIPS.add(line)
                log.info("[id=%s] pominięte — %s", record[COL_ID], line)
        if not url:
            if reason:
                notes.append(f"brak poprawnego zdjęcia ({reason})")
            break
        data, ext, err = w.fetch_image(url, pages.get(url, ""))
        if err:
            notes.append(err)
            continue
        width, height = image_size(data)
        small = min(width, height) < 400
        if vision:
            result, note = w.check_one_image(record, data)
            answer = dict(w.LAST_VISION_ANSWER)
            if (answer.get("zdjecie_produktu") is True and answer.get("rodzaj_zgodny") is True
                    and answer.get("znak_wodny") is False and answer.get("zbedne_napisy") is True):
                # Produkt dobry, przeszkadzają tylko napisy obok niego: szukamy dalej czystego zdjęcia,
                # a wykadrowane zostaje jako zapas do akceptacji.
                log.info("[id=%s] napisy obok produktu — kadruję: %s", record[COL_ID], url)
                cropped = b"" if small else crop_text_away(record, data)
                if cropped:
                    backups.append((url, verified, cropped, ".jpg", pages.get(url, ""), CROP_REASON))
                notes.append("napisy obok produktu" + ("" if cropped else " (kadr się nie udał)"))
                continue
            if result != w.CHECK_OK:
                notes.append(note)
                log.info("[id=%s] odrzucone — %s: %s", record[COL_ID], note, url)
                why = "" if small else backup_reason(record, url, verified, note)
                if why:
                    backups.append((url, verified, data, ext, pages.get(url, ""), why))
                continue
        if small:
            notes.append(f"obrazek za mały ({width}x{height})")
            backups.append((url, verified, data, ext, pages.get(url, ""),
                            f"małe zdjęcie ({width}x{height}) — lepszego nie znaleziono"))
            continue
        return url, verified, data, ext, pages.get(url, "")
    return "", False, b"", "", ""


def process(record: dict, images_dir: str, search: bool, engine: str, vision: bool) -> dict:
    out = {c: record.get(c, "") for c in (COL_ID, COL_NAME, COL_CODE, COL_PRODUCER)}
    out.update({COL_IMG_URL: "", COL_IMG_FILE: "", COL_IMG_PAGE: "", COL_IMG_STATUS: NONE, COL_IMG_REASON: ""})
    tried: set[str] = set()
    for bad in REJECTED.get(record[COL_ID], ()):  # odrzucone ręcznie — nie wracamy do nich ani do ich wersji
        tried.add(bad)
        tried.update(bigger_variants(bad))
    notes: list[str] = []
    # Kolejność: strona źródłowa (potwierdzona kodem) → strona producenta po kodzie → sklep producenta (seria
    # po nazwie) → wyszukiwarka obrazów.
    stages = [("źródło", lambda: (source_candidates(record), "")),
              ("producent-kod", lambda: (producer_code_candidates(record), ""))]
    stages.append(("producent", lambda: producer_shop_candidates(record)))
    # Gdy źródło i producent nic nie dały: inne strony z tym kodem/EAN (wyszukiwarka stron, nie obrazów).
    stages.append(("inne strony", lambda: (web_page_candidates(record, engine), "")))
    if search:
        m = w.ProductMatcher(record.get(COL_CODE, ""), record.get(COL_EAN, ""), record.get(COL_PRODUCER, ""))
        stages.append(("wyszukiwarka", lambda: (w.image_candidates_from_search(
            m, record.get(COL_CODE, ""), record.get(COL_EAN, ""), record.get(COL_PRODUCER, ""), engine,
            f"[id={record[COL_ID]}]"), "")))
    any_candidates = False
    backups: list[tuple] = []
    for stage, get in stages:
        cands, shop_title = get()
        if not cands:
            continue
        any_candidates = True
        url, verified, data, ext, page = pick_checked(record, cands, vision, tried, notes, backups)
        if not url:
            continue
        base = w.safe_filename(f"{record[COL_ID]}_{record.get(COL_CODE, '')}")
        out.update({COL_IMG_URL: url, COL_IMG_PAGE: page,
                    COL_IMG_FILE: w.save_image(data, ext, images_dir, base).replace(os.sep, "/")})
        problems = []
        if stage == "producent":
            problems.append(f"zdjęcie serii ze strony producenta, dopasowane po nazwie („{shop_title}”)")
        elif stage == "producent-kod" and not verified:
            problems.append("zdjęcie serii ze strony producenta, karta dopasowana wzorcem kodu (np. 21.560.DN.1)")
        elif not verified:
            problems.append("zdjęcie niepotwierdzone kodem/EAN")
        if not vision:
            problems.append("nie sprawdzone modelem wizyjnym")
        out[COL_IMG_STATUS] = REVIEW if problems else OK
        out[COL_IMG_REASON] = "; ".join(problems)
        return out
    if backups:  # nic pewnego — najlepszy zapas (z najwcześniejszego etapu) do akceptacji jednym kliknięciem
        url, verified, data, ext, page, why = backups[0]
        base = w.safe_filename(f"{record[COL_ID]}_{record.get(COL_CODE, '')}")
        if why == CROP_REASON:
            base += CROP_SUFFIX
        out.update({COL_IMG_URL: url, COL_IMG_PAGE: page, COL_IMG_STATUS: REVIEW, COL_IMG_REASON: why,
                    COL_IMG_FILE: w.save_image(data, ext, images_dir, base).replace(os.sep, "/")})
        return out
    src = record.get(COL_SOURCE, "")
    if not any_candidates and src and http.host(src) in http.limited:
        out[COL_IMG_REASON] = "strona źródłowa ogranicza liczbę zapytań (HTTP 429) — ponów: --ponow-brak"
    elif not any_candidates:
        out[COL_IMG_REASON] = "brak zdjęcia na stronie źródłowej" if src else "brak strony źródłowej"
        if w.PRODUCER_SEARCH.get(record.get(COL_PRODUCER, "").strip().upper()):
            out[COL_IMG_REASON] += "; brak pasującej pozycji w sklepie producenta"
    else:
        out[COL_IMG_REASON] = "; ".join(dict.fromkeys(n for n in notes if n)) or "brak poprawnego zdjęcia"
    return out


def run(args) -> None:
    if args.bez_wizji:
        log.warning("Bez modelu wizyjnego — wszystkie zdjęcia trafią do akceptacji.")
    else:
        w.check_ollama(dict(w.AI_PROVIDERS["ollama"], models=[w.VISION_MODEL]))
    todo = products_to_do(args.output)
    REJECTED.update(load_rejected(args.output))
    result_path = side(args.output, "zdjecia")
    done, retry = set(), set()
    if os.path.isfile(result_path) and os.path.getsize(result_path):
        prev = w.read_csv(result_path).drop_duplicates(subset=[COL_ID], keep="last")
        retry = set(prev.loc[prev[COL_IMG_STATUS] == NONE, COL_ID]) if args.ponow_brak else set()
        if args.ponow_brak:
            prev = prev[prev[COL_IMG_STATUS] != NONE]  # bez zdjęcia -> do ponownego przetworzenia
        done = set(prev[COL_ID])
    skip = ids_with_shop_photos(args.pomin) if args.pomin else set()
    records = [r for r in todo.to_dict("records") if r[COL_ID] not in done and r[COL_ID] not in skip]
    # Najpierw produkty, których jeszcze nie było, a ponowienia „bez zdjęcia” na końcu — inaczej przebieg
    # zaczyna od tych samych trudnych produktów (np. AEON) i długo nie widać efektów.
    records.sort(key=lambda r: r[COL_ID] in retry)
    if retry:
        log.info("Nowe produkty: %d, ponowienia bez zdjęcia (na końcu): %d.",
                 sum(r[COL_ID] not in retry for r in records), sum(r[COL_ID] in retry for r in records))
    if args.limit:
        records = records[:args.limit]
    images_dir = os.path.relpath(os.path.join(os.path.dirname(os.path.abspath(args.output)), w.IMAGES_DIR))
    log.info("Zdjęcia: do zrobienia %d (zrobione wcześniej: %d). Folder: %s",
             len(records), len(done), os.path.abspath(images_dir))
    started, batch, counts = time.time(), [], {OK: 0, REVIEW: 0, NONE: 0}
    try:
        for i, record in enumerate(records, 1):
            out = process(record, images_dir, args.szukaj, args.search, not args.bez_wizji)
            counts[out[COL_IMG_STATUS]] += 1
            log.info("[id=%s] %s%s", out[COL_ID], out[COL_IMG_STATUS],
                     f" — {out[COL_IMG_REASON]}" if out[COL_IMG_REASON] else "")
            batch.append(out)
            if len(batch) >= w.CHECKPOINT_EVERY or i == len(records):
                w.append_batch(batch, COLUMNS, result_path)
                batch = []
                left = (len(records) - i) * (time.time() - started) / i / 3600
                log.info("CHECKPOINT: %d/%d. Pewne %d, do akceptacji %d, bez zdjęcia %d. Pozostało ok. %.1f h.",
                         i, len(records), counts[OK], counts[REVIEW], counts[NONE], left)
    except KeyboardInterrupt:
        if batch:
            w.append_batch(batch, COLUMNS, result_path)
        log.warning("Przerwano — zapisane, ta sama komenda dokończy resztę.")
    except w.FatalError as exc:
        if batch:
            w.append_batch(batch, COLUMNS, result_path)
        log.error("Zatrzymuję: %s", exc)
    log.info("Wyniki: %s. Podgląd: py zdjecia.py --podglad", result_path)


def load_results(output: str) -> pd.DataFrame:
    path = side(output, "zdjecia")
    if not os.path.isfile(path):
        sys.exit(f"Nie ma pliku {path}. Najpierw uruchom: py zdjecia.py")
    return w.read_csv(path).drop_duplicates(subset=[COL_ID], keep="last")


def accepted_store(output: str) -> str:
    return side(output, "zdjecia_zaakceptowane", ".txt")


def rejected_store(output: str) -> str:
    return side(output, "zdjecia_odrzucone", ".txt")


def load_rejected(output: str) -> dict[str, set[str]]:
    """{id: {adresy zdjęć odrzuconych ręcznie}} — przy ponownym szukaniu te adresy są pomijane."""
    out: dict[str, set[str]] = {}
    path = rejected_store(output)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                pid, _, url = line.rstrip("\n").partition("\t")
                if pid and url:
                    out.setdefault(pid, set()).add(url)
    return out


REJECTED: dict[str, set[str]] = {}
MANUAL_REJECT_REASON = "zdjęcie odrzucone ręcznie — do ponownego szukania (--ponow-brak)"


def reject(args) -> None:
    """--odrzuc: złe zdjęcia (np. zaznaczone w arkuszu) → BRAK, a ich adresy na listę pomijanych."""
    ids = {v.strip() for arg in args.odrzuc for v in re.split(r"[,;\s]+", arg) if v.strip()}
    df = load_results(args.output)
    rows = df[df[COL_ID].isin(ids)]
    missing = sorted(ids - set(rows[COL_ID]))
    with open(rejected_store(args.output), "a", encoding="utf-8") as fh:
        for r in rows.to_dict("records"):
            if r[COL_IMG_URL]:
                fh.write(f"{r[COL_ID]}\t{r[COL_IMG_URL]}\n")
    out = []
    for r in rows.to_dict("records"):
        r.update({COL_IMG_URL: "", COL_IMG_FILE: "", COL_IMG_PAGE: "", COL_IMG_STATUS: NONE,
                  COL_IMG_REASON: MANUAL_REJECT_REASON})
        out.append({c: r.get(c, "") for c in COLUMNS})
    if out:
        w.append_batch(out, COLUMNS, side(args.output, "zdjecia"))
    store = accepted_store(args.output)  # odrzucone nie mogą zostać „zaakceptowane”
    if os.path.isfile(store):
        with open(store, encoding="utf-8") as fh:
            kept = [line.strip() for line in fh if line.strip() and line.strip() not in ids]
        with open(store, "w", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + ("\n" if kept else ""))
    print(f"Odrzucone zdjęcia: {len(out)}. Te produkty dostaną nowe zdjęcie przy: py zdjecia.py --ponow-brak")
    if missing:
        print(f"Nie znaleziono w wynikach zdjęć: {', '.join(missing)}")


def preview(args) -> None:
    df = load_results(args.output)
    store = accepted_store(args.output)
    accepted = set()
    if os.path.isfile(store):
        with open(store, encoding="utf-8") as fh:
            accepted = {line.strip() for line in fh if line.strip()}
    page_path = side(args.output, "zdjecia_podglad", ".html")
    base = os.path.dirname(os.path.abspath(page_path))
    items = []
    for r in df.to_dict("records"):
        img = r[COL_IMG_FILE]
        if img and os.path.exists(img):
            img = os.path.relpath(os.path.abspath(img), base).replace(os.sep, "/")
        items.append({"id": r[COL_ID], "n": r[COL_NAME], "c": r[COL_CODE], "p": r[COL_PRODUCER],
                      "i": img or r[COL_IMG_URL], "u": r[COL_IMG_URL], "s": r[COL_IMG_STATUS], "r": r[COL_IMG_REASON],
                      "pg": r[COL_IMG_PAGE], "a": r[COL_ID] in accepted})
    data = json.dumps({"items": items, "key": os.path.abspath(args.output), "file": DOWNLOAD_NAME + ".csv"},
                      ensure_ascii=False).replace("</", "<\\/")
    with open(page_path, "w", encoding="utf-8") as fh:
        fh.write(PAGE.replace("__DATA__", data))
    c = df[COL_IMG_STATUS].value_counts()
    print(f"Pewne: {c.get(OK, 0)}, do akceptacji: {c.get(REVIEW, 0)}, bez zdjęcia: {c.get(NONE, 0)}")
    print(f"Strona: {page_path}")
    if not args.bez_otwierania:
        webbrowser.open("file://" + os.path.abspath(page_path).replace(os.sep, "/"))


def newest_download() -> str | None:
    folders = [os.path.join(os.path.expanduser("~"), d) for d in ("Downloads", "Pobrane")]
    files = [f for d in folders for f in glob.glob(os.path.join(d, DOWNLOAD_NAME + "*.csv"))]
    return max(files, key=os.path.getmtime) if files else None


def save(args) -> None:
    df = load_results(args.output)
    store = accepted_store(args.output)
    src = args.plik or newest_download()
    accepted: set[str] = set()
    if src and os.path.isfile(src):
        ids = w.read_csv(src)
        accepted = {v.strip() for v in ids[ids.columns[0]] if v.strip()}
        with open(store, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(accepted)) + "\n")
        print(f"Wczytano akceptację zdjęć z {src}: {len(accepted)}.")
    elif os.path.isfile(store):
        with open(store, encoding="utf-8") as fh:
            accepted = {line.strip() for line in fh if line.strip()}
    ok = df[(df[COL_IMG_STATUS] == OK) | ((df[COL_IMG_STATUS] == REVIEW) & df[COL_ID].isin(accepted))]
    ok = ok[ok[COL_IMG_URL].str.strip().ne("")].copy()
    # Wykadrowane zdjęcia są tylko na dysku (link prowadzi do oryginału z napisami): kopiujemy je do
    # jednego folderu jako <id>.jpg — po wgraniu ich do sklepu link to --adres-kadrow + <id>.jpg.
    cropped = ok[ok[COL_IMG_REASON].eq(CROP_REASON) & ok[COL_IMG_FILE].map(lambda f: bool(f) and os.path.isfile(f))]
    if len(cropped):
        folder = os.path.join(os.path.dirname(os.path.abspath(args.output)), "zdjecia_kadry")
        os.makedirs(folder, exist_ok=True)
        for r in cropped.to_dict("records"):
            shutil.copyfile(r[COL_IMG_FILE], os.path.join(folder, f"{r[COL_ID]}.jpg"))
        if args.adres_kadrow:
            ok.loc[cropped.index, COL_IMG_URL] = [args.adres_kadrow.rstrip("/") + f"/{i}.jpg" for i in cropped[COL_ID]]
        else:
            ok = ok.drop(index=cropped.index)
        print(f"Wykadrowane zdjęcia: {len(cropped)} — skopiowane do {folder}. "
              + ("Linki w pliku wskazują na --adres-kadrow." if args.adres_kadrow else
                 "Wgraj je do sklepu i uruchom ponownie z --adres-kadrow <adres folderu> — na razie pominięte."))
    path = side(args.output, "zdjecia_idosell")
    ok[[COL_ID, COL_IMG_URL]].rename(columns={COL_IMG_URL: args.kolumna_zdjecia}).to_csv(
        path, index=False, encoding="utf-8-sig")
    print(f"Zdjęcia do importu: {len(ok)} (pewne {int((ok[COL_IMG_STATUS] == OK).sum())}, "
          f"zaakceptowane {int((ok[COL_IMG_STATUS] == REVIEW).sum())}).")
    print(f"Plik dla IdoSell (@id + link w kolumnie {args.kolumna_zdjecia}): {path}")


def main() -> None:
    p = argparse.ArgumentParser(description="Zdjęcia produktów ze stron źródłowych + kontrola modelem wizyjnym.")
    p.add_argument("--output", "-o", default="opisy_wszystkie.csv", help="Plik z wynikami opisów")
    p.add_argument("--limit", type=int, help="Tylko N produktów (test)")
    p.add_argument("--pomin", metavar="PLIK",
                   help="Eksport z IdoSell (CSV z @id): pomiń produkty, które mają już zdjęcie w sklepie")
    p.add_argument("--ponow-brak", action="store_true",
                   help="Przetwórz ponownie produkty, które zostały bez zdjęcia (np. z --szukaj)")
    p.add_argument("--szukaj", action="store_true",
                   help="Gdy strona źródłowa nie ma zdjęcia — szukaj w wyszukiwarce obrazów")
    p.add_argument("--search", choices=["ddg", "serpapi", "google"], default=w.SEARCH_ENGINE)
    p.add_argument("--bez-wizji", action="store_true", help="Bez modelu wizyjnego (wszystko do akceptacji)")
    p.add_argument("--podglad", action="store_true", help="Strona z miniaturami i akceptacją")
    p.add_argument("--zapisz", action="store_true", help="Plik dla IdoSell (@id + link do zdjęcia)")
    p.add_argument("--plik", help="Plik pobrany ze strony podglądu (domyślnie najnowszy z Pobranych)")
    p.add_argument("--adres-kadrow", metavar="URL",
                   help="Adres folderu w sklepie z wgranymi wykadrowanymi zdjęciami (pliki <id>.jpg z zdjecia_kadry/)")
    p.add_argument("--kolumna-zdjecia", default=IDOSELL_IMAGE_COLUMN,
                   help="Nagłówek kolumny zdjęcia w pliku dla IdoSell (jak w eksporcie IdoSell)")
    p.add_argument("--odrzuc", nargs="+", metavar="ID",
                   help="Złe zdjęcia tych produktów (@id, np. z arkusza): BRAK + adres na liście pomijanych")
    p.add_argument("--bez-otwierania", action="store_true")
    args = p.parse_args()
    if not os.path.isfile(args.output):
        sys.exit(f"Nie ma pliku {args.output}. Podaj --output z wynikami opisów.")
    if args.podglad:
        return preview(args)
    if args.zapisz:
        return save(args)
    if args.odrzuc:
        return reject(args)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S", handlers=[logging.StreamHandler(),
                                                      logging.FileHandler("zdjecia.log", encoding="utf-8")])
    log.setLevel(logging.INFO)
    run(args)


PAGE = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Zdjęcia produktów</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--fg:#1d2433;--muted:#667;--line:#ddd;--ok:#2e9d5b;--warn:#8a6100;--warnbg:#fff8e1}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}
.top{position:sticky;top:0;background:var(--card);padding:10px 16px;border-bottom:1px solid var(--line);z-index:2;
  display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.sum{font-weight:600;margin-right:auto}
button{padding:7px 12px;border:1px solid #bbb;border-radius:6px;background:#fff;color:var(--fg);cursor:pointer;font:inherit}
button.on{background:var(--fg);color:#fff} button.primary{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:600}
main{max-width:1200px;margin:0 auto;padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.card{background:var(--card);border-radius:10px;padding:10px;border-top:5px solid var(--ok);display:flex;flex-direction:column;gap:6px}
.card.DO_AKCEPTACJI{border-top-color:#e0a100} .card.BRAK{border-top-color:#bbb}
.card img{width:100%;aspect-ratio:1;object-fit:contain;background:#fff;border:1px solid #eee;border-radius:6px}
.noimg{width:100%;aspect-ratio:1;display:flex;align-items:center;justify-content:center;background:#f0f0f0;color:#888;border-radius:6px}
.card b{font-size:13px} .card small{color:var(--muted);font-size:12px}
.reason{color:var(--warn);background:var(--warnbg);padding:4px 6px;border-radius:6px;font-size:12px}
.card a{font-size:11px;word-break:break-all;color:var(--muted)}
.card label{display:flex;gap:6px;align-items:center;font-weight:600}
.more{grid-column:1/-1;text-align:center}
.howto{flex-basis:100%;font-size:13px;color:var(--muted)}
@media print{.top,.more{display:none!important}body{background:#fff}main{max-width:none;padding:0;grid-template-columns:repeat(4,1fr)}
  .card{break-inside:avoid;border:1px solid #ccc}}
</style></head><body>
<div class="top"><span class="sum" id="sum"></span>
<button data-f="DO_AKCEPTACJI" class="on">Do akceptacji</button><button data-f="PEWNE">Pewne</button>
<button data-f="BRAK">Bez zdjęcia</button>
<button id="all">Zaznacz wszystkie</button><button id="none">Odznacz</button>
<button id="pdf">PDF / drukuj</button><button class="primary" id="save">Zapisz akceptację</button>
<div class="howto">Jak zaakceptować: zaznacz zdjęcia → „Zapisz akceptację” → plik <b>zaakceptowane_zdjecia.csv</b>
z folderu Pobrane odeślij osobie, która przysłała tę stronę.</div></div>
<main id="main"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent), KEY='zdjecia:'+D.key, PAGE=120;
let sel=new Set(D.items.filter(i=>i.a).map(i=>i.id)), filt='DO_AKCEPTACJI', lim=PAGE, ALL=false;
try{const s=localStorage.getItem(KEY);if(s)sel=new Set(JSON.parse(s));}catch(e){}
const R=D.items.filter(i=>i.s==='DO_AKCEPTACJI');
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function persist(){try{localStorage.setItem(KEY,JSON.stringify([...sel]));}catch(e){}render();}
function render(){
  const n=D.items.reduce((a,i)=>(a[i.s]=(a[i.s]||0)+1,a),{});
  document.getElementById('sum').textContent=`Zaakceptowane: ${R.filter(i=>sel.has(i.id)).length} z ${R.length} · pewne: ${n.PEWNE||0} · bez zdjęcia: ${n.BRAK||0}`;
  const list=D.items.filter(i=>i.s===filt), main=document.getElementById('main');main.innerHTML='';
  list.slice(0,ALL?list.length:lim).forEach(i=>{
    const c=document.createElement('div');c.className='card '+i.s;
    c.innerHTML=(i.i?`<a href="${esc(i.i)}" target="_blank"><img ${ALL?'':'loading="lazy"'} src="${esc(i.i)}" data-u="${esc(i.u)}" onerror="if(this.dataset.u&&this.src!==this.dataset.u)this.src=this.dataset.u" alt=""></a>`:'<div class="noimg">brak zdjęcia</div>')+
      (i.s==='DO_AKCEPTACJI'?`<label><input type="checkbox" ${sel.has(i.id)?'checked':''}> akceptuj</label>`:'')+
      `<b>${esc(i.n)}</b><small>id ${esc(i.id)} · ${esc(i.p)} · ${esc(i.c)}</small>`+
      (i.r?`<div class="reason">${esc(i.r)}</div>`:'')+(i.pg?`<a href="${esc(i.pg)}" target="_blank" rel="noreferrer">${esc(i.pg)}</a>`:'');
    const cb=c.querySelector('input');if(cb)cb.onchange=e=>{e.target.checked?sel.add(i.id):sel.delete(i.id);persist();};
    main.appendChild(c);
  });
  if(!ALL&&list.length>lim){const m=document.createElement('div');m.className='more';
    m.innerHTML=`<button>Pokaż kolejne (${list.length-lim} zostało)</button>`;m.querySelector('button').onclick=()=>{lim+=PAGE;render();};main.appendChild(m);}
}
document.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>{filt=b.dataset.f;lim=PAGE;
  document.querySelectorAll('[data-f]').forEach(x=>x.classList.toggle('on',x===b));render();});
document.getElementById('all').onclick=()=>{R.forEach(i=>sel.add(i.id));persist();};
document.getElementById('none').onclick=()=>{R.forEach(i=>sel.delete(i.id));persist();};
document.getElementById('save').onclick=()=>{
  const ids=R.filter(i=>sel.has(i.id)).map(i=>i.id);
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['﻿@id\n'+ids.join('\n')+'\n'],{type:'text/csv'}));
  a.download=D.file;document.body.appendChild(a);a.click();a.remove();
  alert(`Zapisano ${ids.length} zdjęć do pliku ${D.file} (folder Pobrane).\nTeraz w PowerShell: py zdjecia.py --zapisz`);
};
document.getElementById('pdf').onclick=()=>{
  if(!confirm('Do PDF trafią WSZYSTKIE zdjęcia z wybranej zakładki.\nW oknie drukowania wybierz „Zapisz jako PDF”.'))return;
  ALL=true;render();setTimeout(()=>{window.print();ALL=false;render();},300);
};
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
