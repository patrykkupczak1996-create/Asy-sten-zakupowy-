"""Testy strony z jawnymi cenami:  python3 -m pytest strona_dewelopera -q"""
import csv
import hashlib
import io
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baza  # noqa: E402
import eksport  # noqa: E402
import przyklad_dane  # noqa: E402
from app import create_app  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


@pytest.fixture
def con(tmp_path):
    c = baza.polacz(tmp_path / "t.sqlite3")
    przyklad_dane.wypelnij(c)
    return c


@pytest.fixture
def app(tmp_path, con):
    a = create_app(sciezka_bazy=tmp_path / "t.sqlite3", katalog_danych=tmp_path / "dane",
                   hash_hasla=generate_password_hash("tajnehaslo123"))
    a.config["TESTING"] = True
    return a


@pytest.fixture
def klient(app):
    return app.test_client()


def zaloguj(klient):
    klient.get("/panel/logowanie")
    with klient.session_transaction() as s:
        token = s["csrf"]
    r = klient.post("/panel/logowanie", data={"haslo": "tajnehaslo123", "csrf": token})
    assert r.status_code == 302
    klient.get("/panel")
    with klient.session_transaction() as s:
        return s["csrf"]


# ------------------------------------------------------------ baza

def test_kwoty():
    assert baza.zl_na_gr("7 450,50") == 745050
    assert baza.zl_na_gr("7450.5") == 745050
    assert baza.gr_na_zl(88_206_250) == "882\u00a0062,50\u00a0zł"
    assert baza.gr_na_zl(100_000_00) == "100 000 zł"


def test_cena_nie_jest_nadpisywana(con):
    lid = con.execute("SELECT id FROM lokale WHERE oznaczenie='B1'").fetchone()[0]
    wczoraj = date.today() - timedelta(days=1)
    assert baza.ustaw_cene(con, lid, 7000_00, wczoraj)
    assert baza.ustaw_cene(con, lid, 7100_00)
    assert not baza.ustaw_cene(con, lid, 7100_00)          # bez zmiany = bez nowego wpisu
    assert baza.ustaw_cene(con, lid, 7200_00)               # ten sam dzień = poprawka wpisu
    h = baza.historia_cen(con, lid, 136.20)
    assert [x["cena_m2_gr"] for x in h[:2]] == [7200_00, 7000_00]
    assert h[0]["cena_gr"] == round(7200_00 * 136.20)


def test_sprzedane_zostaja_z_historia(con):
    lokale = baza.lokale_z_cenami(con)
    a2 = next(l for l in lokale if l["oznaczenie"] == "A2")
    assert a2["status"] == "sprzedany" and a2["cena_m2_gr"] == 7350_00
    assert len(baza.historia_cen(con, a2["id"], a2["powierzchnia"])) == 2


def test_cena_z_dodatkami(con):
    l = next(l for l in baza.lokale_z_cenami(con) if l["oznaczenie"] == "B1")
    assert l["cena_z_dodatkami_gr"] == l["cena_gr"] + 18_000_00   # garaż w cenie, przyłącza płatne


# ------------------------------------------------------------ eksport

def test_eksport_csv_xml_md5(con, tmp_path):
    k = eksport.wczytaj_konfiguracje()
    plik = eksport.eksportuj(con, k, tmp_path / "dane")
    tekst = plik.read_text(encoding="utf-8")
    wiersze = list(csv.reader(io.StringIO(tekst), delimiter=";"))
    naglowek, dane = wiersze[0], wiersze[1:]
    assert len(dane) == 4                                       # sprzedane A1, A2 poza plikiem
    assert all(len(w) == len(naglowek) for w in dane)
    b1 = next(w for w in dane if w[naglowek.index("Nr lokalu lub domu jednorodzinnego nadany przez dewelopera")] == "B1")
    assert b1[naglowek.index("Nr NIP")] == k["firma"]["nip"]
    assert "" not in b1                                         # puste = "X"
    assert any(c.startswith("Cena m2") for c in naglowek)
    xml = (tmp_path / "dane" / "data.xml").read_bytes()
    assert (tmp_path / "dane" / "data.xml.md5").read_text() == hashlib.md5(xml).hexdigest()
    assert f"/dane/{plik.name}".encode() in xml


def test_eksport_bez_cen_zglasza_blad(tmp_path):
    c = baza.polacz(tmp_path / "pusta.sqlite3")
    with pytest.raises(RuntimeError):
        eksport.eksportuj(c, eksport.wczytaj_konfiguracje(), tmp_path / "d")


def test_xml_zachowuje_poprzednie_dni(con, tmp_path):
    k = eksport.wczytaj_konfiguracje()
    eksport.eksportuj(con, k, tmp_path / "d", date.today() - timedelta(days=1))
    eksport.eksportuj(con, k, tmp_path / "d")
    xml = (tmp_path / "d" / "data.xml").read_text()
    assert xml.count("<resource ") == 2


# ------------------------------------------------------------ strona

def test_strona_glowna_pokazuje_wszystkie_ceny(klient):
    r = klient.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    for ozn in ("A1", "A2", "A3", "A4", "B1", "B2"):
        assert f"/dom/{ozn}" in html
    assert "application/ld+json" in html and "FAQPage" in html
    assert html.count("<h1") == 1


def test_strona_domu_z_historia(klient):
    html = klient.get("/dom/A2").get_data(as_text=True)
    assert "Historia ceny" in html and "7 350 zł" in html and "7 200 zł" in html
    assert klient.get("/dom/NIE-MA").status_code == 404


def test_pliki_geo(klient):
    assert "GPTBot" in klient.get("/robots.txt").get_data(as_text=True)
    assert "/dom/B1" in klient.get("/sitemap.xml").get_data(as_text=True)
    assert "Domy przy Lipowej" in klient.get("/llms.txt").get_data(as_text=True)


def test_zapytanie(klient, con):
    r = klient.post("/zapytanie", data={"imie": "Jan", "kontakt": "600", "tresc": "B1?", "powrot": "/dom/B1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/dom/B1#kontakt")
    klient.post("/zapytanie", data={"imie": "bot", "kontakt": "x", "strona_www": "spam"})
    klient.post("/zapytanie", data={"imie": "x", "kontakt": "y", "powrot": "//zla.strona"})


def test_panel_wymaga_logowania(klient):
    assert klient.get("/panel").status_code == 302
    r = klient.post("/panel/logowanie", data={"haslo": "zle"})
    assert r.status_code == 400                                 # brak tokenu CSRF


def test_panel_zmiana_ceny_i_eksport(klient, app):
    token = zaloguj(klient)
    con = baza.polacz(app.config["BAZA"])
    lid = con.execute("SELECT id FROM lokale WHERE oznaczenie='B1'").fetchone()[0]
    r = klient.post("/panel", data={"csrf": token, f"cena_{lid}": "7 777,00", f"status_{lid}": "zarezerwowany"})
    assert r.status_code == 302
    l = next(l for l in baza.lokale_z_cenami(con) if l["id"] == lid)
    assert l["cena_m2_gr"] == 7777_00 and l["status"] == "zarezerwowany"
    assert klient.post("/panel", data={f"cena_{lid}": "1"}).status_code == 400   # bez CSRF
    klient.post("/panel/eksport", data={"csrf": token})
    xml = klient.get("/dane/data.xml")
    assert xml.status_code == 200 and b"harvester" in xml.data
    md5 = klient.get("/dane/data.xml.md5").get_data(as_text=True)
    assert md5 == hashlib.md5(xml.data).hexdigest()
    assert klient.get("/dane/../app.py").status_code == 404


def test_panel_dodaj_lokal_i_dodatek(klient, app):
    token = zaloguj(klient)
    r = klient.post("/panel/lokal/nowy", data={
        "csrf": token, "akcja": "zapisz", "oznaczenie": "C1", "rodzaj": "dom jednorodzinny",
        "status": "dostępny", "powierzchnia": "101,5", "cena": "8000", "obrys": "1,1 5,1 5,5"})
    assert r.status_code == 302
    lid = int(re.search(r"/panel/lokal/(\d+)", r.headers["Location"]).group(1))
    klient.post(f"/panel/lokal/{lid}", data={"csrf": token, "akcja": "dodaj_dodatek",
                                            "rodzaj": "miejsce postojowe", "oznaczenie_dodatku": "MP-1",
                                            "cena_dodatku": "25 000"})
    html = klient.get("/dom/C1").get_data(as_text=True)
    assert "MP-1" in html and "25 000 zł" in html
