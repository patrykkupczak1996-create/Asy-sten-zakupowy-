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
def read_source(path_or_url: str) -> bytes:
    if re.match(r"https?://", path_or_url):
        import requests
        resp = requests.get(path_or_url, timeout=300)
        resp.raise_for_status()
        return resp.content
    with open(path_or_url, "rb") as fh:
        return fh.read()


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


def sale_price(buy: float, producer: str, s: dict) -> tuple[float, float, float]:
    pct = margin_for(buy, producer, s["tiers"], s["producers"])
    net = max(buy * (1 + pct / 100), buy + s["min_profit"])
    gross = round_price(net * (1 + s["vat"] / 100), s["rounding"])
    return round(gross / (1 + s["vat"] / 100), 2), gross, pct


# ----------------------------------------------------------------------------- sklep
def read_shop(path: str, cfg) -> list[dict]:
    col_id = cfg.get("sklep", "kolumna_id", fallback="@id")
    col_code = cfg.get("sklep", "kolumna_kod", fallback="@code_producer")
    col_ean = cfg.get("sklep", "kolumna_ean", fallback="/sizes/size@code_producer")
    col_prod = cfg.get("sklep", "kolumna_producent", fallback="/producer@name")
    col_name = cfg.get("sklep", "kolumna_nazwa", fallback="/description/name[pol]")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        rows = list(csv.DictReader(fh, dialect=dialect))
    if rows and col_id not in rows[0]:
        sys.exit(f"W {path} brak kolumny {col_id}. Kolumny: {', '.join(rows[0])[:300]}")
    return [{"id": r.get(col_id, ""), "kod": r.get(col_code, ""), "ean": r.get(col_ean, ""),
             "producent": r.get(col_prod, ""), "nazwa": r.get(col_name, "")} for r in rows]


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- główne
def match_keys(records: list[dict], shop: list[dict], fields: dict, cfg, log=print) -> dict[str, list[str]]:
    """Którymi polami XML łączyć z produktami sklepu — wybrane po faktycznych trafieniach, nie po nazwie pola.

    Dla każdego pola XML liczy, ile wartości pokrywa się z EAN-ami i kodami producenta ze sklepu.
    Pola ustawione ręcznie w [xml] (ean / kod / indeks) mają pierwszeństwo."""
    shop_eans = {norm_ean(p["ean"]) for p in shop} - {""}
    shop_codes = {norm_code(p["kod"]) for p in shop} - {""}
    manual_ean = cfg.get("xml", "ean", fallback="").strip().lower()
    manual_code = [cfg.get("xml", k, fallback="").strip().lower() for k in ("kod", "indeks")]
    manual_code = [k for k in manual_code if k]
    if manual_ean or manual_code:
        return {"ean": [manual_ean] if manual_ean else [], "kod": manual_code}
    hits_ean, hits_code = Counter(), Counter()
    for r in records:
        for k, v in r.items():
            if k.startswith("_"):
                continue
            if norm_ean(v) in shop_eans:
                hits_ean[k] += 1
            if norm_code(v) in shop_codes:
                hits_code[k] += 1
    need = 1 if len(shop) < 300 else max(3, len(shop) // 500)  # ignoruj przypadkowe pojedyncze zbieżności
    keys = {"ean": [k for k, n in hits_ean.most_common(2) if n >= need],
            "kod": [k for k, n in hits_code.most_common(2) if n >= need]}
    log("Łączenie ze sklepem — EAN: " + (", ".join(f"{k} ({hits_ean[k]} trafień)" for k in keys["ean"]) or "brak")
        + "; kod producenta: " + (", ".join(f"{k} ({hits_code[k]} trafień)" for k in keys["kod"]) or "brak"))
    if not keys["ean"] and not keys["kod"]:
        sample_xml = {k: v for k, v in (records[0].items() if records else []) if not k.startswith("_")}
        sample_shop = [(p["kod"], p["ean"]) for p in shop[:5]]
        raise CenyError(
            "Żadne pole w XML nie pasuje do kodów ani EAN produktów sklepu — nie da się połączyć cen.\n"
            f"Przykład produktu z XML: {str(sample_xml)[:600]}\n"
            f"Przykład ze sklepu (kod producenta, EAN): {sample_shop}\n"
            "Sprawdź, czy produkty.csv to eksport z kolumnami kodu producenta i EAN, a XML to cennik Onninen.")
    return keys


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
    return {"tiers": parse_tiers(cfg.get("marza", "progi", fallback="")),
            "min_profit": to_float(cfg.get("marza", "minimalny_zysk_zl", fallback="1")),
            "vat": to_float(cfg.get("marza", "vat", fallback="23")),
            "rounding": cfg.get("marza", "zaokraglenie", fallback="0.01").strip(),
            "producers": {k.upper(): to_float(v) for k, v in cfg.items("producenci")}
            if cfg.has_section("producenci") else {}}


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


def przelicz(cfg, wymus: bool = False, log=print) -> dict:
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
    log(f"Pobieram cennik: {xml_path}")
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
    if not os.path.exists(shop_path):
        raise CenyError(f"Brak pliku produktów sklepu: {shop_path}")
    shop = read_shop(shop_path, cfg)
    keys = match_keys(records, shop, fields, cfg, log)
    by_ean, by_code = {}, {}
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
        for k in keys["ean"]:
            if norm_ean(r.get(k)):
                by_ean.setdefault(norm_ean(r[k]), []).append(r)
        for k in keys["kod"]:
            if norm_code(r.get(k)):
                by_code.setdefault(norm_code(r[k]), []).append(r)
    log(f"XML: {len(records)} pozycji, z ceną: {len(records) - bad_price}. Cena z pola: {fields['cena']}")

    previous = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8-sig", newline="") as fh:
            previous = {r["id"]: r for r in csv.DictReader(fh, delimiter=";")}

    report, changed, matched, warnings = [], [], 0, Counter()
    for p in shop:
        hits = by_ean.get(norm_ean(p["ean"])) or by_code.get(norm_code(p["kod"])) or []
        row = dict(p, zakup_netto="", marza_proc="", cena_netto="", cena_brutto="", poprzednia_brutto="", uwagi="")
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
            net, gross, pct = sale_price(buy, p["producent"], s)
            row.update(zakup_netto=f"{buy:.2f}", marza_proc=f"{pct:g}", cena_netto=f"{net:.2f}",
                       cena_brutto=f"{gross:.2f}")
            if old and old.get("cena_brutto"):
                jump = abs(gross / to_float(old["cena_brutto"]) - 1) * 100
                if jump > max_jump:
                    row["uwagi"] = f"duża zmiana ceny: {old['cena_brutto']} -> {gross:.2f} ({jump:.0f}%)"
                    warnings["duża zmiana"] += 1
            if not old or old.get("cena_brutto") != row["cena_brutto"]:
                changed.append(row)
        report.append(row)

    cols = ["id", "kod", "ean", "producent", "nazwa", "zakup_netto", "marza_proc", "cena_netto", "cena_brutto",
            "poprzednia_brutto", "uwagi"]
    report_path = os.path.join(out_dir, "ceny_raport.csv")
    write_csv(report_path, report, cols)
    share = matched / len(shop) * 100 if shop else 0
    prev_matched = sum(1 for r in previous.values() if r.get("cena_brutto"))
    log(f"Sklep: {len(shop)} produktów, z ceną z Onninen: {matched} ({share:.0f}%), "
        f"zmienione od ostatniego razu: {len(changed)}. " + ", ".join(f"{k}: {v}" for k, v in warnings.items()))
    summary = {"produkty": len(shop), "dopasowane": matched, "zmiany": len(changed), "uwagi": dict(warnings),
               "raport": report_path, "import": "", "zmienione": changed, "czas": time.strftime("%Y-%m-%d %H:%M")}

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
