"""Unit tests for Source Confidence (0-100) scoring and EvidenceBundleSchema."""

from __future__ import annotations

from datetime import datetime, timezone

from localshorts.services.research import build_evidence_bundle, extract_domain


def test_extract_domain() -> None:
    assert extract_domain("https://www.reuters.com/technology/article") == "reuters.com"
    assert extract_domain("https://news.ycombinator.com/item?id=1") == "news.ycombinator.com"


def test_build_evidence_bundle_high_confidence() -> None:
    now = datetime.now(timezone.utc).isoformat()
    sources = [
        {
            "provider": "google_news",
            "headline": "Open Source Robotics Team Demonstrates 100Hz Dexterous Hand",
            "url": "https://www.reuters.com/technology/robotics-hand",
            "published_at": now,
            "snippet": "The robotics research group released open hardware CAD files and 48 hours of stress test telemetry.",
        },
        {
            "provider": "hackernews",
            "headline": "Dexterous Robot Hand Achieves 100Hz Control Loop in Lab Benchmark",
            "url": "https://www.theverge.com/2026/robotics-hand",
            "published_at": now,
            "snippet": "Full benchmark dataset and telemetry verified across 5,000 grasp cycles.",
        },
    ]
    bundle = build_evidence_bundle(
        topic="AI & Robotics",
        headline="Dexterous Robot Hand Achieves 100Hz Control Loop",
        creator="Robotics Lab",
        raw_sources=sources,
        confidence_threshold=70,
    )
    assert bundle.confidence >= 75
    assert bundle.review_status == "VERIFIED"
    assert len(bundle.sources) == 2


def test_build_evidence_bundle_penalizes_speculation_rumors() -> None:
    sources = [
        {
            "provider": "custom",
            "headline": "Unconfirmed rumor and alleged hoax claims secret island purchase",
            "url": "https://unknown-gossip-example.xyz/post",
            "published_at": "",
            "snippet": "Pure rumor and speculation without confirmation.",
        }
    ]
    bundle = build_evidence_bundle(
        topic="Creator Rumor",
        headline="Unconfirmed rumor and alleged hoax claims secret island purchase",
        raw_sources=sources,
        confidence_threshold=70,
    )
    assert bundle.confidence < 70
    assert bundle.review_status == "NEEDS_REVIEW"
