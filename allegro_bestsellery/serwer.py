"""Lokalny serwer aplikacji: strona w przegladarce + proste API JSON.

Uruchomienie:
    python serwer.py --demo        # z przykladowymi danymi
    python serwer.py               # pusta baza, dane wczytasz z CSV w zakladce "Dane"

Serwer nasluchuje tylko na 127.0.0.1. Nie ma uwierzytelnienia - nie wystawiaj go do sieci.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import analiza
import baza

KATALOG = Path(__file__).parent
STATIC = KATALOG / "static"
DOMYSLNA_BAZA = KATALOG / "bestsellery.sqlite3"
MAX_BODY = 5 * 1024 * 1024


def _liczba(q: dict, nazwa: str, domyslna: float | None = None) -> float | None:
    surowa = q.get(nazwa, "")
    if surowa == "":
        return domyslna
    try:
        return float(surowa.replace(",", "."))
    except ValueError:
        raise ValueError(f"Pole {nazwa} musi byc liczba")


def _filtry(q: dict) -> dict:
    return {
        "q": q.get("q", "").strip(),
        "fraza": q.get("fraza", "").strip(),
        "kategoria": q.get("kategoria", "").strip(),
        "cena_min": _liczba(q, "cena_min"),
        "cena_max": _liczba(q, "cena_max"),
        "min_dziennie": _liczba(q, "min_dziennie"),
        "sortuj": q.get("sortuj", "sprzedane"),
    }


def _kalkulator(q: dict) -> dict:
    cena = _liczba(q, "cena")
    koszt = _liczba(q, "koszt", 0.0)
    if cena is None:
        raise ValueError("Podaj cene sprzedazy")
    return analiza.zysk_jednostkowy(
        cena=cena,
        koszt_zakupu=koszt,
        koszt_wysylki=_liczba(q, "wysylka", 0.0),
        prowizja_proc=_liczba(q, "prowizja", analiza.DOMYSLNA_PROWIZJA_PROC),
        oplata_stala=_liczba(q, "stala", 0.0),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "BestselleryAllegro/1.0"

    def do_GET(self):
        adres = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(adres.query).items()}
        try:
            if adres.path == "/":
                return self._plik(STATIC / "index.html", "text/html; charset=utf-8")
            if adres.path == "/api/oferty":
                return self._z_baza(lambda conn: baza.szukaj(conn, **_filtry(q)))
            if adres.path == "/api/frazy":
                return self._z_baza(lambda conn: analiza.analizuj_frazy(baza.wszystkie(conn)))
            if adres.path == "/api/kalkulator":
                return self._json(_kalkulator(q))
            self._json({"blad": "Nie ma takiej strony"}, 404)
        except ValueError as blad:
            self._json({"blad": str(blad)}, 400)

    def do_POST(self):
        adres = urlparse(self.path)
        try:
            if adres.path == "/api/import":
                tekst = self._tresc().decode("utf-8-sig")
                return self._z_baza(lambda conn: {"zaimportowano": baza.importuj_csv(conn, tekst)})
            if adres.path == "/api/oferta":
                dane = json.loads(self._tresc() or b"{}")
                return self._z_baza(lambda conn: {"dodano": baza.dodaj_oferte(conn, dane)})
            if adres.path == "/api/demo":
                return self._z_baza(lambda conn: {"dodano": baza.zaladuj_demo(conn)})
            self._json({"blad": "Nie ma takiej strony"}, 404)
        except UnicodeDecodeError:
            self._json({"blad": "Plik musi byc zapisany w kodowaniu UTF-8"}, 400)
        except (ValueError, TypeError, AttributeError, KeyError) as blad:
            self._json({"blad": f"Niepoprawne dane: {blad}"}, 400)

    def _tresc(self) -> bytes:
        dlugosc = int(self.headers.get("Content-Length", 0))
        if dlugosc > MAX_BODY:
            raise ValueError("Dane sa za duze (limit 5 MB)")
        return self.rfile.read(dlugosc)

    def _z_baza(self, funkcja):
        conn = baza.polacz(self.server.baza_sciezka)
        try:
            wynik = funkcja(conn)
        finally:
            conn.close()
        self._json(wynik)

    def _json(self, obiekt, status: int = 200):
        cialo = json.dumps(obiekt, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cialo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cialo)

    def _plik(self, sciezka: Path, typ: str):
        cialo = sciezka.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(cialo)))
        self.end_headers()
        self.wfile.write(cialo)


def main():
    parser = argparse.ArgumentParser(description="Bestsellery Allegro - lokalna analiza ofert")
    parser.add_argument("--baza", default=str(DOMYSLNA_BAZA), help="plik bazy SQLite")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true", help="dodaj dane przykladowe, jesli baza jest pusta")
    args = parser.parse_args()

    if args.demo:
        conn = baza.polacz(args.baza)
        baza.zaladuj_demo(conn)
        conn.close()

    serwer = ThreadingHTTPServer((args.host, args.port), Handler)
    serwer.baza_sciezka = args.baza
    print(f"Otworz w przegladarce: http://{args.host}:{args.port}")
    try:
        serwer.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymano serwer.")


if __name__ == "__main__":
    main()
