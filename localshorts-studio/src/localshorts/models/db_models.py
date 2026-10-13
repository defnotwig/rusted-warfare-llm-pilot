"""SQLModel database tables for LocalShorts Studio."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SchemaMigration(SQLModel, table=True):
    __tablename__ = "schema_migrations"

    version: int = Field(primary_key=True)
    applied_at: str = Field(default_factory=utc_now_iso)
    description: str = Field(default="")


class AppSettings(SQLModel, table=True):
    __tablename__ = "app_settings"

    id: Optional[int] = Field(default=1, primary_key=True)
    channel_name: str = Field(default="Viral Creator & Internet Moments Explained")
    channel_niche: str = Field(
        default="Fast, factual breakdowns of major creator challenges, streaming milestones, and viral tech events."
    )
    automation_mode: str = Field(default="SEMI_AUTO")  # ASSISTED, SEMI_AUTO, AUTO_FACTORY
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")
    ollama_model: str = Field(default="qwen3:4b")
    ollama_num_ctx: int = Field(default=4096)
    unload_model_after_gen: bool = Field(default=True)
    tts_provider: str = Field(default="kokoro")  # kokoro, piper, sapi
    tts_voice: str = Field(default="af_heart")
    tts_speed: float = Field(default=1.05)
    use_whisper_alignment: bool = Field(default=False)
    visual_preset: str = Field(default="Creator News")
    caption_preset: str = Field(default="Bold")
    enable_wikimedia: bool = Field(default=True)
    enable_sfx: bool = Field(default=True)
    enable_background_music: bool = Field(default=False)
    prefer_nvenc: bool = Field(default=True)
    target_duration_s: int = Field(default=24)
    quality_threshold: int = Field(default=75)
    source_confidence_threshold: int = Field(default=70)
    originality_threshold: float = Field(default=0.78)
    cache_limit_gb: float = Field(default=15.0)
    min_free_disk_gb: float = Field(default=10.0)
    daily_candidate_limit: int = Field(default=10)
    schedule_enabled: bool = Field(default=False)
    schedule_hours_json: str = Field(
        default='["08:00","10:00","12:00","14:00","16:00","18:00","20:00","22:00","00:00","02:00"]'
    )
    last_scheduled_date: str = Field(default="")
    battery_override: bool = Field(default=False)
    lan_access_enabled: bool = Field(default=False)
    windows_startup_enabled: bool = Field(default=False)
    first_run_completed: bool = Field(default=False)
    custom_rss_urls_json: str = Field(default="[]")
    authorized_media_dir: str = Field(default="")
    youtube_api_key: str = Field(default="")
    pixabay_api_key: str = Field(default="")
    coverr_api_key: str = Field(default="")
    updated_at: str = Field(default_factory=utc_now_iso)


class HardwareSnapshot(SQLModel, table=True):
    __tablename__ = "hardware_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    detected_at: str = Field(default_factory=utc_now_iso)
    os_version: str = Field(default="")
    cpu_model: str = Field(default="")
    cores_physical: int = Field(default=4)
    cores_logical: int = Field(default=8)
    ram_total_mb: int = Field(default=16384)
    ram_free_mb: int = Field(default=8192)
    gpu_vendor: str = Field(default="")
    gpu_name: str = Field(default="")
    vram_total_mb: int = Field(default=0)
    vram_free_mb: int = Field(default=0)
    nvidia_driver: str = Field(default="")
    gpu_temp_c: Optional[int] = Field(default=None)
    nvenc_available: bool = Field(default=False)
    on_battery: bool = Field(default=False)
    battery_percent: Optional[int] = Field(default=None)
    free_disk_gb: float = Field(default=100.0)
    active_profile: str = Field(default="BALANCED")
    benchmark_tok_per_s: float = Field(default=0.0)
    benchmark_tier: str = Field(default="GOOD")


class WatchedTopic(SQLModel, table=True):
    __tablename__ = "watched_topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    category: str = Field(default="creator")  # creator, event, tech
    youtube_channel_id: str = Field(default="")
    search_query: str = Field(default="")
    enabled: bool = Field(default=True)
    created_at: str = Field(default_factory=utc_now_iso)


class CreatorBaseline(SQLModel, table=True):
    __tablename__ = "creator_baselines"

    creator_name: str = Field(primary_key=True)
    typical_views_1h: float = Field(default=50000.0)
    typical_views_24h: float = Field(default=500000.0)
    typical_mentions_1h: float = Field(default=2.0)
    sample_count: int = Field(default=1)
    updated_at: str = Field(default_factory=utc_now_iso)


class TrendItem(SQLModel, table=True):
    __tablename__ = "trend_items"

    id: str = Field(primary_key=True)
    cluster_hash: str = Field(index=True, default="")
    provider: str = Field(default="google_news")
    topic: str = Field(default="")
    creator: str = Field(default="")
    headline: str = Field(default="")
    url: str = Field(default="")
    published_at: str = Field(default_factory=utc_now_iso)
    signals_json: str = Field(default="{}")
    trend_score: int = Field(default=50, index=True)
    source_confidence: int = Field(default=70)
    outlier_multiplier: float = Field(default=1.0)
    reasons_json: str = Field(default="[]")
    evidence_bundle_json: str = Field(default="{}")
    sources_count: int = Field(default=1)
    status: str = Field(default="NEW", index=True)  # NEW, SAVED, IGNORED, QUEUED
    discovered_at: str = Field(default_factory=utc_now_iso)


class ScriptRecord(SQLModel, table=True):
    __tablename__ = "script_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    story_id: str = Field(default="")
    event_summary: str = Field(default="")
    hook: str = Field(default="")
    script_text: str = Field(default="")
    word_count: int = Field(default=0)
    visual_search_terms_json: str = Field(default="[]")
    caption_highlights_json: str = Field(default="[]")
    title_options_json: str = Field(default="[]")
    description: str = Field(default="")
    comment_question: str = Field(default="")
    source_claims_json: str = Field(default="[]")
    originality_similarity: float = Field(default=0.0)
    matched_script_id: Optional[int] = Field(default=None)
    created_at: str = Field(default_factory=utc_now_iso)


class ShortJob(SQLModel, table=True):
    __tablename__ = "short_jobs"

    id: str = Field(primary_key=True)
    slug: str = Field(default="short-video", index=True)
    topic: str = Field(default="")
    headline: str = Field(default="")
    mode: str = Field(default="SEMI_AUTO")  # ASSISTED, SEMI_AUTO, AUTO_FACTORY
    state: str = Field(default="DISCOVERED", index=True)
    progress_pct: int = Field(default=5)
    stage_message: str = Field(default="Queued")
    priority: int = Field(default=10, index=True)
    trend_item_id: str = Field(default="")
    script_id: Optional[int] = Field(default=None)
    model_used: str = Field(default="qwen3:4b")
    voice_id: str = Field(default="af_heart")
    tts_speed: float = Field(default=1.05)
    visual_preset: str = Field(default="Creator News")
    caption_preset: str = Field(default="Bold")
    target_duration_s: int = Field(default=24)
    duration_actual_s: float = Field(default=0.0)
    file_size_bytes: int = Field(default=0)
    completed_stages_json: str = Field(default="[]")
    failed_stage: str = Field(default="")
    error_summary: str = Field(default="")
    error_trace: str = Field(default="")
    retry_count: int = Field(default=0)
    source_confidence: int = Field(default=80)
    originality_similarity: float = Field(default=0.0)
    technical_qc_passed: bool = Field(default=False)
    technical_qc_json: str = Field(default="{}")
    quality_score_total: int = Field(default=0)
    quality_breakdown_json: str = Field(default="{}")
    encoder_used: str = Field(default="")
    output_dir: str = Field(default="")
    video_path: str = Field(default="")
    thumbnail_path: str = Field(default="")
    ready_video_path: str = Field(default="")
    is_test_video: bool = Field(default=False)
    created_at: str = Field(default_factory=utc_now_iso, index=True)
    updated_at: str = Field(default_factory=utc_now_iso)
    completed_at: str = Field(default="")


class MediaCacheEntry(SQLModel, table=True):
    __tablename__ = "media_cache_entries"

    sha256: str = Field(primary_key=True)
    provider: str = Field(default="wikimedia")
    source_url: str = Field(default="", index=True)
    query_term: str = Field(default="", index=True)
    title: str = Field(default="")
    license_type: str = Field(default="CC0")
    attribution: str = Field(default="")
    local_path: str = Field(default="")
    media_type: str = Field(default="image")  # image or video
    size_bytes: int = Field(default=0)
    created_at: str = Field(default_factory=utc_now_iso)
    last_used_at: str = Field(default_factory=utc_now_iso, index=True)
