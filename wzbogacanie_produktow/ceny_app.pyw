"""Program „Ceny Onninen” — okno do ustawiania marż i przeliczania cen sklepu z cennika Onninen (XML).

Uruchomienie: dwuklik na ceny_app.pyw (albo: py ceny_app.pyw).
Liczenie robi ceny.py — ten sam, który uruchamia się automatycznie z Harmonogramu zadań Windows.
"""
from __future__ import annotations

import configparser
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import ceny

ROUNDING = {"grosz (0,01)": "0.01", "10 groszy (0,10)": "0.10", "pełne złote (1)": "1", "końcówka ,99": "0.99"}
TASK_PREFIX = "IHT Ceny Onninen"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        os.chdir(ceny.HERE)
        self.title("Ceny Onninen — marże i aktualizacja cen sklepu")
        self.geometry("1220x800")
        self.minsize(860, 600)
        self.cfg = ceny.load_settings()
        self.log_queue: queue.Queue = queue.Queue()
        self.running = False

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_settings = ttk.Frame(tabs, padding=10)
        self.tab_result = ttk.Frame(tabs, padding=10)
        tabs.add(self.tab_settings, text="Ustawienia i marże")
        tabs.add(self.tab_result, text="Przeliczenie i wynik")
        self.tab_api = ttk.Frame(tabs, padding=10)
        tabs.add(self.tab_api, text="IdoSell API (ceny do sklepu)")
        self.tabs = tabs
        self.build_settings()
        self.build_result()
        self.build_api()
        self.loading = True
        self.load_into_form()
        self.loading = False
        self.update_example()
        self.after(150, self.pump_log)

    # ------------------------------------------------------------------ zakładka: ustawienia
    def build_settings(self) -> None:
        f = self.tab_settings
        f.columnconfigure(1, weight=1)

        src = ttk.LabelFrame(f, text="Cennik i produkty", padding=8)
        src.grid(row=0, column=0, columnspan=2, sticky="ew")
        src.columnconfigure(1, weight=1)
        self.v_xml, self.v_shop, self.v_out = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for r, (label, var, browse) in enumerate([
                ("Link do XML Onninen (albo plik):", self.v_xml, "xml"),
                ("Produkty sklepu (CSV albo link do arkusza Google):", self.v_shop, "csv"),
                ("Folder wyników (puste = folder programu):", self.v_out, "dir")]):
            ttk.Label(src, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(src, textvariable=var).grid(row=r, column=1, sticky="ew", padx=4)
            ttk.Button(src, text="…", width=3, command=lambda v=var, b=browse: self.browse(v, b)).grid(row=r, column=2)

        mode = ttk.LabelFrame(f, text="Sposób liczenia marży", padding=8)
        mode.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.v_mode, self.v_flat = tk.StringVar(value="progi"), tk.StringVar(value="25")
        ttk.Radiobutton(mode, text="Jedna marża dla wszystkich produktów:", value="jedna",
                        variable=self.v_mode).pack(side="left")
        ttk.Entry(mode, textvariable=self.v_flat, width=6).pack(side="left", padx=4)
        ttk.Label(mode, text="%").pack(side="left")
        ttk.Radiobutton(mode, text="Marża wg progów ceny i wyjątków producentów (tabele niżej)", value="progi",
                        variable=self.v_mode).pack(side="left", padx=(30, 0))
        for v in (self.v_mode, self.v_flat):
            v.trace_add("write", lambda *_: self.update_example())

        tiers = ttk.LabelFrame(f, text="Marża wg ceny zakupu netto", padding=8)
        tiers.grid(row=2, column=0, sticky="nsew", pady=8, padx=(0, 4))
        self.tiers = self.editable_table(tiers, ("Cena zakupu od (zł)", "Marża %"))
        ttk.Label(tiers, text="Każdy próg obowiązuje od swojej kwoty w górę.\n"
                              "Jedna stała marża = jeden wiersz od 0 zł.", foreground="#555").pack(anchor="w")

        prod = ttk.LabelFrame(f, text="Wyjątki dla producentów (zamiast progów)", padding=8)
        prod.grid(row=2, column=1, sticky="nsew", pady=8, padx=(4, 0))
        self.producers = self.editable_table(prod, ("Producent (jak w sklepie)", "Marża %"))

        other = ttk.LabelFrame(f, text="Zasady ceny", padding=8)
        other.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.v_min, self.v_vat, self.v_round = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(other, text="Minimalny zysk netto na sztuce (zł):").grid(row=0, column=0, sticky="w")
        ttk.Entry(other, textvariable=self.v_min, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(other, text="VAT %:").grid(row=0, column=2, sticky="w", padx=(16, 0))
        ttk.Entry(other, textvariable=self.v_vat, width=5).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(other, text="Zaokrąglenie w górę:").grid(row=0, column=4, sticky="w", padx=(16, 0))
        ttk.Combobox(other, textvariable=self.v_round, values=list(ROUNDING), state="readonly",
                     width=16).grid(row=0, column=5, sticky="w", padx=4)
        self.v_pieces, self.v_cap = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        ttk.Checkbutton(other, text="Minimalny zysk tylko dla sztuk (nie dla metrów, kg, litrów…)",
                        variable=self.v_pieces).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(other, text="Nie drożej niż cena katalogowa Onninen (brutto)",
                        variable=self.v_cap).grid(row=1, column=3, columnspan=3, sticky="w", pady=(6, 0))
        self.v_minmargin = tk.StringVar(value="5")
        ttk.Label(other, text="Cena minimalna (dla Szpiegomatu): zakup + marża %:").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Entry(other, textvariable=self.v_minmargin, width=5).grid(row=2, column=3, sticky="w", padx=4, pady=(6, 0))
        self.v_example = tk.StringVar()
        ttk.Label(other, textvariable=self.v_example, foreground="#0a5", wraplength=900).grid(
            row=3, column=0, columnspan=6, sticky="w", pady=(6, 0))
        for v in (self.v_min, self.v_vat, self.v_round, self.v_pieces, self.v_cap):
            v.trace_add("write", lambda *_: self.update_example())

        auto = ttk.LabelFrame(f, text="Automat i wysyłka do IdoSell", padding=8)
        auto.grid(row=4, column=0, columnspan=2, sticky="ew", pady=8)
        self.v_hours = tk.StringVar()
        ttk.Label(auto, text="Przeliczaj automatycznie o godzinach:").grid(row=0, column=0, sticky="w")
        ttk.Entry(auto, textvariable=self.v_hours, width=20).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Button(auto, text="Włącz automat", command=self.enable_schedule).grid(row=0, column=2, padx=4)
        ttk.Button(auto, text="Wyłącz automat", command=self.disable_schedule).grid(row=0, column=3, padx=4)
        self.v_ftp_host, self.v_ftp_user, self.v_ftp_pass, self.v_ftp_dir = (tk.StringVar() for _ in range(4))
        ftp = ttk.Frame(auto)
        ftp.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        for label, var, width, secret in (("FTP (opcjonalnie) host:", self.v_ftp_host, 22, False),
                                          ("użytkownik:", self.v_ftp_user, 14, False),
                                          ("hasło:", self.v_ftp_pass, 12, True),
                                          ("folder:", self.v_ftp_dir, 10, False)):
            ttk.Label(ftp, text=label).pack(side="left", padx=(8, 2))
            ttk.Entry(ftp, textvariable=var, width=width, show="•" if secret else "").pack(side="left")
        ttk.Label(auto, text="Z FTP IdoSell pobiera ceny_wszystkie.csv sam (Produkty → Import → „Synchronizować "
                             "towary w sposób ciągły od dostawcy”).", foreground="#555").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        bar = ttk.Frame(f)
        bar.grid(row=5, column=0, columnspan=2, sticky="ew")
        ttk.Button(bar, text="Zapisz ustawienia", command=self.save).pack(side="left")
        ttk.Button(bar, text="Sprawdź XML", command=self.check_xml).pack(side="left", padx=6)
        ttk.Button(bar, text="Przelicz ceny teraz", command=self.run_now).pack(side="right")
        f.rowconfigure(2, weight=1)

    def editable_table(self, parent, headings) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=("a", "b"), show="headings", height=6)
        for col, h in zip(("a", "b"), headings):
            tree.heading(col, text=h)
            tree.column(col, width=150)
        tree.pack(fill="both", expand=True)
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        a, b = tk.StringVar(), tk.StringVar()
        ttk.Entry(row, textvariable=a, width=18).pack(side="left")
        ttk.Entry(row, textvariable=b, width=8).pack(side="left", padx=4)

        def add():
            if a.get().strip() and b.get().strip():
                tree.insert("", "end", values=(a.get().strip(), b.get().strip()))
                a.set("")
                b.set("")
                self.update_example()

        def remove():
            for item in tree.selection():
                tree.delete(item)
            self.update_example()

        def pick(_):
            sel = tree.selection()
            if sel:
                va, vb = tree.item(sel[0], "values")
                a.set(va)
                b.set(vb)

        ttk.Button(row, text="Dodaj", command=add).pack(side="left")
        ttk.Button(row, text="Usuń zaznaczone", command=remove).pack(side="left", padx=4)
        tree.bind("<<TreeviewSelect>>", pick)
        return tree

    def browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "dir":
            path = filedialog.askdirectory()
        else:
            path = filedialog.askopenfilename(filetypes=[("XML", "*.xml")] if kind == "xml" else [("CSV", "*.csv")])
        if path:
            var.set(path)

    # ------------------------------------------------------------------ ustawienia <-> formularz
    def load_into_form(self) -> None:
        c = self.cfg
        self.v_xml.set(c.get("pliki", "xml", fallback=""))
        self.v_shop.set(c.get("pliki", "produkty", fallback="produkty.csv"))
        self.v_out.set(c.get("pliki", "folder_wynikow", fallback=""))
        for start, pct in ceny.parse_tiers(c.get("marza", "progi", fallback="0:25")):
            self.tiers.insert("", "end", values=(f"{start:g}", f"{pct:g}"))
        if c.has_section("producenci"):
            for k, v in c.items("producenci"):
                self.producers.insert("", "end", values=(k, v))
        self.v_mode.set(c.get("marza", "tryb", fallback="progi"))
        self.v_flat.set(c.get("marza", "jedna_marza", fallback="25"))
        self.v_min.set(c.get("marza", "minimalny_zysk_zl", fallback="2"))
        self.v_minmargin.set(c.get("marza", "marza_minimalna", fallback="5"))
        self.v_vat.set(c.get("marza", "vat", fallback="23"))
        code = c.get("marza", "zaokraglenie", fallback="0.99").strip()
        self.v_round.set(next((k for k, v in ROUNDING.items() if v == code), "końcówka ,99"))
        self.v_pieces.set(c.getboolean("marza", "minimalny_zysk_tylko_sztuki", fallback=True))
        self.v_cap.set(c.getboolean("marza", "nie_drozej_niz_katalogowa", fallback=False))
        self.v_hours.set(c.get("automat", "godziny", fallback="07:30, 14:30"))
        self.v_ftp_host.set(c.get("ftp", "host", fallback=""))
        self.v_ftp_user.set(c.get("ftp", "uzytkownik", fallback=""))
        self.v_ftp_pass.set(c.get("ftp", "haslo", fallback=""))
        self.v_ftp_dir.set(c.get("ftp", "folder", fallback=""))
        self.v_api_domain.set(c.get("idosell", "domena", fallback="iht.com.pl"))
        self.v_api_key.set(c.get("idosell", "klucz", fallback=""))
        self.v_api_auto.set(c.getboolean("idosell", "wysylaj_automatycznie", fallback=False))
        self.v_api_floor.set(c.get("idosell", "tryb_wysylki", fallback="ceny").strip() == "zakup_minimum")
        self.refresh_api_state()

    def form_to_cfg(self) -> configparser.ConfigParser:
        # Kopia — podgląd przykładu nie może zmieniać ustawień, zanim formularz się wczyta.
        c = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        c.optionxform = str
        c.read_dict(self.cfg)
        for sec in ("pliki", "marza", "producenci", "automat", "ftp", "idosell"):
            if not c.has_section(sec):
                c.add_section(sec)
        c.set("pliki", "xml", self.v_xml.get().strip())
        c.set("pliki", "produkty", self.v_shop.get().strip())
        c.set("pliki", "folder_wynikow", self.v_out.get().strip())
        rows = [self.tiers.item(i, "values") for i in self.tiers.get_children()]
        tiers = sorted((ceny.to_float(a), ceny.to_float(b)) for a, b in rows)
        if not tiers:
            raise ValueError("Dodaj co najmniej jeden próg marży (np. od 0 zł).")
        c.set("marza", "progi", ", ".join(f"{a:g}:{b:g}" for a, b in tiers))
        c.set("marza", "tryb", self.v_mode.get())
        c.set("marza", "jedna_marza", f"{ceny.to_float(self.v_flat.get() or 0):g}")
        c.set("marza", "minimalny_zysk_zl", f"{ceny.to_float(self.v_min.get() or 0):g}")
        c.set("marza", "vat", f"{ceny.to_float(self.v_vat.get() or 23):g}")
        c.set("marza", "marza_minimalna", f"{ceny.to_float(self.v_minmargin.get() or 0):g}")
        c.set("marza", "zaokraglenie", ROUNDING.get(self.v_round.get(), "0.01"))
        c.set("marza", "minimalny_zysk_tylko_sztuki", "true" if self.v_pieces.get() else "false")
        c.set("marza", "nie_drozej_niz_katalogowa", "true" if self.v_cap.get() else "false")
        c.remove_section("producenci")
        c.add_section("producenci")
        for i in self.producers.get_children():
            name, pct = self.producers.item(i, "values")
            c.set("producenci", str(name).strip().upper(), f"{ceny.to_float(pct):g}")
        c.set("automat", "godziny", self.v_hours.get().strip())
        c.set("ftp", "host", self.v_ftp_host.get().strip())
        c.set("ftp", "uzytkownik", self.v_ftp_user.get().strip())
        c.set("ftp", "haslo", self.v_ftp_pass.get())
        c.set("ftp", "folder", self.v_ftp_dir.get().strip())
        c.set("idosell", "domena", self.v_api_domain.get().strip())
        c.set("idosell", "klucz", self.v_api_key.get().strip())
        c.set("idosell", "wysylaj_automatycznie", "true" if self.v_api_auto.get() else "false")
        c.set("idosell", "tryb_wysylki", "zakup_minimum" if self.v_api_floor.get() else "ceny")
        if not c.has_option("idosell", "potwierdzone"):
            c.set("idosell", "potwierdzone", "false")
        return c

    def save(self, quiet: bool = False) -> bool:
        try:
            cfg = self.form_to_cfg()
        except ValueError as exc:
            messagebox.showerror("Błąd w ustawieniach", str(exc))
            return False
        self.cfg = cfg
        with open(ceny.SETTINGS, "w", encoding="utf-8") as fh:
            fh.write("# Ustawienia programu Ceny Onninen (zapisane z okna programu).\n")
            cfg.write(fh)
        if not quiet:
            messagebox.showinfo("Zapisano", "Ustawienia zapisane. Automat użyje ich przy następnym przeliczeniu.")
        return True

    def update_example(self) -> None:
        if getattr(self, "loading", False):
            return
        try:
            cfg = self.form_to_cfg()
            s = ceny.settings_from(cfg)
        except Exception:
            self.v_example.set("Przykład: uzupełnij progi marży.")
            return
        parts = []
        for buy in (10, 100, 1000):
            net, gross, pct, _ = ceny.sale_price(buy, "", s)
            parts.append(f"zakup {buy} zł netto → {gross:.2f} zł brutto (+{pct:g}%)")
        self.v_example.set("Przykład:  " + "   |   ".join(parts))

    # ------------------------------------------------------------------ automat (Harmonogram zadań Windows)
    def enable_schedule(self) -> None:
        if not self.save(quiet=True):
            return
        if os.name != "nt":
            messagebox.showinfo("Automat", "Automat działa w Windows (Harmonogram zadań).")
            return
        hours = [h.strip() for h in self.v_hours.get().replace(";", ",").split(",") if h.strip()]
        if not hours or not all(len(h) == 5 and h[2] == ":" for h in hours):
            messagebox.showerror("Automat", "Wpisz godziny w formacie GG:MM, oddzielone przecinkiem, np. 07:30, 14:30")
            return
        self.disable_schedule(quiet=True)
        script = os.path.join(ceny.HERE, "ceny.ps1")
        action = f'powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}"'
        errors = []
        for h in hours:
            name = f"{TASK_PREFIX} {h.replace(':', '-')}"
            res = subprocess.run(["schtasks", "/Create", "/F", "/TN", name, "/SC", "DAILY", "/ST", h, "/TR", action],
                                 capture_output=True, text=True)
            if res.returncode:
                errors.append(f"{h}: {res.stderr.strip() or res.stdout.strip()}")
        if errors:
            messagebox.showerror("Automat", "Nie udało się dodać zadań:\n" + "\n".join(errors))
        else:
            messagebox.showinfo("Automat", "Automat włączony: ceny przeliczą się codziennie o " + ", ".join(hours)
                                + ".\nKomputer musi być wtedy włączony. Historia: ceny.log.")

    def disable_schedule(self, quiet: bool = False) -> None:
        if os.name != "nt":
            return
        res = subprocess.run(["schtasks", "/Query", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        names = {line.split('","')[0].strip('"').lstrip("\\") for line in res.stdout.splitlines()
                 if TASK_PREFIX in line}
        for name in names:
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", name], capture_output=True)
        if not quiet:
            messagebox.showinfo("Automat", f"Automat wyłączony (usunięte zadania: {len(names)}).")

    # ------------------------------------------------------------------ zakładka: IdoSell API
    def build_api(self) -> None:
        f = self.tab_api
        f.columnconfigure(1, weight=1)
        self.v_api_domain, self.v_api_key = tk.StringVar(), tk.StringVar()
        self.v_api_test_id, self.v_api_auto = tk.StringVar(), tk.BooleanVar(value=False)
        self.v_api_floor = tk.BooleanVar(value=False)
        self.v_api_state = tk.StringVar()
        ttk.Label(f, text="Ceny trafiają do sklepu bez wgrywania plików. Klucz utworzysz w IdoSell: Klucze dostępowe "
                          "do Admin API → Dodaj nowy klucz (uprawnienia: produkty). Klucz zostaje tylko na tym "
                          "komputerze — nie wysyłaj go nikomu.", wraplength=950, foreground="#555").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(f, text="Domena sklepu:").grid(row=1, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_api_domain, width=40).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(f, text="Klucz API:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.v_api_key, width=70, show="•").grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(f, text="ID produktu do testu (z panelu):").grid(row=3, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_api_test_id, width=12).grid(row=3, column=1, sticky="w", padx=4)
        ttk.Checkbutton(f, text="Ceny sprzedaży ustawia Szpiegomat (dynamic pricing) — wysyłaj do sklepu TYLKO cenę "
                                "zakupu i cenę minimalną, nie ruszaj cen sprzedaży",
                        variable=self.v_api_floor).grid(row=7, column=0, columnspan=3, sticky="w", pady=(0, 4))
        steps = ttk.LabelFrame(f, text="Bezpieczny start — po kolei", padding=8)
        steps.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        for r, (text, hint, cmd) in enumerate([
                ("1. Test połączenia", "Tylko odczyt: pokaże nazwę i obecną cenę produktu o podanym ID.",
                 self.api_test),
                ("2. Próba: wyślij 3 ceny", "Wyśle nowe ceny 3 produktów z ostatniego przeliczenia — sprawdź je "
                 "w sklepie.", self.api_try3),
                ("3. Wyślij wszystkie zmiany", "Wyśle ceny z ceny_do_importu.csv (tylko zmienione od ostatniego razu).",
                 self.api_send_all)]):
            ttk.Button(steps, text=text, width=28, command=cmd).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Label(steps, text=hint, foreground="#555").grid(row=r, column=1, sticky="w", padx=10)
        self.chk_auto = ttk.Checkbutton(f, text="Automat (godziny z ustawień) sam wysyła zmienione ceny do sklepu",
                                        variable=self.v_api_auto)
        self.chk_auto.grid(row=5, column=0, columnspan=3, sticky="w")
        ttk.Label(f, textvariable=self.v_api_state, foreground="#a60").grid(row=6, column=0, columnspan=3,
                                                                           sticky="w", pady=6)

    def refresh_api_state(self) -> None:
        ok = self.cfg.getboolean("idosell", "potwierdzone", fallback=False)
        self.chk_auto.configure(state="normal" if ok else "disabled")
        self.v_api_state.set("Próba potwierdzona — można wysyłać wszystko i włączyć automat." if ok else
                             "Najpierw zrób kroki 1 i 2 i potwierdź, że ceny w sklepie są dobre. Do tego czasu "
                             "automat nie wysyła cen.")

    def api_test(self) -> None:
        if not self.save(quiet=True):
            return
        pid = self.v_api_test_id.get().strip()
        if not pid:
            messagebox.showinfo("Test", "Wpisz ID produktu z panelu IdoSell (liczba z karty towaru).")
            return
        import idosell_api
        self.tabs.select(self.tab_result)
        self.background(lambda: self._api_call(lambda: idosell_api.test_read(self.cfg, pid, self.write_log)))

    def _api_call(self, func):
        import idosell_api
        try:
            return func()
        except idosell_api.ApiError as exc:
            self.write_log(f"IdoSell API: {exc}")
            return None

    def _last_changed(self) -> list[dict]:
        if self.last_summary:
            return self.last_summary["zmienione"]
        messagebox.showinfo("Najpierw przelicz", "Najpierw kliknij „Przelicz ceny teraz”.")
        return []

    def api_try3(self) -> None:
        if not self.save(quiet=True):
            return
        rows = [r for r in self._last_changed() if r.get("cena_brutto")][:3]
        if not rows:
            return
        import idosell_api
        self.tabs.select(self.tab_result)
        names = "\n".join(f"• ID {r['id']}: {r['nazwa'][:50]} → {r['cena_brutto']} zł" for r in rows)

        def job():
            res = self._api_call(lambda: idosell_api.push_prices(self.cfg, rows, self.write_log, self.out_dir()))
            if res is not None:
                self.log_queue.put(lambda: self.confirm_try3(names))

        self.background(job)

    def confirm_try3(self, names: str) -> None:
        if messagebox.askyesno("Sprawdź w sklepie", "Wysłano ceny:\n" + names +
                               "\n\nSprawdź je w panelu IdoSell albo w sklepie. Czy są poprawne?"):
            self.cfg.set("idosell", "potwierdzone", "true")
            self.save(quiet=True)
            self.write_log("Próba potwierdzona — można wysyłać wszystkie ceny i włączyć automat.")
        else:
            self.write_log("Próba NIEpotwierdzona — nic więcej nie zostanie wysłane. Napisz, co się nie zgadza.")
        self.refresh_api_state()

    def api_send_all(self) -> None:
        if not self.cfg.getboolean("idosell", "potwierdzone", fallback=False):
            messagebox.showinfo("Najpierw próba", "Najpierw zrób krok 2 (próba na 3 produktach) i potwierdź.")
            return
        rows = [r for r in self._last_changed() if r.get("cena_brutto")]
        if not rows or not messagebox.askyesno("Wyślij", f"Wysłać {len(rows)} cen do sklepu?"):
            return
        import idosell_api
        self.tabs.select(self.tab_result)
        self.background(lambda: self._api_call(
            lambda: idosell_api.push_prices(self.cfg, rows, self.write_log, self.out_dir())))

    # ------------------------------------------------------------------ zakładka: wynik
    def build_result(self) -> None:
        f = self.tab_result
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.v_status = tk.StringVar(value="Jeszcze nie przeliczono w tej sesji.")
        ttk.Label(top, textvariable=self.v_status, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Otwórz folder wyników", command=self.open_folder).pack(side="right")
        ttk.Button(top, text="Otwórz raport", command=lambda: self.open_file("ceny_raport.csv")).pack(side="right",
                                                                                                    padx=6)
        ttk.Button(top, text="Przelicz ceny teraz", command=self.run_now).pack(side="right")
        ttk.Button(top, text="Zacznij od początku", command=self.start_over).pack(side="right", padx=6)
        show = ttk.Frame(f)
        show.pack(fill="x", pady=(6, 0))
        self.v_show = tk.StringVar(value="zmiany")
        ttk.Label(show, text="Pokaż:").pack(side="left")
        for value, text in (("zmiany", "tylko zmienione ceny"), ("wszystkie", "wszystkie produkty z ceną"),
                            ("uwagi", "tylko z uwagami"),
                            ("strata", "PONIŻEJ ZAKUPU / niska marża w sklepie")):
            ttk.Radiobutton(show, text=text, value=value, variable=self.v_show,
                            command=self.refresh_table).pack(side="left", padx=6)
        self.last_summary: dict | None = None
        cols = ("nazwa", "producent", "kod", "ean", "jedn", "zakup", "katalog", "sklep", "marza", "stara", "nowa",
                "uwagi")
        table = ttk.Frame(f)
        table.pack(fill="both", expand=True, pady=6)
        self.result = ttk.Treeview(table, columns=cols, show="headings", height=14)
        self.sort_state = ("nazwa", False)
        for col, text, w in zip(cols, ("Produkt", "Producent", "Kod producenta", "EAN", "Jedn.", "Zakup netto",
                                       "Katalog brutto", "W sklepie brutto", "Marża teraz %", "Było brutto",
                                       "Nowa brutto", "Uwagi"),
                                (240, 85, 100, 105, 40, 75, 80, 85, 75, 70, 75, 230)):
            self.result.heading(col, text=text, command=lambda c=col: self.sort_by(c))
            self.result.column(col, width=w, anchor="e" if col in ("zakup", "katalog", "sklep", "marza", "stara",
                                                                     "nowa") else "w")
        self.result.tag_configure("strata", background="#ffd6d6")
        ys = ttk.Scrollbar(table, orient="vertical", command=self.result.yview)
        self.result.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.result.pack(fill="both", expand=True)
        ttk.Label(f, text="Kliknij nagłówek kolumny, żeby posortować (drugi klik — odwrotnie).",
                  foreground="#555").pack(anchor="w")
        self.log = tk.Text(f, height=9, wrap="word", state="disabled", background="#f7f7f7")
        self.log.pack(fill="x")

    def out_dir(self) -> str:
        return self.v_out.get().strip() or ceny.HERE

    def open_folder(self) -> None:
        self.open_file("")

    def open_file(self, name: str) -> None:
        path = os.path.join(self.out_dir(), name)
        if name and not os.path.exists(path):
            messagebox.showinfo("Brak pliku", f"Nie ma jeszcze {name} — najpierw przelicz ceny.")
            return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 — otwarcie pliku w domyślnym programie Windows
        else:
            subprocess.Popen(["xdg-open", path])

    def write_log(self, text: str) -> None:
        self.log_queue.put(text)

    def pump_log(self) -> None:
        while not self.log_queue.empty():
            item = self.log_queue.get()
            if callable(item):
                item()
                continue
            self.log.configure(state="normal")
            self.log.insert("end", item + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(150, self.pump_log)

    def check_xml(self) -> None:
        if not self.save(quiet=True):
            return
        self.tabs.select(self.tab_result)
        self.background(lambda: ceny.show(self.v_xml.get().strip(), self.cfg, self.write_log))

    def run_now(self) -> None:
        if not self.save(quiet=True):
            return
        self.tabs.select(self.tab_result)

        def job():
            try:
                summary = ceny.przelicz(self.cfg, False, self.write_log, wyslij=False)
            except ceny.CenyError as exc:
                self.write_log(str(exc))
                self.log_queue.put(lambda: self.v_status.set("Zatrzymane przez bezpiecznik — zobacz komunikat niżej."))
                return
            self.log_queue.put(lambda: self.show_summary(summary))

        self.background(job)

    def start_over(self) -> None:
        if not messagebox.askyesno(
                "Zacznij od początku",
                "Program zapomni ceny z poprzednich przeliczeń i policzy wszystko od nowa.\n"
                "Plik do importu będzie zawierał wszystkie produkty z ceną, a nie tylko zmiany.\n\n"
                "Kontynuować?"):
            return
        if not self.save(quiet=True):
            return
        backup = ceny.zacznij_od_nowa(self.cfg)
        self.write_log("Zaczynam od początku." + (f" Poprzednie ceny odłożone do: {os.path.basename(backup)}"
                                                 if backup else ""))
        self.v_show.set("wszystkie")
        self.run_now()

    def refresh_table(self) -> None:
        self.result.delete(*self.result.get_children())
        s = self.last_summary
        if not s:
            return
        mode = self.v_show.get()
        rows = (s["zmienione"] if mode == "zmiany" else
                [r for r in s["wszystkie"] if r["cena_brutto"]] if mode == "wszystkie" else
                [r for r in s["wszystkie"] if "PONIŻEJ" in r["uwagi"] or "niska marża" in r["uwagi"]]
                if mode == "strata" else [r for r in s["wszystkie"] if r["uwagi"]])
        col, desc = self.sort_state
        key = {"nazwa": "nazwa", "producent": "producent", "kod": "kod", "ean": "ean", "zakup": "zakup_netto",
               "sklep": "sklep_brutto", "marza": "marza_teraz_proc", "jedn": "jednostka",
               "katalog": "katalog_brutto",
               "stara": "poprzednia_brutto", "nowa": "cena_brutto", "uwagi": "uwagi"}[col]

        def sort_key(r):
            v = str(r.get(key, ""))
            if col in ("zakup", "katalog", "sklep", "marza", "stara", "nowa"):
                try:
                    return (0, float(v), "")
                except ValueError:
                    return (1, 0.0, "")
            return (0 if v else 1, 0.0, v.lower())

        rows = sorted(rows, key=sort_key, reverse=desc)
        for r in rows[:5000]:
            tag = ("strata",) if "PONIŻEJ" in r["uwagi"] else ()
            self.result.insert("", "end", tags=tag, values=(
                r["nazwa"], r["producent"], r["kod"], r["ean"], r.get("jednostka", ""), r["zakup_netto"],
                r.get("katalog_brutto", ""), r.get("sklep_brutto", ""),
                r.get("marza_teraz_proc", ""), r["poprzednia_brutto"], r["cena_brutto"], r["uwagi"]))
        if len(rows) > 5000:
            self.write_log(f"Tabela pokazuje 5000 z {len(rows)} — całość w raporcie (Otwórz raport).")

    def sort_by(self, col: str) -> None:
        current, desc = self.sort_state
        self.sort_state = (col, not desc if col == current else False)
        self.refresh_table()

    def show_summary(self, summary: dict) -> None:
        self.last_summary = summary
        self.refresh_table()
        uw = ", ".join(f"{k}: {v}" for k, v in summary["uwagi"].items())
        self.v_status.set(f"{summary['czas']} (cennik z {summary.get('cennik_z', '?')}): zmian cen {summary['zmiany']} • z ceną {summary['dopasowane']} z "
                          f"{summary['produkty']}" + (f" • {uw}" if uw else ""))

    def background(self, func) -> None:
        if self.running:
            messagebox.showinfo("Chwila", "Poprzednie zadanie jeszcze trwa.")
            return
        self.running = True

        def wrap():
            try:
                func()
            except Exception as exc:  # pokaż każdy błąd w oknie zamiast cicho zamknąć wątek
                self.write_log(f"Błąd: {type(exc).__name__}: {exc}")
            finally:
                self.running = False

        threading.Thread(target=wrap, daemon=True).start()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # ostre czcionki na ekranach z powiększeniem
        except Exception:
            pass
    App().mainloop()
