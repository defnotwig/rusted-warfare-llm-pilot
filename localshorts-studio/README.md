# LocalShorts Studio (`v0.1.0`)

> **Free Local AI Shorts Factory**  
> *Local-First, Zero-Subscription YouTube Shorts Production on Windows*

---

## 🚀 Overview

**LocalShorts Studio** is a completely local, zero-subscription desktop application designed to research trending internet & creator stories and produce original `1080×1920` 30fps vertical YouTube Shorts automatically — capable of preparing up to **10 candidate Shorts/day** (~300 candidate Shorts/month) using free and open local software.

### 💰 Zero Mandatory Subscriptions
Runs entirely on your computer with **no paid APIs, no recurring cloud costs, and no third-party rendering fees**:
- ❌ No OpenAI / Claude / paid Gemini API required
- ❌ No ElevenLabs / paid TTS required
- ❌ No Runway / paid video generation SaaS required
- ❌ No CapCut Pro / Adobe Premiere subscriptions required
- ❌ No Revid / Virlo / cloud automation SaaS required
- ✅ **The only ongoing costs are electricity, internet, and disk space!**

---

## 💻 Target Hardware & Resource Profiles

Optimized specifically for laptops with **16 GB RAM and 4 GB VRAM** (such as the Acer Predator Helios 300 with NVIDIA RTX 3050 Ti Laptop GPU / RTX 4050 Laptop GPU):

| Preset | Target Hardware | LLM Model | Context | Concurrency | Video Encoder | TTS Engine |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **LIGHTWEIGHT** | $\le 8\text{ GB}$ RAM, no NVIDIA GPU | `qwen2.5:1.5b` | 2048 | 1 | CPU `libx264` | Windows SAPI / DSP |
| **BALANCED** *(Default)* | **16 GB RAM, 4 GB VRAM** | **`qwen3:4b`** | **4096** | **1** | **`h264_nvenc` (CPU fallback)** | **Kokoro ONNX / SAPI** |
| **PERFORMANCE** | $\ge 32\text{ GB}$ RAM, $\ge 8\text{ GB}$ VRAM | `qwen3:4b` / larger | 8192 | 1 | `h264_nvenc` | Kokoro ONNX |

### 🛡️ Thermal & Laptop Safety
- **< 80°C**: Normal operation.
- **80°C – 85°C**: Automatic 10-second thermal cooldown inserted between queue jobs.
- **> 85°C**: GPU queue paused automatically until GPU cools below 80°C.
- **Battery Guard**: Shows banner `Running on battery — batch generation paused by default.` with 1-click user override.
- **VRAM Unload**: LLM model is unloaded (`keep_alive: 0`) before FFmpeg encoding starts so the 4 GB VRAM is never contested.

---

## ⚙️ How to Use It

### 1. One-Click Launch
1. Open PowerShell or Command Prompt in the project folder.
2. Double-click or run:
   ```cmd
   START_LOCAL_SHORTS.bat
   ```
3. The launcher will automatically bootstrap the isolated Python 3.11 virtual environment (`.venv`), verify FFmpeg & audio assets, launch the local server at `http://127.0.0.1:8765`, and open your default web browser.

### 2. Verify Readiness with Diagnostics
Before rendering your first video, run the 15-point system diagnostics:
```cmd
DIAGNOSTICS.bat
```
Checks:
- Python 3.11 virtual environment
- Portable FFmpeg & ffprobe binaries
- Hardware NVENC probe with CPU fallback
- Local Ollama status & model readiness
- Kokoro ONNX / Windows SAPI local TTS
- SQLite WAL mode database
- 5 CC0 procedural WAV sound effects
- OFL kinetic caption fonts
- Real 4.2-second `1080×1920` diagnostic render test

### 3. Producing Your First Short (3 Modes)

#### Mode A: 1-Click Installation Smoke Test
In the top bar, click **1-Click Smoke Test** (or run `POST /api/jobs/sample`). In ~20 seconds, the engine generates an original, verified 18-second Short explaining *Why Local AI Runs Without Sending Prompts to the Cloud*, complete with voiceover, kinetic captions, punch-zooms, and CC0 sound effects.

#### Mode B: Automated Daily Batch Production
1. Navigate to the **Dashboard** (`/dashboard`).
2. Click **Scan Live Trends** to aggregate stories from Google Trends, Google News, YouTube RSS, Reddit, and Hacker News.
3. Click **Generate 3 Candidate Shorts Now** or **Run Full Daily Batch (10)**.
4. The sequential worker processes jobs one at a time, moving passing videos to `output/ready-to-upload/`.

#### Mode C: Custom / Manual Topic Creation
1. Navigate to **Create Short** (`/create`).
2. Enter your custom headline (e.g. *How Modern Creators Engineer High-Retention 30-Second Stories*).
3. Select visual style (`Creator News`, `Tech Explainer`, `Breaking Internet`, `Challenge`, `Minimal Documentary`, `High Energy`) and caption preset (`Bold`, `Clean`, `Minimal`, `High Energy`).
4. Click **Queue & Render Short**.

### 4. Per-Stage Regeneration
Did you love the script but want a different voice, caption style, or visual theme? You do **not** have to re-run the entire pipeline! In the **Queue** (`/queue`), click:
- **Regen Script**: Re-writes 55–80 word script with originality check.
- **Regen Voice**: Re-synthesizes audio with another voice ID.
- **Regen Captions**: Re-styles kinetic subtitle cards without re-recording audio.
- **Regen Visuals**: Re-composites procedural visual plates.
- **Re-Render**: Fast re-mux and re-encode to MP4.

---

## 📁 Output Artifacts (`output/`)

Every approved Short produces a self-contained folder under `output/YYYY-MM-DD/{slug}/`:
```
output/
├── 2026-10-13/
│   └── why-local-ai-can-run-without-sending-your-prompt-job366/
│       ├── video.mp4               # Final 1080x1920 30fps vertical Short
│       ├── thumbnail.jpg           # Extracted 1080x1920 cover image
│       ├── narration.wav           # Raw clean voice narration track
│       ├── captions.ass            # Styled ASS kinetic subtitle file
│       ├── captions.srt            # Standard SubRip subtitle track
│       ├── script.json             # Structured script, hooks, and claims
│       ├── script.txt              # Plain spoken words
│       ├── sources.json            # Discovered sources and URLs
│       ├── source_manifest.json    # Verified EvidenceBundleSchema
│       ├── scene_plan.json         # Beat-by-beat scene layout
│       ├── qc.json                 # ffprobe Technical QC report
│       └── metadata.json           # Title options, description, tags
└── ready-to-upload/
    └── 2026-10-13_why-local-ai-can-run-without-sending-your-prompt-job366.mp4
```

---

## 🛠️ Testing & Release Packaging

### Running Automated Tests
```powershell
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\pytest.exe -v
```
All **29 unit and integration tests** verify:
- Hardware classification & thermal/battery safety
- Trend normalization, clustering & missing-signal scoring
- Source Confidence scoring (0–100) & Evidence Bundle
- Script word count enforcement (`55–80` words) & banned intro stripping
- Originality gate (`<= 0.78` 3-gram + RapidFuzz + TF-IDF cosine check)
- Security slug sanitization & path containment
- Caption card segmentation & keyword emphasis
- FFmpeg command builder & NVENC fallback
- SQLite WAL migrations & crash recovery
- Live CPU TTS & CC0 audio mixer
- Real FFmpeg `1080×1920` rendering & `ffprobe` Technical QC
- FastAPI REST API & server-rendered HTML views

### Building Portable ZIP & Windows Installer
Double-click or run:
```cmd
BUILD_RELEASE.bat
```
Generates:
- `dist/LocalShortsStudio-Windows-x64-portable.zip`
- `dist/LocalShortsStudio-Setup-x64.exe`
- `dist/SHA256SUMS.txt`

---

## 📜 License & Attributions

- **LocalShorts Studio**: Licensed under the [MIT License](LICENSE).
- **Audio Assets**: The 5 procedural sound effects in `assets/sfx/` (`impact.wav`, `whoosh.wav`, `pop.wav`, `click.wav`, `riser.wav`) are synthesized mathematically using NumPy and released under **CC0 1.0 Universal (Public Domain)**.
- **Fonts**: Caption fonts in `assets/fonts/` (`Archivo Black`, `Barlow Condensed`, `Roboto Condensed`) are licensed under the **SIL Open Font License (OFL) / Apache 2.0**.
