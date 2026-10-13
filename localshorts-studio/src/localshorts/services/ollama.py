"""Ollama local HTTP provider with structured JSON validation, auto-repair, VRAM unloading, and speed benchmarking."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from typing import Any, AsyncIterator, TypeVar

import httpx
import psutil
from pydantic import BaseModel, ValidationError

from localshorts.config import resolve_binary, settings
from localshorts.logging_config import get_logger
from localshorts.models.schemas import BenchmarkTier, ModelBenchmarkResult
from localshorts.services.hardware import query_nvidia_gpu

logger = get_logger("ollama")

T = TypeVar("T", bound=BaseModel)


def classify_benchmark_speed(tokens_per_second: float) -> tuple[BenchmarkTier, str]:
    """
    Classify model token generation speed:
    - > 15 tok/s = EXCELLENT
    - 8 - 15 tok/s = GOOD
    - 4 - 8 tok/s = USABLE
    - < 4 tok/s = SLOW (recommend smaller model)
    """
    if tokens_per_second > 15.0:
        return (
            BenchmarkTier.EXCELLENT,
            "Excellent speed (>15 tok/s) — ideal for Balanced local generation.",
        )
    if tokens_per_second >= 8.0:
        return (
            BenchmarkTier.GOOD,
            "Good speed (8–15 tok/s) — comfortable for sequential 10 Shorts/day.",
        )
    if tokens_per_second >= 4.0:
        return (
            BenchmarkTier.USABLE,
            "Usable speed (4–8 tok/s) — sequential generation supported.",
        )
    return (
        BenchmarkTier.SLOW,
        "Slow speed (<4 tok/s) — consider switching to smaller fallback model (qwen2.5:1.5b).",
    )


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """
    Extract and parse a JSON object from LLM text, handling markdown code fences
    or <think>...</think> reasoning blocks emitted by Qwen3 models.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty response from LLM")

    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()

    # Strip ```json ... ``` fences if present
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Try direct JSON parse first
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Locate outermost { ... }
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = cleaned[first_brace : last_brace + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            # Repair trailing commas before } or ]
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                return parsed

    raise ValueError(f"Could not parse valid JSON object from model output: {raw_text[:240]}")


class OllamaService:
    """Local Ollama API client with model lifecycle, JSON schema repair, and VRAM hand-off."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    async def health(self) -> dict[str, Any]:
        """Check whether the Ollama HTTP server is reachable and list installed models."""
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m.get("name", "") for m in data.get("models", [])]
                    return {
                        "online": True,
                        "base_url": self.base_url,
                        "models": models,
                    }
        except Exception as exc:
            logger.debug("Ollama health check offline: %s", exc)
        return {"online": False, "base_url": self.base_url, "models": []}

    async def list_models(self) -> list[dict[str, Any]]:
        """Return detailed metadata for all locally installed Ollama models."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                return resp.json().get("models", [])
        except Exception as exc:
            logger.warning("Failed to list Ollama models: %s", exc)
            return []

    async def select_best_available_model(self, preferred: str = "qwen3:4b") -> str:
        """Select the preferred model if installed, or fall back to any installed qwen/gemma model."""
        models_info = await self.list_models()
        installed = [m.get("name", "") for m in models_info]
        if not installed:
            return preferred
        if preferred in installed:
            return preferred
        for candidate in (preferred, "qwen3:4b", "gemma3:4b", "qwen2.5:3b", "qwen2.5:1.5b"):
            if candidate in installed:
                return candidate
        return installed[0]

    async def pull_model(self, model: str) -> bool:
        """Pull an Ollama model via HTTP API."""
        logger.info("Pulling Ollama model '%s'...", model)
        try:
            async with httpx.AsyncClient(timeout=1800.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/pull",
                    json={"name": model, "stream": False},
                )
                return resp.status_code == 200
        except Exception as exc:
            logger.error("Failed to pull model '%s': %s", model, exc)
            return False

    async def unload_model(self, model: str) -> bool:
        """
        Immediately unload the active LLM from GPU VRAM by sending keep_alive=0.
        Critical on 4 GB VRAM laptops before launching h264_nvenc encoding.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": 0},
                )
                logger.info("Unloaded Ollama model '%s' from VRAM (status=%s)", model, resp.status_code)
                return resp.status_code == 200
        except Exception as exc:
            logger.debug("Unload model notice: %s", exc)
            return False

    async def generate(
        self,
        prompt: str,
        model: str = "qwen3:4b",
        system: str = "",
        num_ctx: int = 4096,
        temperature: float = 0.4,
        format_schema: dict[str, Any] | str | None = "json",
        keep_alive: str | int = "5m",
    ) -> dict[str, Any]:
        """Generate a single response from Ollama."""
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "num_ctx": min(max(num_ctx, 1024), 8192),
                "temperature": temperature,
            },
        }
        if system:
            payload["system"] = system
        if format_schema is not None:
            payload["format"] = format_schema

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def stream_generate(
        self,
        prompt: str,
        model: str = "qwen3:4b",
        system: str = "",
        num_ctx: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream generated tokens from Ollama."""
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {"num_ctx": min(max(num_ctx, 1024), 8192)},
        }
        if system:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        yield token

    def validate_json_response(self, raw_text: str, schema_cls: type[T]) -> T:
        """Extract JSON object and validate strictly against a Pydantic schema class."""
        data = extract_json_object(raw_text)
        return schema_cls.model_validate(data)

    async def generate_structured_with_repair(
        self,
        prompt: str,
        schema_cls: type[T],
        model: str = "qwen3:4b",
        system: str = "",
        num_ctx: int = 4096,
    ) -> T:
        """
        Generate structured JSON matching schema_cls.
        If the first response fails JSON parsing or Pydantic validation, automatically
        repair/retry once with explicit validation feedback.
        """
        json_schema = schema_cls.model_json_schema()
        raw_resp = await self.generate(
            prompt=prompt,
            model=model,
            system=system,
            num_ctx=num_ctx,
            format_schema=json_schema,
        )
        raw_text = str(raw_resp.get("response", ""))

        try:
            return self.validate_json_response(raw_text, schema_cls)
        except (ValueError, ValidationError) as first_err:
            logger.warning("Initial LLM JSON validation failed (%s); executing 1-shot repair...", first_err)
            repair_prompt = (
                f"Your previous output failed JSON validation with error:\n{first_err}\n\n"
                f"Previous output:\n{raw_text[:1500]}\n\n"
                f"Return ONLY a valid JSON object matching this exact schema:\n"
                f"{json.dumps(json_schema, indent=2)}"
            )
            repair_resp = await self.generate(
                prompt=repair_prompt,
                model=model,
                system="You are a strict JSON repair engine. Output valid JSON only.",
                num_ctx=num_ctx,
                temperature=0.1,
                format_schema=json_schema,
            )
            return self.validate_json_response(str(repair_resp.get("response", "")), schema_cls)

    async def benchmark_model(self, model: str = "qwen3:4b") -> ModelBenchmarkResult:
        """
        Execute a real ~80-token benchmark on the selected Ollama model, measuring
        TTFT, tok/s, RAM delta, and VRAM usage.
        """
        ram_before = psutil.virtual_memory().used / (1024 * 1024)
        t0 = time.perf_counter()
        try:
            payload = {
                "model": model,
                "prompt": (
                    "Write a concise 60-word factual summary explaining how local AI models "
                    "run directly on a laptop GPU without cloud subscriptions. /no_think"
                ),
                "stream": False,
                "options": {"num_predict": 80, "num_ctx": 2048, "temperature": 0.2},
            }
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(f"{self.base_url}/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()

            total_dur = time.perf_counter() - t0
            eval_count = int(data.get("eval_count") or 60)
            eval_dur_ns = float(data.get("eval_duration") or (total_dur * 1e9))
            prompt_eval_ns = float(data.get("prompt_eval_duration") or 2e8)

            tok_per_s = round(eval_count / max(eval_dur_ns / 1e9, 0.05), 2)
            ttft_s = round(prompt_eval_ns / 1e9, 3)

            ram_after = psutil.virtual_memory().used / (1024 * 1024)
            gpu_after = query_nvidia_gpu()
            vram_used = float(
                max(0, int(gpu_after.get("vram_total_mb", 0)) - int(gpu_after.get("vram_free_mb", 0)))
            )

            tier, recommendation = classify_benchmark_speed(tok_per_s)
            return ModelBenchmarkResult(
                model=model,
                reachable=True,
                tokens_generated=eval_count,
                ttft_seconds=ttft_s,
                tokens_per_second=tok_per_s,
                total_duration_seconds=round(total_dur, 2),
                ram_delta_mb=round(max(0.0, ram_after - ram_before), 1),
                vram_used_mb=vram_used,
                tier=tier,
                recommendation=recommendation,
            )
        except Exception as exc:
            logger.warning("Benchmark failed for model '%s': %s", model, exc)
            return ModelBenchmarkResult(
                model=model,
                reachable=False,
                tier=BenchmarkTier.SLOW,
                recommendation=f"Ollama benchmark failed: {exc}",
            )

    async def restart_ollama_service(self) -> bool:
        """Attempt to start or restart the local Ollama background process safely."""
        ollama_bin = resolve_binary("ollama")
        if not ollama_bin:
            return False
        try:
            creationflags = (
                subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            )
            subprocess.Popen(
                [ollama_bin, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
            )
            for _ in range(10):
                await asyncio.sleep(1.0)
                status = await self.health()
                if status.get("online"):
                    return True
        except Exception as exc:
            logger.error("Failed to restart Ollama service: %s", exc)
        return False
