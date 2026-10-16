"""Wysyłanie cen do sklepu IdoSell przez Admin API (klucz z panelu: Klucze dostępowe do Admin API).

Ustawienia w ceny_ustawienia.ini, sekcja [idosell]:
  domena = iht.com.pl
  klucz = …                       (klucz API — zostaje tylko na tym komputerze)
  wersja = v6
  pole_id = productId             (nazwy pól API — do zmiany, gdyby dokumentacja Twojej wersji mówiła inaczej)
  pole_ceny = productRetailPrice  (cena detaliczna brutto)
  potwierdzone = false            (true dopiero po udanej próbie na 3 produktach)
  wysylaj_automatycznie = false   (automat sam wysyła zmiany po przeliczeniu)

Kolejność bezpiecznego startu: test (tylko odczyt 1 produktu) → próba na 3 produktach (sprawdzasz w sklepie)
→ potwierdzenie → wysyłanie wszystkiego / automat.
"""
from __future__ import annotations

import json
import os
import time

BATCH = 100  # ile produktów w jednym zapytaniu


class ApiError(Exception):
    pass


def settings(cfg) -> dict:
    sec = "idosell"
    domain = cfg.get(sec, "domena", fallback="").strip().removeprefix("https://").removeprefix("http://").strip("/")
    key = cfg.get(sec, "klucz", fallback="").strip()
    if not domain or not key:
        raise ApiError("Uzupełnij domenę sklepu i klucz API (sekcja „IdoSell API” w programie Ceny).")
    version = cfg.get(sec, "wersja", fallback="v6").strip() or "v6"
    return {"base": f"https://{domain}/api/admin/{version}", "key": key,
            "id_field": cfg.get(sec, "pole_id", fallback="productId").strip() or "productId",
            "price_field": cfg.get(sec, "pole_ceny", fallback="productRetailPrice").strip() or "productRetailPrice"}


def _request(method: str, url: str, key: str, body: dict | None = None) -> dict:
    import requests
    resp = requests.request(method, url, timeout=120, json=body,
                            headers={"X-API-KEY": key, "Accept": "application/json",
                                     "Content-Type": "application/json"})
    text = resp.text[:1500]
    if resp.status_code in (401, 403):
        raise ApiError(f"IdoSell odrzucił klucz API ({resp.status_code}). Sprawdź klucz i jego uprawnienia "
                       f"do produktów. Odpowiedź: {text}")
    if resp.status_code >= 400:
        raise ApiError(f"Błąd API {resp.status_code} dla {method} {url.split('?')[0]}. Odpowiedź: {text}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError(f"API nie zwróciło JSON (czy domena jest dobra?). Odpowiedź: {text}") from exc


def _find(obj, names: tuple[str, ...]):
    """Pierwsza wartość pola o jednej z nazw — gdziekolwiek w odpowiedzi."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and not isinstance(v, (dict, list)):
                return v
        for v in obj.values():
            hit = _find(v, names)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find(v, names)
            if hit is not None:
                return hit
    return None


def _faults(obj, out: list) -> list:
    """Błędy zgłoszone przez API dla pojedynczych produktów (faultCode ≠ 0)."""
    if isinstance(obj, dict):
        code = obj.get("faultCode")
        if code not in (None, 0, "0"):
            out.append(f"{obj.get('productId', obj.get('productIdent', '?'))}: {code} {obj.get('faultString', '')}")
        for v in obj.values():
            _faults(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _faults(v, out)
    return out


def test_read(cfg, product_id: str, log=print) -> dict:
    """Tylko odczyt: pobiera jeden produkt i pokazuje jego cenę — sprawdza klucz, domenę i adres API."""
    s = settings(cfg)
    data = _request("GET", f"{s['base']}/products/products?productIds={product_id}", s["key"])
    name = _find(data, ("productName", "name"))
    price = _find(data, (s["price_field"], "productRetailPrice", "retailPrice"))
    faults = _faults(data, [])
    if faults:
        raise ApiError("API odpowiedziało błędem: " + "; ".join(faults[:3]))
    log(f"Połączenie z IdoSell działa. Produkt {product_id}: {name or '(nazwa nieznana)'}, "
        f"cena detaliczna w sklepie: {price if price is not None else 'nie znaleziona w odpowiedzi'}")
    if price is None:
        log("Odpowiedź (początek): " + json.dumps(data, ensure_ascii=False)[:800])
    return {"name": name, "price": price}


def push_prices(cfg, rows: list[dict], log=print, out_dir: str = "") -> dict:
    """Wysyła ceny brutto (rows: id, cena_brutto) partiami. Zwraca liczby wysłanych i błędów."""
    s = settings(cfg)
    items = []
    for r in rows:
        if not str(r.get("id", "")).strip() or not str(r.get("cena_brutto", "")).strip():
            continue
        pid = str(r["id"]).strip()
        items.append({s["id_field"]: int(pid) if pid.isdigit() else pid, s["price_field"]: float(r["cena_brutto"])})
    sent, errors, log_lines = 0, [], []
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        data = _request("PUT", f"{s['base']}/products/products", s["key"], {"params": {"products": batch}})
        faults = _faults(data, [])
        errors += faults
        sent += len(batch) - len(faults)
        log_lines.append(json.dumps({"czas": time.strftime("%Y-%m-%d %H:%M:%S"), "wyslane": batch,
                                     "bledy": faults}, ensure_ascii=False))
        log(f"IdoSell: wysłano {min(start + BATCH, len(items))}/{len(items)} cen"
            + (f", błędy: {len(faults)}" if faults else ""))
    if out_dir:
        with open(os.path.join(out_dir, "idosell_wysylka.log"), "a", encoding="utf-8") as fh:
            fh.write("\n".join(log_lines) + "\n")
    for e in errors[:10]:
        log("  błąd: " + e)
    return {"wyslane": sent, "bledy": len(errors)}
