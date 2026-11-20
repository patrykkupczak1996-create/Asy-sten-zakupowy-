"""Wypełnia bazę PRZYKŁADOWYMI domami (do pokazu i testów). U klienta nie uruchamiaj.

  python3 przyklad_dane.py            # tworzy strona.sqlite3, jeśli jest pusta
"""
from datetime import date, timedelta

import baza


def obrys(i: int) -> str:
    """Obrys domu nr i na przykładowej wizualizacji, w % szerokości/wysokości obrazu."""
    x0 = 110 + i * 240
    punkty = [(x0, 640), (x0, 476), (x0 + 100, 356), (x0 + 200, 476), (x0 + 200, 640)]
    return " ".join(f"{x / 16:.2f},{y / 9:.2f}" for x, y in punkty)


def wypelnij(con) -> None:
    if con.execute("SELECT COUNT(*) FROM lokale").fetchone()[0]:
        return
    dzis = date.today()
    start = dzis - timedelta(days=60)
    domy = [
        # oznaczenie, pow. użytkowa, działka, pokoje, status, cena/m² na start, zmiana ceny
        ("A1", 118.40, 612, 5, "sprzedany", 7200, None),
        ("A2", 118.40, 598, 5, "sprzedany", 7200, 7350),
        ("A3", 124.75, 640, 5, "zarezerwowany", 7300, 7450),
        ("A4", 124.75, 655, 5, "dostępny", 7300, 7450),
        ("B1", 136.20, 720, 6, "dostępny", 7150, None),
        ("B2", 136.20, 745, 6, "dostępny", 7150, 6990),
    ]
    for i, (ozn, pow_, dz, pok, status, cena, nowa) in enumerate(domy):
        lid = baza.dodaj_lokal(
            con, oznaczenie=ozn, rodzaj="dom jednorodzinny", budynek=f"Dom {ozn}",
            powierzchnia=pow_, pokoje=pok, balkon="taras 18 m²", dzialka=dz,
            termin="IV kwartał 2027", status=status, obrys=obrys(i), kolejnosc=i)
        baza.ustaw_cene(con, lid, cena * 100, start)
        if nowa:
            baza.ustaw_cene(con, lid, nowa * 100, start + timedelta(days=35))
        baza.dodaj_dodatek(con, lid, "garaż", f"Garaż w bryle domu {ozn}", None, w_cenie=True,
                           data_od=start)
        baza.dodaj_dodatek(con, lid, "inne świadczenie pieniężne", "Przyłącza mediów",
                           18_000_00, data_od=start)


if __name__ == "__main__":
    con = baza.polacz()
    wypelnij(con)
    print(f"Lokali w bazie: {con.execute('SELECT COUNT(*) FROM lokale').fetchone()[0]}")
