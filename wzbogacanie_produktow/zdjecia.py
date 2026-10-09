#!/usr/bin/env python3
"""
Zdjęcia produktów — osobny przebieg po opisach.

Dla produktów PEWNYCH (i zaakceptowanych w akceptacja.py) bierze zdjęcie ze strony źródłowej,
którą skrypt opisów już znalazł i potwierdził kodem/EAN (kolumna Zrodlo_URL) — bez ponownego
wyszukiwania. Każde zdjęcie przechodzi filtry (logo/baner w adresie, rozmiar min. 400 px,
proporcje, prawdziwy plik obrazu) i model wizyjny Ollamy (czy to produkt, czy ten rodzaj,
czy nie ma znaku wodnego). Strony ze znakami wodnymi (onninen.pl) są pomijane.

  py zdjecia.py --limit 20          test na 20 produktach
  py zdjecia.py                     cała baza (wznawia od miejsca przerwania, Ctrl+C = przerwa)
  py zdjecia.py --szukaj            produkty bez zdjęcia na stronie źródłowej: także wyszukiwarka obrazów
  py zdjecia.py --podglad           strona z miniaturami: PEWNE do obejrzenia, wątpliwe do akceptacji
  py zdjecia.py --zapisz            plik dla IdoSell: @id + link do zdjęcia (pewne + zaakceptowane)

Wyniki: opisy_wszystkie_zdjecia.csv, pliki w folderze zdjecia/.
Model wizyjny i model opisów nie mieszczą się razem na karcie 12 GB — uruchamiaj ten skrypt,
gdy przebieg opisów stoi (albo po jego zakończeniu).
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import logging
import os
import sys
import time
import webbrowser

import pandas as pd

try:
    import wzbogac_produkty as w
except ImportError as exc:
    sys.exit(f"Uruchom ten skrypt w folderze z wzbogac_produkty.py ({exc}).")

COL_ID, COL_NAME, COL_CODE = w.COL_ID, w.COL_NAME, w.COL_CODE
COL_EAN, COL_PRODUCER, COL_SOURCE = w.COL_EAN, w.COL_PRODUCER, w.COL_SOURCE
COL_IMG_URL, COL_IMG_FILE, COL_IMG_PAGE = "Zdjecie_URL", "Zdjecie_plik", "Zdjecie_strona"
COL_IMG_STATUS, COL_IMG_REASON = "Status_zdjecia", "Powod_zdjecia"
COLUMNS = [COL_ID, COL_NAME, COL_CODE, COL_PRODUCER, COL_IMG_URL, COL_IMG_FILE, COL_IMG_PAGE,
           COL_IMG_STATUS, COL_IMG_REASON]
OK, REVIEW, NONE = "PEWNE", "DO_AKCEPTACJI", "BRAK"
VISION_TRIES = 3             # ile zdjęć jednego produktu obejrzeć modelem, zanim zostanie bez zdjęcia
DOWNLOAD_NAME = "zaakceptowane_zdjecia"
IDOSELL_IMAGE_COLUMN = "/images/large/image@url"
log = w.log


def side(output: str, suffix: str, ext: str = ".csv") -> str:
    return os.path.splitext(output)[0] + f"_{suffix}{ext}"


def products_to_do(output: str) -> pd.DataFrame:
    df = w.read_csv(output).drop_duplicates(subset=[COL_ID], keep="last")
    accepted_path = side(output, "zaakceptowane", ".txt")
    accepted = set()
    if os.path.isfile(accepted_path):
        with open(accepted_path, encoding="utf-8") as fh:
            accepted = {line.strip() for line in fh if line.strip()}
    keep = (df[w.COL_STATUS] == w.STATUS_OK) | df[COL_ID].isin(accepted)
    return df[keep]


def candidates_for(record: dict, search: bool, engine: str) -> list[tuple[str, bool, str]]:
    """[(url_zdjęcia, czy_potwierdzone, strona)] — najpierw strona źródłowa, opcjonalnie wyszukiwarka."""
    m = w.ProductMatcher(record.get(COL_CODE, ""), record.get(COL_EAN, ""), record.get(COL_PRODUCER, ""))
    found: list[tuple[str, bool, str]] = []
    src = record.get(COL_SOURCE, "")
    if src and not w.is_watermark_site(src):
        page = w.fetch_page(src)
        if page:
            source = {"url": src, "og_images": page[1], "imgs": page[2], "gallery": page[4] if len(page) > 4 else [],
                      "producer_site": True}  # strona potwierdzona kodem/EAN — galeria to ten produkt
            found += [(u, True, src) for u in w.image_candidates_from_source(m, source)]
    if search and not found:
        found += w.image_candidates_from_search(m, record.get(COL_CODE, ""), record.get(COL_EAN, ""),
                                                record.get(COL_PRODUCER, ""), engine, f"[id={record[COL_ID]}]")
    return found


def process(record: dict, images_dir: str, search: bool, engine: str, vision: bool) -> dict:
    out = {c: record.get(c, "") for c in (COL_ID, COL_NAME, COL_CODE, COL_PRODUCER)}
    out.update({COL_IMG_URL: "", COL_IMG_FILE: "", COL_IMG_PAGE: "", COL_IMG_STATUS: NONE, COL_IMG_REASON: ""})
    cands = candidates_for(record, search, engine)
    if not cands:
        out[COL_IMG_REASON] = "brak zdjęcia na stronie źródłowej" if record.get(COL_SOURCE) else "brak strony źródłowej"
        return out
    base = w.safe_filename(f"{record[COL_ID]}_{record.get(COL_CODE, '')}")
    pages = {u: p for u, _, p in cands}
    tried: set[str] = set()
    notes: list[str] = []
    for _ in range(VISION_TRIES):
        url, verified, _, reason = w.pick_image(cands, None, base, tried)  # pobiera + filtry wymiarów/logo
        if not url:
            notes.append(f"brak poprawnego zdjęcia ({reason})" if reason else "brak kolejnych zdjęć")
            break
        data, ext, err = w.fetch_image(url, pages.get(url, ""))
        if err:
            notes.append(err)
            continue
        if vision:
            result, note = w.check_one_image(record, data)
            if result != w.CHECK_OK:
                notes.append(note)
                log.info("[id=%s] odrzucone — %s: %s", record[COL_ID], note, url)
                continue
        out.update({COL_IMG_URL: url, COL_IMG_PAGE: pages.get(url, ""),
                    COL_IMG_FILE: w.save_image(data, ext, images_dir, base).replace(os.sep, "/")})
        problems = ([] if verified else ["zdjęcie niepotwierdzone kodem/EAN"]) + \
                   ([] if vision else ["nie sprawdzone modelem wizyjnym"])
        out[COL_IMG_STATUS] = REVIEW if problems else OK
        out[COL_IMG_REASON] = "; ".join(problems)
        return out
    out[COL_IMG_REASON] = "; ".join(n for n in notes if n) or "brak poprawnego zdjęcia"
    return out


def run(args) -> None:
    if args.bez_wizji:
        log.warning("Bez modelu wizyjnego — wszystkie zdjęcia trafią do akceptacji.")
    else:
        w.check_ollama(dict(w.AI_PROVIDERS["ollama"], models=[w.VISION_MODEL]))
    todo = products_to_do(args.output)
    result_path = side(args.output, "zdjecia")
    done = set()
    if os.path.isfile(result_path) and os.path.getsize(result_path):
        done = set(w.read_csv(result_path)[COL_ID])
    records = [r for r in todo.to_dict("records") if r[COL_ID] not in done]
    if args.limit:
        records = records[:args.limit]
    images_dir = os.path.relpath(os.path.join(os.path.dirname(os.path.abspath(args.output)), w.IMAGES_DIR))
    log.info("Zdjęcia: do zrobienia %d (zrobione wcześniej: %d). Folder: %s",
             len(records), len(done), os.path.abspath(images_dir))
    started, batch, counts = time.time(), [], {OK: 0, REVIEW: 0, NONE: 0}
    try:
        for i, record in enumerate(records, 1):
            out = process(record, images_dir, args.szukaj, args.search, not args.bez_wizji)
            counts[out[COL_IMG_STATUS]] += 1
            log.info("[id=%s] %s%s", out[COL_ID], out[COL_IMG_STATUS],
                     f" — {out[COL_IMG_REASON]}" if out[COL_IMG_REASON] else "")
            batch.append(out)
            if len(batch) >= w.CHECKPOINT_EVERY or i == len(records):
                w.append_batch(batch, COLUMNS, result_path)
                batch = []
                left = (len(records) - i) * (time.time() - started) / i / 3600
                log.info("CHECKPOINT: %d/%d. Pewne %d, do akceptacji %d, bez zdjęcia %d. Pozostało ok. %.1f h.",
                         i, len(records), counts[OK], counts[REVIEW], counts[NONE], left)
    except KeyboardInterrupt:
        if batch:
            w.append_batch(batch, COLUMNS, result_path)
        log.warning("Przerwano — zapisane, ta sama komenda dokończy resztę.")
    except w.FatalError as exc:
        if batch:
            w.append_batch(batch, COLUMNS, result_path)
        log.error("Zatrzymuję: %s", exc)
    log.info("Wyniki: %s. Podgląd: py zdjecia.py --podglad", result_path)


def load_results(output: str) -> pd.DataFrame:
    path = side(output, "zdjecia")
    if not os.path.isfile(path):
        sys.exit(f"Nie ma pliku {path}. Najpierw uruchom: py zdjecia.py")
    return w.read_csv(path).drop_duplicates(subset=[COL_ID], keep="last")


def accepted_store(output: str) -> str:
    return side(output, "zdjecia_zaakceptowane", ".txt")


def preview(args) -> None:
    df = load_results(args.output)
    store = accepted_store(args.output)
    accepted = set()
    if os.path.isfile(store):
        with open(store, encoding="utf-8") as fh:
            accepted = {line.strip() for line in fh if line.strip()}
    page_path = side(args.output, "zdjecia_podglad", ".html")
    base = os.path.dirname(os.path.abspath(page_path))
    items = []
    for r in df.to_dict("records"):
        img = r[COL_IMG_FILE]
        if img and os.path.exists(img):
            img = os.path.relpath(os.path.abspath(img), base).replace(os.sep, "/")
        items.append({"id": r[COL_ID], "n": r[COL_NAME], "c": r[COL_CODE], "p": r[COL_PRODUCER],
                      "i": img or r[COL_IMG_URL], "u": r[COL_IMG_URL], "s": r[COL_IMG_STATUS], "r": r[COL_IMG_REASON],
                      "pg": r[COL_IMG_PAGE], "a": r[COL_ID] in accepted})
    data = json.dumps({"items": items, "key": os.path.abspath(args.output), "file": DOWNLOAD_NAME + ".csv"},
                      ensure_ascii=False).replace("</", "<\\/")
    with open(page_path, "w", encoding="utf-8") as fh:
        fh.write(PAGE.replace("__DATA__", data))
    c = df[COL_IMG_STATUS].value_counts()
    print(f"Pewne: {c.get(OK, 0)}, do akceptacji: {c.get(REVIEW, 0)}, bez zdjęcia: {c.get(NONE, 0)}")
    print(f"Strona: {page_path}")
    if not args.bez_otwierania:
        webbrowser.open("file://" + os.path.abspath(page_path).replace(os.sep, "/"))


def newest_download() -> str | None:
    folders = [os.path.join(os.path.expanduser("~"), d) for d in ("Downloads", "Pobrane")]
    files = [f for d in folders for f in glob.glob(os.path.join(d, DOWNLOAD_NAME + "*.csv"))]
    return max(files, key=os.path.getmtime) if files else None


def save(args) -> None:
    df = load_results(args.output)
    store = accepted_store(args.output)
    src = args.plik or newest_download()
    accepted: set[str] = set()
    if src and os.path.isfile(src):
        ids = w.read_csv(src)
        accepted = {v.strip() for v in ids[ids.columns[0]] if v.strip()}
        with open(store, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(accepted)) + "\n")
        print(f"Wczytano akceptację zdjęć z {src}: {len(accepted)}.")
    elif os.path.isfile(store):
        with open(store, encoding="utf-8") as fh:
            accepted = {line.strip() for line in fh if line.strip()}
    ok = df[(df[COL_IMG_STATUS] == OK) | ((df[COL_IMG_STATUS] == REVIEW) & df[COL_ID].isin(accepted))]
    ok = ok[ok[COL_IMG_URL].str.strip().ne("")]
    path = side(args.output, "zdjecia_idosell")
    ok[[COL_ID, COL_IMG_URL]].rename(columns={COL_IMG_URL: args.kolumna_zdjecia}).to_csv(
        path, index=False, encoding="utf-8-sig")
    print(f"Zdjęcia do importu: {len(ok)} (pewne {int((ok[COL_IMG_STATUS] == OK).sum())}, "
          f"zaakceptowane {int((ok[COL_IMG_STATUS] == REVIEW).sum())}).")
    print(f"Plik dla IdoSell (@id + link w kolumnie {args.kolumna_zdjecia}): {path}")


def main() -> None:
    p = argparse.ArgumentParser(description="Zdjęcia produktów ze stron źródłowych + kontrola modelem wizyjnym.")
    p.add_argument("--output", "-o", default="opisy_wszystkie.csv", help="Plik z wynikami opisów")
    p.add_argument("--limit", type=int, help="Tylko N produktów (test)")
    p.add_argument("--szukaj", action="store_true",
                   help="Gdy strona źródłowa nie ma zdjęcia — szukaj w wyszukiwarce obrazów")
    p.add_argument("--search", choices=["ddg", "serpapi", "google"], default=w.SEARCH_ENGINE)
    p.add_argument("--bez-wizji", action="store_true", help="Bez modelu wizyjnego (wszystko do akceptacji)")
    p.add_argument("--podglad", action="store_true", help="Strona z miniaturami i akceptacją")
    p.add_argument("--zapisz", action="store_true", help="Plik dla IdoSell (@id + link do zdjęcia)")
    p.add_argument("--plik", help="Plik pobrany ze strony podglądu (domyślnie najnowszy z Pobranych)")
    p.add_argument("--kolumna-zdjecia", default=IDOSELL_IMAGE_COLUMN,
                   help="Nagłówek kolumny zdjęcia w pliku dla IdoSell (jak w eksporcie IdoSell)")
    p.add_argument("--bez-otwierania", action="store_true")
    args = p.parse_args()
    if not os.path.isfile(args.output):
        sys.exit(f"Nie ma pliku {args.output}. Podaj --output z wynikami opisów.")
    if args.podglad:
        return preview(args)
    if args.zapisz:
        return save(args)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S", handlers=[logging.StreamHandler(),
                                                      logging.FileHandler("zdjecia.log", encoding="utf-8")])
    log.setLevel(logging.INFO)
    run(args)


PAGE = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Zdjęcia produktów</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--fg:#1d2433;--muted:#667;--line:#ddd;--ok:#2e9d5b;--warn:#8a6100;--warnbg:#fff8e1}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}
.top{position:sticky;top:0;background:var(--card);padding:10px 16px;border-bottom:1px solid var(--line);z-index:2;
  display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.sum{font-weight:600;margin-right:auto}
button{padding:7px 12px;border:1px solid #bbb;border-radius:6px;background:#fff;color:var(--fg);cursor:pointer;font:inherit}
button.on{background:var(--fg);color:#fff} button.primary{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:600}
main{max-width:1200px;margin:0 auto;padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
.card{background:var(--card);border-radius:10px;padding:10px;border-top:5px solid var(--ok);display:flex;flex-direction:column;gap:6px}
.card.DO_AKCEPTACJI{border-top-color:#e0a100} .card.BRAK{border-top-color:#bbb}
.card img{width:100%;aspect-ratio:1;object-fit:contain;background:#fff;border:1px solid #eee;border-radius:6px}
.noimg{width:100%;aspect-ratio:1;display:flex;align-items:center;justify-content:center;background:#f0f0f0;color:#888;border-radius:6px}
.card b{font-size:13px} .card small{color:var(--muted);font-size:12px}
.reason{color:var(--warn);background:var(--warnbg);padding:4px 6px;border-radius:6px;font-size:12px}
.card a{font-size:11px;word-break:break-all;color:var(--muted)}
.card label{display:flex;gap:6px;align-items:center;font-weight:600}
.more{grid-column:1/-1;text-align:center}
.howto{flex-basis:100%;font-size:13px;color:var(--muted)}
@media print{.top,.more{display:none!important}body{background:#fff}main{max-width:none;padding:0;grid-template-columns:repeat(4,1fr)}
  .card{break-inside:avoid;border:1px solid #ccc}}
</style></head><body>
<div class="top"><span class="sum" id="sum"></span>
<button data-f="DO_AKCEPTACJI" class="on">Do akceptacji</button><button data-f="PEWNE">Pewne</button>
<button data-f="BRAK">Bez zdjęcia</button>
<button id="all">Zaznacz wszystkie</button><button id="none">Odznacz</button>
<button id="pdf">PDF / drukuj</button><button class="primary" id="save">Zapisz akceptację</button>
<div class="howto">Jak zaakceptować: zaznacz zdjęcia → „Zapisz akceptację” → plik <b>zaakceptowane_zdjecia.csv</b>
z folderu Pobrane odeślij osobie, która przysłała tę stronę.</div></div>
<main id="main"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent), KEY='zdjecia:'+D.key, PAGE=120;
let sel=new Set(D.items.filter(i=>i.a).map(i=>i.id)), filt='DO_AKCEPTACJI', lim=PAGE, ALL=false;
try{const s=localStorage.getItem(KEY);if(s)sel=new Set(JSON.parse(s));}catch(e){}
const R=D.items.filter(i=>i.s==='DO_AKCEPTACJI');
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function persist(){try{localStorage.setItem(KEY,JSON.stringify([...sel]));}catch(e){}render();}
function render(){
  const n=D.items.reduce((a,i)=>(a[i.s]=(a[i.s]||0)+1,a),{});
  document.getElementById('sum').textContent=`Zaakceptowane: ${R.filter(i=>sel.has(i.id)).length} z ${R.length} · pewne: ${n.PEWNE||0} · bez zdjęcia: ${n.BRAK||0}`;
  const list=D.items.filter(i=>i.s===filt), main=document.getElementById('main');main.innerHTML='';
  list.slice(0,ALL?list.length:lim).forEach(i=>{
    const c=document.createElement('div');c.className='card '+i.s;
    c.innerHTML=(i.i?`<a href="${esc(i.i)}" target="_blank"><img ${ALL?'':'loading="lazy"'} src="${esc(i.i)}" data-u="${esc(i.u)}" onerror="if(this.dataset.u&&this.src!==this.dataset.u)this.src=this.dataset.u" alt=""></a>`:'<div class="noimg">brak zdjęcia</div>')+
      (i.s==='DO_AKCEPTACJI'?`<label><input type="checkbox" ${sel.has(i.id)?'checked':''}> akceptuj</label>`:'')+
      `<b>${esc(i.n)}</b><small>id ${esc(i.id)} · ${esc(i.p)} · ${esc(i.c)}</small>`+
      (i.r?`<div class="reason">${esc(i.r)}</div>`:'')+(i.pg?`<a href="${esc(i.pg)}" target="_blank" rel="noreferrer">${esc(i.pg)}</a>`:'');
    const cb=c.querySelector('input');if(cb)cb.onchange=e=>{e.target.checked?sel.add(i.id):sel.delete(i.id);persist();};
    main.appendChild(c);
  });
  if(!ALL&&list.length>lim){const m=document.createElement('div');m.className='more';
    m.innerHTML=`<button>Pokaż kolejne (${list.length-lim} zostało)</button>`;m.querySelector('button').onclick=()=>{lim+=PAGE;render();};main.appendChild(m);}
}
document.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>{filt=b.dataset.f;lim=PAGE;
  document.querySelectorAll('[data-f]').forEach(x=>x.classList.toggle('on',x===b));render();});
document.getElementById('all').onclick=()=>{R.forEach(i=>sel.add(i.id));persist();};
document.getElementById('none').onclick=()=>{R.forEach(i=>sel.delete(i.id));persist();};
document.getElementById('save').onclick=()=>{
  const ids=R.filter(i=>sel.has(i.id)).map(i=>i.id);
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['﻿@id\n'+ids.join('\n')+'\n'],{type:'text/csv'}));
  a.download=D.file;document.body.appendChild(a);a.click();a.remove();
  alert(`Zapisano ${ids.length} zdjęć do pliku ${D.file} (folder Pobrane).\nTeraz w PowerShell: py zdjecia.py --zapisz`);
};
document.getElementById('pdf').onclick=()=>{
  if(!confirm('Do PDF trafią WSZYSTKIE zdjęcia z wybranej zakładki.\nW oknie drukowania wybierz „Zapisz jako PDF”.'))return;
  ALL=true;render();setTimeout(()=>{window.print();ALL=false;render();},300);
};
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
