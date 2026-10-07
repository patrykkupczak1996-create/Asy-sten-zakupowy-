# Wzbogacanie produktów B2B pod IdoSell

Skrypt `wzbogac_produkty.py` czyta CSV z Google Sheets i dla każdego produktu dopisuje dwie kolumny:

* **`Opis_HTML`** — opis SEO (~1000 znaków, czysty HTML: `<h2>`, `<p>`, `<ul>`) wygenerowany przez OpenAI `gpt-4o-mini`,
  z rozwiniętymi skrótami (DN80, PN16, KOŁN. …) i opisem zastosowania,
* **`Zdjecie_URL`** — bezpośredni link do pierwszego znalezionego obrazka `.jpg`/`.png`
  (szuka `"Producent kod_producenta"`, np. `AEON AG0828`, a gdy nic nie znajdzie — po kodzie EAN).

Wymagane kolumny wejściowe: `@id`, `@code_producer`, `/producer@name`, `/sizes/size@code_producer`,
`/navigation/site/menu/item@textid[pol]`, `/description/name[pol]`. Pozostałe kolumny są przepisywane bez zmian.

## 1. Instalacja (jednorazowo)

```bash
cd wzbogacanie_produktow
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Klucze API

**OpenAI (wymagany)** — klucz z https://platform.openai.com/api-keys. Ustaw go w terminalu przed uruchomieniem:

```bash
# Windows (PowerShell)
$env:OPENAI_API_KEY="sk-proj-..."
# macOS / Linux
export OPENAI_API_KEY="sk-proj-..."
```

Alternatywnie wpisz go na górze skryptu w sekcji `KONFIGURACJA`:
`OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-proj-...")`. Wtedy nie udostępniaj tego pliku.

**Wyszukiwarka zdjęć** — parametr `--image-source`:

| Wartość | Klucz | Uwagi |
|---|---|---|
| `ddg` (domyślnie) | brak | darmowe; przy tysiącach zapytań DuckDuckGo potrafi czasowo blokować — skrypt wtedy odczekuje i ponawia |
| `serpapi` | `SERPAPI_API_KEY` | Google Images, najlepsza trafność, płatne (https://serpapi.com) |
| `google` | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | Google Custom Search JSON API; 100 zapytań/dzień gratis, może być niedostępne dla nowych kont |

Klucze ustawiasz tak samo jak klucz OpenAI (zmienna środowiskowa albo sekcja `KONFIGURACJA`).

## 3. Uruchomienie

Najpierw test na 5 produktach i obejrzenie wyniku:

```bash
python wzbogac_produkty.py --input produkty.csv --output produkty_wzbogacone.csv --limit 5
```

Potem pełny przebieg (ten sam plik wyjściowy — skrypt dopisze resztę):

```bash
python wzbogac_produkty.py --input produkty.csv --output produkty_wzbogacone.csv
```

Zamiast pliku możesz podać link eksportu z Google Sheets:
`--input "https://docs.google.com/spreadsheets/d/<ID_ARKUSZA>/export?format=csv&gid=0"`
(arkusz musi być dostępny dla każdego z linkiem). Przy wznawianiu dane w arkuszu nie mogą zmieniać kolejności.
Parametr `gid` to numer zakładki — widać go w pasku adresu po kliknięciu zakładki (`...#gid=1964847349`).

Jeśli nie chcesz udostępniać arkusza, pobierz go ręcznie: **Plik → Pobierz → Wartości rozdzielone przecinkami (.csv)**
(pobiera aktualnie otwartą zakładkę) i podaj ścieżkę do pliku w `--input`.

Inne opcje: `--workers 3` (ile produktów naraz, domyślnie 3), `--no-images` (tylko opisy),
`--sep ";"` (jeśli CSV używa średników), `--image-source serpapi`.

## Checkpointy i błędy

* Co **10 wierszy** wynik jest dopisywany do pliku wyjściowego i zrzucany na dysk.
* Po przerwaniu (Ctrl+C, zanik sieci, restart komputera) uruchom **tę samą komendę** — skrypt policzy wiersze
  w pliku wyjściowym i zacznie od kolejnego. Najwyżej jedna niedokończona paczka (≤10 wierszy) zostanie
  wygenerowana ponownie. Jeśli plik wejściowy zmienił kolejność, skrypt odmówi wznowienia, żeby nie pomieszać danych.
* Timeout / rate limit / błąd sieci → odczekanie 5 s (przy kolejnych błędach 10 s, 15 s …) i ponowienie, do 6 prób.
  Jeśli wszystkie zawiodą, komórka zostaje pusta, a skrypt jedzie dalej.
* Zły klucz API albo brak środków na koncie OpenAI → skrypt zatrzymuje się z komunikatem (dotychczasowy postęp
  zostaje zapisany).
* Pełny log trafia do `wzbogacanie.log`.

## Czas i koszt (orientacyjnie)

* OpenAI `gpt-4o-mini`: ok. 0,0004 USD na produkt, czyli **ok. 10–15 USD za 28 000 produktów**.
* Czas: kilka sekund na produkt; przy 3 wątkach 28 000 produktów to rząd **15–30 godzin**. Można przerywać i wznawiać.

## Import do IdoSell

Plik wynikowy zawiera wszystkie kolumny wejściowe plus `Opis_HTML` i `Zdjecie_URL`. Przed importem zmapuj je
na odpowiednie pola (np. długi opis produktu i zdjęcie z URL) albo zmień nazwy nagłówków na ścieżki z eksportu
IdoSell. Wiersze z pustym `Zdjecie_URL` warto uzupełnić ręcznie. Zdjęcia z wyszukiwarki pochodzą z cudzych
stron — upewnij się, że masz prawo ich użyć (najbezpieczniej: zdjęcia z materiałów producenta/hurtowni).
