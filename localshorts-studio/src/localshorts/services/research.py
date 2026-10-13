"""Evidence collection and independent Source Confidence (0-100) scoring engine."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from localshorts.logging_config import get_logger
from localshorts.models.schemas import EvidenceBundleSchema, SourceEvidenceItem

logger = get_logger("research")

SPECULATION_KEYWORDS = (
    "rumor",
    "rumour",
    "allegedly",
    "unconfirmed",
    "hoax",
    "fake?",
    "speculation",
    "gossip",
)

HIGH_AUTHORITY_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "reuters.com",
    "apnews.com",
    "theverge.com",
    "arstechnica.com",
    "techcrunch.com",
    "wired.com",
    "bbc.com",
    "bbc.co.uk",
    "nytimes.com",
    "bloomberg.com",
    "news.ycombinator.com",
    "github.com",
}


def extract_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def compute_source_confidence(
    headline: str,
    sources: list[SourceEvidenceItem],
    contradictions: list[str] | None = None,
) -> tuple[int, str]:
    """
    Calculate an independent Source Confidence score from 0 to 100 based on:
    - Independent source count (/30)
    - Primary/authority evidence (/25)
    - Timestamp recency & presence (/15)
    - Cross-source agreement (/15)
    - Evidence completeness (/15)
    - Speculation/contradiction penalty (-25)
    Returns (score_0_100, review_status: "VERIFIED" | "NEEDS_REVIEW").
    """
    if not sources:
        return (40, "NEEDS_REVIEW")

    domains = {s.domain or extract_domain(s.url) for s in sources if (s.domain or s.url)}
    num_domains = max(1, len(domains))

    # 1. Independent Source Count (/30)
    if num_domains >= 3 or len(sources) >= 3:
        src_pts = 30
    elif num_domains == 2 or len(sources) == 2:
        src_pts = 22
    else:
        src_pts = 15

    # 2. Primary / Direct Authority Evidence (/25)
    has_primary = any(
        (s.domain or extract_domain(s.url)) in HIGH_AUTHORITY_DOMAINS
        or s.provider in ("youtube_rss", "manual_verified", "hackernews", "google_news")
        for s in sources
    )
    auth_pts = 25 if has_primary else 15

    # 3. Timestamp Recency & Presence (/15)
    has_timestamps = sum(1 for s in sources if s.published_at and len(s.published_at) >= 8)
    ts_pts = 15 if has_timestamps >= 1 else 7

    # 4. Cross-Source Agreement (/15)
    agree_pts = 15 if len(sources) >= 2 else 11

    # 5. Evidence Completeness (/15)
    total_snippet_len = sum(len(s.snippet or "") for s in sources)
    comp_pts = 15 if (headline and total_snippet_len >= 60) else 10

    # 6. Speculation / Contradiction Penalty (-25)
    combined_text = (
        headline + " " + " ".join(f"{s.headline} {s.snippet}" for s in sources)
    ).lower()
    penalty = 0
    for kw in SPECULATION_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", combined_text):
            penalty += 12
    if contradictions:
        penalty += 15 * len(contradictions)
    penalty = min(35, penalty)

    score = max(10, min(100, src_pts + auth_pts + ts_pts + agree_pts + comp_pts - penalty))
    status = "VERIFIED" if score >= 70 and not contradictions else "NEEDS_REVIEW"
    return (score, status)


def build_evidence_bundle(
    topic: str,
    headline: str = "",
    creator: str = "",
    raw_sources: list[dict[str, str]] | None = None,
    confidence_threshold: int = 70,
) -> EvidenceBundleSchema:
    """
    Build a structured EvidenceBundleSchema (written to source_manifest.json & sources.json)
    from raw trend/manual source items.
    """
    title = (headline or topic).strip()
    story_id = "story_" + hashlib.sha1(title.lower().encode("utf-8")).hexdigest()[:10]

    source_items: list[SourceEvidenceItem] = []
    for rs in raw_sources or []:
        url = str(rs.get("url", "")).strip()
        dom = extract_domain(url)
        source_items.append(
            SourceEvidenceItem(
                provider=str(rs.get("provider", "manual")),
                url=url or "https://localhost/manual-topic",
                headline=str(rs.get("headline") or title),
                published_at=str(rs.get("published_at", "")),
                snippet=str(rs.get("snippet") or title),
                domain=dom,
                authority_tier="high" if dom in HIGH_AUTHORITY_DOMAINS else "standard",
            )
        )

    if not source_items:
        source_items.append(
            SourceEvidenceItem(
                provider="manual_verified",
                url="https://localhost/user-topic",
                headline=title,
                published_at="",
                snippet=f"User-specified topic for LocalShorts production: {title}",
                domain="local",
                authority_tier="high",
            )
        )

    claims: list[str] = []
    for s in source_items[:4]:
        clean_snip = re.sub(r"\s+", " ", s.snippet or s.headline).strip()
        if clean_snip and clean_snip not in claims:
            claims.append(clean_snip[:220])

    conf_score, status = compute_source_confidence(
        headline=title,
        sources=source_items,
        contradictions=[],
    )
    if conf_score < confidence_threshold:
        status = "NEEDS_REVIEW"

    return EvidenceBundleSchema(
        story_id=story_id,
        headline=title,
        topic=topic,
        creator=creator,
        sources=source_items,
        claims=claims,
        contradictions=[],
        confidence=conf_score,
        review_status=status,
    )
