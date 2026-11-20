# Strona inwestycji z jawnymi cenami

Strona firmy deweloperskiej z cenami wszystkich domów i mieszkań. Każdej nocy sama przygotowuje plik z cenami dla portalu **dane.gov.pl**, tak jak wymaga ustawa o ochronie praw nabywcy lokalu mieszkalnego lub domu jednorodzinnego oraz Deweloperskim Funduszu Gwarancyjnym. Ceny klient zmienia sam w prostym panelu.

## Co jest na stronie

* **Wizualizacja z metkami.** Na obrazie od architekta każdy dom ma „metkę” z ceną za m² i statusem. Kliknięcie prowadzi na stronę domu.
* **Cennik** wszystkich lokali, także sprzedanych. Pokazuje cenę za m², cenę lokalu (cena za m² × powierzchnia) oraz cenę razem z płatnymi dodatkami.
* **Strona każdego domu** z parametrami, dodatkami (garaż, komórka, ogródek, przyłącza…), rzutami i **pełną historią ceny**.
* Standard, odległości, mapa (OpenStreetMap, bez klucza API), zdjęcia z budowy i prospekt informacyjny w PDF.
* FAQ i formularz zapytania. Odpowiedzi w FAQ powstają **wyłącznie z danych klienta**, nic nie jest dopisywane od siebie.
* Warstwa pod wyszukiwarki i AI (GEO). Treść jest renderowana na serwerze, bez JS. Strona ma JSON-LD (Organization, Offer, FAQPage, BreadcrumbList), `robots.txt` przepuszczający boty AI, `sitemap.xml` i `llms.txt`.

## Zasady wymagane ustawą, wbudowane w kod

| Wymóg | Jak to działa |
|---|---|
| Historia cen | Cena nigdy się nie nadpisuje. Każda zmiana to nowy wpis z datą „obowiązuje od”. Dwie zmiany tego samego dnia poprawiają wpis z tego dnia. |
| Sprzedane zostają | Statusu „sprzedany” nie da się zamienić na usunięcie. Sprzedany lokal zostaje w cenniku z historią. |
| Dodatki z ceną | Każdy dodatek ma cenę brutto albo „w cenie”. Ceny dodatków też mają historię. |
| Codzienna publikacja | `eksport.py` uruchamiany z crona tworzy `dane/ceny-RRRR-MM-DD.csv`, `dane/data.xml` (dla harvestera) i `dane/data.xml.md5`. |
| Alarm | Gdy eksport się nie uda, idzie e-mail na `kontakt.email_alarmy`. |

> ⚠️ **Przed uruchomieniem u klienta sprawdź format pliku.** 11.11.2026 weszło nowe rozporządzenie zmieniające strukturę danych dla dane.gov.pl. Kolumny w `eksport.py` (funkcja `wiersz()`, ustawienia w `USTAWIENIA_CSV`, schemat w `zbuduj_xml()`) odpowiadają dotychczasowemu wzorowi ministerstwa. Porównaj je z aktualnym wzorem na dane.gov.pl („wzorcowe dokumenty elektroniczne”) i popraw, zanim złożysz wniosek o harvester. Cała struktura jest w jednym miejscu, więc poprawka to edycja jednej listy.

## Gdzie wpisać dane z listy „co przygotować”

| Z listy | Gdzie |
|---|---|
| Wizualizacja | `static/` + `inwestycja.wizualizacja` w `konfiguracja.json`; domy zaznaczasz w panelu (klik w narożniki) |
| Prospekt informacyjny | `static/prospekt.pdf` + `"prospekt": "prospekt.pdf"` |
| Logo | `static/logo.svg` + `firma.logo` |
| Rzuty | `static/rzuty/` + pole „Rzuty” przy lokalu w panelu |
| Zdjęcia z budowy | `static/budowa/` + wpisy `{"plik": "...", "data": "2026-11-01", "etap": "fundamenty"}` w `inwestycja.budowa` |
| Firma, biura, inwestycja, kontakt, kolor | `konfiguracja.json` |
| Domy i mieszkania, ceny, dodatki | panel `/panel` |

Plik `konfiguracja.json` zawiera teraz **przykładowe dane** i trzeba je zamienić na prawdziwe.

## Uruchomienie lokalne

```bash
pip install flask
cd strona_dewelopera
python3 przyklad_dane.py     # tylko do pokazu: 6 przykładowych domów
python3 app.py haslo         # hasło do panelu
python3 app.py               # http://127.0.0.1:8000  (panel: /panel)
python3 eksport.py           # ręczny eksport do dane/
python3 -m pytest -q         # testy
```

## Na serwerze

```bash
pip install flask gunicorn
gunicorn -w 2 -b 127.0.0.1:8000 "app:create_app()"     # za nginx z HTTPS
crontab -e
30 2 * * *  cd /srv/strona_dewelopera && STRONA_SMTP_HASLO=... python3 eksport.py
```

Zmienne środowiskowe: `STRONA_SEKRET` (klucz sesji; bez niej powstaje plik `.sekret_sesji`), `STRONA_BAZA` (ścieżka bazy), `STRONA_SMTP_HASLO`.
Kopia zapasowa: plik `strona.sqlite3` (jest w nim cała historia cen, której ustawa każe nie gubić).

## dane.gov.pl — pierwszy dzień

1. Uruchom `eksport.py` i sprawdź, że `https://twojadomena.pl/dane/data.xml` się otwiera.
2. Złóż na dane.gov.pl wniosek o założenie konta dostawcy i o harvester, podając adres `…/dane/data.xml`.
3. Portal odpowiada po kilku dniach albo tygodniach. W tym czasie eksport ma już działać co noc.
