$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[FAIL] .venv not found at $VenvPython. Run scripts\bootstrap.ps1 first." -ForegroundColor Red
    exit 1
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $VenvPython -m localshorts.cli_diagnostics
exit $LASTEXITCODE
