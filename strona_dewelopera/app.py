"""Strona inwestycji z jawnymi cenami + panel do zmiany cen.

  python3 app.py haslo        # ustaw hasło do panelu (/panel)
  python3 app.py              # uruchom stronę na http://127.0.0.1:8000

Na serwerze uruchamiaj przez gunicorn/waitress za nginx, np.:
  gunicorn -w 2 -b 127.0.0.1:8000 "app:create_app()"
"""
from __future__ import annotations

import getpass
import hmac
import json
import os
import secrets
import sys
from datetime import date
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.exceptions import NotFound
from werkzeug.security import check_password_hash, generate_password_hash

import baza
import eksport

KATALOG = Path(__file__).resolve().parent
PLIK_HASLA = KATALOG / ".haslo_panelu"
PLIK_SEKRETU = KATALOG / ".sekret_sesji"


def _sekret() -> str:
    if os.environ.get("STRONA_SEKRET"):
        return os.environ["STRONA_SEKRET"]
    if not PLIK_SEKRETU.exists():
        PLIK_SEKRETU.write_text(secrets.token_hex(32))
        PLIK_SEKRETU.chmod(0o600)
    return PLIK_SEKRETU.read_text().strip()


def create_app(sciezka_bazy=None, sciezka_konfiguracji=None, katalog_danych=None,
               hash_hasla=None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_sekret(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        BAZA=str(sciezka_bazy or os.environ.get("STRONA_BAZA", baza.DOMYSLNA_BAZA)),
        KONFIGURACJA=Path(sciezka_konfiguracji or KATALOG / "konfiguracja.json"),
        DANE=Path(katalog_danych or eksport.KATALOG_DANYCH),
        HASH_HASLA=hash_hasla,
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    )

    # ------------------------------------------------------------ pomocnicze

    def con():
        if "con" not in g:
            g.con = baza.polacz(app.config["BAZA"])
        return g.con

    @app.teardown_appcontext
    def zamknij(_):
        c = g.pop("con", None)
        if c is not None:
            c.close()

    def konf() -> dict:
        if "konf" not in g:
            g.konf = json.loads(app.config["KONFIGURACJA"].read_text(encoding="utf-8"))
        return g.konf

    def hash_hasla():
        if app.config["HASH_HASLA"]:
            return app.config["HASH_HASLA"]
        return PLIK_HASLA.read_text().strip() if PLIK_HASLA.exists() else None

    def token_csrf() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return session["csrf"]

    def sprawdz_csrf():
        oczekiwany = session.get("csrf")
        if not oczekiwany or not hmac.compare_digest(request.form.get("csrf", ""), oczekiwany):
            abort(400, "Formularz wygasł — odśwież stronę i spróbuj ponownie.")

    def tylko_zalogowany(widok):
        @wraps(widok)
        def opakowanie(*a, **kw):
            if not session.get("zalogowany"):
                return redirect(url_for("logowanie", dalej=request.path))
            if request.method == "POST":
                sprawdz_csrf()
            return widok(*a, **kw)
        return opakowanie

    @app.context_processor
    def wspolne():
        k = konf()
        return {"k": k, "csrf": token_csrf, "dzis": date.today().isoformat(),
                "adres_strony": k["strona"]["adres"].rstrip("/")}

    app.jinja_env.filters["zl"] = baza.gr_na_zl
    app.jinja_env.filters["m2"] = lambda v: "—" if v is None else f"{v:.2f}".replace(".", ",").rstrip("0").rstrip(",") + " m²"
    app.jinja_env.filters["szczyt"] = szczyt
    app.jinja_env.filters["data_pl"] = lambda d: "—" if not d else ".".join(reversed(str(d)[:10].split("-")))

    @app.after_request
    def naglowki(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path.startswith("/panel"):
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Frame-Options"] = "DENY"
        return resp

    # ------------------------------------------------------------ strona publiczna

    def podsumowanie(lokale):
        # Widełki cen tylko dla domów, które naprawdę można kupić (bez rezerwacji i sprzedanych).
        oferta = [l for l in lokale if l["status"] == "dostępny" and l["cena_m2_gr"]]
        return {
            "wszystkie": len(lokale),
            "dostepne": sum(l["status"] == "dostępny" for l in lokale),
            "sprzedane": sum(l["status"] == "sprzedany" for l in lokale),
            "min_m2": min((l["cena_m2_gr"] for l in oferta), default=None),
            "max_m2": max((l["cena_m2_gr"] for l in oferta), default=None),
            "min_cena": min((l["cena_gr"] for l in oferta), default=None),
            "max_cena": max((l["cena_gr"] for l in oferta), default=None),
            "aktualizacja": max((l["cena_od"] for l in lokale if l["cena_od"]), default=None),
        }

    @app.get("/")
    def strona_glowna():
        lokale = baza.lokale_z_cenami(con())
        p = podsumowanie(lokale)
        pytania = pytania_kupujacych(konf(), lokale, p)
        adres = konf()["strona"]["adres"].rstrip("/")
        return render_template(
            "index.html", lokale=lokale, p=p, zdjecia=_zdjecia_budowy(konf()), pytania=pytania,
            faq_ld=[{"@type": "Question", "name": q,
                     "acceptedAnswer": {"@type": "Answer", "text": o}} for q, o in pytania],
            oferty=[{"@type": "ListItem", "position": i + 1, "item": oferta_ld(adres, l)}
                    for i, l in enumerate(lokale)])

    @app.get("/dom/<oznaczenie>")
    def lokal(oznaczenie):
        lokale = baza.lokale_z_cenami(con())
        l = next((x for x in lokale if x["oznaczenie"] == oznaczenie), None)
        if l is None:
            abort(404)
        historia = baza.historia_cen(con(), l["id"], l["powierzchnia"])
        return render_template("lokal.html", l=l, historia=historia, lokale=lokale)

    @app.post("/zapytanie")
    def zapytanie():
        if request.form.get("strona_www"):          # pułapka na boty
            return redirect(url_for("strona_glowna"))
        imie = request.form.get("imie", "").strip()[:120]
        kontakt = request.form.get("kontakt", "").strip()[:200]
        tresc = request.form.get("tresc", "").strip()[:4000]
        lokal_id = request.form.get("lokal_id", type=int)
        if not imie or not kontakt:
            flash("Podaj imię i telefon albo e-mail — inaczej nie oddzwonimy.", "blad")
        else:
            con().execute("INSERT INTO zapytania (lokal_id, imie, kontakt, tresc) VALUES (?, ?, ?, ?)",
                          (lokal_id, imie, kontakt, tresc))
            con().commit()
            _powiadom_o_zapytaniu(konf(), imie, kontakt, tresc, lokal_id)
            flash("Dziękujemy. Odezwiemy się najszybciej, jak to możliwe.", "ok")
        powrot = request.form.get("powrot") or url_for("strona_glowna")
        if not powrot.startswith("/") or powrot.startswith("//"):
            powrot = url_for("strona_glowna")
        return redirect(powrot + "#kontakt")

    @app.get("/prospekt")
    def prospekt():
        plik = konf()["inwestycja"].get("prospekt")
        if not plik or not (KATALOG / "static" / plik).exists():
            abort(404, "Prospekt informacyjny nie został jeszcze dodany.")
        return send_from_directory(KATALOG / "static", plik, mimetype="application/pdf")

    @app.get("/dane/<path:plik>")
    def dane(plik):
        typy = {".csv": "text/csv; charset=utf-8", ".xml": "application/xml",
                ".md5": "text/plain; charset=us-ascii"}
        typ = typy.get(Path(plik).suffix)
        if typ is None:
            abort(404)
        resp = send_from_directory(app.config["DANE"], plik, mimetype=typ.split(";")[0], max_age=300)
        resp.headers["Content-Type"] = typ
        return resp

    @app.get("/robots.txt")
    def robots():
        adres = konf()["strona"]["adres"].rstrip("/")
        boty = ["GPTBot", "ChatGPT-User", "OAI-SearchBot", "PerplexityBot", "ClaudeBot",
                "Google-Extended", "Bingbot", "*"]
        tresc = "".join(f"User-agent: {b}\nAllow: /\nDisallow: /panel\n\n" for b in boty)
        return Response(tresc + f"Sitemap: {adres}/sitemap.xml\n", mimetype="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        adres = konf()["strona"]["adres"].rstrip("/")
        lokale = baza.lokale_z_cenami(con())
        ost = max((l["cena_od"] for l in lokale if l["cena_od"]), default=date.today().isoformat())
        urle = [(f"{adres}/", ost)] + [(f"{adres}/dom/{l['oznaczenie']}", l["cena_od"] or ost) for l in lokale]
        xml = "".join(f"<url><loc>{u}</loc><lastmod>{d}</lastmod></url>" for u, d in urle)
        return Response('<?xml version="1.0" encoding="UTF-8"?>'
                        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{xml}</urlset>',
                        mimetype="application/xml")

    @app.get("/llms.txt")
    def llms():
        lokale = baza.lokale_z_cenami(con())
        return Response(render_template("llms.txt", lokale=lokale, p=podsumowanie(lokale)),
                        mimetype="text/plain")

    @app.errorhandler(404)
    def nie_ma(e):
        opis = "" if e.description == NotFound.description else e.description
        return render_template("blad.html", kod=404, opis=opis), 404

    @app.errorhandler(400)
    def zle(e):
        return render_template("blad.html", kod=400, opis=e.description), 400

    # ------------------------------------------------------------ panel

    @app.route("/panel/logowanie", methods=["GET", "POST"])
    def logowanie():
        if request.method == "POST":
            sprawdz_csrf()
            h = hash_hasla()
            if h and check_password_hash(h, request.form.get("haslo", "")):
                session.clear()
                session["zalogowany"] = True
                dalej = request.args.get("dalej", "")
                return redirect(dalej if dalej.startswith("/panel") else url_for("panel"))
            flash("Nieprawidłowe hasło." if h else
                  "Hasło nie jest ustawione. Uruchom na serwerze: python3 app.py haslo", "blad")
        return render_template("panel/logowanie.html")

    @app.post("/panel/wyloguj")
    @tylko_zalogowany
    def wyloguj():
        session.clear()
        return redirect(url_for("strona_glowna"))

    @app.route("/panel", methods=["GET", "POST"])
    @tylko_zalogowany
    def panel():
        if request.method == "POST":
            zmienione = 0
            bledy = []
            for l in baza.lokale_z_cenami(con()):
                cena = request.form.get(f"cena_{l['id']}", "").strip()
                status = request.form.get(f"status_{l['id']}")
                try:
                    if cena and baza.ustaw_cene(con(), l["id"], baza.zl_na_gr(cena)):
                        zmienione += 1
                    if status and status != l["status"]:
                        baza.zmien_lokal(con(), l["id"], status=status)
                        zmienione += 1
                except ValueError as e:
                    bledy.append(f"{l['oznaczenie']}: {e}")
            for b in bledy:
                flash(b, "blad")
            flash(f"Zapisano zmiany: {zmienione}. Do dane.gov.pl trafią dziś w nocy." if zmienione
                  else "Nic się nie zmieniło.", "ok")
            return redirect(url_for("panel"))
        ost = con().execute("SELECT * FROM eksporty ORDER BY data DESC LIMIT 1").fetchone()
        nowe = con().execute("SELECT COUNT(*) FROM zapytania WHERE utworzono >= date('now','-7 day')").fetchone()[0]
        return render_template("panel/lista.html", lokale=baza.lokale_z_cenami(con()),
                               statusy=baza.STATUSY, ostatni_eksport=ost, nowe_zapytania=nowe)

    @app.route("/panel/lokal/<int:lokal_id>", methods=["GET", "POST"])
    @app.route("/panel/lokal/nowy", methods=["GET", "POST"], defaults={"lokal_id": None})
    @tylko_zalogowany
    def panel_lokal(lokal_id):
        c = con()
        if request.method == "POST":
            akcja = request.form.get("akcja", "zapisz")
            try:
                if akcja == "zapisz":
                    pola = {k: (request.form.get(k) or None) for k in baza.EDYTOWALNE if k in request.form}
                    for k in ("powierzchnia", "ogrodek", "dzialka"):
                        if pola.get(k):
                            pola[k] = float(pola[k].replace(",", "."))
                    for k in ("pokoje", "kolejnosc"):
                        if pola.get(k):
                            pola[k] = int(pola[k])
                    if lokal_id is None:
                        if not pola.get("oznaczenie") or not pola.get("powierzchnia"):
                            raise ValueError("Podaj oznaczenie i powierzchnię użytkową")
                        cena = request.form.get("cena", "").strip()
                        if not cena:
                            raise ValueError("Podaj cenę za m² — bez niej lokal nie może być w ofercie")
                        pola.setdefault("status", "dostępny")
                        lokal_id = baza.dodaj_lokal(c, **{k: v for k, v in pola.items() if v is not None})
                        baza.ustaw_cene(c, lokal_id, baza.zl_na_gr(cena))
                        flash("Dodano lokal.", "ok")
                    else:
                        baza.zmien_lokal(c, lokal_id, **pola)
                        flash("Zapisano.", "ok")
                elif akcja == "dodaj_dodatek":
                    w_cenie = bool(request.form.get("w_cenie"))
                    cena = request.form.get("cena_dodatku", "").strip()
                    baza.dodaj_dodatek(c, lokal_id, request.form["rodzaj"],
                                       request.form.get("oznaczenie_dodatku", "").strip() or request.form["rodzaj"],
                                       None if w_cenie or not cena else baza.zl_na_gr(cena), w_cenie)
                    flash("Dodano.", "ok")
                elif akcja == "cena_dodatku":
                    w_cenie = bool(request.form.get("w_cenie"))
                    cena = request.form.get("cena_dodatku", "").strip()
                    baza.ustaw_cene_dodatku(c, int(request.form["dodatek_id"]),
                                            None if w_cenie or not cena else baza.zl_na_gr(cena), w_cenie)
                    flash("Zapisano cenę dodatku.", "ok")
                elif akcja == "usun_dodatek":
                    baza.usun_dodatek(c, int(request.form["dodatek_id"]))
                    flash("Usunięto dodatek.", "ok")
            except (ValueError, KeyError) as e:
                c.rollback()
                flash(f"Nie zapisano: {e}", "blad")
                if lokal_id is None:
                    return redirect(url_for("panel_lokal"))
            return redirect(url_for("panel_lokal", lokal_id=lokal_id))

        l = None
        historia = []
        if lokal_id is not None:
            l = next((x for x in baza.lokale_z_cenami(c) if x["id"] == lokal_id), None)
            if l is None:
                abort(404)
            historia = baza.historia_cen(c, lokal_id, l["powierzchnia"])
        return render_template("panel/lokal.html", l=l, historia=historia,
                               rodzaje=baza.RODZAJE_LOKALI, statusy=baza.STATUSY,
                               rodzaje_dodatkow=list(baza.RODZAJE_DODATKOW),
                               inne=baza.lokale_z_cenami(c))

    @app.get("/panel/zapytania")
    @tylko_zalogowany
    def panel_zapytania():
        wiersze = con().execute(
            "SELECT z.*, l.oznaczenie FROM zapytania z LEFT JOIN lokale l ON l.id = z.lokal_id "
            "ORDER BY z.utworzono DESC").fetchall()
        return render_template("panel/zapytania.html", zapytania=wiersze)

    @app.post("/panel/eksport")
    @tylko_zalogowany
    def panel_eksport():
        try:
            plik = eksport.eksportuj(con(), konf(), app.config["DANE"])
            flash(f"Plik {plik.name} gotowy. Harvester dane.gov.pl pobierze go przy najbliższym przebiegu.", "ok")
        except Exception as e:  # pokazujemy klientowi, zamiast błędu 500
            flash(f"Eksport się nie udał: {e}", "blad")
        return redirect(url_for("panel"))

    return app


def oferta_ld(adres: str, l: dict) -> dict:
    typ = "SingleFamilyResidence" if l["rodzaj"] == "dom jednorodzinny" else "Apartment"
    oferta = {"@type": "Offer", "priceCurrency": "PLN",
              "availability": {"dostępny": "https://schema.org/InStock",
                               "zarezerwowany": "https://schema.org/LimitedAvailability",
                               "sprzedany": "https://schema.org/SoldOut"}[l["status"]],
              "url": f"{adres}/dom/{l['oznaczenie']}",
              "itemOffered": {"@type": typ, "name": f"Dom {l['oznaczenie']}",
                              "floorSize": {"@type": "QuantitativeValue", "value": l["powierzchnia"],
                                            "unitCode": "MTK"},
                              "numberOfRooms": l["pokoje"]}}
    if l["cena_gr"] is not None:
        oferta["price"] = f"{l['cena_gr'] / 100:.2f}"
        oferta["validFrom"] = l["cena_od"]
    return oferta


def pytania_kupujacych(k: dict, lokale: list[dict], p: dict) -> list[tuple[str, str]]:
    """FAQ budowane WYŁĄCZNIE z danych klienta — nic nie dopisujemy od siebie."""
    zl = baza.gr_na_zl
    inw = k["inwestycja"]
    q = []
    if p["min_cena"] is not None:
        q.append((f"Ile kosztuje dom w inwestycji {inw['nazwa']}?",
                  f"Wolne domy kosztują od {zl(p['min_cena'])} do {zl(p['max_cena'])} brutto, czyli od "
                  f"{zl(p['min_m2'])} do {zl(p['max_m2'])} za m² powierzchni użytkowej. Ceny wszystkich "
                  f"domów, także sprzedanych, z historią zmian, są w cenniku na tej stronie."))
    w_cenie = sorted({d["oznaczenie"] for l in lokale for d in l["dodatki"] if d["w_cenie"]})
    platne = sorted({(d["oznaczenie"], d["cena_gr"]) for l in lokale for d in l["dodatki"] if not d["w_cenie"]})
    if w_cenie or platne:
        czesci = []
        if w_cenie:
            czesci.append("W cenie domu: " + ", ".join(w_cenie) + ".")
        if platne:
            czesci.append("Płatne dodatkowo: " + ", ".join(f"{o} — {zl(c)}" for o, c in platne) + ".")
        q.append(("Co jest w cenie domu, a za co trzeba dopłacić?", " ".join(czesci)))
    terminy = sorted({l["termin"] for l in lokale if l["termin"]})
    if terminy:
        q.append(("Kiedy domy będą gotowe?", "Planowany termin oddania: " + "; ".join(terminy) + "."))
    if inw.get("standard"):
        q.append((f"W jakim standardzie są domy w inwestycji {inw['nazwa']}?",
                  "W standardzie: " + ", ".join(inw["standard"][:8]) + "."))
    a = inw["adres"]
    gdzie = f"{a.get('ulica', '')} {a.get('nr_nieruchomosci', '')}, {a.get('kod_pocztowy', '')} {a.get('miejscowosc', '')}".strip()
    q.append((f"Gdzie jest inwestycja {inw['nazwa']}?",
              gdzie + (f" — {inw['polozenie']}." if inw.get("polozenie") else ".")))
    q.append(("Dlaczego ceny domów są jawne?",
              "Wymaga tego ustawa o ochronie praw nabywcy lokalu mieszkalnego lub domu jednorodzinnego "
              "oraz Deweloperskim Funduszu Gwarancyjnym. Deweloper publikuje ceny na stronie inwestycji i "
              "codziennie przekazuje je do portalu dane.gov.pl. Każda zmiana ceny jest zapisywana z datą, "
              "a domy sprzedane zostają w cenniku razem z historią cen."))
    kontakt = k["kontakt"]
    q.append(("Jak umówić się na rozmowę o zakupie?",
              f"Zadzwoń pod numer {kontakt['telefon']}"
              + (f", napisz na {kontakt['email_publiczny']}" if kontakt.get("email_publiczny") else "")
              + " albo wyślij formularz na dole strony."
              + (f" Kontakt: {k['sposob_kontaktu']}." if k.get("sposob_kontaktu") else "")))
    return q


def szczyt(obrys: str | None):
    """Najwyższy punkt obrysu (x, y w %) — tam wieszamy metkę z ceną."""
    try:
        punkty = [tuple(float(v) for v in p.split(",")) for p in (obrys or "").split()]
    except ValueError:
        return None
    if len(punkty) < 3:
        return None
    return min(punkty, key=lambda p: p[1])


def _zdjecia_budowy(k):
    zdj = k["inwestycja"].get("budowa") or []
    return sorted([z for z in zdj if (KATALOG / "static" / "budowa" / z["plik"]).exists()],
                  key=lambda z: z.get("data", ""), reverse=True)


def _powiadom_o_zapytaniu(k, imie, kontakt, tresc, lokal_id):
    s = k.get("smtp") or {}
    adres = k["kontakt"].get("email_zapytania")
    if not (s.get("host") and adres):
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = f"[{k['inwestycja']['nazwa']}] Zapytanie ze strony: {imie}"
    msg["From"] = s.get("nadawca") or s.get("uzytkownik") or adres
    msg["To"] = adres
    msg.set_content(f"Imię: {imie}\nKontakt: {kontakt}\nLokal (id): {lokal_id or '—'}\n\n{tresc}")
    try:
        with smtplib.SMTP(s["host"], int(s.get("port") or 587), timeout=20) as smtp:
            smtp.starttls()
            if s.get("uzytkownik"):
                smtp.login(s["uzytkownik"], os.environ.get("STRONA_SMTP_HASLO", ""))
            smtp.send_message(msg)
    except Exception as e:  # zapytanie i tak jest w panelu
        print(f"Nie wysłano powiadomienia o zapytaniu: {e}", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["haslo"]:
        h1 = getpass.getpass("Nowe hasło do panelu: ")
        if len(h1) < 10:
            sys.exit("Hasło musi mieć co najmniej 10 znaków.")
        if getpass.getpass("Powtórz: ") != h1:
            sys.exit("Hasła się różnią.")
        PLIK_HASLA.write_text(generate_password_hash(h1))
        PLIK_HASLA.chmod(0o600)
        print("Hasło zapisane.")
    else:
        create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), debug=False)
