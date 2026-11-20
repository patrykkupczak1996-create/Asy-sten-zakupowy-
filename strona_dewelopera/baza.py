"""Baza lokali i cen (SQLite).

Zasady wymagane ustawą o jawności cen:
* cena się nie nadpisuje — każda zmiana to nowy wiersz z datą, od której obowiązuje;
* sprzedane lokale zostają w bazie i na stronie razem z historią cen;
* ceny dodatków (garaż, komórka, ogródek, przyłącza...) też mają historię.

Kwoty trzymamy w groszach (int), żeby uniknąć błędów zaokrągleń floatów.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

KATALOG = Path(__file__).resolve().parent
DOMYSLNA_BAZA = KATALOG / "strona.sqlite3"

RODZAJE_LOKALI = ("dom jednorodzinny", "lokal mieszkalny")
STATUSY = ("dostępny", "zarezerwowany", "sprzedany")

# Rodzaj dodatku -> grupa kolumn w pliku dla dane.gov.pl (art. 19a ustawy).
RODZAJE_DODATKOW = {
    "miejsce postojowe": "czesc",
    "garaż": "czesc",
    "komórka lokatorska": "czesc",
    "ogródek (prawo do wyłącznego korzystania z części działki)": "czesc",
    "pomieszczenie przynależne": "pomieszczenie",
    "prawo niezbędne do korzystania z lokalu": "prawo",
    "inne świadczenie pieniężne": "swiadczenie",
}

SCHEMAT = """
CREATE TABLE IF NOT EXISTS lokale (
    id INTEGER PRIMARY KEY,
    oznaczenie TEXT NOT NULL UNIQUE,
    rodzaj TEXT NOT NULL,
    budynek TEXT,
    powierzchnia REAL NOT NULL,
    pokoje INTEGER,
    pietro TEXT,
    balkon TEXT,
    ogrodek REAL,
    dzialka REAL,
    termin TEXT,
    status TEXT NOT NULL DEFAULT 'dostępny',
    rzut TEXT,
    obrys TEXT,
    kolejnosc INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ceny (
    id INTEGER PRIMARY KEY,
    lokal_id INTEGER NOT NULL REFERENCES lokale(id),
    cena_m2_gr INTEGER NOT NULL,
    data_od TEXT NOT NULL,
    UNIQUE (lokal_id, data_od)
);
CREATE TABLE IF NOT EXISTS dodatki (
    id INTEGER PRIMARY KEY,
    lokal_id INTEGER NOT NULL REFERENCES lokale(id),
    rodzaj TEXT NOT NULL,
    oznaczenie TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ceny_dodatkow (
    id INTEGER PRIMARY KEY,
    dodatek_id INTEGER NOT NULL REFERENCES dodatki(id),
    cena_gr INTEGER,             -- NULL gdy w_cenie = 1
    w_cenie INTEGER NOT NULL DEFAULT 0,
    data_od TEXT NOT NULL,
    UNIQUE (dodatek_id, data_od)
);
CREATE TABLE IF NOT EXISTS zapytania (
    id INTEGER PRIMARY KEY,
    lokal_id INTEGER REFERENCES lokale(id),
    imie TEXT NOT NULL,
    kontakt TEXT NOT NULL,
    tresc TEXT,
    utworzono TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS eksporty (
    data TEXT PRIMARY KEY,
    plik TEXT NOT NULL,
    md5 TEXT NOT NULL,
    utworzono TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def polacz(sciezka: Path | str = DOMYSLNA_BAZA) -> sqlite3.Connection:
    con = sqlite3.connect(str(sciezka))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMAT)
    return con


def zl_na_gr(tekst: str | float | int) -> int:
    """'12 345,67' / '12345.67' / 12345.67 -> 1234567."""
    if isinstance(tekst, (int, float)):
        return round(tekst * 100)
    t = str(tekst).replace(" ", "").replace(" ", "").replace("zł", "").replace(",", ".")
    if not t:
        raise ValueError("Pusta kwota")
    return round(float(t) * 100)


def gr_na_zl(gr: int | None, *, waluta: bool = True) -> str:
    if gr is None:
        return "—"
    zl, grosze = divmod(abs(gr), 100)
    tekst = f"{zl:,}".replace(",", " ")
    if grosze:
        tekst += f",{grosze:02d}"
    if gr < 0:
        tekst = "−" + tekst
    return tekst + (" zł" if waluta else "")


# ---------------------------------------------------------------- lokale

def dodaj_lokal(con, **pola) -> int:
    if pola.get("rodzaj") not in RODZAJE_LOKALI:
        raise ValueError(f"Rodzaj lokalu musi być jednym z: {RODZAJE_LOKALI}")
    kolumny = ", ".join(pola)
    znaki = ", ".join("?" * len(pola))
    cur = con.execute(f"INSERT INTO lokale ({kolumny}) VALUES ({znaki})", tuple(pola.values()))
    con.commit()
    return cur.lastrowid


EDYTOWALNE = ("oznaczenie", "rodzaj", "budynek", "powierzchnia", "pokoje", "pietro", "balkon",
              "ogrodek", "dzialka", "termin", "status", "rzut", "obrys", "kolejnosc")


def zmien_lokal(con, lokal_id: int, **pola) -> None:
    pola = {k: v for k, v in pola.items() if k in EDYTOWALNE}
    if "status" in pola and pola["status"] not in STATUSY:
        raise ValueError(f"Status musi być jednym z: {STATUSY}")
    if "rodzaj" in pola and pola["rodzaj"] not in RODZAJE_LOKALI:
        raise ValueError(f"Rodzaj musi być jednym z: {RODZAJE_LOKALI}")
    if not pola:
        return
    ustaw = ", ".join(f"{k} = ?" for k in pola)
    con.execute(f"UPDATE lokale SET {ustaw} WHERE id = ?", (*pola.values(), lokal_id))
    con.commit()


def ustaw_cene(con, lokal_id: int, cena_m2_gr: int, data_od: date | str | None = None) -> bool:
    """Zapisuje nową cenę za m². Zwraca False, gdy cena się nie zmieniła.

    Zmiana kilka razy tego samego dnia poprawia wpis z tego dnia (do portalu i tak
    trafia stan z jednego dnia), wcześniejsze dni zostają nietknięte.
    """
    if cena_m2_gr <= 0:
        raise ValueError("Cena musi być dodatnia")
    data_od = str(data_od or date.today())
    obecna = aktualna_cena(con, lokal_id, data_od)
    if obecna and obecna["cena_m2_gr"] == cena_m2_gr:
        return False
    con.execute(
        "INSERT INTO ceny (lokal_id, cena_m2_gr, data_od) VALUES (?, ?, ?) "
        "ON CONFLICT (lokal_id, data_od) DO UPDATE SET cena_m2_gr = excluded.cena_m2_gr",
        (lokal_id, cena_m2_gr, data_od),
    )
    con.commit()
    return True


def aktualna_cena(con, lokal_id: int, na_dzien: str | None = None):
    return con.execute(
        "SELECT * FROM ceny WHERE lokal_id = ? AND data_od <= ? ORDER BY data_od DESC LIMIT 1",
        (lokal_id, str(na_dzien or date.today())),
    ).fetchone()


def historia_cen(con, lokal_id: int, powierzchnia: float):
    wiersze = con.execute(
        "SELECT cena_m2_gr, data_od FROM ceny WHERE lokal_id = ? ORDER BY data_od DESC", (lokal_id,)
    ).fetchall()
    return [
        {"data_od": w["data_od"], "cena_m2_gr": w["cena_m2_gr"],
         "cena_gr": cena_calkowita(w["cena_m2_gr"], powierzchnia)}
        for w in wiersze
    ]


def cena_calkowita(cena_m2_gr: int, powierzchnia: float) -> int:
    return round(cena_m2_gr * powierzchnia)


# ---------------------------------------------------------------- dodatki

def dodaj_dodatek(con, lokal_id: int, rodzaj: str, oznaczenie: str,
                  cena_gr: int | None, w_cenie: bool = False, data_od=None) -> int:
    if rodzaj not in RODZAJE_DODATKOW:
        raise ValueError(f"Nieznany rodzaj dodatku: {rodzaj}")
    cur = con.execute("INSERT INTO dodatki (lokal_id, rodzaj, oznaczenie) VALUES (?, ?, ?)",
                      (lokal_id, rodzaj, oznaczenie))
    ustaw_cene_dodatku(con, cur.lastrowid, cena_gr, w_cenie, data_od)
    return cur.lastrowid


def ustaw_cene_dodatku(con, dodatek_id: int, cena_gr: int | None, w_cenie: bool,
                       data_od=None) -> bool:
    if not w_cenie and (cena_gr is None or cena_gr < 0):
        raise ValueError("Podaj cenę dodatku albo zaznacz, że jest w cenie")
    data_od = str(data_od or date.today())
    cena_gr = None if w_cenie else cena_gr
    obecna = con.execute(
        "SELECT * FROM ceny_dodatkow WHERE dodatek_id = ? AND data_od <= ? "
        "ORDER BY data_od DESC LIMIT 1", (dodatek_id, data_od)).fetchone()
    if obecna and obecna["cena_gr"] == cena_gr and bool(obecna["w_cenie"]) == bool(w_cenie):
        return False
    con.execute(
        "INSERT INTO ceny_dodatkow (dodatek_id, cena_gr, w_cenie, data_od) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (dodatek_id, data_od) DO UPDATE SET cena_gr = excluded.cena_gr, "
        "w_cenie = excluded.w_cenie",
        (dodatek_id, cena_gr, int(bool(w_cenie)), data_od),
    )
    con.commit()
    return True


def dodatki_lokalu(con, lokal_id: int, na_dzien: str | None = None):
    na_dzien = str(na_dzien or date.today())
    wynik = []
    for d in con.execute("SELECT * FROM dodatki WHERE lokal_id = ? ORDER BY id", (lokal_id,)):
        c = con.execute(
            "SELECT * FROM ceny_dodatkow WHERE dodatek_id = ? AND data_od <= ? "
            "ORDER BY data_od DESC LIMIT 1", (d["id"], na_dzien)).fetchone()
        if c is None:
            continue
        wynik.append({**dict(d), "cena_gr": c["cena_gr"], "w_cenie": bool(c["w_cenie"]),
                      "data_od": c["data_od"], "grupa": RODZAJE_DODATKOW[d["rodzaj"]]})
    return wynik


def usun_dodatek(con, dodatek_id: int) -> None:
    con.execute("DELETE FROM ceny_dodatkow WHERE dodatek_id = ?", (dodatek_id,))
    con.execute("DELETE FROM dodatki WHERE id = ?", (dodatek_id,))
    con.commit()


# ---------------------------------------------------------------- widok

def lokale_z_cenami(con, na_dzien: str | None = None) -> list[dict]:
    """Wszystkie lokale (także sprzedane) z aktualną ceną i dodatkami."""
    na_dzien = str(na_dzien or date.today())
    wynik = []
    for l in con.execute("SELECT * FROM lokale ORDER BY kolejnosc, oznaczenie"):
        l = dict(l)
        c = aktualna_cena(con, l["id"], na_dzien)
        l["cena_m2_gr"] = c["cena_m2_gr"] if c else None
        l["cena_od"] = c["data_od"] if c else None
        l["cena_gr"] = cena_calkowita(c["cena_m2_gr"], l["powierzchnia"]) if c else None
        l["dodatki"] = dodatki_lokalu(con, l["id"], na_dzien)
        platne = [d["cena_gr"] for d in l["dodatki"] if not d["w_cenie"] and d["cena_gr"]]
        l["cena_z_dodatkami_gr"] = l["cena_gr"] + sum(platne) if l["cena_gr"] is not None else None
        wynik.append(l)
    return wynik
