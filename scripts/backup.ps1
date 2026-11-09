# Backs up the SQLite database and stored files into backups\<timestamp>.zip. Stop the server first for a consistent copy.
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force "$root\backups" | Out-Null
Compress-Archive -Path "$root\backend\data\*" -DestinationPath "$root\backups\tenderpilot-$stamp.zip"
Write-Host "Backup written to backups\tenderpilot-$stamp.zip"
