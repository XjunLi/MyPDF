$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
    & .\.venv\Scripts\python.exe main.py @args
} else {
    & python main.py @args
}
