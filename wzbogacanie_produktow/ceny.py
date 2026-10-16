"""Ceny sprzedaży z aktualnego cennika Onninen (XML) — z marżą wg Twoich progów i ochroną przed sprzedażą
poniżej ceny zakupu.

Co robi:
  1. Wczytuje XML z Onninen (plik albo adres URL) i sam rozpoznaje, gdzie jest kod, EAN i cena zakupu.
  2. Łączy go z produktami sklepu (produkty.csv: @id, kod producenta, EAN) — najpierw po EAN, potem po kodzie.
  3. Liczy cenę: zakup netto + marża (progi, wyjątki dla producentów) + minimalny zysk w zł + VAT
     + zaokrąglenie. Zaokrąglenie nigdy nie schodzi poniżej ceny z marżą.
  4. Zapisuje ceny_do_importu.csv — tylko produkty, którym cena się zmieniła od ostatniego przebiegu —
     oraz ceny_raport.csv (wszystko, z ostrzeżeniami).
  5. Bezpiecznik: gdy XML jest uszkodzony albo dopasuje się dużo mniej produktów niż ostatnio,
     nie robi pliku importu (zła cena na całym sklepie to gorszy problem niż ceny sprzed kilku godzin).

Ustawienia (marża, progi, VAT, zaokrąglenie, nazwy kolumn) — w ceny_ustawienia.ini; przy pierwszym
uruchomieniu skrypt tworzy go z przykładu.

Uruchomienie:
  py ceny.py --pokaz onninen.xml     # pokaż, co skrypt rozpoznał w XML (pierwsze produkty)
  py ceny.py                         # przelicz ceny wg ceny_ustawienia.ini
"""
from __future__ import annotations

import argparse
import configparser
import csv
import io
import math
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "ceny_ustawienia.ini")
SETTINGS_EXAMPLE = os.path.join(HERE, "ceny_ustawienia.przyklad.ini")
STATE = "ceny_ostatnie.csv"          # ceny z poprzedniego przebiegu (do wykrywania zmian)

# Nazwy pól w XML hurtowni — rozpoznawane automatycznie (małe litery, bez polskich znaków).
GUESS = {
    "ean": ["ean", "gtin", "kod_kreskowy", "kodkreskowy", "barcode", "ean13"],
    "kod": ["kod_producenta", "kodproducenta", "indeks_producenta", "symbol_producenta", "nr_katalogowy",
            "numer_katalogowy", "manufacturer_code", "mpn", "katalog", "kod_katalogowy"],
    "indeks": ["indeks", "index", "symbol", "sku", "kod", "code", "id", "nr", "numer"],
    "cena": ["cena_netto_po_rabacie", "cena_zakupu", "twoja_cena", "cena_klienta", "cena_netto", "netto",
             "price_net", "net_price", "cena", "price"],
    "nazwa": ["nazwa", "name", "opis", "description"],
}


# ----------------------------------------------------------------------------- ustawienia
def load_settings() -> configparser.ConfigParser:
    if not os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS_EXAMPLE, SETTINGS)
        print(f"Utworzyłem {os.path.basename(SETTINGS)} z przykładu — ustaw w nim marże i ścieżkę do XML.")
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cfg.optionxform = str  # nazwy producentów z wielkimi literami
    cfg.read(SETTINGS, encoding="utf-8-sig")
    return cfg


def parse_tiers(text: str) -> list[tuple[float, float]]:
    """„0:40, 50:30, 200:25” -> [(0, 40), (50, 30), (200, 25)] — od ceny zakupu netto: marża w %."""
    tiers = []
    for part in re.split(r"[,\n]+", text or ""):
        if part.strip():
            start, pct = part.split(":")
            tiers.append((to_float(start), to_float(pct)))
    if not tiers:
        sys.exit("W ceny_ustawienia.ini brak progów marży ([marza] progi = ...).")
    return sorted(tiers)


def to_float(text) -> float:
    s = str(text).strip().replace("\xa0", "").replace(" ", "").replace("zł", "").replace("PLN", "")
    if "," in s and "." in s:
        s = s.replace(".", "") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    return float(s.replace(",", "."))


def norm_code(text: str) -> str:
    return re.sub(r"[\s\-./]", "", str(text or "")).upper()


def norm_ean(text: str) -> str:
    digits = re.sub(r"\D", "", str(text or ""))
    return digits.lstrip("0") if 8 <= len(digits) <= 14 else ""


def plain_key(tag: str) -> str:
    tag = tag.split("}")[-1].lower()
    return tag.translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


# ----------------------------------------------------------------------------- XML
SOURCE_INFO: dict[str, str] = {}  # skąd i kiedy pobrano ostatni cennik (feed nie zawiera własnej daty)


def read_source(path_or_url: str) -> bytes:
    if re.match(r"https?://", path_or_url):
        import requests
        resp = requests.get(path_or_url, timeout=300)
        resp.raise_for_status()
        SOURCE_INFO["pobrano"] = time.strftime("%Y-%m-%d %H:%M")
        SOURCE_INFO["serwer"] = resp.headers.get("Last-Modified", "")
        return resp.content
    with open(path_or_url, "rb") as fh:
        data = fh.read()
    SOURCE_INFO["pobrano"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path_or_url)))
    SOURCE_INFO["serwer"] = ""
    return data


def find_record_tag(data: bytes) -> str:
    """Element, który powtarza się najczęściej i ma pola (dzieci albo atrybuty) — to jeden produkt."""
    counts, fields = Counter(), Counter()
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        n = len(el) + len(el.attrib)
        if n >= 2:
            counts[el.tag] += 1
            fields[el.tag] += n
        if sum(counts.values()) > 5000:
            break
    if not counts:
        sys.exit("Nie rozpoznałem produktów w XML — wyślij początek pliku, dopiszę obsługę.")
    return max(counts, key=lambda t: (counts[t] >= 3, counts[t] * fields[t] / counts[t]))


def iter_records(data: bytes, record_tag: str):
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag != record_tag:
            continue
        rec = {plain_key(k): v for k, v in el.attrib.items()}
        for child in el.iter():
            if child is el:
                continue
            key = plain_key(child.tag)
            # <a name="EAN">590…</a> — pola opisane atrybutem name/key (np. feed Onninen) bierzemy po tej nazwie
            label = child.attrib.get("name") or child.attrib.get("key")
            if label and child.text and child.text.strip():
                rec.setdefault(plain_key(label).replace(" ", "_"), child.text.strip())
                continue
            if child.text and child.text.strip() and key not in rec:
                rec[key] = child.text.strip()
            for ak, av in child.attrib.items():
                rec.setdefault(f"{key}@{plain_key(ak)}", av)
        yield rec
        el.clear()


def pick_fields(sample: list[dict], cfg) -> dict[str, str]:
    """Które pole XML to EAN / kod / cena — z ustawień albo zgadnięte po nazwie."""
    keys = Counter(k for r in sample for k in r)
    chosen = {}
    for role, guesses in GUESS.items():
        manual = cfg.get("xml", role, fallback="").strip().lower()
        if manual:
            chosen[role] = manual
            continue
        for g in guesses:
            hit = next((k for k in keys if k == g), None) or next((k for k in keys if g in k), None)
            if hit and hit not in chosen.values():
                chosen[role] = hit
                break
    return chosen


# ----------------------------------------------------------------------------- cena
def margin_for(buy: float, producer: str, tiers, producers: dict[str, float]) -> float:
    if producer.upper() in producers:
        return producers[producer.upper()]
    pct = tiers[0][1]
    for start, p in tiers:
        if buy >= start:
            pct = p
    return pct


def round_price(gross: float, mode: str) -> float:
    """Zaokrąglenie w górę (nigdy w dół — żeby nie zjeść marży): 0.01, 0.10, 1, albo 0.99 (końcówka ,99)."""
    if mode == "0.99":
        return math.ceil(gross + 0.01) - 0.01
    step = to_float(mode or "0.01")
    return round(math.ceil(round(gross / step, 6)) * step, 2)


PIECE_UNITS = {"", "szt", "szt.", "sztuka", "pcs", "kpl", "kpl.", "komplet", "op", "op.", "opak", "para", "zestaw"}
CATALOG_KEYS = ("price_catalog", "cena_katalogowa", "katalogowa", "price_list", "srp")


def sale_price(buy: float, producer: str, s: dict, unit: str = "", catalog: float = 0.0
               ) -> tuple[float, float, float, str]:
    """Cena sprzedaży: zakup + marża (progi / producent), minimalny zysk, VAT, zaokrąglenie w górę.

    Minimalny zysk w zł liczony tylko dla sztuk (nie dla metrów, kg…), gdy tak ustawiono.
    Opcjonalnie cena brutto nie wyższa niż katalogowa Onninen — ale nigdy poniżej zakupu + minimalny zysk."""
    pct = margin_for(buy, producer, s["tiers"], s["producers"])
    per_piece = str(unit).strip().lower() in PIECE_UNITS
    min_profit = s["min_profit"] if (per_piece or not s.get("min_profit_pieces_only", True)) else 0.0
    vat = 1 + s["vat"] / 100
    net = max(buy * (1 + pct / 100), buy + min_profit)
    gross = round_price(net * vat, s["rounding"])
    note = ""
    if s.get("cap_catalog") and catalog > 0:
        cap = math.floor(catalog * vat * 100 + 1e-6) / 100
        floor = round_price((buy + min_profit) * vat, "0.01")
        if gross > cap:
            gross = max(cap, floor)
            note = "obcięte do katalogowej Onninen" if gross == cap else "katalogowa poniżej min. zysku"
    return round(gross / vat, 2), gross, pct, note


# ----------------------------------------------------------------------------- sklep
SHOP_COLUMNS = {  # rola -> możliwe nagłówki (eksport IdoSell, arkusz Google, własne pliki)
    "id": ["@id", "id", "id towaru", "id produktu", "identyfikator"],
    "kod": ["@code_producer", "kod", "kod producenta", "kod_producenta", "symbol producenta", "nr katalogowy"],
    "ean": ["/sizes/size@code_producer", "ean", "kod ean", "gtin", "kod kreskowy"],
    "producent": ["/producer@name", "producent", "marka"],
    "nazwa": ["/description/name[pol]", "nazwa", "nazwa produktu", "name"],
    # Obecna cena w sklepie (opcjonalnie) — do kontroli, czy nie sprzedajemy poniżej zakupu
    "sklep_brutto": ["/price@gross", "/sizes/size/price@gross", "cena detaliczna brutto", "cena brutto",
                     "detaliczna brutto", "cena sprzedaży brutto", "cena"],
    "sklep_netto": ["/price@net", "/sizes/size/price@net", "cena detaliczna netto", "cena netto",
                    "detaliczna netto", "cena sprzedaży netto"],
}


def google_sheet_csv_url(url: str) -> str:
    """Link do arkusza Google (…/edit#gid=…) -> link pobrania CSV tej karty."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]+)", url)
    if not m or "export?" in url:
        return url
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")


def read_shop(path: str, cfg) -> list[dict]:
    """Produkty sklepu z pliku CSV albo linku do arkusza Google; kolumny rozpoznawane po nagłówkach."""
    if re.match(r"https?://", path):
        try:
            data = read_source(google_sheet_csv_url(path))
        except Exception as exc:
            raise CenyError(f"Nie udało się pobrać arkusza z produktami: {exc}") from exc
        text = data.decode("utf-8-sig", errors="replace")
        if text.lstrip().lower().startswith(("<!doctype", "<html")):
            raise CenyError("Arkusz Google nie jest udostępniony do odczytu. W arkuszu: Udostępnij → "
                            "„Każda osoba mająca link” (Przeglądający) — albo pobierz go jako CSV i wskaż plik.")
    else:
        if not os.path.exists(path):
            raise CenyError(f"Brak pliku produktów sklepu: {path}")
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            text = fh.read()
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        first = text.split("\n", 1)[0]
        dialect = type("d", (csv.excel,), {"delimiter": max(",;\t", key=first.count)})
    # krótsze/dłuższe wiersze (puste komórki na końcu, przecinki w opisach) — zawsze tekst, nigdy None
    rows = [{k: (v if isinstance(v, str) else "") for k, v in r.items() if k is not None}
            for r in csv.DictReader(io.StringIO(text), dialect=dialect, restval="")]
    if not rows:
        raise CenyError(f"Plik produktów jest pusty: {path}")
    headers = {h.strip().lower(): h for h in rows[0] if h}
    cols = {}
    for role, names in SHOP_COLUMNS.items():
        manual = cfg.get("sklep", f"kolumna_{role}", fallback="").strip()
        cols[role] = manual or next((headers[n] for n in names if n in headers), "")
    if not cols["id"]:
        raise CenyError(f"W produktach brak kolumny z ID towaru (np. @id). Kolumny: {', '.join(rows[0])[:300]}")
    return [{"id": r.get(cols["id"], ""), "kod": r.get(cols["kod"], ""), "ean": r.get(cols["ean"], ""),
             "producent": r.get(cols["producent"], ""), "nazwa": r.get(cols["nazwa"], ""), "_raw": r,
             "_idcol": cols["id"], "_sklep_brutto": r.get(cols["sklep_brutto"], "") if cols["sklep_brutto"] else "",
             "_sklep_netto": r.get(cols["sklep_netto"], "") if cols["sklep_netto"] else ""}
            for r in rows if r.get(cols["id"], "").strip()]


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- główne
def nkey(value) -> str:
    """Klucz do łączenia: kod bez spacji/kresek, wielkie litery; same cyfry (EAN) bez zer na początku."""
    c = norm_code(value)
    if c.isdigit():
        c = c.lstrip("0")
    return c if len(c) >= 3 else ""


def match_pairs(records: list[dict], shop: list[dict], cfg, log=print) -> list[tuple[str, str, int]]:
    """Pary (pole XML, kolumna sklepu), po których produkty faktycznie się łączą — od najlepszej.

    Sprawdza wszystkie pola XML i wszystkie kolumny eksportu sklepu (EAN, kod producenta, kod zewnętrzny,
    symbol hurtowni…), bo nie wiadomo z góry, gdzie sklep trzyma kod z Onninen."""
    manual = cfg.get("xml", "laczenie", fallback="").strip()
    if manual:  # np. laczenie = ident@katalog=@code_producer, ident@gtin=/sizes/size@code_producer
        pairs = [tuple(x.strip() for x in part.split("=", 1)) for part in manual.split(",") if "=" in part]
        return [(x.lower(), col, 0) for x, col in pairs]
    def unique_sets(rows) -> dict[str, set]:
        """Wartości pól, które identyfikują produkt (prawie każda inna) — bez nazw producentów, kategorii itp."""
        vals: dict[str, set] = {}
        filled = Counter()
        for row in rows:
            for k, v in row.items():
                if k and not k.startswith("_") and v and len(str(v)) <= 40:
                    key = nkey(v)
                    if key:
                        vals.setdefault(k, set()).add(key)
                        filled[k] += 1
        return {k: v for k, v in vals.items() if len(v) >= 0.8 * filled[k]}

    # Nazwy i kategorie nie nadają się do łączenia — ten sam opis bywa u różnych producentów.
    text_like = ("name", "nazwa", "desc", "opis", "cat", "kategori", "marka")
    xml_vals = {k: v for k, v in unique_sets(records).items() if not any(t in k for t in text_like)}
    shop_vals = {k: v for k, v in unique_sets(p["_raw"] for p in shop).items()
                 if not any(t in k.lower() for t in text_like)}
    scored = sorted(((len(xv & sv), xk, sc) for xk, xv in xml_vals.items() for sc, sv in shop_vals.items()),
                    reverse=True)
    need = 1 if len(shop) < 300 else max(3, len(shop) // 500)  # pomijamy przypadkowe zbieżności
    pairs, used = [], set()
    for n, xk, sc in scored:
        if n < need or len(pairs) >= 4:
            break
        if (xk, sc) not in used and sc != (shop[0].get("_idcol") if shop else "@id"):
            pairs.append((xk, sc, n))
            used.add((xk, sc))
    log("Łączenie ze sklepem: " + ("; ".join(f"XML „{xk}” = sklep „{sc}” ({n} wspólnych)" for xk, sc, n in pairs)
                                    or "nic nie pasuje"))
    if not pairs:
        sample_xml = {k: v for k, v in (records[0].items() if records else []) if not k.startswith("_")}
        sample_shop = {k: v for k, v in (shop[0]["_raw"].items() if shop else []) if v and len(v) <= 40}
        raise CenyError(
            "Żadne pole w XML nie pasuje do żadnej kolumny produktów sklepu — nie da się połączyć cen.\n"
            f"Przykład produktu z XML: {str(sample_xml)[:600]}\n"
            f"Przykład produktu ze sklepu: {str(sample_shop)[:600]}")
    return pairs


FEED_STALE_HOURS = 36  # feed odświeżany 2× dziennie — dłużej bez zmian = coś jest nie tak


def check_feed_freshness(data: bytes, out_dir: str, log=print) -> None:
    """Ostrzega, gdy pod tym samym linkiem od dawna leży ten sam plik (Onninen przestał go odświeżać)."""
    import hashlib
    import json
    digest = hashlib.sha1(data).hexdigest()
    path = os.path.join(out_dir, "ceny_feed_stan.json")
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    now = time.time()
    if state.get("sha1") != digest:
        if state.get("sha1"):
            log("Cennik zmienił się od poprzedniego pobrania — Onninen go odświeżył.")
        state = {"sha1": digest, "od": now}
    else:
        hours = (now - state.get("od", now)) / 3600
        if hours > FEED_STALE_HOURS:
            log(f"UWAGA: cennik Onninen jest identyczny od {hours:.0f} godzin — mógł przestać się odświeżać "
                "(np. wygasł token w linku). Sprawdź link albo zapytaj opiekuna w Onninen.")
        else:
            log(f"Cennik bez zmian od {hours:.0f} h (to normalne między odświeżeniami Onninen).")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def shop_net_price(p: dict, vat: float) -> float:
    """Obecna cena netto produktu w sklepie (z kolumny netto albo brutto / VAT); 0 = brak danych."""
    for key, divide in (("_sklep_netto", 1.0), ("_sklep_brutto", 1 + vat / 100)):
        try:
            value = to_float(p.get(key, "") or "")
        except ValueError:
            continue
        if value > 0:
            return value / divide
    return 0.0


def zacznij_od_nowa(cfg) -> str:
    """Kasuje pamięć poprzedniego przeliczenia (ceny_ostatnie.csv) — kopia zostaje obok. Zwraca ścieżkę kopii."""
    out_dir = cfg.get("pliki", "folder_wynikow", fallback="").strip() or HERE
    path = os.path.join(out_dir, STATE)
    if not os.path.exists(path):
        return ""
    backup = os.path.join(out_dir, f"ceny_ostatnie_kopia_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    os.replace(path, backup)
    return backup


class CenyError(Exception):
    """Błąd, przy którym nie wolno zrobić pliku importu (komunikat dla użytkownika)."""


def show(path: str, cfg, log=print) -> dict[str, str]:
    data = read_source(path)
    tag = find_record_tag(data)
    sample = [r for _, r in zip(range(200), iter_records(data, tag))]
    fields = pick_fields(sample, cfg)
    log(f"Produkt w XML: <{tag.split('}')[-1]}>. Pola w pierwszym produkcie:")
    for k, v in sample[0].items():
        log(f"   {k} = {v[:60]}")
    log("Rozpoznane: " + (", ".join(f"{r} -> {k}" for r, k in fields.items()) or "nic"))
    for r in sample[:5]:
        log("    " + str({role: r.get(k, "") for role, k in fields.items()}))
    if "cena" not in fields:
        log("Nie rozpoznałem ceny — wpisz nazwę pola w ustawieniach (XML: pole ceny).")
    return fields


def settings_from(cfg) -> dict:
    if cfg.get("marza", "tryb", fallback="progi").strip() == "jedna":
        # Jedna marża dla wszystkich — bez progów i bez wyjątków dla producentów.
        flat = to_float(cfg.get("marza", "jedna_marza", fallback="25"))
        return {"tiers": [(0.0, flat)],
                "min_profit": to_float(cfg.get("marza", "minimalny_zysk_zl", fallback="1")),
                "vat": to_float(cfg.get("marza", "vat", fallback="23")),
                "rounding": cfg.get("marza", "zaokraglenie", fallback="0.01").strip(),
                "producers": {}, **extra_rules(cfg)}
    return {"tiers": parse_tiers(cfg.get("marza", "progi", fallback="")),
            "min_profit": to_float(cfg.get("marza", "minimalny_zysk_zl", fallback="1")),
            "vat": to_float(cfg.get("marza", "vat", fallback="23")),
            "rounding": cfg.get("marza", "zaokraglenie", fallback="0.01").strip(),
            "producers": {k.upper(): to_float(v) for k, v in cfg.items("producenci")}
            if cfg.has_section("producenci") else {}, **extra_rules(cfg)}


def extra_rules(cfg) -> dict:
    return {"min_profit_pieces_only": cfg.getboolean("marza", "minimalny_zysk_tylko_sztuki", fallback=True),
            "cap_catalog": cfg.getboolean("marza", "nie_drozej_niz_katalogowa", fallback=False)}


def upload(path: str, cfg, log=print) -> None:
    """Wysyła plik na FTP (sekcja [ftp]) — stamtąd IdoSell może go pobierać sam."""
    host = cfg.get("ftp", "host", fallback="").strip()
    if not host:
        return
    import ftplib
    ftp_cls = ftplib.FTP_TLS if cfg.getboolean("ftp", "tls", fallback=True) else ftplib.FTP
    with ftp_cls(host, timeout=120) as ftp:
        ftp.login(cfg.get("ftp", "uzytkownik", fallback=""), cfg.get("ftp", "haslo", fallback=""))
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        folder = cfg.get("ftp", "folder", fallback="").strip()
        if folder:
            ftp.cwd(folder)
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            ftp.storbinary(f"STOR {name}.tmp", fh)
        try:
            ftp.delete(name)
        except ftplib.error_perm:
            pass
        ftp.rename(f"{name}.tmp", name)
    log(f"Wysłano {os.path.basename(path)} na FTP {host}.")


def przelicz(cfg, wymus: bool = False, log=print, wyslij: bool | None = None) -> dict:
    """Pobiera XML, liczy ceny, zapisuje pliki. Zwraca podsumowanie; CenyError = bezpiecznik/zły plik."""
    s = settings_from(cfg)
    xml_path = cfg.get("pliki", "xml", fallback="onninen.xml").strip()
    shop_path = cfg.get("pliki", "produkty", fallback="produkty.csv").strip()
    out_dir = cfg.get("pliki", "folder_wynikow", fallback="").strip() or HERE
    os.makedirs(out_dir, exist_ok=True)
    max_jump = to_float(cfg.get("bezpiecznik", "max_zmiana_proc", fallback="40"))
    min_match = to_float(cfg.get("bezpiecznik", "min_dopasowanych_proc", fallback="80"))
    state_path = os.path.join(out_dir, STATE)

    started = time.time()
    log(f"Pobieram cennik: {re.sub(r'(token|key|klucz)=[^&]+', r'\1=***', xml_path)}")
    try:
        data = read_source(xml_path)
    except Exception as exc:
        raise CenyError(f"Nie udało się pobrać cennika: {exc}") from exc
    try:
        tag = find_record_tag(data)
        records = list(iter_records(data, tag))
    except ET.ParseError as exc:
        raise CenyError(f"Cennik XML jest uszkodzony albo niepełny: {exc}") from exc
    fields = pick_fields(records[:200], cfg)
    if "cena" not in fields:
        raise CenyError("Nie rozpoznałem ceny w XML — użyj „Sprawdź XML” i wpisz pole ceny w ustawieniach.")
    shop = read_shop(shop_path, cfg)
    log(f"Produkty sklepu: {len(shop)} z {shop_path if len(shop_path) < 80 else shop_path[:77] + '…'}")
    no_code = sum(1 for p in shop if not p["kod"].strip())
    no_ean = sum(1 for p in shop if not p["ean"].strip())
    no_both = sum(1 for p in shop if not p["kod"].strip() and not p["ean"].strip())
    log(f"W bazie sklepu: bez kodu producenta {no_code}, bez EAN {no_ean}, bez obu {no_both}"
        + (" — tych produktów nie da się połączyć z cennikiem po kodzie/EAN." if no_both else "."))
    if shop and no_code == len(shop) and no_ean == len(shop):
        log("UWAGA: nie znalazłem kolumn z kodem i EAN w pliku produktów. Kolumny w pliku: "
            + ", ".join(k for k in shop[0]["_raw"] if k)[:400])
    pairs = match_pairs(records, shop, cfg, log)
    index: dict[str, dict[str, list]] = {xk: {} for xk, _, _ in pairs}
    bad_price = 0
    for r in records:
        try:
            r["_cena"] = to_float(r.get(fields["cena"], ""))
        except ValueError:
            bad_price += 1
            continue
        if r["_cena"] <= 0:
            bad_price += 1
            continue
        for xk in index:
            key = nkey(r.get(xk, ""))
            if key:
                index[xk].setdefault(key, []).append(r)
    log(f"XML: {len(records)} pozycji, z ceną: {len(records) - bad_price}. Cena z pola: {fields['cena']}")
    check_feed_freshness(data, out_dir, log)
    stamp = SOURCE_INFO.get("pobrano", "")
    log(f"Ceny z cennika z dnia: {stamp}" + (f" (wg serwera Onninen: {SOURCE_INFO['serwer']})"
                                             if SOURCE_INFO.get("serwer") else
                                             " — feed nie podaje własnej daty, to chwila pobrania/zapisu pliku"))

    previous = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8-sig", newline="") as fh:
            previous = {r["id"]: r for r in csv.DictReader(fh, delimiter=";")}

    report, changed, matched, warnings = [], [], 0, Counter()
    for p in shop:
        hits = []
        for xk, sc, _ in pairs:
            key = nkey(p["_raw"].get(sc, ""))
            hits = index[xk].get(key, []) if key else []
            if hits:
                break
        row = dict(p, zakup_netto="", marza_proc="", cena_netto="", cena_brutto="", poprzednia_brutto="", uwagi="",
                   sklep_brutto="", marza_teraz_proc="", jednostka="", katalog_brutto="")
        old = previous.get(p["id"])
        if old:
            row["poprzednia_brutto"] = old.get("cena_brutto", "")
        if not hits:
            row["uwagi"] = "brak w cenniku Onninen"
            warnings["brak w cenniku"] += 1
        elif len({round(h["_cena"], 2) for h in hits}) > 1:
            row["uwagi"] = "kilka pozycji w cenniku z różnymi cenami — sprawdź ręcznie"
            warnings["niejednoznaczne"] += 1
        else:
            matched += 1
            buy = hits[0]["_cena"]
            unit = hits[0].get("unit", "") or hits[0].get("jednostka", "")
            catalog = 0.0
            for key in CATALOG_KEYS:
                try:
                    catalog = to_float(hits[0].get(key, "") or "0")
                except ValueError:
                    catalog = 0.0
                if catalog:
                    break
            producer = p["producent"] or hits[0].get("marka", "")
            net, gross, pct, note = sale_price(buy, producer, s, unit, catalog)
            row.update(zakup_netto=f"{buy:.2f}", marza_proc=f"{pct:g}", cena_netto=f"{net:.2f}",
                       cena_brutto=f"{gross:.2f}", jednostka=unit,
                       katalog_brutto=f"{catalog * (1 + s['vat'] / 100):.2f}" if catalog else "")
            if note:
                row["uwagi"] = note
                warnings["obcięte do katalogowej"] += 1
            if old and old.get("cena_brutto"):
                jump = abs(gross / to_float(old["cena_brutto"]) - 1) * 100
                if jump > max_jump:
                    row["uwagi"] = f"duża zmiana ceny: {old['cena_brutto']} -> {gross:.2f} ({jump:.0f}%)"
                    warnings["duża zmiana"] += 1
            shop_net = shop_net_price(p, s["vat"])
            if shop_net:
                row["sklep_brutto"] = f"{shop_net * (1 + s['vat'] / 100):.2f}"
                now = (shop_net / buy - 1) * 100
                row["marza_teraz_proc"] = f"{now:.1f}"
                min_profit = s["min_profit"] if (str(unit).strip().lower() in PIECE_UNITS
                                                 or not s.get("min_profit_pieces_only", True)) else 0.0
                if shop_net < buy:
                    row["uwagi"] = (row["uwagi"] + "; " if row["uwagi"] else "") + \
                        f"PONIŻEJ ZAKUPU: sklep {shop_net:.2f} netto < zakup {buy:.2f}"
                    warnings["poniżej zakupu"] += 1
                elif shop_net - buy < min_profit or now < s["tiers"][0][1] / 3:
                    row["uwagi"] = (row["uwagi"] + "; " if row["uwagi"] else "") + f"niska marża w sklepie ({now:.0f}%)"
                    warnings["niska marża"] += 1
            if not old or old.get("cena_brutto") != row["cena_brutto"]:
                changed.append(row)
        report.append(row)

    cols = ["id", "kod", "ean", "producent", "nazwa", "jednostka", "zakup_netto", "katalog_brutto", "sklep_brutto",
            "marza_teraz_proc",
            "marza_proc", "cena_netto", "cena_brutto", "poprzednia_brutto", "uwagi"]
    report_path = os.path.join(out_dir, "ceny_raport.csv")
    write_csv(report_path, report, cols)
    with_shop = [r for r in report if r["marza_teraz_proc"]]
    if with_shop:
        risky = sorted((r for r in with_shop if "PONIŻEJ" in r["uwagi"] or "niska marża" in r["uwagi"]),
                       key=lambda r: float(r["marza_teraz_proc"]))
        write_csv(os.path.join(out_dir, "kontrola_marzy.csv"), risky, cols)
        loss = sum((to_float(r["zakup_netto"]) - to_float(r["sklep_brutto"]) / (1 + s["vat"] / 100))
                   for r in risky if "PONIŻEJ" in r["uwagi"])
        log(f"KONTROLA MARŻY (ceny obecnie w sklepie vs zakup z feedu): sprawdzono {len(with_shop)}, "
            f"poniżej zakupu {warnings['poniżej zakupu']} (strata ok. {loss:.2f} zł netto na 1 szt. każdego), "
            f"niska marża {warnings['niska marża']}. Lista od najgorszych: kontrola_marzy.csv")
    else:
        log("Kontrola marży pominięta: w pliku produktów nie ma kolumny z obecną ceną sklepu "
            "(np. „/price@gross” albo „Cena detaliczna brutto”).")
    share = matched / len(shop) * 100 if shop else 0
    prev_matched = sum(1 for r in previous.values() if r.get("cena_brutto"))
    log(f"Sklep: {len(shop)} produktów, z ceną z Onninen: {matched} ({share:.0f}%), "
        f"zmienione od ostatniego razu: {len(changed)}. " + ", ".join(f"{k}: {v}" for k, v in warnings.items()))
    summary = {"produkty": len(shop), "dopasowane": matched, "zmiany": len(changed), "uwagi": dict(warnings),
               "raport": report_path, "import": "", "zmienione": changed, "wszystkie": report, "cennik_z": SOURCE_INFO.get("pobrano", ""), "czas": time.strftime("%Y-%m-%d %H:%M")}

    fuse = []
    if bad_price > len(records) * 0.2:
        fuse.append(f"{bad_price} pozycji XML bez poprawnej ceny")
    if prev_matched and matched < prev_matched * min_match / 100:
        fuse.append(f"dopasowanych {matched}, a ostatnio {prev_matched}")
    if fuse and not wymus:
        raise CenyError("BEZPIECZNIK: " + "; ".join(fuse) + ". Nie zrobiłem pliku importu (sprawdź cennik). "
                        "Raport: ceny_raport.csv.")

    out_cols = [c.strip() for c in cfg.get("wyjscie", "kolumny",
                                           fallback="@id;cena_netto;cena_brutto").split(";") if c.strip()]
    rename = {"@id": "id"}
    import_path = os.path.join(out_dir, "ceny_do_importu.csv")
    write_csv(import_path, [{c: r[rename.get(c, c)] for c in out_cols} for r in changed], out_cols)
    # Pełny cennik sklepu (wszystkie produkty z ceną) — do stałego pobierania przez IdoSell z URL/FTP.
    full_path = os.path.join(out_dir, "ceny_wszystkie.csv")
    write_csv(full_path, [{c: r[rename.get(c, c)] for c in out_cols} for r in report if r["cena_brutto"]], out_cols)
    priced = {r["id"] for r in report if r["cena_brutto"]}
    write_csv(state_path, [r for r in report if r["cena_brutto"]] +
              [previous[i] for i in previous if i not in priced], cols)
    summary["import"] = import_path
    try:
        upload(full_path, cfg, log)
    except Exception as exc:  # błąd FTP nie unieważnia przeliczenia
        log(f"UWAGA: nie udało się wysłać na FTP: {exc}")
        summary["ftp_blad"] = str(exc)
    if wyslij is None:  # automat: wysyła sam tylko po potwierdzonej próbie i z włączoną opcją
        wyslij = (cfg.getboolean("idosell", "wysylaj_automatycznie", fallback=False)
                  and cfg.getboolean("idosell", "potwierdzone", fallback=False))
    if wyslij and changed:
        import idosell_api
        try:
            summary["api"] = idosell_api.push_prices(cfg, changed, log, out_dir)
        except idosell_api.ApiError as exc:
            log(f"UWAGA: nie udało się wysłać cen do IdoSell: {exc}")
            summary["api_blad"] = str(exc)
    log(f"Gotowe w {time.time() - started:.0f} s: ceny_do_importu.csv ({len(changed)} zmian), "
        "ceny_wszystkie.csv, ceny_raport.csv.")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Ceny sprzedaży z cennika Onninen (XML) z marżą.")
    ap.add_argument("--pokaz", metavar="XML", help="pokaż, co skrypt rozpoznał w pliku XML, i zakończ")
    ap.add_argument("--wymus", action="store_true", help="zrób plik importu mimo zadziałania bezpiecznika")
    args = ap.parse_args()
    os.chdir(HERE)
    cfg = load_settings()
    if args.pokaz:
        show(args.pokaz, cfg)
        return
    try:
        przelicz(cfg, args.wymus)
    except CenyError as exc:
        print(exc)
        sys.exit(2)


if __name__ == "__main__":
    main()
