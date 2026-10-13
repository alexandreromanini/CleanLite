# Retire les raccourcis de CleanLite (et sa tâche planifiée si elle existe).
# Le dossier du dépôt et son .venv restent en place : supprimez-les à la main si besoin.
# Usage : powershell -ExecutionPolicy Bypass -File uninstall.ps1
foreach ($Dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $Path = Join-Path $Dir "CleanLite.lnk"
    if (Test-Path $Path) { Remove-Item $Path; Write-Host "Supprimé : $Path" }
}
$Task = "CleanLite - Nettoyage hebdo"
schtasks /Query /TN $Task *> $null
if ($LASTEXITCODE -eq 0) {
    schtasks /Delete /TN $Task /F | Out-Null
    Write-Host "Tâche planifiée supprimée : $Task"
}
Write-Host "CleanLite désinstallé." -ForegroundColor Green
