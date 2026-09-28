# Builds dist\BoostAI\BoostAI.exe and, if Inno Setup 6 is installed, an installer in installer_output\.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -q -r requirements-dev.txt
& $py scripts\make_icon.py
& $py -m PyInstaller packaging\boostai.spec --noconfirm --clean
Write-Host "Built dist\BoostAI\BoostAI.exe"

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc packaging\installer.iss
    Write-Host "Installer written to installer_output\"
} else {
    Write-Host "Inno Setup 6 not found - skipping installer. Install it from https://jrsoftware.org/isinfo.php and re-run."
}
