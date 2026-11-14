# Builds installer\TenderPilot-Setup.exe and installer\TenderPilot-Demo-Setup.exe.
# Needs: the project .venv (with pyinstaller), Node.js, Tesseract at C:\Program Files\Tesseract-OCR, Inno Setup 6.
# Run from the TENDERPRO folder:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "1/4 Building the web interface..."
Push-Location frontend; npm run build; if ($LASTEXITCODE) { throw "frontend build failed" }; Pop-Location

Write-Host "2/4 Bundling the program (PyInstaller)..."
.\.venv\Scripts\pyinstaller packaging\tenderpilot.spec --noconfirm --distpath packaging\dist --workpath packaging\build
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup" }

Write-Host "3/4 Building the full installer..."
& $iscc /Q packaging\tenderpilot.iss; if ($LASTEXITCODE) { throw "full installer failed" }
Write-Host "4/4 Building the demo installer..."
& $iscc /Q /DDEMO packaging\tenderpilot.iss; if ($LASTEXITCODE) { throw "demo installer failed" }

Get-ChildItem installer\*.exe | Format-Table Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB) } }
