# Installe CleanLite pour l'utilisateur courant : environnement virtuel + raccourcis Bureau et menu Démarrer.
# Usage : powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"

function Test-Python([string]$Exe, [string[]]$Pre) {
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        # Exige 3.11+ ET tkinter ; écarte aussi l'alias « python » du Microsoft Store.
        $out = & $Exe @Pre -c "import sys, tkinter; print(sys.version_info >= (3, 11))" 2>$null
        return ("$out".Trim() -eq "True")
    } catch { return $false }
}

$PyExe = $null; $PyArgs = @()
if (Test-Python "py" @("-3")) { $PyExe = "py"; $PyArgs = @("-3") }
elseif (Test-Python "python" @()) { $PyExe = "python" }
if (-not $PyExe) {
    Write-Host "Python 3.11+ (avec tkinter) est introuvable." -ForegroundColor Yellow
    Write-Host "Installez-le avec :   winget install -e --id Python.Python.3.12"
    Write-Host "puis ouvrez un NOUVEAU terminal et relancez ce script."
    exit 1
}

if (-not (Test-Path (Join-Path $Venv "Scripts\pythonw.exe"))) {
    Write-Host "Création de l'environnement virtuel..."
    & $PyExe @PyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Échec de la création de l'environnement virtuel." }
}
& (Join-Path $Venv "Scripts\python.exe") -m pip install --disable-pip-version-check -q -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Échec de l'installation des dépendances." }

$Shell = New-Object -ComObject WScript.Shell
foreach ($Dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
    $Path = Join-Path $Dir "CleanLite.lnk"
    $Lnk = $Shell.CreateShortcut($Path)
    $Lnk.TargetPath = Join-Path $Venv "Scripts\pythonw.exe"   # pythonw : pas de console
    $Lnk.Arguments = "-m cleanlite"
    $Lnk.WorkingDirectory = $Root
    $Lnk.IconLocation = (Join-Path $Root "assets\icon.ico") + ",0"
    $Lnk.Description = "CleanLite - nettoyage prudent de Windows"
    $Lnk.Save()
    Write-Host "Raccourci créé : $Path"
}
Write-Host "Installation terminée : lancez CleanLite depuis le Bureau ou le menu Démarrer." -ForegroundColor Green
