#!/usr/bin/env bash
# Uruchamia serwer wersji mobilnej na macOS i Linuksie.
# Przy pierwszym starcie sam instaluje brakujace biblioteki.
set -u
cd "$(dirname "$0")" || exit 1

echo "=========================================================="
echo "  ASYSTENT POMIAROWY - uruchamianie serwera"
echo "=========================================================="
echo

if [ ! -f serve_wall.py ] || [ ! -f requirements.txt ]; then
    echo "[BLAD] W tym katalogu nie ma plikow aplikacji."
    echo "  Katalog: $(pwd)"
    echo "  Rozpakuj cale archiwum i uruchom skrypt z rozpakowanego folderu."
    exit 1
fi

PY=""
for kandydat in python3 python; do
    if command -v "$kandydat" >/dev/null 2>&1; then
        PY="$kandydat"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "[BLAD] Nie znaleziono Pythona."
    echo "  macOS:  brew install python   (albo https://www.python.org/downloads/)"
    echo "  Linux:  sudo apt install python3 python3-pip"
    exit 1
fi

echo "Znaleziono: $("$PY" --version 2>&1)"
echo

if ! "$PY" -c "import flask, cv2, numpy" >/dev/null 2>&1; then
    echo "Pierwsze uruchomienie - instaluje biblioteki. Potrwa 1-3 minuty..."
    echo
    if ! "$PY" -m pip install -r requirements.txt; then
        echo
        echo "[BLAD] Instalacja bibliotek sie nie powiodla."
        echo "  Sprobuj w srodowisku wirtualnym:"
        echo "      $PY -m venv .venv && source .venv/bin/activate"
        echo "      pip install -r requirements.txt && python serve_wall.py"
        exit 1
    fi
    echo
    echo "Biblioteki zainstalowane."
    echo
fi

exec "$PY" serve_wall.py "$@"
