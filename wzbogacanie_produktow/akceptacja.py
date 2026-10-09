#!/usr/bin/env python3
"""
Szybka akceptacja produktów DO_AKCEPTACJI — grupami, jednym kliknięciem.

Krok 1 — strona do przeglądania (można w trakcie przebiegu, plik roboczy jest tylko czytany):
    py akceptacja.py
  Otwiera w przeglądarce opisy_wszystkie_akceptacja.html. Produkty są pogrupowane po
  powodzie (np. „nagłówek bez rodzaju produktu”, „brak strony źródłowej”). Przy każdej
  grupie jest przycisk „Zaakceptuj całą grupę”, pojedyncze produkty można odznaczyć.
  Przycisk „Zapisz akceptację” pobiera plik zaakceptowane_produkty.csv (do Pobranych).

Krok 2 — plik do importu:
    py akceptacja.py --zapisz
  Bierze najnowszy zaakceptowane_produkty*.csv z folderu Pobrane (albo --plik ŚCIEŻKA)
  i tworzy opisy_wszystkie_do_importu.csv = produkty PEWNE + zaakceptowane.

Bez przeglądarki — cała grupa od razu:
    py akceptacja.py --akceptuj-grupe naglowek --zapisz
  Grupy: naglowek, brak_strony, ai_niezgodny, krotki, inne.

Zaakceptowane id są pamiętane w opisy_wszystkie_zaakceptowane.txt, więc po dokończeniu
przebiegu wystarczy powtórzyć krok 1 — wcześniejsze wybory są już zaznaczone.
Produkty bez opisu nie dają się zaakceptować.
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import sys
import webbrowser

import pandas as pd

COL_ID = "@id"
COL_CODE = "@code_producer"
COL_PRODUCER = "/producer@name"
COL_EAN = "/sizes/size@code_producer"
COL_NAME = "/description/name[pol]"
COL_DESC = "Opis_HTML"
COL_SOURCE = "Zrodlo_URL"
COL_STATUS = "Status"
COL_REASON = "Powod"
COL_ACCEPT = "Akceptacja"
STATUS_OK = "PEWNY"
STATUS_ACCEPTED = "ZAAKCEPTOWANY"
DOWNLOAD_NAME = "zaakceptowane_produkty"

# (klucz, etykieta, czy domyślnie warto akceptować, wzorzec w kolumnie Powod)
GROUPS = [
    ("naglowek", "Nagłówek bez rodzaju produktu — prawie zawsze fałszywy alarm",
     r"nagłówek"),
    ("ai_niezgodny", "AI: strona źródłowa nie pasuje do produktu — sprawdź uważnie",
     r"AI:|nie pasuje"),
    ("brak_strony", "Nie znaleziono strony z kodem/EAN — opis tylko z nazwy",
     r"nie znaleziono strony"),
    ("krotki", "Krótki opis po usunięciu ogólników",
     r"krótk|za mało"),
    ("inne", "Inne powody", r""),
]
ALLOWED_TAGS = ("h2", "h3", "p", "ul", "ol", "li", "strong", "b", "em", "br")


def side_path(output_path: str, suffix: str) -> str:
    base, ext = os.path.splitext(output_path)
    return f"{base}_{suffix}{ext or '.csv'}"


def read_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def safe_html(fragment: str) -> str:
    escaped = html.escape(fragment, quote=False)
    tags = "|".join(ALLOWED_TAGS)
    return re.sub(rf"&lt;(/?)({tags})(?:\s[^&]*)?&gt;", r"<\1\2>", escaped, flags=re.IGNORECASE)


def group_of(reason: str) -> str:
    for key, _, pattern in GROUPS:
        if pattern and re.search(pattern, reason, re.IGNORECASE):
            return key
    return "inne"


def load_results(output_path: str) -> pd.DataFrame:
    if not os.path.isfile(output_path):
        sys.exit(f"Nie ma pliku {output_path}. Podaj właściwy --output.")
    try:
        df = read_csv(output_path)
    except Exception as exc:  # trafiliśmy w moment dopisywania paczki przez główny skrypt
        sys.exit(f"Nie udało się odczytać {output_path} ({exc}). Spróbuj ponownie za kilka sekund.")
    for col in (COL_ID, COL_STATUS):
        if col not in df.columns:
            sys.exit(f"W pliku {output_path} brakuje kolumny '{col}'.")
    for col in (COL_DESC, COL_REASON, COL_NAME, COL_CODE, COL_EAN, COL_PRODUCER, COL_SOURCE):
        if col not in df.columns:
            df[col] = ""
    return df.drop_duplicates(subset=[COL_ID], keep="last")


def load_accepted(path: str) -> set[str]:
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def save_accepted(path: str, ids: set[str]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(ids, key=lambda x: (len(x), x))) + ("\n" if ids else ""))


def ids_from_download(path: str) -> set[str]:
    df = read_csv(path)
    col = COL_ID if COL_ID in df.columns else df.columns[0]
    if COL_ACCEPT in df.columns:
        df = df[df[COL_ACCEPT].str.strip().str.upper().isin({"TAK", "T", "OK", "X", "1", "YES", "Y"})]
    return {v.strip() for v in df[col] if v.strip()}


def newest_download() -> str | None:
    folders = [os.path.join(os.path.expanduser("~"), d) for d in ("Downloads", "Pobrane")]
    files = [f for d in folders for f in glob.glob(os.path.join(d, DOWNLOAD_NAME + "*.csv"))]
    return max(files, key=os.path.getmtime) if files else None


def write_page(review: pd.DataFrame, accepted: set[str], path: str, storage_key: str) -> None:
    items = []
    for _, row in review.iterrows():
        items.append({
            "id": row[COL_ID], "g": group_of(row[COL_REASON]),
            "n": row[COL_NAME], "p": row[COL_PRODUCER], "c": row[COL_CODE], "e": row[COL_EAN],
            "r": row[COL_REASON], "s": row[COL_SOURCE],
            "d": safe_html(row[COL_DESC]), "ok": bool(row[COL_DESC].strip()),
            "a": row[COL_ID] in accepted,
        })
    groups = [{"k": k, "t": t} for k, t, _ in GROUPS]
    data = json.dumps({"items": items, "groups": groups, "key": storage_key,
                       "file": DOWNLOAD_NAME + ".csv"}, ensure_ascii=False).replace("</", "<\\/")
    page = PAGE.replace("__DATA__", data)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)


def build_import(df: pd.DataFrame, accepted: set[str], output_path: str) -> str:
    ok = df[df[COL_STATUS] == STATUS_OK]
    acc = df[(df[COL_STATUS] != STATUS_OK) & df[COL_ID].isin(accepted) & df[COL_DESC].str.strip().ne("")].copy()
    acc[COL_STATUS] = STATUS_ACCEPTED
    result = pd.concat([ok, acc], ignore_index=True).drop(columns=[COL_ACCEPT], errors="ignore")
    import_path = side_path(output_path, "do_importu")
    result.to_csv(import_path, index=False, encoding="utf-8-sig")
    print(f"Pewne: {len(ok)}, zaakceptowane: {len(acc)}, "
          f"pominięte: {int((df[COL_STATUS] != STATUS_OK).sum()) - len(acc)}.")
    print(f"Plik do importu: {import_path}")
    return import_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Akceptacja produktów DO_AKCEPTACJI grupami.")
    parser.add_argument("--output", "-o", default="opisy_wszystkie.csv",
                        help="Plik roboczy z wynikami (ten sam co --output głównego skryptu)")
    parser.add_argument("--zapisz", action="store_true",
                        help="Wczytaj akceptację z przeglądarki i utwórz plik *_do_importu.csv")
    parser.add_argument("--plik", help="Plik pobrany ze strony akceptacji (domyślnie najnowszy z Pobranych)")
    parser.add_argument("--akceptuj-grupe", nargs="+", metavar="GRUPA", default=[],
                        choices=[k for k, _, _ in GROUPS], help="Zaakceptuj całe grupy bez przeglądarki")
    parser.add_argument("--bez-otwierania", action="store_true", help="Nie otwieraj przeglądarki")
    args = parser.parse_args()

    df = load_results(args.output)
    review = df[df[COL_STATUS] != STATUS_OK]
    store_path = side_path(args.output, "zaakceptowane").rsplit(".", 1)[0] + ".txt"
    accepted = load_accepted(store_path)

    if args.zapisz and not args.akceptuj_grupe:
        src = args.plik or newest_download()
        if src and os.path.isfile(src):
            accepted = ids_from_download(src)  # strona zawiera pełny bieżący wybór -> zastępujemy
            print(f"Wczytano akceptację z {src}: {len(accepted)} produktów.")
        elif args.plik or not accepted:
            sys.exit("Nie znalazłem pliku z akceptacją. Kliknij „Zapisz akceptację” na stronie "
                     "albo podaj --plik ŚCIEŻKA.")
        else:
            print(f"Używam wcześniej zapisanej akceptacji ({store_path}): {len(accepted)} produktów.")

    if args.akceptuj_grupe:
        groups = review[COL_REASON].map(group_of)
        chosen = review[groups.isin(args.akceptuj_grupe) & review[COL_DESC].str.strip().ne("")]
        accepted |= set(chosen[COL_ID])
        print(f"Zaakceptowano grupy {', '.join(args.akceptuj_grupe)}: {len(chosen)} produktów.")

    if args.zapisz or args.akceptuj_grupe:
        save_accepted(store_path, accepted)

    if args.zapisz:
        build_import(df, accepted, args.output)
        return

    page_path = side_path(args.output, "akceptacja").rsplit(".", 1)[0] + ".html"
    write_page(review, accepted, page_path, os.path.abspath(args.output))
    counts = review[COL_REASON].map(group_of).value_counts()
    print(f"Do akceptacji: {len(review)} produktów (już zaakceptowane: {len(accepted & set(review[COL_ID]))}).")
    for key, title, _ in GROUPS:
        if counts.get(key):
            print(f"  {key:13} {counts[key]:6}  {title}")
    print(f"Strona: {page_path}")
    if not args.bez_otwierania:
        webbrowser.open("file://" + os.path.abspath(page_path).replace(os.sep, "/"))


PAGE = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Akceptacja produktów</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--fg:#1d2433;--muted:#667;--line:#ddd;--ok:#2e9d5b;--warn:#8a6100;--warnbg:#fff8e1}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}
.top{position:sticky;top:0;background:var(--card);padding:10px 16px;border-bottom:1px solid var(--line);z-index:2;
  display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.top .sum{font-weight:600;margin-right:auto}
button{padding:7px 12px;border:1px solid #bbb;border-radius:6px;background:#fff;color:var(--fg);cursor:pointer;font:inherit}
button.primary{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:600}
main{max-width:1100px;margin:0 auto;padding:16px}
.group{background:var(--card);border-radius:10px;margin-bottom:14px;overflow:hidden}
.ghead{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:12px 16px;cursor:pointer}
.ghead h2{font-size:16px;margin:0;flex:1;min-width:220px}
.ghead .cnt{color:var(--muted);font-size:14px}
.glist{border-top:1px solid var(--line);padding:8px 16px}
.item{display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #eee}
.item:last-child{border-bottom:0}
.item input{width:20px;height:20px;margin-top:2px;flex:none}
.item .main{flex:1;min-width:0}
.item small{display:block;color:var(--muted)}
.reason{color:var(--warn);background:var(--warnbg);padding:4px 8px;border-radius:6px;font-size:13px;margin:6px 0}
.desc{margin-top:6px;padding:8px 12px;border-left:3px solid var(--line)} .desc h2{font-size:16px;margin:4px 0}
.src{font-size:12px;word-break:break-all}
.more{text-align:center;padding:8px}
.off{opacity:.5}
</style></head><body>
<div class="top"><span class="sum" id="sum"></span>
<button id="all">Zaznacz wszystkie</button><button id="none">Odznacz wszystkie</button>
<button class="primary" id="save">Zapisz akceptację</button></div>
<main id="main"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const KEY='akceptacja:'+D.key, PAGE=50;
let sel=new Set(D.items.filter(i=>i.a).map(i=>i.id));
try{const s=localStorage.getItem(KEY);if(s)sel=new Set(JSON.parse(s));}catch(e){}
const byId=new Map(D.items.map(i=>[i.id,i]));
const shown={}, open={};
function persist(){try{localStorage.setItem(KEY,JSON.stringify([...sel]));}catch(e){}render();}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function setMany(list,on){list.filter(i=>i.ok).forEach(i=>on?sel.add(i.id):sel.delete(i.id));persist();}
function render(){
  const accN=D.items.filter(i=>sel.has(i.id)&&i.ok).length;
  document.getElementById('sum').textContent=`Zaakceptowane: ${accN} z ${D.items.length}`;
  const main=document.getElementById('main');main.innerHTML='';
  D.groups.forEach(g=>{
    const list=D.items.filter(i=>i.g===g.k); if(!list.length)return;
    const n=list.filter(i=>sel.has(i.id)&&i.ok).length;
    const box=document.createElement('section');box.className='group';
    box.innerHTML=`<div class="ghead"><h2>${esc(g.t)}</h2><span class="cnt">${n} / ${list.length} zaakceptowanych</span>
      <button data-a="on">Zaakceptuj całą grupę</button><button data-a="off">Odznacz grupę</button>
      <button data-a="tog">${open[g.k]?'Zwiń':'Pokaż produkty'}</button></div>`;
    box.querySelector('[data-a=on]').onclick=e=>{e.stopPropagation();setMany(list,true);};
    box.querySelector('[data-a=off]').onclick=e=>{e.stopPropagation();setMany(list,false);};
    box.querySelector('[data-a=tog]').onclick=e=>{e.stopPropagation();open[g.k]=!open[g.k];render();};
    if(open[g.k]){
      const ul=document.createElement('div');ul.className='glist';
      const lim=shown[g.k]||PAGE;
      list.slice(0,lim).forEach(i=>{
        const row=document.createElement('label');row.className='item'+(i.ok?'':' off');
        row.innerHTML=`<input type="checkbox" ${sel.has(i.id)&&i.ok?'checked':''} ${i.ok?'':'disabled'}>
          <div class="main"><b>${esc(i.n)}</b><small>id ${esc(i.id)} · ${esc(i.p)} · kod ${esc(i.c)} · EAN ${esc(i.e)||'—'}</small>
          <div class="reason">⚠ ${esc(i.r)}</div>
          <div class="desc">${i.ok?i.d:'<i>brak opisu — nie można zaakceptować</i>'}</div>
          ${i.s?`<div class="src">Źródło: <a href="${esc(i.s)}" target="_blank" rel="noreferrer">${esc(i.s)}</a></div>`:''}</div>`;
        row.querySelector('input').onchange=e=>{e.target.checked?sel.add(i.id):sel.delete(i.id);persist();};
        ul.appendChild(row);
      });
      if(list.length>lim){const m=document.createElement('div');m.className='more';
        m.innerHTML=`<button>Pokaż kolejne (${list.length-lim} zostało)</button>`;
        m.querySelector('button').onclick=()=>{shown[g.k]=lim+PAGE;render();};ul.appendChild(m);}
      box.appendChild(ul);
    }
    main.appendChild(box);
  });
}
document.getElementById('all').onclick=()=>setMany(D.items,true);
document.getElementById('none').onclick=()=>setMany(D.items,false);
document.getElementById('save').onclick=()=>{
  const ids=D.items.filter(i=>sel.has(i.id)&&i.ok).map(i=>i.id);
  const csv='﻿@id\n'+ids.join('\n')+'\n';
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  a.download=D.file;document.body.appendChild(a);a.click();a.remove();
  alert(`Zapisano ${ids.length} produktów do pliku ${D.file} (folder Pobrane).\nTeraz w PowerShell: py akceptacja.py --zapisz`);
};
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
