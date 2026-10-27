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
& ./.venv/Scripts/python.exe -m pytest tests -q --basetemp=work/pytest -o cache_dir=work/pytest-cache
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& ./.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --name 'PDF Reader' --icon (Join-Path $PSScriptRoot 'assets/reader.ico') --version-file (Join-Path $PSScriptRoot 'assets/version_info.txt') --add-data "$PSScriptRoot\assets;assets" --add-data "$PSScriptRoot\samples;samples" --exclude-module winpty --exclude-module pyte --exclude-module wcwidth --exclude-module pytest --distpath dist --workpath work/pyinstaller --specpath work main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath 'README.md','LICENSE','THIRD_PARTY.md','CHANGELOG.md','VALIDATION.md','VALIDATION.json','DEVELOPMENT.md' -Destination 'dist/PDF Reader'
Copy-Item -LiteralPath 'licenses','plugins' -Destination 'dist/PDF Reader' -Recurse -Force
Write-Output 'Built: dist/PDF Reader/PDF Reader.exe'
