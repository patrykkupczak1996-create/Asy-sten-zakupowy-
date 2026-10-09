# Ponowne przetworzenie produktów "nie znaleziono strony" w opisy_wszystkie.csv (Ollama, tylko opisy),
# z automatycznym wznawianiem. Uruchomienie (osobne okno, działa niezależnie od Claude):
#   powershell -ExecutionPolicy Bypass -File ponow_brak_strony.ps1
# Skrypt Pythona blokuje plik wyników (*.lock), więc nie ruszy równolegle z uruchom_cala_baze.ps1.
Set-Location $PSScriptRoot
$env:AI_PROVIDER = "ollama"
$env:OLLAMA_MODEL = "qwen2.5:14b-instruct"
$env:PYTHONIOENCODING = "utf-8"

for ($i = 1; $i -le 100; $i++) {
    "=== ponow ${i}: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 ponow_restarty.log
    py wzbogac_produkty.py --ponow-brak-strony --output opisy_wszystkie.csv --bez-zdjec
    $code = $LASTEXITCODE
    "=== koniec ${i}, kod ${code}: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 ponow_restarty.log
    if ($code -eq 0) { break }
    Start-Sleep -Seconds 60
}
"=== PETLA ZAKONCZONA: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 ponow_restarty.log
