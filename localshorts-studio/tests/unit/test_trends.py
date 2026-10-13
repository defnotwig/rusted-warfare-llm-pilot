"""Unit tests for Trend normalization, RapidFuzz clustering, and missing-signal scoring."""

from __future__ import annotations

from datetime import datetime, timezone

from localshorts.services.trends import (
    RawFeedEntry,
    cluster_and_deduplicate_entries,
    compute_explainable_trend_score,
    strip_html_tags,
)


def test_strip_html_tags_cleans_markup() -> None:
    raw = "<p>Breaking: <b>MrBeast</b> launches <i>100-camera</i> studio!</p>"
    cleaned = strip_html_tags(raw)
    assert cleaned == "Breaking: MrBeast launches 100-camera studio!"


def test_cluster_and_deduplicate_entries_merges_similar_stories() -> None:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = [
        RawFeedEntry(
            provider="google_news",
            topic="MrBeast",
            creator="MrBeast",
            headline="MrBeast launches massive 100 camera studio challenge in North Carolina",
            url="https://reuters.com/story1",
            published_at=now_iso,
            snippet="Full report on the studio build.",
        ),
        RawFeedEntry(
            provider="reddit_rss",
            topic="r/youtube",
            creator="MrBeast",
            headline="MrBeast launches massive 100-camera studio challenge in North Carolina today",
            url="https://bbc.com/story2",
            published_at=now_iso,
            snippet="Second independent report on the studio.",
        ),
        RawFeedEntry(
            provider="hackernews",
            topic="AI & Tech",
            creator="",
            headline="Humanoid Robot Performs Autonomous Backflip in Warehouse Benchmark",
            url="https://news.ycombinator.com/item?id=1",
            published_at=now_iso,
            snippet="Robotics research paper.",
        ),
    ]
    clusters = cluster_and_deduplicate_entries(entries)
    assert len(clusters) == 2
    assert len(clusters[0]) == 2


def test_compute_explainable_trend_score_redistributes_missing_signals() -> None:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cluster = [
        RawFeedEntry(
            provider="google_news",
            topic="IShowSpeed",
            creator="IShowSpeed",
            headline="IShowSpeed Breaks Live Concurrent Streaming Milestone in Europe",
            url="https://theverge.com/speed-stream",
            published_at=now_iso,
            snippet="Over 500,000 concurrent viewers watched the live broadcast.",
            signals={},  # No view/upvote numbers -> weight redistributed automatically
        ),
        RawFeedEntry(
            provider="youtube_rss",
            topic="IShowSpeed",
            creator="IShowSpeed",
            headline="IShowSpeed Breaks Live Concurrent Streaming Milestone in Europe",
            url="https://youtube.com/watch?v=123",
            published_at=now_iso,
            snippet="Official stream archive.",
            signals={"views": 240000},
        ),
    ]
    score, outlier_mult, reasons = compute_explainable_trend_score(
        cluster=cluster,
        watched_names=["IShowSpeed", "MrBeast"],
        recent_headlines=["Old unrelated story from last week"],
        creator_baseline_views_1h=80000.0,
    )
    assert 60 <= score <= 100
    assert outlier_mult >= 1.5
    assert len(reasons) >= 2
