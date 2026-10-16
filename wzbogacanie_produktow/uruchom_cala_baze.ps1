# Cała baza w trybie "tylko opisy" (Ollama), z automatycznym wznawianiem po przerwaniu.
# Uruchomienie (osobne okno, działa niezależnie od Claude):
#   powershell -ExecutionPolicy Bypass -File uruchom_cala_baze.ps1
# Skrypt Pythona sam wznawia pracę od pierwszego niezapisanego wiersza, więc ponowne uruchomienie
# niczego nie dubluje. Pętla kończy się, gdy skrypt zakończy się sukcesem (kod 0).
Set-Location $PSScriptRoot

# Tylko jedna kopia naraz — dwie dopisywałyby do tego samego pliku i dzieliły kartę graficzną.
$mutex = New-Object System.Threading.Mutex($false, "Global\wzbogacanie_cala_baza")
if (-not $mutex.WaitOne(0)) {
    Write-Host "Cała baza jest już przetwarzana w innym oknie — ta kopia kończy pracę." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
    exit 1
}

$env:AI_PROVIDER = "ollama"
$env:OLLAMA_MODEL = "SpeakLeash/bielik-11b-v3.0-instruct:Q4_K_M"   # lepsza polszczyzna niż qwen2.5:14b-instruct
$env:PYTHONIOENCODING = "utf-8"

for ($i = 1; $i -le 500; $i++) {
    "=== uruchomienie ${i}: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 opisy_wszystkie_restarty.log
    py wzbogac_produkty.py --input produkty.csv --output opisy_wszystkie.csv --bez-zdjec
    $code = $LASTEXITCODE
    "=== koniec ${i}, kod ${code}: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 opisy_wszystkie_restarty.log
    if ($code -eq 0) { break }
    Start-Sleep -Seconds 60   # np. Ollama chwilowo nie odpowiada — chwila przerwy przed wznowieniem
}
"=== PETLA ZAKONCZONA: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 opisy_wszystkie_restarty.log
