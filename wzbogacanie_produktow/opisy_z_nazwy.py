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
  py opisy_z_nazwy.py arkusz_opisy.csv            # uzupełnij brakujące
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
    args = ap.parse_args()

    df = wp.read_csv(args.plik)
    print(f"Plik {args.plik}: {len(df)} wierszy, kolumny: {', '.join(df.columns)}")
    missing = [c for c in (COL_NAME, COL_DESC, COL_STATUS) if c not in df.columns]
    if missing:
        sys.exit(f"W pliku brak kolumn: {', '.join(missing)}. To nie jest aktualny arkusz z kolumnami "
                 "„Opis nowy” i „Status”. Usuń arkusz_opisy.csv i pobierz arkusz jeszcze raz "
                 "(Plik > Pobierz > CSV).")
    if COL_PRODUCER not in df.columns:
        df[COL_PRODUCER] = ""

    done = df[COL_STATUS].str.strip().str.upper().isin(["NOWY", "OK"]) & (df[COL_DESC].str.strip() != "")
    bad = {i: problems(df.at[i, COL_DESC], df.at[i, COL_NAME]) for i in df.index[done]}
    bad = {i: p for i, p in bad.items() if p}
    print(f"Gotowe opisy: {int(done.sum())}, w tym nie przechodzi kontroli: {len(bad)}.")
    if args.sprawdz:
        for i, p in list(bad.items())[:200]:
            print(f"  wiersz {i + 2}: {'; '.join(p)}")
        return

    todo = [i for i in df.index if not str(df.at[i, COL_STATUS]).strip() and str(df.at[i, COL_NAME]).strip()]
    if args.popraw_zle:
        todo = list(bad) + todo
    if args.limit:
        todo = todo[:args.limit]
    pool = [(words(df.at[i, COL_NAME]), df.at[i, COL_NAME], df.at[i, COL_DESC])
            for i in df.index[done] if i not in bad]
    if not pool:
        sys.exit("W arkuszu nie ma jeszcze gotowych opisów, z których model mógłby brać wzór.")

    os.environ.setdefault("OLLAMA_MODEL", "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M")
    wp.AI_PROVIDERS["ollama"]["models"] = os.environ["OLLAMA_MODEL"].split(",")
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
                pool.append((words(name), name, desc))
                written += 1
            else:
                df.at[i, COL_DESC], df.at[i, COL_STATUS] = "", "RĘCZNIE"
                manual += 1
                print(f"  wiersz {i + 2} RĘCZNIE ({name[:50]}): {'; '.join(why)}")
            if n % SAVE_EVERY == 0:
                save(df, args.plik)
                left = (time.time() - started) / n * (len(todo) - n) / 3600
                print(f"{n}/{len(todo)} — nowe: {written}, ręcznie: {manual}. Pozostało ok. {left:.1f} h.")
    except KeyboardInterrupt:
        print("Przerwano — zapisuję postęp. Ta sama komenda dokończy resztę.")
    save(df, args.plik)
    print(f"Koniec. Nowe opisy: {written}, do ręcznego uzupełnienia: {manual}. Plik: {args.plik}")


if __name__ == "__main__":
    main()
