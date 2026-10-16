# Opisy dla CAŁEJ bazy sklepu (produkty.csv) lokalnym Bielikiem — najpierw znane i największe marki.
# Wzory stylu bierze z arkusza (arkusz_opisy.csv); produkty już opisane w arkuszu pomija.
#   powershell -ExecutionPolicy Bypass -File opisy_baza.ps1
# Potem: py wzbogac_produkty.py --output opisy_wszystkie.csv --z-arkusza opisy_baza.csv
Set-Location $PSScriptRoot
function WaitForDisk { while (-not (Test-Path (Join-Path $PSScriptRoot "opisy_z_nazwy.py"))) { Write-Host "Brak dostępu do folderu skryptu (dysk sieciowy?) — czekam 30 s…" -ForegroundColor Yellow; Start-Sleep -Seconds 30 } }
$env:OLLAMA_MODEL = "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M"
$env:PYTHONIOENCODING = "utf-8"
ollama stop "qwen2.5vl:7b" 2>$null | Out-Null
foreach ($f in @("produkty.csv", "arkusz_opisy.csv")) {
    if (-not (Test-Path $f)) { Write-Host "Brak pliku $f w tym folderze." -ForegroundColor Red; exit 1 }
}
for ($i = 1; $i -le 50; $i++) {
    WaitForDisk
    py opisy_z_nazwy.py opisy_baza.csv --baza produkty.csv --wzory arkusz_opisy.csv
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 60
}
