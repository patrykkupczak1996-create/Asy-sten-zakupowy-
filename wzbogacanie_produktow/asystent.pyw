"""Asystent sklepu IHT — jedno okno do wszystkich narzędzi: opisy, zdjęcia, akceptacja/import, ceny.

Uruchomienie: dwuklik na Asystent.bat (albo: py asystent.pyw).
Każdy przycisk uruchamia ten sam skrypt, który działa z PowerShella — tu widać jego wynik na żywo
i można go zatrzymać przyciskiem „Zatrzymaj”.
"""
from __future__ import annotations

import glob
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
BIELIK = "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M"
SHEET_PATTERN = "opisy_wszystkie_gotowe_arkusz*.csv"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW — bez czarnego okna konsoli


def python_exe() -> str:
    """python.exe obok pythonw.exe — żeby wynik skryptu dało się czytać na żywo."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        console = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(console):
            return console
    return exe


class Asystent(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        os.chdir(HERE)
        self.title("Asystent sklepu IHT — opisy, zdjęcia, import, ceny")
        self.geometry("1080x760")
        self.minsize(900, 620)
        self.proc: subprocess.Popen | None = None
        self.out: queue.Queue = queue.Queue()

        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        self.v_status = tk.StringVar(value="Gotowy.")
        ttk.Label(top, textvariable=self.v_status, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Zatrzymaj", command=self.stop).pack(side="right")
        ttk.Button(top, text="Otwórz folder", command=lambda: self.open_path(HERE)).pack(side="right", padx=6)
        ttk.Button(top, text="Aktualizuj program", command=self.update_program).pack(side="right")

        tabs = ttk.Notebook(self)
        tabs.pack(fill="x", padx=10)
        self.build_opisy(self.tab(tabs, "1. Opisy"))
        self.build_zdjecia(self.tab(tabs, "2. Zdjęcia"))
        self.build_import(self.tab(tabs, "3. Akceptacja i import"))
        self.build_ceny(self.tab(tabs, "4. Ceny"))

        logf = ttk.LabelFrame(self, text="Przebieg (na żywo)", padding=6)
        logf.pack(fill="both", expand=True, padx=10, pady=8)
        self.log = tk.Text(logf, wrap="word", state="disabled", background="#111", foreground="#ddd",
                           insertbackground="#ddd", font=("Consolas", 10))
        scroll = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.after(150, self.pump)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.write(f"Folder: {HERE}\nPliki: " + ", ".join(
            f"{f} {'✔' if os.path.exists(f) else '✘ brak'}" for f in
            ("produkty.csv", "opisy_wszystkie.csv", "arkusz_opisy.csv", "opisy_baza.csv")))

    @staticmethod
    def tab(tabs: ttk.Notebook, title: str) -> ttk.Frame:
        f = ttk.Frame(tabs, padding=10)
        tabs.add(f, text=title)
        return f

    def row(self, parent, title: str, hint: str, command, r: int) -> None:
        ttk.Button(parent, text=title, width=34, command=command).grid(row=r, column=0, sticky="w", pady=3)
        ttk.Label(parent, text=hint, foreground="#555", wraplength=640).grid(row=r, column=1, sticky="w", padx=10)

    # ------------------------------------------------------------------ zakładki
    def build_opisy(self, f) -> None:
        self.v_limit = tk.StringVar(value="20")
        lim = ttk.Frame(f)
        lim.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(lim, text="Próba na ilu produktach:").pack(side="left")
        ttk.Entry(lim, textvariable=self.v_limit, width=6).pack(side="left", padx=4)
        ttk.Label(lim, text="Kolejność: najpierw znane i największe marki. Model: Bielik (lokalnie, bez kosztów).",
                  foreground="#555").pack(side="left", padx=10)
        self.row(f, "Weź arkusz z Pobranych", "Kopiuje najnowszy pobrany arkusz Google (Plik → Pobierz → CSV) "
                 "jako arkusz_opisy.csv — wzory stylu i opisy z arkusza.", self.take_sheet, 1)
        self.row(f, "Próba: opisy dla całej bazy", "Pisze opisy dla podanej liczby produktów z produkty.csv "
                 "i otwiera podgląd.", lambda: self.opisy_baza(limit=True), 2)
        self.row(f, "Opisy całej bazy (na noc)", "Wszystkie produkty bez opisu; wznawia od miejsca przerwania.",
                 lambda: self.opisy_baza(limit=False), 3)
        self.row(f, "Dokończ brakujące w arkuszu", "Uzupełnia wiersze arkusza_opisy.csv bez statusu.",
                 self.opisy_arkusz, 4)
        self.row(f, "Podgląd nowych opisów", "Strona w przeglądarce z ostatnio dopisanymi opisami (bez AI).",
                 self.podglad_opisow, 5)
        self.row(f, "Sprawdź jakość gotowych opisów", "Lista opisów z liczbami spoza nazwy, obcymi słowami itp.",
                 self.sprawdz_opisy, 6)

    def build_zdjecia(self, f) -> None:
        self.row(f, "Próba: zdjęcia dla 20 produktów", "Szuka i sprawdza zdjęcia (model wizyjny).",
                 lambda: self.run_py(["zdjecia.py", "--limit", "20"]), 0)
        self.row(f, "Zdjęcia na noc", "Pełny nocny przebieg zdjęć ze wznawianiem (zdjecia_noc.ps1).",
                 lambda: self.run_ps("zdjecia_noc.ps1"), 1)
        self.row(f, "Podgląd i akceptacja zdjęć", "Strona z miniaturami — wątpliwe zdjęcia do zaakceptowania.",
                 lambda: self.run_py(["zdjecia.py", "--podglad"]), 2)
        self.row(f, "Plik zdjęć dla IdoSell", "@id + link do zdjęcia (pewne i zaakceptowane).",
                 lambda: self.run_py(["zdjecia.py", "--zapisz"]), 3)

    def build_import(self, f) -> None:
        self.row(f, "Przenieś opisy do wyników", "Wpisuje nowe opisy (z arkusza albo z opisy_baza.csv) do "
                 "opisy_wszystkie.csv — tylko te, które przejdą kontrolę.", self.przenies_opisy, 0)
        self.row(f, "Strona akceptacji", "Produkty do akceptacji pogrupowane po powodzie; zapisujesz wybór "
                 "przyciskiem na stronie.", lambda: self.run_py(["akceptacja.py"]), 1)
        self.row(f, "Zrób plik importu do IdoSell", "PEWNE + zaakceptowane → opisy_wszystkie_idosell.csv "
                 "(opisy z błędami są pomijane).", lambda: self.run_py(["akceptacja.py", "--zapisz"]), 2)
        self.row(f, "Przelicz statusy", "Ponowna kontrola zapisanych opisów bez AI (kopia pliku przed zmianą).",
                 lambda: self.run_py(["wzbogac_produkty.py", "--przelicz-statusy",
                                      "--output", "opisy_wszystkie.csv"]), 3)

    def build_ceny(self, f) -> None:
        self.row(f, "Otwórz program Ceny", "Marże, progi, automat 2× dziennie, podgląd zmian cen.",
                 lambda: subprocess.Popen([sys.executable, os.path.join(HERE, "ceny_app.pyw")], cwd=HERE), 0)
        self.row(f, "Przelicz ceny teraz", "Pobiera XML Onninen i liczy ceny wg zapisanych ustawień.",
                 lambda: self.run_py(["ceny.py"]), 1)

    # ------------------------------------------------------------------ akcje
    def need(self, *files: str) -> bool:
        missing = [f for f in files if not os.path.exists(f)]
        if missing:
            messagebox.showerror("Brak pliku", "W folderze programu brak: " + ", ".join(missing))
        return not missing

    def take_sheet(self) -> None:
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        found = sorted(glob.glob(os.path.join(downloads, SHEET_PATTERN)), key=os.path.getmtime, reverse=True)
        if not found:
            path = filedialog.askopenfilename(title="Wskaż pobrany arkusz (CSV)", filetypes=[("CSV", "*.csv")])
            if not path:
                return
        else:
            path = found[0]
        if os.path.exists("arkusz_opisy.csv") and not messagebox.askyesno(
                "Arkusz", f"Zastąpić arkusz_opisy.csv plikiem:\n{os.path.basename(path)}?"):
            return
        shutil.copy2(path, "arkusz_opisy.csv")
        self.write(f"Skopiowano {path} → arkusz_opisy.csv")

    def opisy_baza(self, limit: bool) -> None:
        if not self.need("produkty.csv", "arkusz_opisy.csv"):
            return
        cmd = ["opisy_z_nazwy.py", "opisy_baza.csv", "--baza", "produkty.csv", "--wzory", "arkusz_opisy.csv"]
        if limit:
            cmd += ["--limit", self.v_limit.get().strip() or "20"]
        self.run_py(cmd, ollama=True)

    def opisy_arkusz(self) -> None:
        if self.need("arkusz_opisy.csv"):
            self.run_py(["opisy_z_nazwy.py", "arkusz_opisy.csv"], ollama=True)

    def podglad_opisow(self) -> None:
        name = "opisy_baza.csv" if os.path.exists("opisy_baza.csv") else "arkusz_opisy.csv"
        if self.need(name):
            self.run_py(["opisy_z_nazwy.py", name, "--podglad"])

    def sprawdz_opisy(self) -> None:
        name = "opisy_baza.csv" if os.path.exists("opisy_baza.csv") else "arkusz_opisy.csv"
        if self.need(name):
            self.run_py(["opisy_z_nazwy.py", name, "--sprawdz"])

    def przenies_opisy(self) -> None:
        options = [f for f in ("opisy_baza.csv", "arkusz_opisy.csv") if os.path.exists(f)]
        if not self.need("opisy_wszystkie.csv") or not options:
            if not options:
                messagebox.showerror("Brak pliku", "Nie ma opisy_baza.csv ani arkusz_opisy.csv.")
            return
        src = options[0]
        if len(options) > 1 and not messagebox.askyesno(
                "Skąd opisy", "Przenieść opisy z opisy_baza.csv?\n(Nie = z arkusz_opisy.csv)"):
            src = options[1]
        self.run_py(["wzbogac_produkty.py", "--output", "opisy_wszystkie.csv", "--z-arkusza", src])

    def update_program(self) -> None:
        repo = os.path.dirname(HERE)
        self.run(["git", "-C", repo, "pull", "--no-rebase", "--no-edit", "origin",
                  "claude/b2b-product-enrichment-script-uve0u0"],
                 "Aktualizacja programu (po zakończeniu zamknij i otwórz okno ponownie)")

    # ------------------------------------------------------------------ uruchamianie
    def run_py(self, args: list[str], ollama: bool = False) -> None:
        self.run([python_exe(), "-u"] + args, " ".join(args), ollama)

    def run_ps(self, script: str) -> None:
        self.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", script], script)

    def run(self, cmd: list[str], label: str, ollama: bool = False) -> None:
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Trwa praca", "Poprzednie zadanie jeszcze działa. Zaczekaj albo kliknij „Zatrzymaj”.")
            return
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        if ollama:
            env["OLLAMA_MODEL"] = BIELIK  # zawsze Bielik — stara zmienna systemowa nie może podmienić modelu
            env["AI_PROVIDER"] = "ollama"
        self.write(f"\n▶ {label}")
        self.v_status.set(f"Pracuje: {label}")
        try:
            self.proc = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except FileNotFoundError as exc:
            self.write(f"Nie mogę uruchomić: {exc}")
            self.v_status.set("Błąd uruchomienia.")
            return
        threading.Thread(target=self.reader, args=(self.proc, label), daemon=True).start()

    def reader(self, proc: subprocess.Popen, label: str) -> None:
        for raw in iter(proc.stdout.readline, b""):
            self.out.put(raw.decode("utf-8", errors="replace").rstrip())
        code = proc.wait()
        self.out.put(("koniec", label, code))

    def pump(self) -> None:
        while not self.out.empty():
            item = self.out.get()
            if isinstance(item, tuple):
                _, label, code = item
                ok = code == 0
                self.write(f"■ {'Zakończono' if ok else f'Zakończono z kodem {code}'}: {label}")
                self.v_status.set("Gotowy." if ok else f"Ostatnie zadanie zakończone z kodem {code}.")
            else:
                self.write(item)
        self.after(150, self.pump)

    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 5000:
            self.log.delete("1.0", "1000.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            self.write("Nic nie działa.")
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
        else:
            self.proc.terminate()
        self.write("Zatrzymano. Postęp jest zapisany — ten sam przycisk wznowi pracę.")

    def open_path(self, path: str) -> None:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", path])

    def on_close(self) -> None:
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Zamknąć?", "Zadanie jeszcze działa. Zatrzymać je i zamknąć okno?"):
                return
            self.stop()
        self.destroy()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    Asystent().mainloop()
