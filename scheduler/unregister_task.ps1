# Poistaa aiemmin rekisteroidyn ajastetun tehtavan.
$taskName = "CS2-Pelivoimat-KertoimienKeruu"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Tehtava '$taskName' poistettu."
