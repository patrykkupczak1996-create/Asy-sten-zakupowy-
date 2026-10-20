"""Plik importu towarów do IdoSell (CSV wg „Import, aktualizacja i synchronizacja towarów”) z arkusza produktów.

Z arkusza bierze: Nazwa, Producent, Kod, EAN, Opis nowy (albo Opis), Link do zdjęcia.
Z feedu Onninen (opcjonalnie): jednostkę, kategorię i cenę katalogową brutto.

Najprościej (arkusz = najnowszy „opisy_wszystkie_gotowe_arkusz*.csv” z Pobranych,
feed = link z programu Ceny):
  py idosell_import.py --tylko-z-feedem --limit 50
  py idosell_import.py
Albo wprost:  py idosell_import.py arkusz.csv --feed onninen.xml --widoczne

Wynik: idosell_import_towarow.csv — w panelu: Towary → Dodawanie nowych towarów → Dodaj źródło danych.
Towary są domyślnie UKRYTE przed klientami (/visibility/site@visible = n); --widoczne je pokazuje.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys

import ceny

HERE = os.path.dirname(os.path.abspath(__file__))
# jednostki z feedu -> /unit@id w IdoSell (0 szt., 1 para, 2 komplet, 3 opakowanie, 5 kg, 6 m2, 8 metr, 10 litr)
UNIT_IDS = {"szt": 0, "para": 1, "kpl": 2, "opk": 3, "op": 3, "kg": 5, "m2": 6, "m": 8, "mb": 8, "l": 10}
COLUMNS = ["@type", "@vat", "@currency", "@code_producer", "/producer@name", "/category@name[pol]",
           "/description/name[pol]", "/description/long_desc[pol]", "/sizes@group_id",
           "/sizes/size@code_producer", "@producer_code_standard", "/unit@id", "/price_retail_base@gross",
           "/visibility/site@visible", "/images/large/image@url"]


def default_sheet() -> str:
    """Najnowszy arkusz pobrany z Google do folderu Pobrane (albo arkusz_opisy.csv obok programu)."""
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    found = [os.path.join(downloads, f) for f in (os.listdir(downloads) if os.path.isdir(downloads) else [])
             if f.lower().startswith("opisy_wszystkie_gotowe_arkusz") and f.lower().endswith(".csv")]
    if found:
        return max(found, key=os.path.getmtime)
    local = os.path.join(HERE, "arkusz_opisy.csv")
    if os.path.exists(local):
        return local
    sys.exit("Nie znalazłem arkusza. W Google: Plik → Pobierz → CSV (trafi do Pobranych) "
             "albo podaj ścieżkę: py idosell_import.py \"C:\\…\\plik.csv\"")


def read_sheet(path: str) -> list[dict]:
    if not os.path.exists(path):
        sys.exit(f"Brak pliku arkusza: {path}")
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        text = fh.read()
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    csv.field_size_limit(10**9)
    rows = [{(k or "").strip(): (v if isinstance(v, str) else "") for k, v in r.items()}
            for r in csv.DictReader(io.StringIO(text), dialect=dialect, restval="")]
    if rows and "Nazwa" not in rows[0]:
        sys.exit(f"W arkuszu brak kolumny „Nazwa”. Kolumny: {', '.join(rows[0])[:300]}")
    return rows


def read_feed(path_or_url: str) -> dict[str, dict]:
    data = ceny.read_source(path_or_url)
    index = {}
    for r in ceny.iter_records(data, ceny.find_record_tag(data)):
        for field in ("kod_producenta", "ean"):
            key = ceny.nkey(r.get(field, ""))
            if key:
                index.setdefault(key, r)
    return index


def build_rows(sheet: list[dict], feed: dict[str, dict], only_feed: bool, visible: bool, images: bool,
               limit: int) -> list[dict]:
    out, seen = [], set()
    for p in sheet:
        name = p.get("Nazwa", "").strip()
        code, ean = p.get("Kod", "").strip(), ceny.norm_ean(p.get("EAN", ""))
        if not name or (code, ean) in seen:
            continue
        rec = feed.get(ceny.nkey(code)) or feed.get(ceny.nkey(ean)) or {}
        if only_feed and not rec:
            continue
        seen.add((code, ean))
        unit = str(rec.get("unit", "")).strip().lower().rstrip(".")
        catalog = ceny.to_float(rec.get("price_catalog", "")) if rec else 0.0
        category = str(rec.get("cat", "")).split("/")[-1].strip() or p.get("Producent", "").strip()
        desc = (p.get("Opis nowy", "").strip() if p.get("Status", "").strip() != "RĘCZNIE" else "") \
            or p.get("Opis", "").strip()
        out.append({
            "@type": "regular", "@vat": "23", "@currency": "PLN",
            "@code_producer": code,
            "/producer@name": p.get("Producent", "").strip(),
            "/category@name[pol]": category,
            "/description/name[pol]": name,
            "/description/long_desc[pol]": desc,
            "/sizes@group_id": "-1",
            "/sizes/size@code_producer": ean,
            "@producer_code_standard": "GTIN13" if len(ean) == 13 else ("OTHER" if ean else ""),
            "/unit@id": str(UNIT_IDS.get(unit, 0)),
            "/price_retail_base@gross": f"{catalog * 1.23:.2f}" if catalog > 0 else "",
            "/visibility/site@visible": "y" if visible else "n",
            "/images/large/image@url": p.get("Link do zdjęcia", "").strip() if images else "",
        })
        if limit and len(out) >= limit:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Arkusz produktów -> CSV importu towarów IdoSell.")
    ap.add_argument("arkusz", nargs="?", default="", help="CSV arkusza (Nazwa, Producent, Kod, EAN, Opis nowy, Link do zdjęcia)")
    ap.add_argument("--feed", default=None, help="feed Onninen (plik lub link); domyślnie z programu Ceny")
    ap.add_argument("--tylko-z-feedem", action="store_true", help="tylko produkty, które są w feedzie")
    ap.add_argument("--limit", type=int, default=0, help="najwyżej tyle produktów (0 = wszystkie)")
    ap.add_argument("--widoczne", action="store_true", help="towary widoczne dla klientów (domyślnie ukryte)")
    ap.add_argument("--bez-zdjec", action="store_true", help="bez linków do zdjęć (szybszy import)")
    ap.add_argument("-o", "--wynik", default=os.path.join(HERE, "idosell_import_towarow.csv"))
    a = ap.parse_args()

    path = a.arkusz or default_sheet()
    print(f"Arkusz: {path}")
    sheet = read_sheet(path)
    feed_src = a.feed
    if feed_src is None and os.path.exists(ceny.SETTINGS):
        feed_src = ceny.load_settings().get("pliki", "xml", fallback="").strip()
    if feed_src:
        print("Feed: " + re.sub(r"(token|key|klucz)=[^&]+", r"\1=***", feed_src))
    feed = read_feed(feed_src) if feed_src else {}
    if a.tylko_z_feedem and not feed:
        sys.exit("--tylko-z-feedem wymaga --feed")
    rows = build_rows(sheet, feed, a.tylko_z_feedem, a.widoczne, not a.bez_zdjec, a.limit)
    with open(a.wynik, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)
    in_feed = sum(1 for r in rows if r["/price_retail_base@gross"])
    print(f"Zapisano {len(rows)} towarów (z ceną z feedu: {in_feed}) -> {a.wynik}")
    print("W panelu IdoSell: Towary → Dodawanie nowych towarów → Dodaj źródło danych → ten plik.")


if __name__ == "__main__":
    main()
