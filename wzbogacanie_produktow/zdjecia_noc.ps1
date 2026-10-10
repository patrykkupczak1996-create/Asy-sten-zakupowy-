# Zdjęcia na noc dla produktów z gotowym opisem (PEWNE + zaakceptowane), z automatycznym wznawianiem.
# Uruchomienie (osobne okno, działa niezależnie od Claude):
#   powershell -ExecutionPolicy Bypass -File zdjecia_noc.ps1
# Kolejno:
#   1) odrzucone ręcznie zdjęcia z arkusza gotowych (64 produkty) — tylko jeśli jeszcze nie były odrzucone,
#   2) wszystkie produkty bez wyniku zdjęcia: źródło → producent → inne strony → wyszukiwarka obrazów,
#   3) jeszcze raz produkty, które zostały bez zdjęcia,
#   4) strona podglądu (otwierasz rano: opisy_wszystkie_zdjecia_podglad.html).
# Każde zdjęcie sprawdza model wizyjny; niepewne idą DO_AKCEPTACJI, a nie od razu do sklepu.
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$log = "zdjecia_noc.log"
function Note($text) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $text" | Tee-Object -Append -FilePath $log }

# Opisy i zdjęcia naraz nie mieszczą się w karcie graficznej — nie startujemy, gdy działa przebieg opisów.
$desc = Get-CimInstance Win32_Process -Filter "Name like 'py%'" | Where-Object { $_.CommandLine -like "*wzbogac_produkty.py*" }
if ($desc) {
    Write-Host "Działa przebieg opisów (wzbogac_produkty.py) — zatrzymaj go (Ctrl+C) i uruchom ten skrypt ponownie." -ForegroundColor Yellow
    Start-Sleep -Seconds 15
    exit 1
}
# Zwalniamy kartę z modelu opisów, jeśli Ollama go jeszcze trzyma w pamięci.
ollama stop "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M" 2>$null | Out-Null

# Komputer nie zaśnie, dopóki to okno pracuje (bez zmiany ustawień zasilania, bez uprawnień administratora).
Add-Type -Namespace Noc -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[Noc.Power]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

# Tylko raz: powtórne --odrzuc skasowałoby nowe zdjęcia tych produktów znalezione w poprzednią noc.
$rejected = "opisy_wszystkie_zdjecia_odrzucone.txt"
$marker = "zdjecia_noc_odrzucone.ok"
$already = (Test-Path $marker) -or ((Test-Path $rejected) -and (Select-String -Path $rejected -Pattern "^6381\t" -Quiet))
if (-not $already) {
    Note "Odrzucam 64 złe zdjęcia z arkusza gotowych"
    py zdjecia.py --odrzuc 6381 7226 7522 8234 8280 8441 8442 9122 9123 9150 9325 9327 9328 9329 9346 24670 26276 26586 29033 7166 8158 8338 8350 8356 8360 8368 8374 20828 20834 20837 20844 20861 20862 20878 20884 20894 20911 20913 20914 20935 20940 20944 21062 21901 21902 6296 6340 7015 7016 7357 7442 7654 7773 7818 7929 5455 5468 17225 17661 18016 18054 18122 6674 17664
    if ($LASTEXITCODE -eq 0) { New-Item -ItemType File -Force $marker | Out-Null }
}

for ($i = 1; $i -le 50; $i++) {
    Note "=== zdjęcia, uruchomienie $i"
    py zdjecia.py --szukaj
    $code = $LASTEXITCODE
    Note "=== koniec $i, kod $code"
    if ($code -eq 0) { break }
    Start-Sleep -Seconds 60   # np. Ollama chwilowo nie odpowiada
}

# Ponowienie „bez zdjęcia” robimy raz (po awarii najwyżej 3 razy) — każde uruchomienie z --ponow-brak
# zaczyna je od nowa, więc pętla bez limitu kręciłaby się w kółko po tych samych produktach.
for ($i = 1; $i -le 3; $i++) {
    Note "=== ponowienie bez zdjęcia, uruchomienie $i"
    py zdjecia.py --ponow-brak --szukaj
    $code = $LASTEXITCODE
    Note "=== koniec ponowienia $i, kod $code"
    if ($code -eq 0) { break }
    Start-Sleep -Seconds 60
}

py zdjecia.py --podglad --bez-otwierania
[Noc.Power]::SetThreadExecutionState([uint32]"0x80000000") | Out-Null
Note "=== NOC ZAKOŃCZONA — rano: py zdjecia.py --podglad"
