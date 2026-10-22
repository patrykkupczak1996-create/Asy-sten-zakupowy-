"""Testy: python -m unittest test_bestsellery (uruchamiac z tego katalogu)."""

import sqlite3
import unittest

import analiza
import baza


def polaczenie() -> sqlite3.Connection:
    return baza.polacz(":memory:")


class AnalizaTest(unittest.TestCase):
    def test_sprzedaz_dziennie_dzieli_przez_dni(self):
        self.assertEqual(analiza.sprzedaz_dziennie(60, 30), 2.0)

    def test_sprzedaz_dziennie_nie_dzieli_przez_zero(self):
        self.assertEqual(analiza.sprzedaz_dziennie(5, 0), 5.0)

    def test_zysk_jednostkowy_odejmuje_wszystkie_koszty(self):
        wynik = analiza.zysk_jednostkowy(
            cena=100, koszt_zakupu=60, koszt_wysylki=10, prowizja_proc=10, oplata_stala=2
        )
        self.assertEqual(wynik, {"prowizja": 10.0, "zysk": 18.0, "marza_proc": 18.0})

    def test_zysk_jednostkowy_przy_zerowej_cenie(self):
        self.assertEqual(analiza.zysk_jednostkowy(0, 5)["marza_proc"], 0.0)

    def test_analizuj_frazy_sortuje_po_sprzedazy_na_oferte(self):
        oferty = [
            {"fraza": "a", "sprzedane": 30, "dni": 30, "cena": 10},
            {"fraza": "a", "sprzedane": 10, "dni": 30, "cena": 20},
            {"fraza": "b", "sprzedane": 20, "dni": 10, "cena": 50},
        ]
        wyniki = analiza.analizuj_frazy(oferty)
        self.assertEqual([w["fraza"] for w in wyniki], ["b", "a"])
        self.assertEqual(wyniki[0]["sprzedaz_dziennie_na_oferte"], 2.0)
        self.assertEqual(wyniki[1]["liczba_ofert"], 2)
        self.assertEqual(wyniki[1]["sprzedaz_dziennie_lacznie"], 1.33)
        self.assertEqual(wyniki[1]["mediana_ceny"], 15.0)


class ImportTest(unittest.TestCase):
    def test_importuje_csv_z_separatorem_srednikiem_i_przecinkiem_dziesietnym(self):
        conn = polaczenie()
        tekst = "﻿tytul;fraza;cena;sprzedane;dni\nEtui;etui;\"1 299,50\";12;10\n"
        self.assertEqual(baza.importuj_csv(conn, tekst), 1)
        oferta = baza.szukaj(conn)[0]
        self.assertEqual(oferta["cena"], 1299.5)
        self.assertEqual(oferta["dziennie"], 1.2)

    def test_brak_wymaganej_kolumny_daje_czytelny_blad(self):
        conn = polaczenie()
        with self.assertRaisesRegex(ValueError, "Brakuje kolumn"):
            baza.importuj_csv(conn, "tytul;cena\nEtui;10\n")

    def test_bledna_liczba_wskazuje_numer_wiersza(self):
        conn = polaczenie()
        with self.assertRaisesRegex(ValueError, "wiersz 3"):
            baza.importuj_csv(conn, "tytul,cena,sprzedane\nA,10,1\nB,xx,1\n")

    def test_brakujaca_wartosc_wymaganego_pola(self):
        conn = polaczenie()
        with self.assertRaisesRegex(ValueError, "wiersz 2"):
            baza.importuj_csv(conn, "tytul,cena,sprzedane\n,10,1\n")


class WyszukiwanieTest(unittest.TestCase):
    def setUp(self):
        self.conn = polaczenie()
        baza.importuj_csv(
            self.conn,
            "tytul,fraza,cena,sprzedane,dni,sprzedawca\n"
            "Sluchawki A,sluchawki,100,50,10,sklep1\n"
            "Sluchawki B,sluchawki,300,200,100,sklep2\n"
            "Etui C,etui,20,500,30,sklep1\n",
        )

    def test_filtr_frazy_i_ceny(self):
        wyniki = baza.szukaj(self.conn, fraza="sluchawki", cena_max=200)
        self.assertEqual([o["tytul"] for o in wyniki], ["Sluchawki A"])

    def test_szukanie_po_sprzedawcy(self):
        wyniki = baza.szukaj(self.conn, q="sklep2")
        self.assertEqual(len(wyniki), 1)

    def test_minimalna_sprzedaz_dzienna(self):
        wyniki = baza.szukaj(self.conn, min_dziennie=3.0)
        self.assertEqual({o["tytul"] for o in wyniki}, {"Sluchawki A", "Etui C"})

    def test_sortowanie_po_sprzedazy_dziennej(self):
        wyniki = baza.szukaj(self.conn, sortuj="dziennie")
        self.assertEqual(wyniki[0]["tytul"], "Etui C")

    def test_nieznane_sortowanie_wraca_do_domyslnego(self):
        wyniki = baza.szukaj(self.conn, sortuj="DROP TABLE oferty")
        self.assertEqual(wyniki[0]["tytul"], "Etui C")

    def test_demo_dodaje_dane_tylko_do_pustej_bazy(self):
        self.assertGreater(baza.zaladuj_demo(polaczenie()), 0)
        self.assertEqual(baza.zaladuj_demo(self.conn), 0)


if __name__ == "__main__":
    unittest.main()
