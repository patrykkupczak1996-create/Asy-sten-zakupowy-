# Przeliczenie cen z cennika Onninen (XML). Do ręcznego uruchomienia albo z Harmonogramu zadań, np. 2× dziennie:
#   schtasks /Create /TN "Ceny Onninen" /SC DAILY /ST 07:30 /TR "powershell -ExecutionPolicy Bypass -File Z:\ai\iht\Asy-sten-zakupowy-\wzbogacanie_produktow\ceny.ps1"
#   schtasks /Create /TN "Ceny Onninen popoludnie" /SC DAILY /ST 14:30 /TR "powershell -ExecutionPolicy Bypass -File Z:\ai\iht\Asy-sten-zakupowy-\wzbogacanie_produktow\ceny.ps1"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
for ($i = 0; $i -lt 20 -and -not (Test-Path (Join-Path $PSScriptRoot "ceny.py")); $i++) { Start-Sleep -Seconds 30 }
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -Append -Encoding utf8 ceny.log
py ceny.py 2>&1 | Tee-Object -Append -FilePath ceny.log
