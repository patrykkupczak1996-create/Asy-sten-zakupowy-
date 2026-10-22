"""Analiza ofert: wskazniki sprzedazy, zysk jednostkowy i grupowanie po frazach.

Modul nie zna bazy ani HTTP - dzieki temu da sie go testowac na zwyklych slownikach.
Wskazniki to proste przyblizenia oparte na danych z pliku lub wpisanych recznie.
"""

from __future__ import annotations

from statistics import median

# Orientacyjna prowizja. Stawki zaleza od kategorii - sprawdz cennik Allegro dla swojej kategorii.
DOMYSLNA_PROWIZJA_PROC = 10.0


def sprzedaz_dziennie(sprzedane: int, dni: int) -> float:
    """Srednia liczba sztuk sprzedawanych dziennie w czasie, przez ktory oferta byla aktywna."""
    return sprzedane / max(dni, 1)


def zysk_jednostkowy(
    cena: float,
    koszt_zakupu: float,
    koszt_wysylki: float = 0.0,
    prowizja_proc: float = DOMYSLNA_PROWIZJA_PROC,
    oplata_stala: float = 0.0,
) -> dict:
    """Zysk z jednej sztuki po odjeciu kosztu zakupu, wysylki, prowizji i oplat stalych."""
    prowizja = cena * prowizja_proc / 100
    zysk = cena - koszt_zakupu - koszt_wysylki - prowizja - oplata_stala
    marza = zysk / cena * 100 if cena > 0 else 0.0
    return {
        "prowizja": round(prowizja, 2),
        "zysk": round(zysk, 2),
        "marza_proc": round(marza, 1),
    }


def analizuj_frazy(oferty: list[dict]) -> list[dict]:
    """Grupuje oferty po frazie i liczy, jak dobrze sprzedaja sie w przecietnym przypadku.

    Wazny wskaznik to sprzedaz_dziennie_na_oferte: mowi, ile sztuk dziennie
    sprzedaje przecietna oferta z danej frazy. Im wiecej, tym lepszy popyt
    na kazda pozycje - a mala liczba ofert oznacza mniejsza konkurencje.
    """
    grupy: dict[str, list[dict]] = {}
    for oferta in oferty:
        grupy.setdefault(oferta["fraza"], []).append(oferta)

    wyniki = []
    for fraza, lista in grupy.items():
        dzienne = [sprzedaz_dziennie(o["sprzedane"], o["dni"]) for o in lista]
        wyniki.append(
            {
                "fraza": fraza,
                "liczba_ofert": len(lista),
                "sprzedane_lacznie": sum(o["sprzedane"] for o in lista),
                "sprzedaz_dziennie_lacznie": round(sum(dzienne), 2),
                "sprzedaz_dziennie_na_oferte": round(sum(dzienne) / len(lista), 2),
                "mediana_ceny": round(median(o["cena"] for o in lista), 2),
            }
        )
    wyniki.sort(key=lambda w: w["sprzedaz_dziennie_na_oferte"], reverse=True)
    return wyniki
