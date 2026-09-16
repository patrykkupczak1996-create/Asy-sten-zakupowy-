# Asystent pomiarowy ArUco — bezdotykowe wymiarowanie ściany ze zdjęcia

Proof of Concept narzędzia dla stolarza: ze zwykłego zdjęcia ściany (zrobionego telefonem, także pod lekkim kątem)
odczytujesz w milimetrach położenie gniazdek, podejść wody i kratek wentylacyjnych — bez miarki i bez dotykania ściany.

Jedynym wzorcem skali jest sztywny marker ArUco o znanym rozmiarze fizycznym (domyślnie **180 × 180 mm**,
słownik `DICT_4X4_50`, ID 0) naklejony na mierzoną ścianę.

## Jak to działa

1. **Detekcja markera** — `cv2.aruco` z subpikselową korektą narożników (`CORNER_REFINE_SUBPIX` + dodatkowy
   przebieg `cv2.cornerSubPix`). Dokładność narożnika schodzi poniżej 1 piksela, co jest warunkiem poprawnej skali.
2. **Rektyfikacja perspektywy** — cztery narożniki markera i jego znana geometria dają homografię, która prostuje
   całą płaszczyznę ściany do rzutu prostopadłego (`cv2.getPerspectiveTransform` + `cv2.warpPerspective`).
   Obraz wynikowy ma **stałą skalę** (domyślnie 1 px = 0,5 mm), więc odległość w pikselach jest wprost odległością w mm.
3. **Pomiar** — klikasz punkty instalacyjne w oknie, aplikacja podaje DX, DY i odległość w linii prostej
   względem punktu bazowego.
4. **Eksport** — klawisz `s` zapisuje obraz z naniesionymi wymiarami oraz tabelę punktów w JSON.

> **Uwaga o fizyce pomiaru:** metoda zakłada, że mierzone punkty leżą w tej samej płaszczyźnie co marker.
> Punkt wystający ze ściany (np. rura przed licem tynku) albo marker przyklejony na wypukłości wprowadzi błąd
> paralaksy. Marker musi być idealnie płaski — naklejony na sztywną płytę, nie na pofalowaną kartkę.

## Instalacja

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Wymagane: Python 3.9+, `opencv-contrib-python` (zwykły `opencv-python` **nie zawiera** modułu `cv2.aruco`) oraz `numpy`.

## Przygotowanie markera

```bash
python tools/generate_marker.py --id 0 --size-mm 180 --dpi 300 --output marker_180mm.png
```

Wydrukuj **w skali 100 %** (bez „dopasuj do strony”), sprawdź linijką odległość między znacznikami kontrolnymi
i naklej na sztywną płytę. Błąd rozmiaru wydruku przenosi się wprost na wszystkie pomiary:
marker mniejszy o 1 % daje wyniki zawyżone o 1 % (na 2 metrach to 2 cm).

## Uruchomienie

```bash
python measure_wall.py --image sciana.jpg --marker-size-mm 180
```

Najważniejsze argumenty:

| Argument | Domyślnie | Znaczenie |
|---|---|---|
| `--image` | — | zdjęcie ściany (wymagane) |
| `--marker-size-mm` | `180` | fizyczny bok markera w mm |
| `--marker-id` | `0` | ID markera referencyjnego (`-1` = pierwszy znaleziony) |
| `--dictionary` | `DICT_4X4_50` | słownik ArUco |
| `--mm-per-px` | `0.5` | skala obrazu wyprostowanego (0.5 → 1 px = 0,5 mm) |
| `--y-up` | wył. | oś Y dodatnia w górę (domyślnie w dół, jak we współrzędnych obrazu) |
| `--output-image` | `wynik_pomiaru.png` | plik zapisywany klawiszem `s` |
| `--output-json` | `wymiary.json` | tabela punktów zapisywana klawiszem `s` |
| `--save-rectified` | — | dodatkowo zapisz czysty obraz wyprostowany |
| `--no-gui` | wył. | tryb wsadowy: detekcja + rektyfikacja + zapis, bez okna |
| `--max-output-px` | `5000` | limit boku obrazu wyprostowanego |
| `--view-max-px` | `1100` | rozmiar okna podglądu |

Pełna lista: `python measure_wall.py --help`.

## Obsługa okna pomiarowego

| Klawisz / przycisk | Działanie |
|---|---|
| **lewy przycisk myszy** | dodaj punkt instalacyjny — celownik + DX, DY i odległość w linii prostej |
| **prawy przycisk myszy** | ustaw nowy punkt bazowy (0,0), np. krawędź ściany lub linia posadzki |
| `r` | powrót punktu bazowego na lewy górny róg markera |
| `u` / `c` | cofnij ostatni punkt / wyczyść wszystkie |
| `+` / `-` / `0` | powiększenie do kursora / pomniejszenie / widok całości |
| **środkowy przycisk** (przeciąganie), strzałki | przesuwanie kadru |
| `s` | zapis `wynik_pomiaru.png` oraz `wymiary.json` |
| `q` lub `Esc` | wyjście |

Domyślny układ odniesienia: **(0,0) = lewy górny róg markera**, X rośnie w prawo, Y w dół (`--y-up` odwraca oś Y).
Zmiana punktu bazowego przelicza wszystkie zebrane punkty — możesz więc zmierzyć ścianę, a dopiero na końcu
wskazać krawędź, od której liczy stolarz.

## Format `wymiary.json`

```json
{
  "wersja": 1,
  "data": "2026-09-16T20:46:27",
  "metadane": {
    "zrodlo": "sciana.jpg",
    "marker_mm": 180.0,
    "mm_na_piksel": 0.5,
    "obraz_wyprostowany_px": [4319, 4851],
    "obraz_wyprostowany_mm": [2159.5, 2425.5],
    "osnowa": {
      "opis": "lewy gorny rog markera",
      "piksel_x": 1638.29, "piksel_y": 2170.38,
      "wzgledem_markera_x_mm": 0.0, "wzgledem_markera_y_mm": 0.0
    },
    "os_y": "w dol dodatnia",
    "liczba_punktow": 2
  },
  "punkty": [
    { "id": 1, "nazwa": "P1", "x_mm": 63.18, "y_mm": -423.69,
      "odleglosc_mm": 428.38, "piksel_x": 1764.66, "piksel_y": 1323.0 }
  ]
}
```

## Jak to przetestować

### A. Bez drukarki i aparatu — 2 minuty

Repozytorium generuje własne zdjęcie testowe o dokładnie znanej geometrii wraz z kluczem odpowiedzi:

```bash
python tools/make_test_photo.py --output sciana_testowa.jpg
python measure_wall.py --image sciana_testowa.jpg --marker-size-mm 180
```

W oknie klikaj w środki szarych kółek (przybliż klawiszem `+`, celownik zostaje cienki) i porównuj odczyty
z kluczem wypisanym przez generator:

```
punkt                 X [mm]    Y [mm]   odleglosc [mm]
gniazdko_1             300.0     250.0            390.5
gniazdko_2             700.0     250.0            743.3
podejscie_wody        -250.0     620.0            668.5
wentylacja             950.0    -300.0            996.2
puszka_dolna           120.0     980.0            987.3
```

Błąd 1–2 mm jest poprawny. Kilkanaście milimetrów oznacza, że kliknięcie poszło obok środka kółka —
przybliż mocniej. Setki milimetrów albo przeskalowanie wszystkich wyników o stały procent oznaczają
błąd w `--marker-size-mm`.

Ten sam potok bez klikania (przydatne w CI):

```bash
python tests/test_accuracy.py     # raport w konsoli, kod wyjścia 0/1
pytest tests/test_accuracy.py     # 3 testy: łańcuch przekształceń, dokładność, niezależność od skali
```

### B. Na własnym wydruku, bez ryzyka skalowania drukarki

Nie musisz trafić w skalę 100 % — wystarczy **zmierzyć, co faktycznie wyszło z drukarki**:

```bash
python tools/generate_marker.py --output marker.png     # wydrukuj jakkolwiek
```

Zmierz linijką bok czarnego kwadratu (między znacznikami kontrolnymi), np. wyszło 164 mm, i podaj tę wartość:

```bash
python measure_wall.py --image sciana.jpg --marker-size-mm 164
```

Marker naklej na sztywną płytę — pofalowana kartka psuje pomiar bardziej niż błąd wydruku.

### C. Prawdziwy test dokładności — referencja z miarki

To jedyny test, który mówi, czy narzędzie nadaje się na budowę:

1. Naklej marker na ścianę (lub płytę) i zaznacz ołówkiem dwa punkty oddalone o ok. 1–1,5 m.
2. Zmierz odległość między nimi miarką i zapisz — to twoja referencja.
3. Zrób zdjęcie **pod kątem** (15–30° od prostopadłej), z odległości ok. 2 m, tak żeby marker miał
   w kadrze co najmniej 150–200 px boku. Nie używaj zoomu cyfrowego.
4. Zmierz oba punkty w aplikacji i porównaj odległość w linii prostej z miarką.

Kryterium: **błąd poniżej 0,5 % zmierzonej odległości** (na 1 m to 5 mm). Powtórz z 3–4 zdjęć zrobionych
pod różnymi kątami — rozrzut wyników powie ci więcej niż pojedynczy pomiar.

Aplikacja sama ostrzega przed typowymi błędami ujęcia, jeszcze zanim otworzy okno:

```
Marker ID 0 (180.0 mm): bok sredni 67.2 px, ukos 2.21, rozdzielczosc zrodla 2.68 mm/px
UWAGA: Marker zajmuje tylko ~67 px boku - podejdz blizej lub uzyj wiekszej rozdzielczosci, bo precyzja spadnie.
UWAGA: Duzy ukos ujecia (stosunek bokow markera 2.21). Zrob zdjecie bardziej prostopadle do sciany.
```

Zdjęcie testowe z punktu A nie wywołuje żadnego ostrzeżenia (bok 205 px, ukos 1,22) — tak wygląda poprawne ujęcie.

Szybka kontrola samego zdjęcia, bez otwierania okna:

```bash
python measure_wall.py --image sciana.jpg --no-gui --save-rectified rektyfikacja.png
```

Obejrzyj `rektyfikacja.png`: jeśli fugi płytek albo krawędź ościeżnicy są na nim pionowe i poziome,
rektyfikacja się udała. Jeśli nadal „uciekają”, marker nie leży w płaszczyźnie ściany.

## Dokładność

Test `tests/test_accuracy.py` buduje syntetyczną ścianę o znanej geometrii, symuluje zdjęcie zrobione pod kątem
(homografia + rozmycie + szum) i przepuszcza je przez pełny potok:

```bash
python tests/test_accuracy.py        # raport w konsoli
pytest tests/test_accuracy.py        # jako test jednostkowy
```

Przy zdjęciu 3200 × 2400 px, w którym marker ma bok ~205 px (1 px zdjęcia ≈ 0,88 mm ściany), błąd punktów
oddalonych o 0,3–1,1 m od markera wynosi **0,5–1,9 mm**. To rząd wielkości jednego piksela zdjęcia źródłowego
przeniesiony na odległość — czyli tyle, ile teoria przewiduje.

Co realnie psuje wynik na budowie:

* **za mały marker w kadrze** — im mniej pikseli na bok markera, tym gorsza skala; podejdź bliżej albo powiększ marker,
* **duży ukos zdjęcia** — aplikacja ostrzega, gdy stosunek boków markera przekracza 1,6,
* **błąd wydruku markera** — patrz wyżej, przekłada się wprost proporcjonalnie,
* **punkty poza płaszczyzną ściany** — paralaksa,
* **obiektyw szerokokątny** — dystorsja beczkowata nie jest korygowana (homografia zakłada model dziurki).
  Przy skrajach kadru warto trzymać mierzone punkty bliżej środka zdjęcia.

## Struktura projektu

```
measure_wall.py             # punkt wejścia CLI
wallmeasure/
    detect.py               # detekcja markera ArUco, subpiksel, ocena jakości ujęcia
    rectify.py              # homografia, prostowanie perspektywy, skala mm/px
    session.py              # stan pomiaru: osnowa i punkty instalacyjne
    render.py               # celowniki, linie wymiarowe, panel informacyjny
    export.py               # zapis PNG + JSON
    ui.py                   # okno OpenCV HighGUI (zoom, przesuwanie, klawisze)
    cli.py                  # argumenty wiersza poleceń, spięcie potoku
tools/generate_marker.py    # generator markera do wydruku
tools/make_test_photo.py    # syntetyczne zdjęcie testowe z kluczem odpowiedzi
tests/test_accuracy.py      # test dokładności na syntetycznym zdjęciu
```

## Ograniczenia PoC

* Jeden marker na zdjęciu = jedna płaszczyzna. Ściana z narożnikiem wymaga osobnych zdjęć dla każdej płaszczyzny.
* Brak kalibracji obiektywu (dystorsja radialna nie jest usuwana).
* Punkty klikane są ręcznie — nie ma automatycznego wykrywania puszek elektrycznych.
* Napisy nanoszone na obraz są bez polskich znaków diakrytycznych: czcionki Hershey w OpenCV ich nie zawierają.
