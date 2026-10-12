# Jedno okno na całą noc: najpierw zdjęcia (zdjecia_noc.ps1), potem opisy produktów bez strony
# (ponow_brak_strony.ps1). Oba modele naraz nie mieszczą się w karcie graficznej, więc idą po kolei.
# Uruchomienie:
#   powershell -ExecutionPolicy Bypass -File zdjecia_potem_opisy.ps1
Set-Location $PSScriptRoot
$log = "zdjecia_noc.log"
function Note($text) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $text" | Tee-Object -Append -FilePath $log }

# Komputer nie zaśnie przez całą noc (zdjecia_noc.ps1 na końcu zwalnia blokadę, więc ustawiamy ją tu ponownie).
Add-Type -Namespace Lancuch -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'

Note "=== ŁAŃCUCH: 1/2 zdjęcia"
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "zdjecia_noc.ps1")

[Lancuch.Power]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
# Zwalniamy kartę z modelu wizyjnego, zanim wystartuje model opisów.
ollama stop "qwen2.5vl:7b" 2>$null | Out-Null
Note "=== ŁAŃCUCH: 2/2 opisy produktów bez strony (np. De Dietrich z e-katalogu)"
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ponow_brak_strony.ps1")

[Lancuch.Power]::SetThreadExecutionState([uint32]"0x80000000") | Out-Null
Note "=== ŁAŃCUCH ZAKOŃCZONY — rano: py zdjecia.py --podglad oraz py akceptacja.py"
