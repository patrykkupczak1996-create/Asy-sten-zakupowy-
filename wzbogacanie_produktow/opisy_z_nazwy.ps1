# Dokończenie opisów w arkuszu Google lokalnym Bielikiem (bez kosztów).
# 1. Arkusz: Plik > Pobierz > CSV, zapisz jako arkusz_opisy.csv w tym folderze.
# 2. powershell -ExecutionPolicy Bypass -File opisy_z_nazwy.ps1
# 3. py wzbogac_produkty.py --output opisy_wszystkie.csv --z-arkusza arkusz_opisy.csv
Set-Location $PSScriptRoot
function WaitForDisk { while (-not (Test-Path (Join-Path $PSScriptRoot "opisy_z_nazwy.py"))) { Write-Host "Brak dostępu do folderu skryptu (dysk sieciowy?) — czekam 30 s…" -ForegroundColor Yellow; Start-Sleep -Seconds 30 } }
$env:OLLAMA_MODEL = "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M"
$env:PYTHONIOENCODING = "utf-8"
ollama stop "qwen2.5vl:7b" 2>$null | Out-Null
if (-not (Test-Path "arkusz_opisy.csv")) { Write-Host "Brak pliku arkusz_opisy.csv — pobierz arkusz jako CSV (Plik > Pobierz > CSV)." -ForegroundColor Red; exit 1 }
for ($i = 1; $i -le 20; $i++) {
    WaitForDisk
    py opisy_z_nazwy.py arkusz_opisy.csv
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 60
}
