param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$BackgroundCatchUp
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[INFO] First launch detected — running bootstrap..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:LOCALSHORTS_PORT = "$Port"

if ($BackgroundCatchUp) {
    Write-Host "[INFO] Running background catch-up check..." -ForegroundColor Cyan
}

if (-not $NoBrowser -and -not $BackgroundCatchUp) {
    Start-Job -ScriptBlock {
        param($p)
        Start-Sleep -Seconds 2
        Start-Process "http://127.0.0.1:$p/dashboard"
    } -ArgumentList $Port | Out-Null
}

Write-Host "[OK] Starting LocalShorts Studio server on http://127.0.0.1:$Port ..." -ForegroundColor Green
& $VenvPython -m uvicorn localshorts.main:app --host 127.0.0.1 --port $Port
