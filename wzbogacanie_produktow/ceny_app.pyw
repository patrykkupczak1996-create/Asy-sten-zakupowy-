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
        self.geometry("1060x780")
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
        self.tabs = tabs
        self.build_settings()
        self.build_result()
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
        self.v_example = tk.StringVar()
        ttk.Label(other, textvariable=self.v_example, foreground="#0a5", wraplength=900).grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))
        for v in (self.v_min, self.v_vat, self.v_round):
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
        self.v_vat.set(c.get("marza", "vat", fallback="23"))
        code = c.get("marza", "zaokraglenie", fallback="0.99").strip()
        self.v_round.set(next((k for k, v in ROUNDING.items() if v == code), "końcówka ,99"))
        self.v_hours.set(c.get("automat", "godziny", fallback="07:30, 14:30"))
        self.v_ftp_host.set(c.get("ftp", "host", fallback=""))
        self.v_ftp_user.set(c.get("ftp", "uzytkownik", fallback=""))
        self.v_ftp_pass.set(c.get("ftp", "haslo", fallback=""))
        self.v_ftp_dir.set(c.get("ftp", "folder", fallback=""))

    def form_to_cfg(self) -> configparser.ConfigParser:
        # Kopia — podgląd przykładu nie może zmieniać ustawień, zanim formularz się wczyta.
        c = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
        c.optionxform = str
        c.read_dict(self.cfg)
        for sec in ("pliki", "marza", "producenci", "automat", "ftp"):
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
        c.set("marza", "zaokraglenie", ROUNDING.get(self.v_round.get(), "0.01"))
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
            net, gross, pct = ceny.sale_price(buy, "", s)
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
                            ("uwagi", "tylko z uwagami (brak w cenniku, duża zmiana)")):
            ttk.Radiobutton(show, text=text, value=value, variable=self.v_show,
                            command=self.refresh_table).pack(side="left", padx=6)
        self.last_summary: dict | None = None
        cols = ("nazwa", "zakup", "stara", "nowa", "uwagi")
        self.result = ttk.Treeview(f, columns=cols, show="headings", height=14)
        for col, text, w in zip(cols, ("Produkt", "Zakup netto", "Było brutto", "Teraz brutto", "Uwagi"),
                                (380, 90, 90, 90, 300)):
            self.result.heading(col, text=text)
            self.result.column(col, width=w, anchor="w" if col in ("nazwa", "uwagi") else "e")
        self.result.pack(fill="both", expand=True, pady=6)
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
                summary = ceny.przelicz(self.cfg, False, self.write_log)
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
                [r for r in s["wszystkie"] if r["uwagi"]])
        for r in rows[:5000]:
            self.result.insert("", "end", values=(r["nazwa"], r["zakup_netto"], r["poprzednia_brutto"],
                                                  r["cena_brutto"], r["uwagi"]))
        if len(rows) > 5000:
            self.write_log(f"Tabela pokazuje 5000 z {len(rows)} — całość w raporcie (Otwórz raport).")

    def show_summary(self, summary: dict) -> None:
        self.last_summary = summary
        self.refresh_table()
        uw = ", ".join(f"{k}: {v}" for k, v in summary["uwagi"].items())
        self.v_status.set(f"{summary['czas']}: zmian cen {summary['zmiany']} • z ceną {summary['dopasowane']} z "
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
