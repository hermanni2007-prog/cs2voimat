# Rekisteroi Windows Task Scheduler -tehtavan, joka ajaa kertoimien keruun
# 15 minuutin valein, myos silloin kun kukaan ei ole kirjautuneena sisaan.
# 15 min pollausvali + 25 min CLOSING_WINDOW_MINUTES (.env) takaa etta jokaiselle
# ottelulle osuu vahintaan yksi pollaus sulkeutuvan kertoimen ikkunaan.
#
# HUOM: Tama vaatii etta koneessa on virta paalla ja se ei ole uniessa niina
# hetkina kun tehtavan pitaisi ajaa - Task Scheduler ei hera koneita itse
# (ellei "Wake the computer to run this task" ole paalla virranhallinnassa).
#
# Aja tama PowerShell-ikkunasta (ei tarvitse admin-oikeuksia nykyisen
# kayttajan tehtavalle):
#   powershell -ExecutionPolicy Bypass -File .\scheduler\register_task.ps1

$taskName = "CS2-Pelivoimat-KertoimienKeruu"
$root = Split-Path -Parent $PSScriptRoot
$scriptPath = "$root\scheduler\run_collector.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "CS2-vedonlyontikertoimien automaattinen keruu (Tehtava 0)" `
    -Force

Write-Host "Tehtava '$taskName' rekisteroity. Tarkista Task Schedulerista tai komennolla:"
Write-Host "  Get-ScheduledTask -TaskName '$taskName'"
