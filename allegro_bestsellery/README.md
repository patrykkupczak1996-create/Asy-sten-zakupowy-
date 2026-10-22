# Bestsellery Allegro

Lokalne narzędzie do szukania ofert, które dobrze się sprzedają, sprawdzania
popytu w danej frazie i liczenia zysku z jednej sztuki przed wystawieniem oferty.

Działa bez instalowania czegokolwiek poza Pythonem 3.10+ (tylko biblioteka standardowa
i SQLite).

## Uruchomienie

```bash
cd allegro_bestsellery
python serwer.py --demo     # z przykładowymi danymi, żeby zobaczyć, jak to działa
python serwer.py            # pusta baza - dane wczytasz z CSV
```

Potem otwórz w przeglądarce http://127.0.0.1:8000. Serwer słucha tylko na
komputerze, na którym działa.

## Zakładki

- **Wyszukiwarka** - filtrowanie ofert po frazie, tytule, sprzedawcy, cenie i minimalnej
  sprzedaży dziennej. Sortowanie po liczbie sprzedanych sztuk, sprzedaży dziennej, przychodzie lub cenie.
- **Analiza fraz** - ranking fraz. Kolumna *Dziennie na ofertę* pokazuje, ile sztuk dziennie
  sprzedaje przeciętna oferta z danej frazy. Kliknięcie wiersza otwiera oferty tej frazy.
- **Kalkulator zysku** - zysk i marża z jednej sztuki po odjęciu kosztu zakupu, wysyłki,
  prowizji i opłat stałych.
- **Dane** - import pliku CSV.

## Format CSV

Pierwszy wiersz to nagłówek. Separator `;` lub `,`. Zapisz plik jako **CSV UTF-8**.

| Kolumna      | Wymagana | Opis                                                       |
|--------------|----------|------------------------------------------------------------|
| `tytul`      | tak      | tytuł oferty                                               |
| `cena`       | tak      | cena brutto, np. `89,99` albo `89.99`                      |
| `sprzedane`  | tak      | liczba sprzedanych sztuk (widoczna na ofercie)             |
| `fraza`      | nie      | fraza, z której zebrano ofertę (grupowanie w analizie)     |
| `kategoria`  | nie      | kategoria Allegro                                          |
| `dni`        | nie      | ile dni oferta była aktywna (domyślnie 30)                 |
| `ocena`      | nie      | ocena sprzedawcy/produktu                                  |
| `opinie`     | nie      | liczba opinii                                              |
| `sprzedawca` | nie      | nazwa sprzedawcy                                           |
| `url`        | nie      | link do oferty (otwiera się z wyszukiwarki)                |

Przykład: `allegro_bestsellery/przyklad.csv` (plik w repo).

**Ważne:** wskaźnik dzienny liczy się z kolumny `dni`. Jeśli jej nie podasz, zakłada
30 dni, więc wyniki dla ofert wystawionych krócej będą zaniżone.

## Skąd brać dane

Aplikacja nie pobiera danych z Allegro sama. Dane wpisujesz lub wczytujesz z pliku, co
zapewnia zgodność z zasadami serwisu. Przydatne źródła:

- oferty widoczne w wyszukiwarce Allegro i ich liczba sprzedanych sztuk - przepisz lub
  wklej do arkusza kilkadziesiąt ofert z danej frazy,
- własna historia sprzedaży z panelu sprzedawcy (eksport zamówień),
- oficjalne API Allegro (wymaga konta deweloperskiego i rejestracji aplikacji) -
  jeśli zdecydujesz się je podpiąć, dane z endpointu ofert można zapisać do CSV w tym samym formacie.

## Testy

```bash
cd allegro_bestsellery
python -m unittest test_bestsellery
```

## Ograniczenia

- Liczba sprzedanych sztuk i dni aktywności to dane wejściowe - aplikacja niczego nie weryfikuje u Allegro.
- Prowizja i opłaty w kalkulatorze to wartości orientacyjne (domyślnie 10%). Stawki zależą od
  kategorii i zmieniają się - wpisz aktualne z cennika.
- Wskaźniki pokazują popyt i konkurencję, a nie gwarancję zysku. Przed zakupem towaru
  sprawdź koszty, przepisy (np. VAT, gwarancja, ewentualne ograniczenia kategorii) i konkurencję.
