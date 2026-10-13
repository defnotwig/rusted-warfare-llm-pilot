$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$DistDir = Join-Path $ProjectRoot "dist"
$StageDir = Join-Path $DistDir "LocalShortsStudio-Portable"
$ZipPath = Join-Path $DistDir "LocalShortsStudio-Windows-x64-portable.zip"

if (Test-Path $StageDir) {
    Remove-Item -Recurse -Force $StageDir
}
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

Write-Host "[1/3] Staging portable files into $StageDir ..." -ForegroundColor Cyan

$ItemsToCopy = @(
    "src",
    "scripts",
    "assets",
    "installer",
    "START_LOCAL_SHORTS.bat",
    "DIAGNOSTICS.bat",
    "BUILD_RELEASE.bat",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "VERSION"
)

foreach ($item in $ItemsToCopy) {
    $srcPath = Join-Path $ProjectRoot $item
    if (Test-Path $srcPath) {
        Copy-Item -Path $srcPath -Destination (Join-Path $StageDir $item) -Recurse -Force
    }
}

# Include lightweight runtime tools (uv.exe + ffmpeg/ffprobe) if present
$RtBin = Join-Path $ProjectRoot ".runtime\bin"
if (Test-Path $RtBin) {
    $DestRtBin = Join-Path $StageDir ".runtime\bin"
    New-Item -ItemType Directory -Force -Path $DestRtBin | Out-Null
    Copy-Item "$RtBin\*" $DestRtBin -Recurse -Force
}

$RtFfmpeg = Join-Path $ProjectRoot ".runtime\ffmpeg\bin"
if (Test-Path $RtFfmpeg) {
    $DestRtFfmpeg = Join-Path $StageDir ".runtime\ffmpeg\bin"
    New-Item -ItemType Directory -Force -Path $DestRtFfmpeg | Out-Null
    Copy-Item "$RtFfmpeg\*" $DestRtFfmpeg -Recurse -Force
}

Write-Host "[2/3] Compressing $ZipPath ..." -ForegroundColor Cyan
if (Test-Path $ZipPath) {
    Remove-Item -Force $ZipPath
}
Compress-Archive -Path "$StageDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal

Write-Host "[3/3] Updating SHA256SUMS.txt ..." -ForegroundColor Cyan
$Hash = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash.ToLower()
$ShaFile = Join-Path $DistDir "SHA256SUMS.txt"
"$Hash  LocalShortsStudio-Windows-x64-portable.zip" | Set-Content -Path $ShaFile -Encoding UTF8

Write-Host "[OK] Built portable release: $ZipPath ($Hash)" -ForegroundColor Green
