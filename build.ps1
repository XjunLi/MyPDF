param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
}
& .\.venv\Scripts\python.exe -m pip install -r dependency-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv\Scripts\python.exe -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --name 'PDF Reader' --icon assets\reader.ico --version-file assets\version_info.txt --add-data 'assets;assets' --add-data 'samples;samples' --collect-binaries winpty --collect-data winpty --exclude-module winpty.tests --exclude-module pytest main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath 'README.md','LICENSE','THIRD_PARTY.md' -Destination 'dist\PDF Reader'
Copy-Item -LiteralPath 'licenses','plugins' -Destination 'dist\PDF Reader' -Recurse -Force
Write-Output 'Built: dist\PDF Reader\PDF Reader.exe'
