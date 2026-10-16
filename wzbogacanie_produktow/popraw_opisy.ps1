# Poprawa opisów łamiących zasady sklepu (ceny, listy parametrów, obcy język, linki) w opisy_wszystkie.csv —
# pisane od nowa krótko, po polsku, z nazwy produktu. Z automatycznym wznawianiem; postęp zapisywany na bieżąco.
# Uruchomienie:
#   powershell -ExecutionPolicy Bypass -File popraw_opisy.ps1
Set-Location $PSScriptRoot
function WaitForDisk { while (-not (Test-Path (Join-Path $PSScriptRoot "wzbogac_produkty.py"))) { Write-Host "Brak dostępu do folderu skryptu (dysk sieciowy?) — czekam 30 s…" -ForegroundColor Yellow; Start-Sleep -Seconds 30 } }
$env:AI_PROVIDER = "ollama"
$env:OLLAMA_MODEL = "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M"
$env:PYTHONIOENCODING = "utf-8"
ollama stop "qwen2.5vl:7b" 2>$null | Out-Null

for ($i = 1; $i -le 100; $i++) {
    WaitForDisk
    "=== popraw ${i}: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 popraw_restarty.log
    py wzbogac_produkty.py --popraw-opisy --output opisy_wszystkie.csv
    $code = $LASTEXITCODE
    "=== koniec ${i}, kod ${code}: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 popraw_restarty.log
    if ($code -eq 0 -or $code -eq 130) { break }
    Start-Sleep -Seconds 60
}
