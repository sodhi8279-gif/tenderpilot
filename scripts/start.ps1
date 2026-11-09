# TenderPilot one-click local start (Windows PowerShell). Run:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "Creating Python environment..."
  python -m venv .venv
  .\.venv\Scripts\python -m pip install --upgrade pip
  .\.venv\Scripts\python -m pip install -r backend\requirements.txt
}
if (-not (Test-Path "frontend\dist\index.html")) {
  Write-Host "Building the web interface..."
  Push-Location frontend; npm install; npm run build; Pop-Location
}
if ($args -contains "-demo") {
  Push-Location backend; ..\.venv\Scripts\python seed_demo.py; Pop-Location
}
Write-Host "TenderPilot is starting at http://127.0.0.1:8000  (press Ctrl+C to stop)"
Start-Process "http://127.0.0.1:8000"
Push-Location backend
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Pop-Location
