# Optionnel : produit dist\CleanLite.exe autonome avec PyInstaller. À lancer après install.ps1.
# Usage : powershell -ExecutionPolicy Bypass -File build_exe.ps1
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Host "Lancez d'abord install.ps1." -ForegroundColor Yellow; exit 1 }

Push-Location $Root
try {
    & $Py -m pip install --disable-pip-version-check -q pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Échec de l'installation de PyInstaller." }
    & $Py -m PyInstaller --noconfirm --onefile --noconsole --name CleanLite `
        --icon assets\icon.ico --add-data "assets\icon.ico;assets" --paths . cleanlite\__main__.py
    if ($LASTEXITCODE -ne 0) { throw "Échec de PyInstaller." }
    Write-Host "Exécutable : $(Join-Path $Root 'dist\CleanLite.exe')" -ForegroundColor Green
} finally { Pop-Location }
