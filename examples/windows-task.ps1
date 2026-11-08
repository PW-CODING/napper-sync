# Windows alternative to the systemd timer: an hourly scheduled task.
# Run once in PowerShell after `pipx install napper-sync` (or pip install --user).
$exe = (Get-Command napper-sync).Source
schtasks /Create /TN "napper-sync" /SC HOURLY /TR "`"$exe`" sync-famly --live" /F
# Check:    schtasks /Query  /TN "napper-sync" /FO LIST
# Disable:  schtasks /Change /TN "napper-sync" /DISABLE
