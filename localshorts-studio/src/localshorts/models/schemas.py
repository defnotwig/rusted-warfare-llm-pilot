"""Pydantic schemas for structured LLM outputs, pipeline artifacts, and API payloads."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class HardwareProfileType(str, Enum):
    LIGHTWEIGHT = "LIGHTWEIGHT"
    BALANCED = "BALANCED"
    PERFORMANCE = "PERFORMANCE"


class ThermalState(str, Enum):
    NORMAL = "NORMAL"      # < 80C
    WARM = "WARM"          # 80-85C (cooldown delay)
    HOT = "HOT"            # > 85C (pause GPU queue)


class BenchmarkTier(str, Enum):
    EXCELLENT = "EXCELLENT"  # > 15 tok/s
    GOOD = "GOOD"            # 8 - 15 tok/s
    USABLE = "USABLE"        # 4 - 8 tok/s
    SLOW = "SLOW"            # < 4 tok/s


class AutomationMode(str, Enum):
    ASSISTED = "ASSISTED"
    SEMI_AUTO = "SEMI_AUTO"
    AUTO_FACTORY = "AUTO_FACTORY"


class VisualPreset(str, Enum):
    CREATOR_NEWS = "Creator News"
    TECH_EXPLAINER = "Tech Explainer"
    BREAKING_INTERNET = "Breaking Internet"
    CHALLENGE = "Challenge"
    MINIMAL_DOCUMENTARY = "Minimal Documentary"
    HIGH_ENERGY = "High Energy"


class CaptionPreset(str, Enum):
    CLEAN = "Clean"
    BOLD = "Bold"
    MINIMAL = "Minimal"
    HIGH_ENERGY = "High Energy"


class JobState(str, Enum):
    DISCOVERED = "DISCOVERED"
    RESEARCHING = "RESEARCHING"
    RESEARCHED = "RESEARCHED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SCRIPTING = "SCRIPTING"
    SCRIPT_READY = "SCRIPT_READY"
    TTS = "TTS"
    CAPTIONS = "CAPTIONS"
    MEDIA = "MEDIA"
    SCENE_PLANNING = "SCENE_PLANNING"
    RENDERING = "RENDERING"
    TECHNICAL_QC = "TECHNICAL_QC"
    QUALITY_CHECK = "QUALITY_CHECK"
    READY = "READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


STAGE_PROGRESS_MAP: dict[JobState, tuple[int, str]] = {
    JobState.DISCOVERED: (5, "Story discovered"),
    JobState.RESEARCHING: (10, "Researching sources"),
    JobState.RESEARCHED: (16, "Evidence bundle verified"),
    JobState.NEEDS_REVIEW: (18, "Awaiting source/editorial review"),
    JobState.SCRIPTING: (22, "Writing script"),
    JobState.SCRIPT_READY: (28, "Script verified & originality checked"),
    JobState.TTS: (34, "Generating narration"),
    JobState.CAPTIONS: (46, "Preparing captions"),
    JobState.MEDIA: (56, "Preparing visuals"),
    JobState.SCENE_PLANNING: (64, "Planning scene timeline"),
    JobState.RENDERING: (76, "Rendering"),
    JobState.TECHNICAL_QC: (88, "Running technical video QC"),
    JobState.QUALITY_CHECK: (94, "Quality checks"),
    JobState.READY: (100, "Ready"),
    JobState.FAILED: (0, "Failed"),
    JobState.CANCELLED: (0, "Cancelled"),
}


class ScriptGenerationSchema(BaseModel):
    """Structured JSON schema required from the local Ollama model."""

    event_summary: str = Field(..., min_length=10, description="Factual summary of the story")
    hook: str = Field(..., min_length=8, description="Immediate opening hook sentence")
    script: str = Field(..., min_length=40, description="55-80 spoken words narration script")
    visual_search_terms: list[str] = Field(
        default_factory=list,
        description="3-6 search queries for visuals",
    )
    caption_highlights: list[str] = Field(
        default_factory=list,
        description="Key numbers, names, or payoff words to emphasize in captions",
    )
    title_options: list[str] = Field(
        default_factory=list,
        min_length=1,
        description="3 non-deceptive YouTube Shorts title options",
    )
    description: str = Field(default="", description="Short YouTube description with context")
    comment_question: str = Field(
        default="",
        description="Natural discussion question for the comment section",
    )
    source_claims: list[str] = Field(
        default_factory=list,
        description="Specific factual claims grounded in the provided sources",
    )

    @field_validator("visual_search_terms", "caption_highlights", "title_options", "source_claims", mode="before")
    @classmethod
    def ensure_string_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        return []


class SourceEvidenceItem(BaseModel):
    provider: str
    url: str
    headline: str
    published_at: str = ""
    snippet: str = ""
    domain: str = ""
    authority_tier: str = "standard"


class EvidenceBundleSchema(BaseModel):
    story_id: str
    headline: str
    topic: str = ""
    creator: str = ""
    sources: list[SourceEvidenceItem] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)
    review_status: str = "VERIFIED"  # VERIFIED or NEEDS_REVIEW


class NormalizedTrendSchema(BaseModel):
    id: str
    provider: str
    topic: str
    headline: str
    url: str
    published_at: str
    creator: str = ""
    signals: dict[str, Any] = Field(default_factory=dict)
    trend_score: int = Field(default=0, ge=0, le=100)
    source_confidence: int = Field(default=0, ge=0, le=100)
    outlier_multiplier: float = Field(default=1.0, ge=0.0)
    reasons: list[str] = Field(default_factory=list)
    sources_count: int = Field(default=1, ge=1)


class ScenePlanItemSchema(BaseModel):
    scene_index: int
    start: float
    end: float
    duration: float
    purpose: str  # hook, context, evidence, escalation, stat_callout, payoff, question
    spoken_text: str
    visual_type: str  # headline_card, stat_callout, evidence_card, timeline_card, media_composite, payoff_card
    visual_query: str = ""
    caption_emphasis: list[str] = Field(default_factory=list)
    motion: str = "slow_drift"  # punch_zoom_in, punch_zoom_mid, punch_zoom_strong, slow_pan_up, slow_drift, static
    zoom_start: float = 1.0
    zoom_end: float = 1.03
    sfx: str = ""  # impact, whoosh, pop, click, riser, or empty
    media_asset_path: str = ""
    media_attribution: str = ""
    rendered_plate_path: str = ""


class CaptionCardSchema(BaseModel):
    index: int
    start: float
    end: float
    text: str
    words: list[str] = Field(default_factory=list)
    emphasized_words: list[str] = Field(default_factory=list)


class TechnicalQCReport(BaseModel):
    passed: bool
    file_exists: bool = False
    file_size_bytes: int = 0
    playable: bool = False
    has_h264_video: bool = False
    has_aac_audio: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration_s: float = 0.0
    audio_sample_rate: int = 0
    has_voice_energy: bool = False
    non_blank_video: bool = False
    captions_completed: bool = False
    errors: list[str] = Field(default_factory=list)


class ContentQualityBreakdown(BaseModel):
    hook: int = Field(default=0, ge=0, le=20)
    clarity: int = Field(default=0, ge=0, le=15)
    pacing: int = Field(default=0, ge=0, le=15)
    originality: int = Field(default=0, ge=0, le=15)
    source_confidence: int = Field(default=0, ge=0, le=15)
    caption_quality: int = Field(default=0, ge=0, le=10)
    visual_diversity: int = Field(default=0, ge=0, le=10)
    total: int = Field(default=0, ge=0, le=100)
    passed_threshold: bool = False
    threshold: int = 75
    notes: list[str] = Field(default_factory=list)


class ModelBenchmarkResult(BaseModel):
    model: str
    reachable: bool
    tokens_generated: int = 0
    ttft_seconds: float = 0.0
    tokens_per_second: float = 0.0
    total_duration_seconds: float = 0.0
    ram_delta_mb: float = 0.0
    vram_used_mb: float = 0.0
    tier: BenchmarkTier = BenchmarkTier.GOOD
    recommendation: str = ""


class HealthResponseSchema(BaseModel):
    status: str
    version: str
    database: bool
    ffmpeg: bool
    ollama: bool
    model: str
    tts: bool
    nvenc: bool
    queue: str
    profile: str = "BALANCED"
    gpu_temp_c: int | None = None
    thermal_state: str = "NORMAL"
    on_battery: bool = False
    free_disk_gb: float = 0.0
