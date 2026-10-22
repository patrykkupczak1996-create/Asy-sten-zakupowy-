"""Baza SQLite z ofertami: schemat, import CSV, wyszukiwanie i dane demonstracyjne."""

from __future__ import annotations

import csv
import io
import random
import sqlite3
from pathlib import Path

from analiza import sprzedaz_dziennie

SCHEMAT = """
CREATE TABLE IF NOT EXISTS oferty (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tytul       TEXT    NOT NULL,
    fraza       TEXT    NOT NULL DEFAULT 'ogolne',
    kategoria   TEXT    NOT NULL DEFAULT '',
    cena        REAL    NOT NULL,
    sprzedane   INTEGER NOT NULL DEFAULT 0,
    dni         INTEGER NOT NULL DEFAULT 30,
    ocena       REAL,
    opinie      INTEGER NOT NULL DEFAULT 0,
    sprzedawca  TEXT    NOT NULL DEFAULT '',
    url         TEXT    NOT NULL DEFAULT '',
    dodano      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS oferty_fraza ON oferty (fraza);
"""

WYMAGANE_KOLUMNY = ("tytul", "cena", "sprzedane")
MAX_WIERSZY = 20_000
MAX_ROZMIAR_CSV = 5 * 1024 * 1024

# Whitelist sortowan - nazwa z zapytania nigdy nie trafia do SQL wprost.
SORTOWANIE = {
    "sprzedane": "sprzedane DESC",
    "dziennie": "CAST(sprzedane AS REAL) / MAX(dni, 1) DESC",
    "przychod": "cena * sprzedane DESC",
    "cena_rosnaco": "cena ASC",
    "cena_malejaco": "cena DESC",
}

SQL_DODAJ = """
INSERT INTO oferty (tytul, fraza, kategoria, cena, sprzedane, dni, ocena, opinie, sprzedawca, url)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def polacz(sciezka: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(sciezka)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMAT)
    return conn


def _norm(tekst) -> str:
    """Liczby zapisane po polsku: '1 299,99' -> '1299.99'."""
    return str(tekst).strip().replace(" ", "").replace("\xa0", "").replace(",", ".")


def _przygotuj(w: dict, nr: int) -> tuple:
    brak = [k for k in WYMAGANE_KOLUMNY if not str(w.get(k) or "").strip()]
    if brak:
        raise ValueError(f"wiersz {nr}: brakuje wartosci w polu {', '.join(brak)}")
    try:
        cena = float(_norm(w["cena"]))
        sprzedane = int(float(_norm(w["sprzedane"])))
        dni = int(float(_norm(w.get("dni") or "30")))
        ocena = float(_norm(w["ocena"])) if w.get("ocena") else None
        opinie = int(float(_norm(w["opinie"]))) if w.get("opinie") else 0
    except ValueError:
        raise ValueError(f"wiersz {nr}: cena, sprzedane, dni, ocena i opinie musza byc liczbami")
    if cena < 0 or sprzedane < 0 or dni <= 0:
        raise ValueError(f"wiersz {nr}: cena i sprzedaz nie moga byc ujemne, dni musza byc > 0")
    return (
        w["tytul"].strip(),
        (w.get("fraza") or "").strip() or "ogolne",
        (w.get("kategoria") or "").strip(),
        cena,
        sprzedane,
        dni,
        ocena,
        opinie,
        (w.get("sprzedawca") or "").strip(),
        (w.get("url") or "").strip(),
    )


def importuj_csv(conn: sqlite3.Connection, tekst: str) -> int:
    """Wczytuje oferty z CSV (separator ; lub ,). Zwraca liczbe dodanych wierszy."""
    if len(tekst.encode("utf-8")) > MAX_ROZMIAR_CSV:
        raise ValueError("Plik CSV jest za duzy (limit 5 MB)")
    tekst = tekst.lstrip("﻿")
    naglowek = tekst.split("\n", 1)[0]
    separator = ";" if naglowek.count(";") >= naglowek.count(",") else ","

    czytnik = csv.DictReader(io.StringIO(tekst), delimiter=separator)
    if not czytnik.fieldnames:
        raise ValueError("Plik CSV jest pusty")
    czytnik.fieldnames = [n.strip().lower() for n in czytnik.fieldnames]
    brak = [k for k in WYMAGANE_KOLUMNY if k not in czytnik.fieldnames]
    if brak:
        raise ValueError(f"Brakuje kolumn w naglowku: {', '.join(brak)}")

    wiersze = []
    for nr, w in enumerate(czytnik, start=2):
        if len(wiersze) >= MAX_WIERSZY:
            raise ValueError(f"Za duzo wierszy (limit {MAX_WIERSZY})")
        wiersze.append(_przygotuj(w, nr))

    conn.executemany(SQL_DODAJ, wiersze)
    conn.commit()
    return len(wiersze)


def dodaj_oferte(conn: sqlite3.Connection, dane: dict) -> int:
    conn.execute(SQL_DODAJ, _przygotuj(dane, nr=1))
    conn.commit()
    return 1


def _do_slownika(wiersz: sqlite3.Row) -> dict:
    oferta = dict(wiersz)
    oferta["dziennie"] = round(sprzedaz_dziennie(oferta["sprzedane"], oferta["dni"]), 2)
    oferta["przychod"] = round(oferta["cena"] * oferta["sprzedane"], 2)
    return oferta


def szukaj(
    conn: sqlite3.Connection,
    q: str = "",
    fraza: str = "",
    kategoria: str = "",
    cena_min: float | None = None,
    cena_max: float | None = None,
    min_dziennie: float | None = None,
    sortuj: str = "sprzedane",
    limit: int = 300,
) -> list[dict]:
    warunki: list[str] = []
    parametry: list = []
    if q:
        warunki.append("(tytul LIKE ? OR sprzedawca LIKE ?)")
        parametry += [f"%{q}%", f"%{q}%"]
    if fraza:
        warunki.append("fraza = ?")
        parametry.append(fraza)
    if kategoria:
        warunki.append("kategoria = ?")
        parametry.append(kategoria)
    if cena_min is not None:
        warunki.append("cena >= ?")
        parametry.append(cena_min)
    if cena_max is not None:
        warunki.append("cena <= ?")
        parametry.append(cena_max)
    if min_dziennie is not None:
        warunki.append("CAST(sprzedane AS REAL) / MAX(dni, 1) >= ?")
        parametry.append(min_dziennie)

    where = f"WHERE {' AND '.join(warunki)}" if warunki else ""
    order = SORTOWANIE.get(sortuj, SORTOWANIE["sprzedane"])
    sql = f"SELECT * FROM oferty {where} ORDER BY {order} LIMIT ?"
    parametry.append(limit)
    return [_do_slownika(w) for w in conn.execute(sql, parametry).fetchall()]


def wszystkie(conn: sqlite3.Connection) -> list[dict]:
    return [_do_slownika(w) for w in conn.execute("SELECT * FROM oferty").fetchall()]


def zaladuj_demo(conn: sqlite3.Connection) -> int:
    """Dodaje zmyslone dane demonstracyjne, ale tylko gdy baza jest pusta."""
    if conn.execute("SELECT COUNT(*) FROM oferty").fetchone()[0]:
        return 0
    losowy = random.Random(42)
    frazy = {
        "sluchawki bezprzewodowe": (89, 399),
        "etui iphone": (19, 79),
        "kubek termiczny": (29, 89),
        "mata do jogi": (49, 149),
        "lampka biurkowa led": (39, 129),
    }
    wiersze = []
    for fraza, (cena_min, cena_max) in frazy.items():
        for i in range(1, 9):
            cena = round(losowy.uniform(cena_min, cena_max), 2)
            wiersze.append(
                (
                    f"[DEMO] {fraza} model {i}",
                    fraza,
                    "dane przykladowe",
                    cena,
                    losowy.randint(0, 400),
                    losowy.choice([14, 30, 60, 90]),
                    round(losowy.uniform(3.8, 5.0), 1),
                    losowy.randint(0, 900),
                    f"sprzedawca-demo-{i}",
                    "",
                )
            )
    conn.executemany(SQL_DODAJ, wiersze)
    conn.commit()
    return len(wiersze)
