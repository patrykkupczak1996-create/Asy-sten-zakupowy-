# Wzbogacanie produktów B2B pod IdoSell

Skrypt `wzbogac_produkty.py` czyta CSV z Google Sheets i dla każdego produktu:

1. **szuka w internecie strony produktu** po kodzie producenta (`"AG0828" AEON`) i po EAN,
   pobiera ją i **sprawdza, czy ten kod lub EAN faktycznie na niej występuje**,
2. **pisze opis HTML** (~1000 znaków, `<h2>`, `<p>`, `<ul>`) przez Gemini `gemini-3.8-flash` (domyślnie) albo OpenAI `gpt-4o-mini`
   **wyłącznie na podstawie nazwy i tekstu potwierdzonej strony** — model dodatkowo ocenia,
   czy strona opisuje dokładnie ten produkt (kod, DN, PN …),
3. **bierze zdjęcie** z potwierdzonej strony, a gdy go tam nie ma — z wyszukiwarki obrazów,
   ale za potwierdzone uznaje je tylko, jeśli kod/EAN jest w nazwie pliku, tytule albo na stronie, z której pochodzi,
4. **pobiera zdjęcie na dysk** do folderu `zdjecia/` (np. `zdjecia/28846_AG0828.jpg`) i sprawdza, czy to
   naprawdę plik obrazu — jeśli link nie działa albo zamiast zdjęcia przychodzi strona błędu, produkt idzie do akceptacji,
5. **nadaje status**:

| Status | Kiedy | Gdzie trafia |
|---|---|---|
| `PEWNY` | strona potwierdzona kodem/EAN **i** AI potwierdza zgodność **i** zdjęcie potwierdzone | `produkty_wzbogacone_pewne.csv` |
| `DO_AKCEPTACJI` | cokolwiek z powyższych niespełnione — powód w kolumnie `Powod` | `produkty_wzbogacone_do_akceptacji.csv` |

Przykładowe powody: *nie znaleziono strony z tym kodem/EAN — opis tylko z nazwy*,
*AI: strona nie pasuje do produktu (DN150 zamiast DN200)*, *zdjęcie niepotwierdzone kodem/EAN*, *brak zdjęcia*.
Kolumna `Zrodlo_URL` pokazuje stronę, z której wzięto dane — każdy opis sprawdzisz jednym kliknięciem.

## 1. Instalacja (jednorazowo)

```powershell
cd wzbogacanie_produktow
py -m pip install -r requirements.txt
```

(Na Windows używaj `py` zamiast `python`/`pip`, jeśli te polecenia nie są rozpoznawane.)

### Aktualizacja skryptu

```powershell
py wzbogac_produkty.py --aktualizuj
```

Pobiera najnowszą wersję skryptu, `requirements.txt` i README z GitHuba i podmienia je w folderze
(Twoje pliki CSV, zdjęcia i postęp zostają). Jeśli zmieniły się biblioteki, skrypt napisze, żeby uruchomić
`py -m pip install -r requirements.txt`. Przy każdym starcie skrypt sam sprawdza, czy jest nowsza wersja,
i wypisuje ostrzeżenie.

## 2. Klucze API

**Gemini (domyślnie)** — klucz z https://aistudio.google.com/apikey (zaczyna się od `AIza`):

```powershell
# Windows (PowerShell) — tylko dla bieżącego okna
$env:GEMINI_API_KEY="AIza...cały klucz..."
# albo na stałe (zadziała w NOWYCH oknach)
setx GEMINI_API_KEY "AIza...cały klucz..."
```

Darmowy limit Gemini ma ograniczoną liczbę zapytań na minutę i dzień — przy pełnej bazie włącz płatności
w Google AI Studio, inaczej skrypt będzie często czekał na limit (to nie błąd, tylko wolniejsza praca).
Inny model Gemini ustawisz zmienną `GEMINI_MODEL`, np. `$env:GEMINI_MODEL="gemini-3.7-flash"`.

**Ollama (darmowo, lokalnie, bez klucza)** — model działa na Twoim komputerze; potrzebna karta graficzna
(np. RTX 3060 12 GB wystarcza na domyślny `gemma3:12b`):

1. Zainstaluj Ollamę z https://ollama.com/download i uruchom ją.
2. Pobierz model (jednorazowo, kilka GB): `ollama pull gemma3:12b`
3. Uruchamiaj skrypt z `--ai ollama`, np. `py wzbogac_produkty.py --input produkty.csv --ai ollama --limit 10`

Inny model: `$env:OLLAMA_MODEL="nazwa:tag"` (najpierw `ollama pull nazwa:tag`). Przy 6–8 GB VRAM wybierz mniejszy model.
Lokalny model jest darmowy, ale wolniejszy i zwykle słabszy po polsku — porównaj opisy w podglądzie.

**OpenAI (opcjonalnie, zamiast Gemini)** — klucz z https://platform.openai.com/api-keys, ustawiany jako
`OPENAI_API_KEY`; uruchamiasz wtedy skrypt z `--ai openai`.

Alternatywnie wpisz klucz na górze skryptu w sekcji `KONFIGURACJA`. Wtedy nie udostępniaj tego pliku.

**Wyszukiwarka** — parametr `--search`:

| Wartość | Klucz | Uwagi |
|---|---|---|
| `ddg` (domyślnie) | brak | darmowe; przy tysiącach zapytań DuckDuckGo potrafi czasowo blokować — skrypt odczekuje i ponawia |
| `serpapi` | `SERPAPI_API_KEY` | wyniki Google, najlepsza trafność i stabilność przy pełnym przebiegu, płatne (https://serpapi.com) |
| `google` | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | Google Custom Search JSON API; 100 zapytań/dzień gratis, może być niedostępne dla nowych kont |

## 3. Uruchomienie

1. W Google Sheets otwórz zakładkę z produktami → **Plik → Pobierz → Wartości rozdzielone przecinkami (.csv)**.
   Skopiuj plik do folderu `wzbogacanie_produktow` i nazwij go `produkty.csv` (sprawdź `dir *.csv` —
   Windows lubi tworzyć `produkty.csv.csv`).
2. Test na kilku produktach:
   ```powershell
   py wzbogac_produkty.py --input produkty.csv --limit 10
   ```
3. Pełny przebieg (ta sama komenda bez `--limit` — skrypt dopisze resztę):
   ```powershell
   py wzbogac_produkty.py --input produkty.csv
   ```

Powstają pliki:

* `produkty_wzbogacone.csv` — plik roboczy z postępem (nie edytuj go),
* **`zdjecia/`** — pobrane zdjęcia, nazwane `ID_KOD.jpg`; przejrzysz je w Eksploratorze Windows (widok „Duże ikony”).
  Kolumna `Zdjecie_plik` w CSV wskazuje plik danego produktu, a `Zdjecie_URL` — link, z którego go pobrano,
* `produkty_wzbogacone_pewne.csv` — produkty potwierdzone, gotowe do importu,
* `produkty_wzbogacone_do_akceptacji.csv` — produkty do przejrzenia (pierwsza kolumna `Akceptacja` jest pusta).
* **`produkty_wzbogacone_podglad.html`** — podgląd w przeglądarce (dwuklik w pliku): każdy produkt jako karta
  z wyrenderowanym opisem, miniaturą zdjęcia, linkiem do źródła i powodem, jeśli jest do akceptacji.
  Przyciski u góry filtrują *Pewne* / *Do akceptacji*.

**Excel / Google Sheets:** pliki CSV otworzysz w obu. W Google Sheets: Plik → Importuj (kody EAN zostają bez zmian).
W Excelu nie otwieraj CSV dwuklikiem, bo EAN zamieni się na `5,9E+12`. Użyj Dane → Z tekstu/CSV i ustaw kolumny
z kodami jako *Tekst*. Pliku do importu w IdoSell nie zapisuj z Excela, tylko używaj CSV wygenerowanego przez skrypt
albo pobranego z Google Sheets.

Pliki `_pewne` i `_do_akceptacji` są odświeżane po każdym uruchomieniu (także po Ctrl+C), więc
możesz zacząć przeglądać produkty, zanim skończy się cała baza.

Inne opcje: `--bez-pobierania` (tylko linki do zdjęć, bez zapisywania plików), `--workers 3` (ile produktów naraz), `--sep ";"` (CSV ze średnikami), `--search serpapi`,
`--output inna_nazwa.csv`.

## Strony producentów (najlepsze zdjęcia i dane)

Skrypt **najpierw** szuka produktu na stronach producenta (`site:aeon-sale.com AG0828` itd.), dopiero potem
w reszcie internetu. Ze strony producenta bierze też zdjęcie serii bez kodu w nazwie pliku (producent często ma
jedno zdjęcie na wszystkie DN). Listę stron ustawiasz na górze skryptu w `PRODUCER_SITES`:

```python
PRODUCER_SITES = {
    # "HAWLE": ["hawle.pl"],   # producent, którego strona pokazuje kod lub EAN produktu
}
```

Wpisuj tylko strony, na których przy produkcie widać kod producenta lub EAN — inaczej skrypt nie potwierdzi
na nich produktu, a tylko wydłuży szukanie. (Sklep AEON aeon-sale.com kodów nie pokazuje, więc go tu nie ma.)

Zaraz po stronach producenta skrypt sprawdza hurtownie z rzetelnymi kartami produktów (kod producenta + EAN),
ustawione w `TRUSTED_SITES` (domyślnie `cetel-hurtownia.pl`, `mateomarket.pl`). Na tych stronach szuka po kodzie
producenta; EAN sprawdza w ogólnym wyszukiwaniu.

**Sklep producenta bez kodów (np. AEON)** — w `PRODUCER_SEARCH` jest adres wewnętrznej wyszukiwarki sklepu
(`https://aeon-sale.com/?s={q}&post_type=product`). Gdy hurtownia potwierdzi produkt po kodzie, model AI
wyciąga z jej karty pełną nazwę serii (np. „zasuwa gaz OptiValve typ A kołnierzowa”), skrypt wpisuje ją
w wyszukiwarkę sklepu producenta, a model wybiera z wyników **jedną** pozycję tej samej serii (rodzaj,
gaz/woda, sposób połączenia, typ, F4/F5). Zdjęcie z tej karty ma pierwszeństwo przed innymi; gdy nic nie
pasuje na pewno, skrypt bierze zdjęcie z innych stron.

**Bezpośrednie wyszukiwanie w hurtowni** — `DIRECT_SEARCH` może zawierać adres wyszukiwarki hurtowni; skrypt
wpisuje tam kod i bierze kartę z tym kodem w adresie, bez DuckDuckGo. Onninen blokuje automatyczne pobieranie
(HTTP 403), więc domyślnie lista jest pusta. Czy wyszukiwarka danej hurtowni odpowiada skryptowi, sprawdzisz:
`py wzbogac_produkty.py --test-wyszukiwarki AG0828`. Strony, które kilka razy z rzędu odmówią dostępu (403),
są pomijane do końca przebiegu.

Strony, które nakładają **znak wodny** na zdjęcia, wpisz w `WATERMARK_SITES` (domyślnie `onninen.pl`).
Skrypt bierze z nich tylko tekst (potwierdzenie kodu/EAN i dane do opisu), a zdjęcie od razu szuka gdzie indziej
— np. w innej hurtowni czy sklepie z tym samym kodem.

Zdjęcia mniejsze niż 400 px (krótszy bok) są odrzucane jako słabej jakości.

## Kontrola zdjęć (znak wodny, logo, czy to produkt)

Już przy wyborze zdjęcia skrypt odrzuca pliki z „logo/banner/icon” w adresie, ikonki (< 150 px) i obrazki
o proporcjach banera (szersze niż 1,9:1). Znaku wodnego nie da się jednak wykryć regułami, więc jest drugi etap:
model wizyjny w Ollamie ogląda każde zdjęcie.

```powershell
ollama pull qwen2.5vl:7b          # jednorazowo, jeśli go nie masz
py wzbogac_produkty.py --sprawdz-zdjecia
```

* Sprawdza tylko zdjęcia, których jeszcze nie sprawdził — można przerywać (Ctrl+C) i wznawiać.
* **Gdy zdjęcie zostanie odrzucone, skrypt szuka zastępczego** (ze strony źródłowej i z wyszukiwarki obrazów),
  ogląda je tym samym modelem i podstawia pierwsze bez znaku wodnego. Jeśli żadne nie przejdzie (do 3 prób),
  produkt zostaje **bez zdjęcia** i trafia do akceptacji — lepiej brak zdjęcia niż cudzy znak wodny.
  Zdjęcie zastępcze niepotwierdzone kodem/EAN też trafia do akceptacji.
* Odrzuca: znak wodny, nałożone logo sklepu/firmy, adres www lub telefon na zdjęciu, a także obrazki,
  które nie są zdjęciem produktu (logo, baner, rysunek, tabela, inny przedmiot).
* **Produkt jest PEWNY tylko wtedy, gdy jego zdjęcie przeszło tę kontrolę.** Dopóki jej nie uruchomisz,
  produkty ze zdjęciem trafiają do akceptacji z powodem „zdjęcie niesprawdzone”.
* Wyniki są w `produkty_wzbogacone_kontrola_zdjec.csv`; pliki `_pewne`, `_do_akceptacji` i podgląd
  odświeżają się automatycznie.
* Uruchamiaj ją **po** przetwarzaniu opisów (albo w przerwie) — karta graficzna z 12 GB nie pomieści naraz
  modelu do opisów i modelu do zdjęć. Inny model wizyjny: `$env:OLLAMA_VISION_MODEL="nazwa:tag"`.
* Model może się czasem pomylić (np. nie zauważyć bardzo bladego znaku wodnego) — przy akceptacji
  rzuć okiem na zdjęcia w podglądzie.

## 4. Akceptacja niepewnych produktów

1. Wgraj `produkty_wzbogacone_do_akceptacji.csv` do Google Sheets (Plik → Importuj).
2. Przejrzyj kolumny `Powod`, `Opis_HTML`, `Zdjecie_URL`. Popraw opis/zdjęcie, jeśli trzeba,
   i wpisz **`TAK`** w kolumnie `Akceptacja` przy produktach, które mają iść do sklepu.
   Wiersze bez `TAK` zostaną pominięte.
3. Pobierz arkusz jako CSV (np. `zaakceptowane.csv`) do folderu ze skryptem i uruchom:
   ```powershell
   py wzbogac_produkty.py --zatwierdz zaakceptowane.csv
   ```
4. Powstaje **`produkty_wzbogacone_do_importu.csv`** = produkty pewne + zaakceptowane przez Ciebie
   (status `ZAAKCEPTOWANY`). Ten plik importujesz do IdoSell.

Jeśli po akceptacji uruchomisz przetwarzanie ponownie, skrypt nie nadpisze pliku, w którym są już Twoje
oznaczenia `TAK` — nowa lista trafi do `produkty_wzbogacone_do_akceptacji_nowe.csv`.

## Test na próbce całej bazy

Pierwsze wiersze pliku to zwykle jeden producent — test na nich nie mówi, jak skrypt poradzi sobie z resztą.
Próbka proporcjonalna do producentów (najwięcej z największych, min. 1 z każdego z 15 największych):

```powershell
py wzbogac_produkty.py --input produkty.csv --utworz-probke 50
py wzbogac_produkty.py --input produkty_probka.csv --output wyniki_probka.csv
py wzbogac_produkty.py --sprawdz-zdjecia --output wyniki_probka.csv
start wyniki_probka_podglad.html
```

## Checkpointy i błędy

* Co **10 wierszy** wynik jest dopisywany do pliku roboczego i zrzucany na dysk.
* Po przerwaniu (Ctrl+C, zanik sieci, restart komputera) uruchom **tę samą komendę** — skrypt zacznie od
  pierwszego niezapisanego wiersza. Jeśli plik wejściowy zmienił kolejność, odmówi wznowienia.
* Timeout / rate limit / błąd sieci → odczekanie 5 s (przy kolejnych błędach 10 s, 15 s …) i ponowienie, do 6 prób.
  Jeśli wszystkie zawiodą, produkt trafia do akceptacji z odpowiednim powodem, a skrypt jedzie dalej.
* Zły klucz API albo brak środków na koncie Gemini/OpenAI → skrypt zatrzymuje się (postęp zostaje zapisany).
* Pełny log trafia do `wzbogacanie.log`.

## Zdjęcia a import do IdoSell

* Import przez CSV: IdoSell pobiera zdjęcie z linku w `Zdjecie_URL`. To, że skrypt pobrał zdjęcie, oznacza,
  że link działał w chwili przetwarzania.
* Jeśli wolisz nie zależeć od cudzych stron, wgraj pliki z folderu `zdjecia/` na własny serwer/FTP sklepu
  i podmień linki, albo dodaj je ręcznie w panelu IdoSell.
* Jeśli w pliku do akceptacji wpiszesz inny link w `Zdjecie_URL`, plik w `zdjecia/` zostaje stary —
  do importu liczy się link.

## Czego skrypt NIE gwarantuje

* Status `PEWNY` oznacza, że kod/EAN występuje na stronie źródłowej i AI nie znalazło niezgodności —
  to mocny filtr, ale nie zastępuje wyrywkowej kontroli. Przejrzyj kilkadziesiąt produktów `PEWNY` po teście.
* Dla produktów, których nie ma nigdzie w sieci, opis powstaje tylko z nazwy — zawsze trafiają do akceptacji.
* Zdjęcia pochodzą z cudzych stron — upewnij się, że masz prawo ich użyć (najbezpieczniej: materiały
  producenta/hurtowni).

## Czas i koszt (orientacyjnie)

* Koszt AI zależy od modelu i cennika dostawcy — sprawdź go po teście na 10 produktach w panelu Google AI Studio
  (lub OpenAI) i przelicz na 28 000. Tekst strony źródłowej wydłuża każde zapytanie.
* Każdy produkt to 1–2 wyszukiwania i pobranie kilku stron, więc pełny przebieg potrwa **od kilkudziesięciu godzin
  wzwyż**. Można przerywać i wznawiać. Przy DuckDuckGo część zapytań może być blokowana — do pełnego przebiegu
  stabilniejszy jest SerpApi.
