"""Nocny eksport cen do portalu dane.gov.pl.

Tworzy w katalogu `dane/`:
  ceny-RRRR-MM-DD.csv   dzienny plik z cenami (nowy plik każdego dnia, stare zostają)
  data.xml              plik dla harvestera dane.gov.pl, zawsze pod tym samym adresem;
                        lista wszystkich plików dziennych
  data.xml.md5          suma kontrolna MD5 pliku data.xml

Uruchamianie (cron, codziennie np. o 2:30):
  30 2 * * *  cd /sciezka/strona_dewelopera && python3 eksport.py

Gdy eksport się nie uda: kod wyjścia 1 i e-mail na adres `kontakt.email_alarmy`
(jeśli skonfigurowano SMTP; hasło w zmiennej STRONA_SMTP_HASLO).

!!! WAŻNE — SPRAWDŹ PRZED URUCHOMIENIEM U KLIENTA !!!
Struktura pliku (lista i kolejność kolumn, separator, znak braku wartości,
schemat XML harvestera) jest zebrana w jednym miejscu: KOLUMNY, USTAWIENIA_CSV
i zbuduj_xml(). Od 11.11.2026 obowiązuje nowe rozporządzenie zmieniające format
danych. Porównaj te definicje z aktualnym wzorem opublikowanym na dane.gov.pl
(„wzorcowe dokumenty elektroniczne") i popraw je, zanim złożysz wniosek o harvester.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import smtplib
import sys
import traceback
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from xml.sax.saxutils import escape

import baza

KATALOG = Path(__file__).resolve().parent
KATALOG_DANYCH = KATALOG / "dane"

USTAWIENIA_CSV = {
    "separator": ";",
    "przecinek_dziesietny": True,   # 7 500,00 zamiast 7500.00
    "brak": "X",                    # znak „nie dotyczy / brak wartości"
    "kodowanie": "utf-8",
    "laczenie_wielu": " | ",        # kilka dodatków jednego rodzaju w jednej komórce
    "eksportuj_sprzedane": False,   # w pliku sama oferta; sprzedane zostają na stronie
}


def wczytaj_konfiguracje(sciezka: Path | None = None) -> dict:
    return json.loads((sciezka or KATALOG / "konfiguracja.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ CSV

def _kwota(gr):
    if gr is None:
        return None
    tekst = f"{gr / 100:.2f}"
    return tekst.replace(".", ",") if USTAWIENIA_CSV["przecinek_dziesietny"] else tekst


def _adres(prefiks: str, a: dict | None):
    a = a or {}
    return [
        (f"Województwo {prefiks}", a.get("wojewodztwo")),
        (f"Powiat {prefiks}", a.get("powiat")),
        (f"Gmina {prefiks}", a.get("gmina")),
        (f"Miejscowość {prefiks}", a.get("miejscowosc")),
        (f"Ulica {prefiks}", a.get("ulica")),
        (f"Nr nieruchomości {prefiks}", a.get("nr_nieruchomosci")),
        (f"Nr lokalu {prefiks}", a.get("nr_lokalu")),
        (f"Kod pocztowy {prefiks}", a.get("kod_pocztowy")),
    ]


def _grupa(lokal, grupa, pole):
    lacz = USTAWIENIA_CSV["laczenie_wielu"]
    elementy = [d for d in lokal["dodatki"] if d["grupa"] == grupa]
    if not elementy:
        return None
    if pole == "rodzaj":
        return lacz.join(d["rodzaj"] for d in elementy)
    if pole == "oznaczenie":
        return lacz.join(d["oznaczenie"] for d in elementy)
    if pole == "cena":
        return lacz.join("w cenie lokalu" if d["w_cenie"] else _kwota(d["cena_gr"]) for d in elementy)
    if pole == "data":
        return lacz.join(d["data_od"] for d in elementy)
    raise KeyError(pole)


def wiersz(k: dict, lokal: dict) -> list[tuple[str, object]]:
    """Jeden lokal -> lista (nazwa kolumny, wartość). To jest definicja struktury pliku."""
    f = k["firma"]
    inw = k["inwestycja"]
    biura = k.get("biura_sprzedazy") or []
    pierwsze = biura[0] if biura else f["siedziba"]
    dodatkowe = "; ".join(
        ", ".join(str(x) for x in (b.get("ulica"), b.get("nr_nieruchomosci"),
                                   b.get("kod_pocztowy"), b.get("miejscowosc")) if x)
        for b in biura[1:]) or None
    strona = k["strona"]["adres"].rstrip("/")
    prospekt = f"{strona}/prospekt" if inw.get("prospekt") else None

    return [
        ("Nazwa dewelopera", f["pelna_nazwa"]),
        ("Forma prawna dewelopera", f.get("forma_prawna")),
        ("Nr KRS", f.get("krs")),
        ("Nr wpisu do CEIDG", f.get("ceidg")),
        ("Nr NIP", f["nip"]),
        ("Nr REGON", f.get("regon")),
        ("Nr telefonu", k["kontakt"]["telefon"]),
        ("Adres poczty elektronicznej", k["kontakt"].get("email_publiczny")),
        ("Nr faxu", None),
        ("Adres strony internetowej dewelopera", strona),
        *_adres("adresu siedziby/głównego miejsca wykonywania działalności gospodarczej dewelopera",
                f["siedziba"]),
        *_adres("adresu lokalu, w którym prowadzona jest sprzedaż", pierwsze),
        ("Dodatkowe lokalizacje, w których prowadzona jest sprzedaż", dodatkowe),
        ("Sposób kontaktu nabywcy z deweloperem", k.get("sposob_kontaktu")),
        *[(n, v) for n, v in _adres("lokalizacji przedsięwzięcia deweloperskiego lub zadania inwestycyjnego",
                                    inw["adres"]) if not n.startswith("Nr lokalu")],
        ("Rodzaj nieruchomości: lokal mieszkalny, dom jednorodzinny", lokal["rodzaj"]),
        ("Nr lokalu lub domu jednorodzinnego nadany przez dewelopera", lokal["oznaczenie"]),
        ("Cena m2 powierzchni użytkowej lokalu mieszkalnego / domu jednorodzinnego [zł]",
         _kwota(lokal["cena_m2_gr"])),
        ("Data od której obowiązuje cena m2 powierzchni użytkowej lokalu mieszkalnego / domu jednorodzinnego",
         lokal["cena_od"]),
        ("Cena lokalu mieszkalnego lub domu jednorodzinnego będących przedmiotem umowy stanowiąca "
         "iloczyn ceny m2 oraz powierzchni [zł]", _kwota(lokal["cena_gr"])),
        ("Data od której obowiązuje cena lokalu mieszkalnego lub domu jednorodzinnego będących "
         "przedmiotem umowy stanowiąca iloczyn ceny m2 oraz powierzchni", lokal["cena_od"]),
        ("Cena lokalu mieszkalnego lub domu jednorodzinnego uwzględniająca cenę lokalu stanowiącą "
         "iloczyn powierzchni oraz metrażu i innych składowych ceny, o których mowa w art. 19a ust. 1 "
         "pkt 1), 2) lub 3) [zł]", _kwota(lokal["cena_z_dodatkami_gr"])),
        ("Data od której obowiązuje cena lokalu mieszkalnego lub domu jednorodzinnego uwzględniająca "
         "cenę lokalu stanowiącą iloczyn powierzchni oraz metrażu i innych składowych ceny, o których "
         "mowa w art. 19a ust. 1 pkt 1), 2) lub 3)",
         max([lokal["cena_od"]] + [d["data_od"] for d in lokal["dodatki"]]) if lokal["cena_od"] else None),
        ("Rodzaj części nieruchomości będących przedmiotem umowy", _grupa(lokal, "czesc", "rodzaj")),
        ("Oznaczenie części nieruchomości nadane przez dewelopera", _grupa(lokal, "czesc", "oznaczenie")),
        ("Cena części nieruchomości, będących przedmiotem umowy [zł]", _grupa(lokal, "czesc", "cena")),
        ("Data od której obowiązuje cena części nieruchomości, będących przedmiotem umowy",
         _grupa(lokal, "czesc", "data")),
        ("Rodzaj pomieszczeń przynależnych, o których mowa w art. 2 ust. 4 ustawy z dnia 24 czerwca "
         "1994 r. o własności lokali", _grupa(lokal, "pomieszczenie", "rodzaj")),
        ("Oznaczenie pomieszczeń przynależnych, o których mowa w art. 2 ust. 4 ustawy z dnia 24 czerwca "
         "1994 r. o własności lokali", _grupa(lokal, "pomieszczenie", "oznaczenie")),
        ("Wyszczególnienie cen pomieszczeń przynależnych, o których mowa w art. 2 ust. 4 ustawy z dnia "
         "24 czerwca 1994 r. o własności lokali [zł]", _grupa(lokal, "pomieszczenie", "cena")),
        ("Data od której obowiązuje cena wyszczególnionych pomieszczeń przynależnych, o których mowa w "
         "art. 2 ust. 4 ustawy z dnia 24 czerwca 1994 r. o własności lokali",
         _grupa(lokal, "pomieszczenie", "data")),
        ("Wyszczególnienie praw niezbędnych do korzystania z lokalu mieszkalnego lub domu jednorodzinnego",
         _grupa(lokal, "prawo", "oznaczenie")),
        ("Wartość praw niezbędnych do korzystania z lokalu mieszkalnego lub domu jednorodzinnego [zł]",
         _grupa(lokal, "prawo", "cena")),
        ("Data od której obowiązuje cena wartości praw niezbędnych do korzystania z lokalu mieszkalnego "
         "lub domu jednorodzinnego", _grupa(lokal, "prawo", "data")),
        ("Wyszczególnienie rodzajów innych świadczeń pieniężnych, które nabywca zobowiązany jest spełnić "
         "na rzecz dewelopera w wykonaniu umowy przenoszącej własność", _grupa(lokal, "swiadczenie", "oznaczenie")),
        ("Wartość innych świadczeń pieniężnych, które nabywca zobowiązany jest spełnić na rzecz dewelopera "
         "w wykonaniu umowy przenoszącej własność [zł]", _grupa(lokal, "swiadczenie", "cena")),
        ("Data od której obowiązuje cena wartości innych świadczeń pieniężnych, które nabywca zobowiązany "
         "jest spełnić na rzecz dewelopera w wykonaniu umowy przenoszącej własność",
         _grupa(lokal, "swiadczenie", "data")),
        ("Adres strony internetowej, pod którym dostępny jest prospekt informacyjny", prospekt),
    ]


def zbuduj_csv(k: dict, lokale: list[dict]) -> str:
    u = USTAWIENIA_CSV
    oferta = [l for l in lokale if l["cena_m2_gr"] is not None
              and (u["eksportuj_sprzedane"] or l["status"] != "sprzedany")]
    bufor = io.StringIO()
    w = csv.writer(bufor, delimiter=u["separator"], quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    przyklad = wiersz(k, oferta[0] if oferta else _pusty_lokal())
    w.writerow([n for n, _ in przyklad])
    for l in oferta:
        w.writerow([u["brak"] if v in (None, "") else v for _, v in wiersz(k, l)])
    return bufor.getvalue()


def _pusty_lokal():
    return {"rodzaj": None, "oznaczenie": None, "cena_m2_gr": None, "cena_od": None,
            "cena_gr": None, "cena_z_dodatkami_gr": None, "dodatki": [], "status": None}


# ------------------------------------------------------------------ XML harvestera

def id_zbioru(k: dict) -> str:
    return f"ceny-{k['firma']['nip']}-{_slug(k['inwestycja']['nazwa'])}"


def _slug(t: str) -> str:
    tabela = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    t = t.translate(tabela).lower()
    return "-".join("".join(c if c.isalnum() else " " for c in t).split())


def zbuduj_xml(k: dict, pliki: list[tuple[str, str]]) -> str:
    """pliki: lista (data, nazwa_pliku), najnowszy pierwszy."""
    strona = k["strona"]["adres"].rstrip("/")
    firma = k["firma"]["pelna_nazwa"]
    inw = k["inwestycja"]["nazwa"]
    zasoby = []
    for dzien, plik in pliki:
        zasoby.append(f"""      <resource status="published">
        <extIdent>{escape(id_zbioru(k))}-{dzien}</extIdent>
        <url>{escape(strona)}/dane/{escape(plik)}</url>
        <title>
          <polish>Ceny ofertowe mieszkań i domów — {escape(inw)} — {dzien}</polish>
          <english>Offer prices of dwellings — {escape(inw)} — {dzien}</english>
        </title>
        <description>
          <polish>Ceny lokali mieszkalnych i domów jednorodzinnych oferowanych przez {escape(firma)} według stanu na dzień {dzien}.</polish>
          <english>Prices of dwellings offered by {escape(firma)} as of {dzien}.</english>
        </description>
        <availability>local</availability>
        <dataDate>{dzien}</dataDate>
        <specialSigns>
          <specialSign>{escape(USTAWIENIA_CSV['brak'])}</specialSign>
        </specialSigns>
        <hasDynamicData>false</hasDynamicData>
        <hasHighValueData>true</hasHighValueData>
        <hasHighValueDataFromEuropeanCommissionList>false</hasHighValueDataFromEuropeanCommissionList>
        <hasResearchData>false</hasResearchData>
        <containsProtectedData>false</containsProtectedData>
      </resource>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ns2:datasets xmlns:ns2="urn:otwarte-dane:harvester:1.13">
  <dataset status="published">
    <extIdent>{escape(id_zbioru(k))}</extIdent>
    <title>
      <polish>Ceny ofertowe mieszkań dewelopera {escape(firma)} — {escape(inw)}</polish>
      <english>Offer prices of dwellings of developer {escape(firma)} — {escape(inw)}</english>
    </title>
    <description>
      <polish>Codziennie aktualizowane ceny ofertowe lokali mieszkalnych i domów jednorodzinnych zgodnie z ustawą o ochronie praw nabywcy lokalu mieszkalnego lub domu jednorodzinnego oraz Deweloperskim Funduszu Gwarancyjnym.</polish>
      <english>Daily updated offer prices of dwellings published under the Polish developer act.</english>
    </description>
    <updateFrequency>daily</updateFrequency>
    <hasDynamicData>false</hasDynamicData>
    <hasHighValueData>true</hasHighValueData>
    <hasHighValueDataFromEuropeanCommissionList>false</hasHighValueDataFromEuropeanCommissionList>
    <hasResearchData>false</hasResearchData>
    <categories>
      <category>ECON</category>
    </categories>
    <resources>
{chr(10).join(zasoby)}
    </resources>
    <tags>
      <tag lang="pl">Deweloper</tag>
    </tags>
  </dataset>
</ns2:datasets>
"""


# ------------------------------------------------------------------ uruchomienie

def eksportuj(con, k: dict, katalog: Path = KATALOG_DANYCH, dzien: date | None = None) -> Path:
    dzien = str(dzien or date.today())
    katalog.mkdir(parents=True, exist_ok=True)
    lokale = baza.lokale_z_cenami(con, dzien)
    if not any(l["cena_m2_gr"] is not None for l in lokale):
        raise RuntimeError("Brak lokali z ceną — nie ma czego wysłać do dane.gov.pl")

    tresc = zbuduj_csv(k, lokale).encode(USTAWIENIA_CSV["kodowanie"])
    nazwa = f"ceny-{dzien}.csv"
    (katalog / nazwa).write_bytes(tresc)
    con.execute("INSERT INTO eksporty (data, plik, md5) VALUES (?, ?, ?) "
                "ON CONFLICT (data) DO UPDATE SET plik = excluded.plik, md5 = excluded.md5, "
                "utworzono = datetime('now', 'localtime')",
                (dzien, nazwa, hashlib.md5(tresc).hexdigest()))
    con.commit()

    pliki = [(w["data"], w["plik"]) for w in
             con.execute("SELECT data, plik FROM eksporty ORDER BY data DESC")
             if (katalog / w["plik"]).exists()]
    xml = zbuduj_xml(k, pliki).encode("utf-8")
    # Zapis atomowy: harvester nigdy nie pobierze połowy pliku.
    tymczasowy = katalog / "data.xml.tmp"
    tymczasowy.write_bytes(xml)
    os.replace(tymczasowy, katalog / "data.xml")
    (katalog / "data.xml.md5").write_text(hashlib.md5(xml).hexdigest(), encoding="ascii")
    return katalog / nazwa


def wyslij_alarm(k: dict, blad: str) -> bool:
    s = k.get("smtp") or {}
    adres = k["kontakt"].get("email_alarmy")
    if not (s.get("host") and adres):
        return False
    msg = EmailMessage()
    msg["Subject"] = f"[{k['inwestycja']['nazwa']}] Nocna aktualizacja cen NIE POWIODŁA SIĘ"
    msg["From"] = s.get("nadawca") or s.get("uzytkownik") or adres
    msg["To"] = adres
    msg.set_content(
        "Dzisiejszy plik z cenami dla dane.gov.pl nie powstał.\n"
        "Ustawa wymaga codziennej aktualizacji — sprawdź to jeszcze dziś.\n\n" + blad)
    with smtplib.SMTP(s["host"], int(s.get("port") or 587), timeout=30) as smtp:
        smtp.starttls()
        if s.get("uzytkownik"):
            smtp.login(s["uzytkownik"], os.environ.get("STRONA_SMTP_HASLO", ""))
        smtp.send_message(msg)
    return True


def main() -> int:
    k = wczytaj_konfiguracje()
    try:
        con = baza.polacz(os.environ.get("STRONA_BAZA", baza.DOMYSLNA_BAZA))
        plik = eksportuj(con, k)
        print(f"OK: {plik}")
        return 0
    except Exception:
        blad = traceback.format_exc()
        print(blad, file=sys.stderr)
        try:
            if not wyslij_alarm(k, blad):
                print("Brak konfiguracji SMTP — alarm nie został wysłany e-mailem.", file=sys.stderr)
        except Exception:
            print("Nie udało się wysłać alarmu:\n" + traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
