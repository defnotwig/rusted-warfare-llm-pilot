param(
    [switch]$Force = $false,
    [switch]$SkipModelPull = $false
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$RuntimeDir = Join-Path $ProjectRoot ".runtime"
$BinDir = Join-Path $RuntimeDir "bin"
$FfmpegBinDir = Join-Path $RuntimeDir "ffmpeg\bin"
$KokoroDir = Join-Path $RuntimeDir "models\kokoro"
$SetupStateFile = Join-Path $RuntimeDir "setup-state.json"
$LogDir = Join-Path $ProjectRoot "logs"
$SetupLog = Join-Path $LogDir "setup.log"

$RequiredDirs = @(
    "data",
    "cache",
    "cache\media",
    "cache\temp",
    "output",
    "output\ready-to-upload",
    "logs",
    "logs\ffmpeg",
    "runtime",
    ".runtime",
    ".runtime\bin",
    ".runtime\ffmpeg\bin",
    ".runtime\models\kokoro",
    "assets\fonts",
    "assets\sfx",
    "assets\music",
    "assets\backgrounds",
    "assets\authorized_media"
)

foreach ($dir in $RequiredDirs) {
    $full = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $full)) {
        New-Item -ItemType Directory -Path $full -Force | Out-Null
    }
}

foreach ($keepDir in @("data", "cache", "output", "logs", "runtime", "assets\music", "assets\authorized_media")) {
    $keepFile = Join-Path (Join-Path $ProjectRoot $keepDir) ".gitkeep"
    if (-not (Test-Path $keepFile)) {
        New-Item -ItemType File -Path $keepFile -Force | Out-Null
    }
}

function Write-SetupLog([string]$Message, [string]$Level = "INFO") {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $SetupLog -Value $line -ErrorAction SilentlyContinue
}

function Download-FastFile([string]$Url, [string]$OutFile) {
    $curlExe = "$env:SystemRoot\System32\curl.exe"
    if (Test-Path $curlExe) {
        & $curlExe -L --fail --retry 3 --connect-timeout 20 -s -S -o $OutFile $Url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $OutFile) -and ((Get-Item $OutFile).Length -gt 0)) {
            return
        }
    }
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec 300
}

$AppVersion = (Get-Content (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
Write-SetupLog "=== LocalShorts Studio v$AppVersion Bootstrap Starting ==="

$State = @{
    app_version = $AppVersion
    python_ready = $false
    python_version = ""
    ffmpeg_ready = $false
    ffmpeg_path = ""
    ffprobe_path = ""
    nvenc_supported = $false
    ollama_installed = $false
    ollama_path = ""
    ollama_model = ""
    tts_ready = $false
    tts_provider = "kokoro-onnx"
    fonts_ready = $false
    sfx_ready = $false
    schema_version = 1
    diagnostic_status = "PENDING"
    last_verified_at = ""
}

if ((Test-Path $SetupStateFile) -and (-not $Force)) {
    try {
        $existing = Get-Content $SetupStateFile -Raw | ConvertFrom-Json
        foreach ($prop in $existing.PSObject.Properties) {
            $State[$prop.Name] = $prop.Value
        }
    } catch {
        Write-SetupLog "Could not parse existing setup-state.json; re-running checks." "WARN"
    }
}

function Save-SetupState() {
    $State.last_verified_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $State | ConvertTo-Json -Depth 5 | Set-Content -Path $SetupStateFile -Encoding UTF8
}

$env:PATH = "$BinDir;$FfmpegBinDir;$env:LOCALAPPDATA\Programs\Ollama;$env:USERPROFILE\.local\bin;$env:PATH"

# 1. Provision uv
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    $localUv = Join-Path $BinDir "uv.exe"
    if (-not (Test-Path $localUv)) {
        Write-SetupLog "Downloading uv package manager into .runtime\bin..."
        $uvZip = Join-Path $RuntimeDir "uv.zip"
        $uvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
        Download-FastFile -Url $uvUrl -OutFile $uvZip
        Expand-Archive -Path $uvZip -DestinationPath $BinDir -Force
        $nestedUv = Get-ChildItem -Path $BinDir -Filter "uv.exe" -Recurse | Select-Object -First 1
        if ($nestedUv -and ($nestedUv.FullName -ne $localUv)) {
            Copy-Item $nestedUv.FullName $localUv -Force
        }
        Remove-Item $uvZip -Force -ErrorAction SilentlyContinue
    }
    $uvExe = $localUv
} else {
    $uvExe = $uvCmd.Source
}
Write-SetupLog "uv ready at: $uvExe"

# 2. Provision Python 3.11 & project .venv
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if ((-not (Test-Path $venvPython)) -or (-not $State.python_ready) -or $Force) {
    Write-SetupLog "Ensuring Python 3.11 runtime via uv..."
    & $uvExe python install 3.11
    if (-not (Test-Path $venvPython)) {
        Write-SetupLog "Creating project virtual environment (.venv)..."
        & $uvExe venv --python 3.11 ".venv"
    }
    Write-SetupLog "Syncing Python dependencies into .venv..."
    & $uvExe sync --extra dev
    $pyVer = (& $venvPython -c "import platform; print(platform.python_version())").Trim()
    $State.python_ready = $true
    $State.python_version = $pyVer
    Save-SetupState
} else {
    Write-SetupLog "Python virtual environment (.venv) already verified ($($State.python_version))."
}

# 3. Provision FFmpeg & ffprobe
$localFfmpeg = Join-Path $FfmpegBinDir "ffmpeg.exe"
$localFfprobe = Join-Path $FfmpegBinDir "ffprobe.exe"
$ffmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobeCmd = Get-Command ffprobe -ErrorAction SilentlyContinue

if ((Test-Path $localFfmpeg) -and (Test-Path $localFfprobe)) {
    $ffmpegExe = $localFfmpeg
    $ffprobeExe = $localFfprobe
} elseif ($ffmpegCmd -and $ffprobeCmd) {
    $ffmpegExe = $ffmpegCmd.Source
    $ffprobeExe = $ffprobeCmd.Source
} else {
    Write-SetupLog "Downloading portable FFmpeg + ffprobe (Windows x64 GPL with NVENC) via curl..."
    $ffmpegZip = Join-Path $RuntimeDir "ffmpeg.zip"
    $ffmpegExtract = Join-Path $RuntimeDir "ffmpeg_extract"
    $btbnUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    $gyanUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    try {
        Download-FastFile -Url $btbnUrl -OutFile $ffmpegZip
    } catch {
        Write-SetupLog "Using gyan.dev official FFmpeg mirror..." "WARN"
        Download-FastFile -Url $gyanUrl -OutFile $ffmpegZip
    }
    if (Test-Path $ffmpegExtract) { Remove-Item $ffmpegExtract -Recurse -Force }
    Expand-Archive -Path $ffmpegZip -DestinationPath $ffmpegExtract -Force
    $foundFfmpeg = Get-ChildItem -Path $ffmpegExtract -Filter "ffmpeg.exe" -Recurse | Select-Object -First 1
    $foundFfprobe = Get-ChildItem -Path $ffmpegExtract -Filter "ffprobe.exe" -Recurse | Select-Object -First 1
    if ($foundFfmpeg -and $foundFfprobe) {
        Copy-Item $foundFfmpeg.FullName $localFfmpeg -Force
        Copy-Item $foundFfprobe.FullName $localFfprobe -Force
    } else {
        throw "Failed to locate ffmpeg.exe/ffprobe.exe in downloaded archive."
    }
    Remove-Item $ffmpegExtract -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $ffmpegZip -Force -ErrorAction SilentlyContinue
    $ffmpegExe = $localFfmpeg
    $ffprobeExe = $localFfprobe
}

$State.ffmpeg_ready = $true
$State.ffmpeg_path = $ffmpegExe
$State.ffprobe_path = $ffprobeExe
Write-SetupLog "FFmpeg ready at: $ffmpegExe"

try {
    $nvencProc = Start-Process -FilePath $ffmpegExe -ArgumentList @("-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-") -Wait -PassThru -NoNewWindow
    $State.nvenc_supported = ($nvencProc.ExitCode -eq 0)
} catch {
    $State.nvenc_supported = $false
}
Write-SetupLog "NVENC hardware encoder probe: $($State.nvenc_supported)"
Save-SetupState

# 4. Provision Redistributable Open Fonts (SIL OFL 1.1)
$FontsDir = Join-Path $ProjectRoot "assets\fonts"
$FontFiles = @{
    "ArchivoBlack-Regular.ttf" = "https://github.com/google/fonts/raw/main/ofl/archivoblack/ArchivoBlack-Regular.ttf"
    "BarlowCondensed-Bold.ttf" = "https://github.com/google/fonts/raw/main/ofl/barlowcondensed/BarlowCondensed-Bold.ttf"
    "RobotoCondensed-Bold.ttf" = "https://github.com/google/fonts/raw/main/ofl/robotocondensed/RobotoCondensed%5Bwght%5D.ttf"
}
$allFontsPresent = $true
foreach ($fontName in $FontFiles.Keys) {
    $targetFont = Join-Path $FontsDir $fontName
    if (-not (Test-Path $targetFont)) {
        Write-SetupLog "Downloading redistributable OFL font: $fontName..."
        try {
            Download-FastFile -Url $FontFiles[$fontName] -OutFile $targetFont
        } catch {
            Write-SetupLog "Warning: Could not download $fontName ($($_.Exception.Message))" "WARN"
            $allFontsPresent = $false
        }
    }
}
$oflFile = Join-Path $FontsDir "OFL.txt"
if (-not (Test-Path $oflFile)) {
    Set-Content -Path $oflFile -Value "SIL OPEN FONT LICENSE Version 1.1 - http://scripts.sil.org/OFL`nBundled open fonts: Archivo Black, Barlow Condensed, Roboto Condensed." -Encoding UTF8
}
$State.fonts_ready = $allFontsPresent
Save-SetupState

# 5. Provision Kokoro Local TTS ONNX weights
$kokoroModel = Join-Path $KokoroDir "kokoro-v1.0.onnx"
$kokoroVoices = Join-Path $KokoroDir "voices-v1.0.bin"
if ((-not (Test-Path $kokoroModel)) -or (-not (Test-Path $kokoroVoices))) {
    Write-SetupLog "Downloading Kokoro Local CPU TTS ONNX model & voice pack via curl..."
    try {
        if (-not (Test-Path $kokoroModel)) {
            Download-FastFile -Url "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx" -OutFile $kokoroModel
        }
        if (-not (Test-Path $kokoroVoices)) {
            Download-FastFile -Url "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin" -OutFile $kokoroVoices
        }
        $State.tts_ready = $true
        $State.tts_provider = "kokoro-onnx"
    } catch {
        Write-SetupLog "Kokoro download warning ($($_.Exception.Message)); Windows SAPI offline TTS remains ready." "WARN"
        $State.tts_ready = $true
        $State.tts_provider = "windows-sapi"
    }
} else {
    $State.tts_ready = $true
    $State.tts_provider = "kokoro-onnx"
    Write-SetupLog "Kokoro Local TTS model & voices already verified."
}
Save-SetupState

# 6. Provision Ollama & Local LLM Model (qwen3:4b)
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
$defaultOllamaPath = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if ($ollamaCmd) {
    $ollamaExe = $ollamaCmd.Source
} elseif (Test-Path $defaultOllamaPath) {
    $ollamaExe = $defaultOllamaPath
} else {
    Write-SetupLog "Ollama not found. Installing official Ollama for Windows..."
    $ollamaInstaller = Join-Path $RuntimeDir "OllamaSetup.exe"
    Write-SetupLog "Downloading official OllamaSetup.exe via curl..."
    Download-FastFile -Url "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaInstaller
    Write-SetupLog "Running OllamaSetup.exe /SILENT /NORESTART..."
    Start-Process -FilePath $ollamaInstaller -ArgumentList "/SILENT", "/NORESTART" -Wait
    Remove-Item $ollamaInstaller -Force -ErrorAction SilentlyContinue
    if (Test-Path $defaultOllamaPath) {
        $ollamaExe = $defaultOllamaPath
    } else {
        $ollamaCmdAfter = Get-Command ollama -ErrorAction SilentlyContinue
        if ($ollamaCmdAfter) { $ollamaExe = $ollamaCmdAfter.Source }
    }
}

if ($ollamaExe -and (Test-Path $ollamaExe)) {
    $State.ollama_installed = $true
    $State.ollama_path = $ollamaExe
    Write-SetupLog "Ollama binary ready at: $ollamaExe"

    $ollamaAlive = $false
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
        $ollamaAlive = $true
    } catch {
        Write-SetupLog "Starting Ollama background server (ollama serve)..."
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 1
            try {
                $resp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
                $ollamaAlive = $true
                break
            } catch {}
        }
    }

    if ($ollamaAlive -and (-not $SkipModelPull)) {
        $tagsResp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
        $installedModels = @()
        if ($tagsResp.models) {
            $installedModels = $tagsResp.models | ForEach-Object { $_.name }
        }
        $targetModel = "qwen3:4b"
        if ($installedModels -contains $targetModel) {
            Write-SetupLog "Model $targetModel is already pulled."
            $State.ollama_model = $targetModel
        } else {
            Write-SetupLog "Pulling recommended local model: $targetModel (one-time download)..."
            & $ollamaExe pull $targetModel
            if ($LASTEXITCODE -eq 0) {
                $State.ollama_model = $targetModel
            } else {
                Write-SetupLog "Fallback: pulling qwen2.5:1.5b..." "WARN"
                & $ollamaExe pull "qwen2.5:1.5b"
                $State.ollama_model = "qwen2.5:1.5b"
            }
        }
    }
} else {
    Write-SetupLog "Ollama binary could not be verified automatically." "WARN"
}

Save-SetupState
Write-SetupLog "=== Bootstrap Provisioning Complete ==="
