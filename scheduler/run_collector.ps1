# Kutsuu kertoimien keruun. Task Scheduler kutsuu tata tiedostoa.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& py -3 "$root\src\collect_odds.py"
exit $LASTEXITCODE
