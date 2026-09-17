@echo off
rem Uruchamia serwer wersji mobilnej. Plik mozna kliknac dwa razy w Eksploratorze.
rem Przy pierwszym starcie sam instaluje brakujace biblioteki.
rem
rem Uwaga dla edytujacych: sprawdzenia bledow celowo uzywaja etykiet GOTO,
rem a nie blokow w nawiasach - wewnatrz nawiasow %ERRORLEVEL% jest rozwijany
rem przy parsowaniu calego bloku, czyli zanim komenda w ogole sie wykona.
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Asystent pomiarowy - serwer

echo ==========================================================
echo   ASYSTENT POMIAROWY - uruchamianie serwera
echo ==========================================================
echo.

rem Launcher "py" instaluje sie razem z Pythonem z python.org i dziala
rem nawet wtedy, gdy instalator nie dopisal Pythona do PATH.
set "PY="
where py >nul 2>&1
if not errorlevel 1 set "PY=py"
if defined PY goto :mam_pythona

where python >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :mam_pythona

echo [BLAD] Nie znaleziono Pythona na tym komputerze.
echo.
echo   Python to srodowisko, w ktorym dziala ta aplikacja. Trzeba go raz zainstalowac:
echo.
echo   1. Otworz strone:  https://www.python.org/downloads/windows/
echo   2. Pobierz "Windows installer (64-bit)" dla wersji 3.12.
echo   3. W instalatorze ZAZNACZ pole "Add python.exe to PATH" (na dole okna).
echo   4. Dokoncz instalacje, zamknij to okno i kliknij ten plik ponownie.
echo.
pause
exit /b 1

:mam_pythona
for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do echo Znaleziono: %%v
echo.

%PY% -c "import flask, cv2, numpy" >nul 2>&1
if not errorlevel 1 goto :serwer

echo Pierwsze uruchomienie - instaluje biblioteki. Potrwa 1-3 minuty...
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :blad_instalacji
echo.
echo Biblioteki zainstalowane.
echo.
goto :serwer

:blad_instalacji
echo.
echo [BLAD] Instalacja bibliotek sie nie powiodla.
echo   Sprawdz polaczenie z internetem i kliknij ten plik ponownie.
echo.
pause
exit /b 1

:serwer
%PY% serve_wall.py %*
echo.
echo Serwer zostal zatrzymany.
pause
