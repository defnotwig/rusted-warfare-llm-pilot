$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$DistDir = Join-Path $ProjectRoot "dist"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

$SetupExe = Join-Path $DistDir "LocalShortsStudio-Setup-x64.exe"
$IssFile = Join-Path $ProjectRoot "installer\LocalShortsStudio.iss"
$CsFile = Join-Path $ProjectRoot "installer\SetupStub.cs"

$IsccCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$IsccPath = $null
foreach ($c in $IsccCandidates) {
    if (Test-Path $c) {
        $IsccPath = $c
        break
    }
}

if ($IsccPath) {
    Write-Host "[INFO] Compiling installer with Inno Setup ($IsccPath)..." -ForegroundColor Cyan
    & $IsccPath $IssFile
} else {
    Write-Host "[INFO] Compiling Windows x64 Setup EXE from installer\SetupStub.cs..." -ForegroundColor Cyan
    $CscCandidates = Get-ChildItem -Path "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" -ErrorAction SilentlyContinue
    if ($CscCandidates) {
        $Csc = $CscCandidates[0].FullName
        & $Csc /nologo /target:exe "/out:$SetupExe" /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll $CsFile
    }
}

if (Test-Path $SetupExe) {
    $Hash = (Get-FileHash -Path $SetupExe -Algorithm SHA256).Hash.ToLower()
    $ShaFile = Join-Path $DistDir "SHA256SUMS.txt"
    $Existing = ""
    if (Test-Path $ShaFile) {
        $Existing = (Get-Content $ShaFile | Where-Object { $_ -notmatch "LocalShortsStudio-Setup-x64.exe" }) -join "`r`n"
    }
    ($Existing + "`r`n$Hash  LocalShortsStudio-Setup-x64.exe").Trim() | Set-Content -Path $ShaFile -Encoding UTF8
    Write-Host "[OK] Built Windows Installer: $SetupExe ($Hash)" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Failed to build $SetupExe" -ForegroundColor Red
    exit 1
}
