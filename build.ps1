param([string]$Python = "python", [switch]$SkipInstall)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Path 'work/tmp' -Force | Out-Null
$env:TEMP = Join-Path $PSScriptRoot 'work/tmp'
$env:TMP = $env:TEMP
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PSScriptRoot 'work/pyinstaller-config'
$env:PYTHONDONTWRITEBYTECODE = '1'
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
}
if (-not $SkipInstall) {
    & ./.venv/Scripts/python.exe -m pip install -r dependency-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& ./.venv/Scripts/python.exe scripts/build.py
if ($LASTEXITCODE -ne 0) { throw 'Build or verification failed.' }
Write-Output 'Built and verified: dist/PDF Reader/PDF Reader.exe'
