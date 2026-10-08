# Wzbogacanie produktów B2B pod IdoSell

Skrypt `wzbogac_produkty.py` czyta CSV z Google Sheets i dla każdego produktu:

1. **szuka w internecie strony produktu** po kodzie producenta (`"AG0828" AEON`) i po EAN,
   pobiera ją i **sprawdza, czy ten kod lub EAN faktycznie na niej występuje**,
2. **pisze opis HTML** (~1000 znaków, `<h2>`, `<p>`, `<ul>`) przez OpenAI `gpt-4o-mini`
   **wyłącznie na podstawie nazwy i tekstu potwierdzonej strony** — model dodatkowo ocenia,
   czy strona opisuje dokładnie ten produkt (kod, DN, PN …),
3. **bierze zdjęcie** z potwierdzonej strony, a gdy go tam nie ma — z wyszukiwarki obrazów,
   ale za potwierdzone uznaje je tylko, jeśli kod/EAN jest w nazwie pliku, tytule albo na stronie, z której pochodzi,
4. **nadaje status**:

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

## 2. Klucze API

**OpenAI (wymagany)** — klucz z https://platform.openai.com/api-keys (konto musi mieć doładowane środki):

```powershell
# Windows (PowerShell) — tylko dla bieżącego okna
$env:OPENAI_API_KEY="sk-proj-...cały klucz..."
# albo na stałe (zadziała w NOWYCH oknach)
setx OPENAI_API_KEY "sk-proj-...cały klucz..."
```

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

Inne opcje: `--workers 3` (ile produktów naraz), `--sep ";"` (CSV ze średnikami), `--search serpapi`,
`--output inna_nazwa.csv`.

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

## Checkpointy i błędy

* Co **10 wierszy** wynik jest dopisywany do pliku roboczego i zrzucany na dysk.
* Po przerwaniu (Ctrl+C, zanik sieci, restart komputera) uruchom **tę samą komendę** — skrypt zacznie od
  pierwszego niezapisanego wiersza. Jeśli plik wejściowy zmienił kolejność, odmówi wznowienia.
* Timeout / rate limit / błąd sieci → odczekanie 5 s (przy kolejnych błędach 10 s, 15 s …) i ponowienie, do 6 prób.
  Jeśli wszystkie zawiodą, produkt trafia do akceptacji z odpowiednim powodem, a skrypt jedzie dalej.
* Zły klucz API albo brak środków na koncie OpenAI → skrypt zatrzymuje się (postęp zostaje zapisany).
* Pełny log trafia do `wzbogacanie.log`.

## Czego skrypt NIE gwarantuje

* Status `PEWNY` oznacza, że kod/EAN występuje na stronie źródłowej i AI nie znalazło niezgodności —
  to mocny filtr, ale nie zastępuje wyrywkowej kontroli. Przejrzyj kilkadziesiąt produktów `PEWNY` po teście.
* Dla produktów, których nie ma nigdzie w sieci, opis powstaje tylko z nazwy — zawsze trafiają do akceptacji.
* Zdjęcia pochodzą z cudzych stron — upewnij się, że masz prawo ich użyć (najbezpieczniej: materiały
  producenta/hurtowni).

## Czas i koszt (orientacyjnie)

* OpenAI `gpt-4o-mini`: tekst strony źródłowej zwiększa długość zapytania — rząd **kilkunastu–kilkudziesięciu USD
  za 28 000 produktów**.
* Każdy produkt to 1–2 wyszukiwania i pobranie kilku stron, więc pełny przebieg potrwa **od kilkudziesięciu godzin
  wzwyż**. Można przerywać i wznawiać. Przy DuckDuckGo część zapytań może być blokowana — do pełnego przebiegu
  stabilniejszy jest SerpApi.
