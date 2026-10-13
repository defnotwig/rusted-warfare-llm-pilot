// LocalShorts Studio v0.1.0 — Client Controller (Zero External Dependency)

function showToast(message, isError = false) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = "toast";
  if (isError) {
    el.style.borderColor = "#ef4444";
  }
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

async function apiRequest(url, method = "GET", body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== null) {
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    let errText = `HTTP ${resp.status}`;
    try {
      const errJson = await resp.json();
      errText = errJson.detail || JSON.stringify(errJson);
    } catch (_) {}
    throw new Error(errText);
  }
  return await resp.json();
}

async function refreshStatusBar() {
  try {
    const data = await apiRequest("/api/status-bar");
    const stageEl = document.getElementById("telemetry-stage");
    if (stageEl) {
      stageEl.textContent = data.queue_paused ? "PAUSED" : (data.queue_count > 0 ? `ACTIVE (${data.queue_count})` : "IDLE");
    }
    const tempEl = document.getElementById("telemetry-temp");
    if (tempEl && data.gpu_temp_c !== null) {
      tempEl.textContent = `${data.gpu_temp_c}°C (${data.thermal_state})`;
    }
  } catch (_) {}
}

setInterval(refreshStatusBar, 5000);

async function triggerSmokeTest() {
  try {
    showToast("Queueing 1-Click Local AI Smoke Test Short...");
    await apiRequest("/api/jobs/sample", "POST", {});
    setTimeout(() => { window.location.href = "/queue"; }, 600);
  } catch (err) {
    showToast(`Smoke test failed: ${err.message}`, true);
  }
}

async function generateDailyBatch(count) {
  try {
    showToast(`Queueing batch of ${count} candidate Short(s)...`);
    const res = await apiRequest("/api/jobs/batch", "POST", { count });
    showToast(`Queued ${res.queued_count} Short job(s). Redirecting...`);
    setTimeout(() => { window.location.href = "/queue"; }, 700);
  } catch (err) {
    showToast(`Batch failed: ${err.message}`, true);
  }
}

async function scanTrends(researchOnly = false) {
  try {
    showToast("Scanning public RSS/JSON feeds & calculating Source Confidence...");
    const res = await apiRequest("/api/trends/scan", "POST", {});
    showToast(`Discovered & ranked ${res.count} candidate stories!`);
    setTimeout(() => { window.location.reload(); }, 700);
  } catch (err) {
    showToast(`Trend scan failed: ${err.message}`, true);
  }
}

async function queueFromTrend(trendId, visualPreset = "Creator News") {
  try {
    showToast(`Queueing Short for trend ${trendId}...`);
    await apiRequest("/api/trends/generate-selected", "POST", { trend_ids: [String(trendId)] });
    setTimeout(() => { window.location.href = "/queue"; }, 600);
  } catch (err) {
    showToast(`Failed to queue trend: ${err.message}`, true);
  }
}

async function submitCreateForm() {
  try {
    const topic = document.getElementById("create-topic")?.value || "Local AI Shorts Production";
    const trendId = document.getElementById("create-trend-id")?.value || "";
    const visualPreset = document.getElementById("create-preset")?.value || "Creator News";
    const captionPreset = document.getElementById("create-caption")?.value || "Bold";
    const voiceId = document.getElementById("create-voice")?.value || "af_heart";
    showToast("Enqueuing new Short production job...");
    await apiRequest("/api/jobs", "POST", {
      topic,
      headline: topic,
      trend_item_id: String(trendId),
      visual_preset: visualPreset,
      caption_preset: captionPreset,
      voice_id: voiceId,
    });
    setTimeout(() => { window.location.href = "/queue"; }, 600);
  } catch (err) {
    showToast(`Create failed: ${err.message}`, true);
  }
}

async function regenerateStage(jobId, stage) {
  try {
    const stageNorm = String(stage).toLowerCase();
    const mapped = stageNorm === "scenes" ? "scene_plan" : stageNorm;
    showToast(`Regenerating ${mapped} stage for job ${jobId}...`);
    await apiRequest(`/api/jobs/${jobId}/regenerate-stage`, "POST", { stage: mapped });
    setTimeout(() => { window.location.href = "/queue"; }, 600);
  } catch (err) {
    showToast(`Stage regeneration failed: ${err.message}`, true);
  }
}

async function cancelJob(jobId) {
  try {
    await apiRequest(`/api/jobs/${jobId}/cancel`, "POST", {});
    showToast(`Cancelled job ${jobId}`);
    setTimeout(() => { window.location.reload(); }, 500);
  } catch (err) {
    showToast(`Cancel failed: ${err.message}`, true);
  }
}

async function saveSettings() {
  try {
    const payload = {
      daily_candidate_limit: parseInt(document.getElementById("setting-daily-target")?.value || "10", 10),
      ollama_model: document.getElementById("setting-ollama-model")?.value || "qwen3:4b",
      ollama_num_ctx: parseInt(document.getElementById("setting-ollama-ctx")?.value || "4096", 10),
      tts_provider: document.getElementById("setting-tts-engine")?.value || "kokoro",
      tts_voice: document.getElementById("setting-tts-voice")?.value || "af_heart",
      visual_preset: document.getElementById("setting-visual-preset")?.value || "Creator News",
      caption_preset: document.getElementById("setting-caption-preset")?.value || "Bold",
      channel_niche: document.getElementById("setting-niche")?.value || "Viral Creator & Internet Moments Explained",
      battery_override: Boolean(document.getElementById("setting-battery-override")?.checked),
      windows_startup_enabled: Boolean(document.getElementById("setting-auto-start")?.checked),
    };
    await apiRequest("/api/settings", "POST", payload);
    showToast("Settings saved to SQLite WAL!");
  } catch (err) {
    showToast(`Failed to save settings: ${err.message}`, true);
  }
}

async function toggleBatteryOverride(enable) {
  try {
    await apiRequest("/api/settings", "POST", { battery_override: Boolean(enable) });
    showToast("Battery override updated");
    setTimeout(() => { window.location.reload(); }, 400);
  } catch (err) {
    showToast(`Failed: ${err.message}`, true);
  }
}

async function restartOllama() {
  try {
    showToast("Starting / restarting local Ollama service...");
    const res = await apiRequest("/api/system/ollama/restart", "POST", {});
    showToast(res.restarted ? "Ollama service online!" : "Ollama not running — offline deterministic fallback active.");
    setTimeout(() => { window.location.reload(); }, 800);
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
  }
}

async function pullOllamaModel(modelName) {
  showToast(`Model ${modelName} configured. If Ollama is running, it will pull automatically.`);
}

async function benchmarkOllama() {
  try {
    showToast("Running 80-token Ollama speed benchmark...");
    const res = await apiRequest("/api/system/ollama/benchmark", "POST", {});
    const out = document.getElementById("benchmark-output");
    if (out) {
      out.textContent = `Benchmark Tier: ${res.tier} | Speed: ${res.tokens_per_second} tok/s | ${res.recommendation}`;
    }
    showToast(`Benchmark complete: ${res.tokens_per_second} tok/s (${res.tier})`);
  } catch (err) {
    showToast(`Benchmark error: ${err.message}`, true);
  }
}

async function clearTempCache() {
  try {
    await apiRequest("/api/settings/cache/clean", "POST", { include_media_cache: false });
    showToast("Cleaned temporary render cache!");
    setTimeout(() => { window.location.reload(); }, 600);
  } catch (err) {
    showToast(`Cache clean failed: ${err.message}`, true);
  }
}

async function exportDiagnostics() {
  try {
    showToast("Running full diagnostics & exporting report...");
    const res = await apiRequest("/api/system/diagnostics/run", "POST", {});
    showToast(`Diagnostics complete — System Ready: ${res.system_ready}`);
  } catch (err) {
    showToast(`Diagnostics error: ${err.message}`, true);
  }
}
