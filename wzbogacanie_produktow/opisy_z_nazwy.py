"""Krótkie opisy produktów z samej nazwy — lokalnie (Ollama/Bielik), bez kosztów.

Wejście: arkusz Google pobrany jako CSV (Plik > Pobierz > CSV) z kolumnami Nazwa, Producent, @id, Opis nowy, Status.
Skrypt uzupełnia wiersze bez statusu: pisze 2–3 zdania po polsku w stylu opisów już gotowych w arkuszu
(podobne produkty idą do modelu jako wzór), a każdy opis sprawdza:
  - liczby tylko takie, które są w nazwie (żadnych dopowiedzianych parametrów),
  - bez norm, atestów, gwarancji, „idealny”, „niezawodny” itd.,
  - bez obcych słów i znaków, bez cen, linków, list, rozsądna długość.
Opis, który 3 razy nie przejdzie kontroli, dostaje status RĘCZNIE.
Postęp zapisuje się co kilka wierszy — przerwany przebieg wznawia się od miejsca, w którym stanął.

Uruchomienie:
  py opisy_z_nazwy.py arkusz_opisy.csv            # uzupełnij brakujące (najpierw znane i duże marki)
  py opisy_z_nazwy.py opisy_baza.csv --baza produkty.csv --wzory arkusz_opisy.csv
                                                  # cała baza sklepu, wzory stylu z arkusza
  py opisy_z_nazwy.py arkusz_opisy.csv --sprawdz  # tylko sprawdź gotowe opisy (bez AI)
Potem:
  py wzbogac_produkty.py --output opisy_wszystkie.csv --z-arkusza arkusz_opisy.csv
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

import requests

import wzbogac_produkty as wp

COL_NAME, COL_PRODUCER, COL_DESC, COL_STATUS = "Nazwa", "Producent", "Opis nowy", "Status"
COL_ADDED = "Dodano"
SAVE_EVERY = 10
MAX_TRIES = 3
EXAMPLES = 4
MIN_CHARS, MAX_CHARS = 120, 520

RULES = """Jesteś redaktorem polskiego sklepu internetowego hurtowni instalacyjnej (woda, gaz, ogrzewanie,
kanalizacja, sanitariaty, wentylacja). Piszesz krótki opis produktu na podstawie jego nazwy.

ZASADY:
1. Poprawna, prosta polszczyzna. Żadnych słów obcych ani zmyślonych.
2. Jeden akapit, 2–3 zdania, 180–450 znaków. Bez list, nagłówków, wykrzykników.
3. Czym jest produkt, do czego służy, gdzie się go stosuje, plus parametry z nazwy opisane słowami.
4. Liczby, wymiary, materiały, napięcia, ciśnienia — WYŁĄCZNIE te, które są w nazwie. Nie dopisuj żadnych innych.
5. Nie pisz o normach, atestach, certyfikatach, dopuszczeniach, gwarancji, trwałości, odporności na korozję.
6. Bez słów: idealny, kluczowy, niezawodny, gwarantuje, wysokiej jakości, innowacyjny, doskonały, nowoczesny.
7. Bez cen, kodów, EAN, linków. Nie przepisuj nazwy wielkimi literami.
8. Rozwiń skróty: GW = gwint wewnętrzny, GZ = gwint zewnętrzny, DN = średnica nominalna, PN = ciśnienie
   nominalne, C.O. = centralne ogrzewanie, C.W.U. = ciepła woda użytkowa, NC/NO = normalnie zamknięty/otwarty.
9. Jeśli nie wiesz dokładnie, czym jest model, pisz ogólnie o typie produktu, ale prawdziwie.
Odpowiedz wyłącznie tekstem opisu."""

BANNED = re.compile(
    r"idealn|kluczow|niezawodn|gwarant|wysokiej jako|innowac|doskona|nowoczesn|\bnorm[aąyie]|\bnormą|"
    r"\bPN-EN\b|\bEN \d|atest|certyfik|dopuszcz|gwarancj|bezawaryjn|korozj|na lata|trwał|ekskluzywn|"
    r"najlepsz|wyjątkow|perfekcyj|plumbing|\bseries\b|\bdesign\b",
    re.IGNORECASE)
ALLOWED_CHARS = re.compile(r"[^0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż \t.,;:()\"'’„”/+×x°%–\-=<>²³µΩ]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".").lstrip("0") or "0" for n in NUMBER_RE.findall(text)}


def problems(desc: str, name: str) -> list[str]:
    """Powody odrzucenia opisu ([] = opis w porządku)."""
    desc = str(desc or "").strip()
    if not desc:
        return ["pusty"]
    out = wp.description_quality_problems(f"<p>{desc}</p>", name)
    if "\n" in desc:
        out.append("kilka akapitów")
    if not MIN_CHARS <= len(desc) <= MAX_CHARS:
        out.append(f"długość {len(desc)} znaków")
    flat = re.sub(r"\b(np|m\.in|kl|ok|tzw|tj|itp|itd|wg|nr|ew|max|min|zw|gw|gz)\.", r"\1", desc, flags=re.I)
    sentences = len(re.findall(r"[.!?](?:\s|$)", flat))
    if not 2 <= sentences <= 4:
        out.append(f"{sentences} zdań")
    extra = numbers(desc) - numbers(name)
    if extra:
        out.append("liczby spoza nazwy: " + ", ".join(sorted(extra))[:40])
    hit = BANNED.search(desc)
    if hit:
        out.append(f"zakazane słowo „{hit.group(0)}”")
    odd = [c for c in ALLOWED_CHARS.findall(desc) if c not in name]
    if odd:
        out.append("dziwne znaki: " + "".join(dict.fromkeys(odd))[:10])
    if desc[:25].isupper():
        out.append("zaczyna się wielkimi literami")
    return out


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-ząćęłńóśźż]{3,}", str(text).lower())}


def pick_examples(name: str, pool: list[tuple[set[str], str, str]]) -> list[tuple[str, str]]:
    """Najbardziej podobne gotowe opisy (wspólne słowa nazwy) — model naśladuje ich styl i zakres treści."""
    w = words(name)
    scored = sorted(pool, key=lambda p: len(w & p[0]) / (len(w | p[0]) or 1), reverse=True)
    return [(n, d) for _, n, d in scored[:EXAMPLES]]


def ask(model: str, prompt: str, temperature: float) -> str:
    host = wp.AI["base_url"].removesuffix("/v1")
    resp = requests.post(host + "/api/chat", timeout=wp.AI.get("timeout", 600), json={
        "model": model, "stream": False,
        "messages": [{"role": "system", "content": RULES}, {"role": "user", "content": prompt}],
        "options": {"temperature": temperature, "num_ctx": 4096, "num_predict": 400},
    })
    resp.raise_for_status()
    text = ((resp.json().get("message") or {}).get("content") or "").strip()
    return re.sub(r"\s+", " ", text.strip('"„” ')).strip()


def write_description(name: str, producer: str, examples: list[tuple[str, str]]) -> tuple[str, list[str]]:
    shots = "\n\n".join(f"Nazwa: {n}\nOpis: {d}" for n, d in examples)
    base = f"Przykłady dobrych opisów:\n\n{shots}\n\nNapisz opis w tym samym stylu.\nNazwa: {name}\n" \
           f"Producent: {producer}\nOpis:"
    prompt, last = base, []
    for attempt in range(MAX_TRIES):
        desc = ask(wp.AI["models"][0].strip(), prompt, 0.2 + 0.15 * attempt)
        last = problems(desc, name)
        if not last:
            return desc, []
        prompt = base + f"\n\n(Poprzednia wersja odrzucona: {'; '.join(last)}. Popraw to.)"
    return "", last


# Najbardziej znane marki instalacyjne — idą pierwsze (w tej kolejności), potem reszta wg liczby produktów.
TOP_BRANDS = ["GEBERIT", "GROHE", "HANSGROHE", "VIESSMANN", "VAILLANT", "BOSCH", "BUDERUS", "JUNKERS",
              "DE DIETRICH", "DANFOSS", "GRUNDFOS", "WILO", "UPONOR", "REHAU", "KAN", "WAVIN", "TECE", "KOŁO",
              "CERSANIT", "RADAWAY", "OVENTROP", "HERZ", "CALEFFI", "HONEYWELL", "RESIDEO", "IMMERGAS",
              "TERMET", "ARISTON", "KOSPEL", "GALMET", "PURMO", "ZEHNDER", "REFLEX", "FLAMCO", "MEIBES",
              "AFRISO", "ARMACELL", "FERRO", "OMNIRES", "DEANTE", "ROCA", "VILLEROY", "LAUFEN", "IDEAL STANDARD"]


def brand_rank(producer: str) -> int:
    p = str(producer).upper()
    for n, b in enumerate(TOP_BRANDS):
        if p == b or p.startswith(b + " ") or p.startswith(b + "-"):
            return n
    return len(TOP_BRANDS)


def order_by_brand(df, todo: list[int]) -> list[int]:
    """Najpierw znane marki, potem producenci z największą liczbą produktów; w obrębie marki kolejność pliku."""
    counts = df.loc[todo, COL_PRODUCER].str.upper().value_counts()
    ordered = sorted(todo, key=lambda i: (brand_rank(df.at[i, COL_PRODUCER]),
                                          -counts.get(str(df.at[i, COL_PRODUCER]).upper(), 0),
                                          str(df.at[i, COL_PRODUCER]).upper(), i))
    seen = []
    for i in ordered:
        b = str(df.at[i, COL_PRODUCER]).upper() or "(bez producenta)"
        if b not in seen:
            seen.append(b)
    print("Kolejność marek: " + ", ".join(f"{b} ({counts.get(b, 0)})" for b in seen[:15])
          + (" …" if len(seen) > 15 else ""))
    return ordered


BASE_COLUMNS = {  # rola -> możliwe nagłówki (eksport IdoSell albo arkusz Google)
    "id": ["@id", "id", "id towaru", "id produktu"],
    "nazwa": ["/description/name[pol]", "nazwa", "nazwa produktu", "name"],
    "producent": ["/producer@name", "producent", "marka"],
}


def read_base(base: str):
    """Baza produktów z pliku CSV albo linku do arkusza Google; kolumny rozpoznawane po nagłówkach."""
    import io
    import pandas as pd
    if re.match(r"https?://", base):
        import ceny
        resp = requests.get(ceny.google_sheet_csv_url(base), timeout=300)
        resp.raise_for_status()
        text = resp.content.decode("utf-8-sig", errors="replace")
        if text.lstrip().lower().startswith(("<!doctype", "<html")):
            sys.exit("Arkusz Google nie jest udostępniony do odczytu. Udostępnij → „Każda osoba mająca link” "
                     "(Przeglądający) — albo pobierz go jako CSV i wskaż plik.")
        src = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=None, engine="python")
    else:
        if not os.path.exists(base):
            sys.exit(f"Nie ma pliku z produktami: {base}")
        src = pd.read_csv(base, dtype=str, keep_default_na=False, sep=None, engine="python", encoding="utf-8-sig")
    headers = {str(h).strip().lower(): h for h in src.columns}
    cols = {role: next((headers[n] for n in names if n in headers), None) for role, names in BASE_COLUMNS.items()}
    if not cols["id"] or not cols["nazwa"]:
        sys.exit(f"W pliku z produktami nie znalazłem kolumn z ID i nazwą. Kolumny: {', '.join(src.columns)[:300]}")
    df = pd.DataFrame({wp.COL_ID: src[cols["id"]].astype(str).str.strip(), COL_NAME: src[cols["nazwa"]],
                       COL_PRODUCER: src[cols["producent"]] if cols["producent"] else ""})
    return df[(df[wp.COL_ID] != "") & (df[COL_NAME].str.strip() != "")].drop_duplicates(wp.COL_ID)


def build_from_base(base: str, out: str, skip_ids: set[str]) -> None:
    """Plik roboczy (@id, Nazwa, Producent, Opis nowy, Status): tworzy go z bazy albo dopisuje nowe produkty."""
    import pandas as pd
    new = read_base(base)
    new = new[~new[wp.COL_ID].isin(skip_ids)]
    if os.path.exists(out):
        cur = wp.read_csv(out)
        new = new[~new[wp.COL_ID].isin(set(cur[wp.COL_ID].astype(str)))]
        if new.empty:
            print(f"{out}: bez nowych produktów z {base} ({len(cur)} w pliku roboczym).")
            return
        new = new.assign(**{COL_DESC: "", COL_STATUS: ""})
        save(pd.concat([cur, new], ignore_index=True).fillna(""), out)
        print(f"Dopisałem do {out} {len(new)} nowych produktów z {base}.")
        return
    new = new.assign(**{COL_DESC: "", COL_STATUS: ""})
    save(new, out)
    print(f"Utworzyłem {out}: {len(new)} produktów z {base} (pominięte już opisane w arkuszu: {len(skip_ids)}).")


def preview(df, path: str, limit: int = 300) -> str:
    """Strona HTML z ostatnio dopisanymi opisami (najnowsze na górze) — do szybkiego przejrzenia."""
    import html
    import webbrowser
    rows = df[df.get(COL_ADDED, "").astype(str).str.strip() != ""] if COL_ADDED in df.columns else df.iloc[0:0]
    rows = rows.sort_values(COL_ADDED, ascending=False).head(limit)
    trs = "".join(
        f"<tr class='{'r' if r[COL_STATUS] == 'RĘCZNIE' else ''}'><td>{html.escape(str(r[COL_ADDED]))}</td>"
        f"<td>{html.escape(str(r[COL_PRODUCER]))}</td><td><b>{html.escape(str(r[COL_NAME]))}</b></td>"
        f"<td>{html.escape(str(r[COL_DESC])) or '<i>do uzupełnienia ręcznie</i>'}</td></tr>"
        for _, r in rows.iterrows())
    out = os.path.splitext(path)[0] + "_podglad.html"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("<!doctype html><meta charset='utf-8'><title>Nowe opisy</title><style>"
                 "body{font-family:Segoe UI,Arial;margin:20px}table{border-collapse:collapse;width:100%}"
                 "td,th{border:1px solid #ccc;padding:6px;vertical-align:top;font-size:14px}"
                 "th{background:#eee}tr.r{background:#fde}td:nth-child(4){max-width:600px}</style>"
                 f"<h2>Ostatnio dopisane opisy ({len(rows)})</h2><table><tr><th>Kiedy</th><th>Producent</th>"
                 f"<th>Nazwa</th><th>Opis</th></tr>{trs}</table>")
    try:
        webbrowser.open("file:///" + os.path.abspath(out).replace(os.sep, "/"))
    except Exception:
        pass
    return out


BIELIK = "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M"


def pick_model() -> None:
    """Bielik, jeśli jest pobrany; inaczej model z OLLAMA_MODEL albo pierwszy z listy preferowanych."""
    settings = wp.AI_PROVIDERS["ollama"]
    installed = wp.ollama_installed(settings)
    names = installed | {n.removesuffix(":latest") for n in installed}
    if not installed:
        sys.exit("Ollama działa, ale nie widzi ŻADNEGO pobranego modelu. Najczęściej to znaczy, że modele leżą "
                 "w innym folderze (np. na dysku D:), a Ollama została uruchomiona bez ustawienia OLLAMA_MODELS.\n"
                 "Sprawdź w PowerShell:  [Environment]::GetEnvironmentVariable('OLLAMA_MODELS','User')\n"
                 "Potem zamknij Ollamę (ikona w zasobniku → Quit Ollama) i uruchom ją ponownie z menu Start; "
                 "„ollama list” powinno pokazać bielika. Jeśli modelu naprawdę nie ma:  ollama pull " + BIELIK)
    wanted = [m.strip() for m in os.getenv("OLLAMA_MODEL", "").split(",") if m.strip()]
    for m in [BIELIK] + wanted + wp.OLLAMA_PREFERRED:
        if m in names:
            os.environ["OLLAMA_MODEL"] = m
            settings["models"] = [m]
            return
    sys.exit(f"Brak modelu {BIELIK}. Pobierz go:  ollama pull {BIELIK}\n"
             f"Pobrane modele: {', '.join(sorted(installed))}")


def save(df, path: str) -> None:
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Opisy produktów z nazwy (Ollama/Bielik) dla arkusza Google.")
    ap.add_argument("plik", help="arkusz pobrany jako CSV")
    ap.add_argument("--sprawdz", action="store_true", help="tylko sprawdź gotowe opisy, bez AI")
    ap.add_argument("--popraw-zle", action="store_true",
                    help="opisy, które nie przechodzą kontroli, napisz od nowa")
    ap.add_argument("--limit", type=int, default=0, help="najwyżej tyle wierszy (do testów)")
    ap.add_argument("--podglad", action="store_true",
                    help="otwórz w przeglądarce ostatnio dopisane opisy (bez AI) i zakończ")
    ap.add_argument("--kolejnosc", choices=["marki", "plik"], default="marki",
                    help="marki = najpierw znane i największe marki (domyślnie), plik = po kolei")
    ap.add_argument("--baza", metavar="CSV",
                    help="cała baza z IdoSell (produkty.csv): gdy pliku roboczego nie ma, utwórz go z bazy")
    ap.add_argument("--wzory", metavar="CSV",
                    help="arkusz z gotowymi opisami (arkusz_opisy.csv) — wzory stylu; jego produkty są pomijane")
    args = ap.parse_args()

    extra = wp.read_csv(args.wzory) if args.wzory else None
    if args.baza:
        skip = set()
        if extra is not None and wp.COL_ID in extra.columns and COL_STATUS in extra.columns:
            skip = set(extra.loc[extra[COL_STATUS].str.strip() != "", wp.COL_ID].astype(str))
        build_from_base(args.baza, args.plik, skip)

    df = wp.read_csv(args.plik)
    print(f"Plik {args.plik}: {len(df)} wierszy, kolumny: {', '.join(df.columns)}")
    missing = [c for c in (COL_NAME, COL_DESC, COL_STATUS) if c not in df.columns]
    if missing:
        sys.exit(f"W pliku brak kolumn: {', '.join(missing)}. To nie jest aktualny arkusz z kolumnami "
                 "„Opis nowy” i „Status”. Usuń arkusz_opisy.csv i pobierz arkusz jeszcze raz "
                 "(Plik > Pobierz > CSV).")
    if COL_PRODUCER not in df.columns:
        df[COL_PRODUCER] = ""
    if COL_ADDED not in df.columns:
        df[COL_ADDED] = ""
    if args.podglad:
        print("Podgląd: " + preview(df, args.plik))
        return

    done = df[COL_STATUS].str.strip().str.upper().isin(["NOWY", "OK"]) & (df[COL_DESC].str.strip() != "")
    bad = {i: problems(df.at[i, COL_DESC], df.at[i, COL_NAME]) for i in df.index[done]}
    bad = {i: p for i, p in bad.items() if p}
    print(f"Gotowe opisy: {int(done.sum())}, w tym nie przechodzi kontroli: {len(bad)}.")
    if args.sprawdz:
        for i, p in list(bad.items())[:200]:
            print(f"  wiersz {i + 2}: {'; '.join(p)}")
        return

    todo = [i for i in df.index if not str(df.at[i, COL_STATUS]).strip() and str(df.at[i, COL_NAME]).strip()]
    if args.kolejnosc == "marki" and todo:
        todo = order_by_brand(df, todo)
    if args.popraw_zle:
        todo = list(bad) + todo
    if args.limit:
        todo = todo[:args.limit]
    pool = [(words(df.at[i, COL_NAME]), df.at[i, COL_NAME], df.at[i, COL_DESC])
            for i in df.index[done] if i not in bad]
    if extra is not None and {COL_NAME, COL_DESC, COL_STATUS} <= set(extra.columns):
        ok = extra[extra[COL_STATUS].str.strip().str.upper().isin(["NOWY", "OK"]) & (extra[COL_DESC].str.strip() != "")]
        pool += [(words(n), n, d) for n, d in zip(ok[COL_NAME], ok[COL_DESC]) if not problems(d, n)]
        print(f"Wzory stylu z {args.wzory}: {len(ok)} opisów.")
    if not pool:
        sys.exit("W arkuszu nie ma jeszcze gotowych opisów, z których model mógłby brać wzór.")

    pick_model()
    wp.configure_ai("ollama")
    print(f"Do napisania: {len(todo)} opisów. Model: {wp.AI['models'][0]}. Ctrl+C przerywa (postęp zostaje).")

    started, written, manual = time.time(), 0, 0
    try:
        for n, i in enumerate(todo, 1):
            name, producer = str(df.at[i, COL_NAME]), str(df.at[i, COL_PRODUCER])
            try:
                desc, why = write_description(name, producer, pick_examples(name, pool))
            except requests.RequestException as exc:
                print(f"  wiersz {i + 2}: błąd połączenia z Ollamą ({exc}); spróbuję przy następnym uruchomieniu.")
                continue
            if desc:
                df.at[i, COL_DESC], df.at[i, COL_STATUS] = desc, "NOWY"
                df.at[i, COL_ADDED] = time.strftime("%Y-%m-%d %H:%M")
                pool.append((words(name), name, desc))
                written += 1
            else:
                df.at[i, COL_DESC], df.at[i, COL_STATUS] = "", "RĘCZNIE"
                df.at[i, COL_ADDED] = time.strftime("%Y-%m-%d %H:%M")
                manual += 1
                print(f"  wiersz {i + 2} RĘCZNIE ({name[:50]}): {'; '.join(why)}")
            if n % SAVE_EVERY == 0:
                save(df, args.plik)
                left = (time.time() - started) / n * (len(todo) - n) / 3600
                print(f"{n}/{len(todo)} — nowe: {written}, ręcznie: {manual}. Pozostało ok. {left:.1f} h.")
    except KeyboardInterrupt:
        print("Przerwano — zapisuję postęp. Ta sama komenda dokończy resztę.")
    save(df, args.plik)
    if written or manual:
        print("Podgląd: " + preview(df, args.plik))
    print(f"Koniec. Nowe opisy: {written}, do ręcznego uzupełnienia: {manual}. Plik: {args.plik}")


if __name__ == "__main__":
    main()
