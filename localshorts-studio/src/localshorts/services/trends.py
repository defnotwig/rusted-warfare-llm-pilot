"""Zero-API multi-provider trend discovery, duplicate clustering, CreatorBaseline velocity, and explainable 0-100 Trend Scoring."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Sequence
from urllib.parse import quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from sqlmodel import select

from localshorts.database import get_session
from localshorts.logging_config import get_logger
from localshorts.models.db_models import (
    CreatorBaseline,
    ScriptRecord,
    TrendItem,
    WatchedTopic,
    utc_now_iso,
)
from localshorts.models.schemas import NormalizedTrendSchema
from localshorts.security import validate_external_url
from localshorts.services.originality import tokenize_words
from localshorts.services.research import build_evidence_bundle, extract_domain

logger = get_logger("trends")


@dataclass
class RawFeedEntry:
    provider: str
    topic: str
    creator: str
    headline: str
    url: str
    published_at: str
    snippet: str = ""
    signals: dict[str, Any] = field(default_factory=dict)


def parse_timestamp_utc(raw_ts: str) -> datetime:
    """Parse RFC-2822 or ISO-8601 timestamp into a timezone-aware UTC datetime."""
    if not raw_ts:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(raw_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def strip_html_tags(raw_html: str) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


class TrendDiscoveryService:
    """Fetches public RSS/JSON feeds with per-provider fault isolation, deduplication, and explainable scoring."""

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LocalShortsStudio/0.1.0"

    async def fetch_google_trends_rss(self) -> list[RawFeedEntry]:
        url = "https://trends.google.com/trending/rss?geo=US"
        entries: list[RawFeedEntry] = []
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": self.USER_AGENT}, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
                feed = feedparser.parse(resp.text)
                for item in feed.entries[:12]:
                    title = str(item.get("title", "")).strip()
                    link = str(item.get("link", "")).strip()
                    pub = parse_timestamp_utc(str(item.get("published", ""))).strftime("%Y-%m-%dT%H:%M:%SZ")
                    traffic_raw = str(item.get("ht_approx_traffic", "1000+")).replace(",", "").replace("+", "")
                    traffic = int(re.sub(r"\D", "", traffic_raw) or 1000)
                    news_title = str(item.get("ht_news_item_title", title))
                    headline = strip_html_tags(news_title) if len(news_title) > len(title) else title
                    entries.append(
                        RawFeedEntry(
                            provider="google_trends",
                            topic=title,
                            creator="",
                            headline=headline,
                            url=link or "https://trends.google.com",
                            published_at=pub,
                            snippet=strip_html_tags(str(item.get("summary", headline))),
                            signals={"approx_traffic": traffic},
                        )
                    )
        except Exception as exc:
            logger.debug("Google Trends RSS unavailable: %s", exc)
        return entries

    async def fetch_google_news_rss(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        entries: list[RawFeedEntry] = []
        active_topics = [w for w in watched if w.enabled][:6]
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": self.USER_AGENT}, follow_redirects=True) as client:
            for topic in active_topics:
                q = quote_plus(f"{topic.search_query or topic.name} when:2d")
                url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    feed = feedparser.parse(resp.text)
                    for item in feed.entries[:4]:
                        raw_title = str(item.get("title", "")).strip()
                        # Strip trailing " - Publisher" from Google News titles
                        clean_title = re.sub(r"\s+-\s+[^-]+$", "", raw_title).strip() or raw_title
                        pub = parse_timestamp_utc(str(item.get("published", ""))).strftime("%Y-%m-%dT%H:%M:%SZ")
                        entries.append(
                            RawFeedEntry(
                                provider="google_news",
                                topic=topic.name,
                                creator=topic.name if topic.category == "creator" else "",
                                headline=clean_title,
                                url=str(item.get("link", "")),
                                published_at=pub,
                                snippet=strip_html_tags(str(item.get("summary", clean_title))),
                                signals={"matched_watch": topic.name},
                            )
                        )
                except Exception as exc:
                    logger.debug("Google News RSS failed for '%s': %s", topic.name, exc)
        return entries

    async def fetch_youtube_channel_rss(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        entries: list[RawFeedEntry] = []
        creators = [w for w in watched if w.enabled and w.youtube_channel_id]
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": self.USER_AGENT}, follow_redirects=True) as client:
            for creator in creators[:6]:
                url = f"https://www.youtube.com/feeds/videos.xml?channel_id={creator.youtube_channel_id}"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    feed = feedparser.parse(resp.text)
                    for item in feed.entries[:3]:
                        title = str(item.get("title", "")).strip()
                        link = str(item.get("link", "")).strip()
                        pub = parse_timestamp_utc(str(item.get("published", ""))).strftime("%Y-%m-%dT%H:%M:%SZ")
                        views = 0
                        media_stats = item.get("media_statistics")
                        if isinstance(media_stats, dict) and media_stats.get("views"):
                            try:
                                views = int(media_stats["views"])
                            except ValueError:
                                views = 0
                        entries.append(
                            RawFeedEntry(
                                provider="youtube_rss",
                                topic=creator.name,
                                creator=creator.name,
                                headline=f"{creator.name}: {title}",
                                url=link,
                                published_at=pub,
                                snippet=strip_html_tags(str(item.get("summary", title)))[:280],
                                signals={"views": views, "direct_creator_upload": True},
                            )
                        )
                except Exception as exc:
                    logger.debug("YouTube channel RSS failed for '%s': %s", creator.name, exc)
        return entries

    async def fetch_reddit_rss(self) -> list[RawFeedEntry]:
        subreddits = ["youtube", "LivestreamFail", "technology"]
        entries: list[RawFeedEntry] = []
        headers = {"User-Agent": "windows:localshorts.studio:v0.1.0 (by /u/localshorts)"}
        async with httpx.AsyncClient(timeout=8.0, headers=headers, follow_redirects=True) as client:
            for sub in subreddits:
                url = f"https://www.reddit.com/r/{sub}/hot/.rss?limit=5"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    feed = feedparser.parse(resp.text)
                    for item in feed.entries[:4]:
                        title = str(item.get("title", "")).strip()
                        if title.lower().startswith("pinned") or len(title) < 15:
                            continue
                        pub = parse_timestamp_utc(str(item.get("updated") or item.get("published", ""))).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        )
                        entries.append(
                            RawFeedEntry(
                                provider="reddit_rss",
                                topic=f"r/{sub}",
                                creator="",
                                headline=title,
                                url=str(item.get("link", "")),
                                published_at=pub,
                                snippet=strip_html_tags(str(item.get("summary", title)))[:240],
                                signals={"subreddit": sub},
                            )
                        )
                except Exception as exc:
                    logger.debug("Reddit RSS failed for r/%s: %s", sub, exc)
        return entries

    async def fetch_hackernews_api(self) -> list[RawFeedEntry]:
        url = "https://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=points>40&hitsPerPage=6"
        entries: list[RawFeedEntry] = []
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": self.USER_AGENT}) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return []
                hits = resp.json().get("hits", [])
                for hit in hits:
                    title = str(hit.get("title", "")).strip()
                    story_url = str(hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}")
                    created_at = str(hit.get("created_at") or utc_now_iso())
                    points = int(hit.get("points") or 0)
                    comments = int(hit.get("num_comments") or 0)
                    entries.append(
                        RawFeedEntry(
                            provider="hackernews",
                            topic="AI & Tech",
                            creator="",
                            headline=title,
                            url=story_url,
                            published_at=created_at,
                            snippet=f"{title} ({points} points, {comments} comments on Hacker News)",
                            signals={"points": points, "comments": comments},
                        )
                    )
        except Exception as exc:
            logger.debug("HackerNews API unavailable: %s", exc)
        return entries

    async def fetch_custom_rss(self, urls: Sequence[str]) -> list[RawFeedEntry]:
        entries: list[RawFeedEntry] = []
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": self.USER_AGENT}, follow_redirects=True) as client:
            for raw_url in urls:
                try:
                    valid_url = validate_external_url(raw_url)
                    resp = await client.get(valid_url)
                    if resp.status_code != 200:
                        continue
                    feed = feedparser.parse(resp.text)
                    for item in feed.entries[:5]:
                        title = str(item.get("title", "")).strip()
                        pub = parse_timestamp_utc(str(item.get("published", ""))).strftime("%Y-%m-%dT%H:%M:%SZ")
                        entries.append(
                            RawFeedEntry(
                                provider="custom_rss",
                                topic="Custom Feed",
                                creator="",
                                headline=title,
                                url=str(item.get("link", valid_url)),
                                published_at=pub,
                                snippet=strip_html_tags(str(item.get("summary", title)))[:240],
                                signals={},
                            )
                        )
                except Exception as exc:
                    logger.warning("Custom RSS failed for %s: %s", raw_url, exc)
        return entries


def cluster_and_deduplicate_entries(entries: Sequence[RawFeedEntry]) -> list[list[RawFeedEntry]]:
    """
    Cluster duplicate stories appearing across multiple sources using token Jaccard
    similarity (>= 0.50) or RapidFuzz token_set_ratio (>= 76).
    """
    clusters: list[list[RawFeedEntry]] = []
    for entry in entries:
        if not entry.headline.strip():
            continue
        matched_cluster: list[RawFeedEntry] | None = None
        entry_tokens = set(tokenize_words(entry.headline, remove_stopwords=True))

        for cluster in clusters:
            rep = cluster[0]
            rep_tokens = set(tokenize_words(rep.headline, remove_stopwords=True))
            union_len = len(entry_tokens | rep_tokens)
            jaccard = (len(entry_tokens & rep_tokens) / union_len) if union_len > 0 else 0.0
            fuzzy = fuzz.token_set_ratio(entry.headline.lower(), rep.headline.lower())
            if jaccard >= 0.50 or fuzzy >= 76 or (entry.url and entry.url == rep.url):
                matched_cluster = cluster
                break

        if matched_cluster is not None:
            matched_cluster.append(entry)
        else:
            clusters.append([entry])

    return clusters


def compute_explainable_trend_score(
    cluster: Sequence[RawFeedEntry],
    watched_names: Sequence[str],
    recent_headlines: Sequence[str],
    creator_baseline_views_1h: float | None = None,
) -> tuple[int, float, list[str]]:
    """
    Compute explainable 0-100 Trend Score with dynamic missing-signal weight redistribution:
    - Freshness              20%
    - Cross-source mentions  20%
    - Creator/topic match    20%
    - Velocity / outlier     20%
    - Novelty                10%
    - Engagement signal      10%
    Returns (trend_score_0_100, outlier_multiplier, human_reasons).
    """
    primary = cluster[0]
    reasons: list[str] = []
    weighted_components: list[tuple[float, float]] = []  # (weight, score_0_100)

    # 1. Freshness (weight 0.20)
    now = datetime.now(timezone.utc)
    pub_dt = min(parse_timestamp_utc(e.published_at) for e in cluster)
    age_minutes = max(1.0, (now - pub_dt).total_seconds() / 60.0)
    age_hours = age_minutes / 60.0
    freshness_score = 100.0 * math.pow(2.0, -age_hours / 18.0)
    weighted_components.append((0.20, freshness_score))
    if age_minutes < 90:
        reasons.append(f"Published {int(round(age_minutes))} minutes ago")
    elif age_hours < 24:
        reasons.append(f"Published {int(round(age_hours))} hours ago")

    # 2. Cross-Source Mentions (weight 0.20)
    unique_providers_or_domains = len({(e.provider, extract_domain(e.url)) for e in cluster})
    if unique_providers_or_domains >= 4:
        cross_score = 100.0
    elif unique_providers_or_domains == 3:
        cross_score = 88.0
    elif unique_providers_or_domains == 2:
        cross_score = 72.0
    else:
        cross_score = 48.0
    weighted_components.append((0.20, cross_score))
    if unique_providers_or_domains >= 2:
        reasons.append(f"Confirmed across {unique_providers_or_domains} sources")

    # 3. Creator / Topic Relevance (weight 0.20)
    combined_text = " ".join(f"{e.headline} {e.topic} {e.creator}" for e in cluster).lower()
    matched_watch = next((w for w in watched_names if w.lower() in combined_text), "")
    if matched_watch:
        rel_score = 95.0
        reasons.append(f"Matches watched topic: {matched_watch}")
    else:
        rel_score = 65.0
    weighted_components.append((0.20, rel_score))

    # 4. Velocity / Outlier Signal (weight 0.20 — only included when measurable data exists)
    outlier_mult = 1.0
    views = max((int(e.signals.get("views") or 0) for e in cluster), default=0)
    if views > 0 and creator_baseline_views_1h and creator_baseline_views_1h > 0:
        views_per_hour = views / max(0.5, age_hours)
        outlier_mult = round(views_per_hour / creator_baseline_views_1h, 2)
        vel_score = min(100.0, 50.0 + (outlier_mult - 1.0) * 25.0)
        weighted_components.append((0.20, max(25.0, vel_score)))
        if outlier_mult >= 1.4:
            reasons.append(f"Outperforming creator baseline ({outlier_mult:.2f}x normal velocity)")
    elif unique_providers_or_domains >= 2 and age_hours <= 12:
        outlier_mult = round(1.0 + (unique_providers_or_domains * 0.45) / max(1.0, age_hours / 3.0), 2)
        vel_score = min(100.0, 55.0 + outlier_mult * 15.0)
        weighted_components.append((0.20, vel_score))
        reasons.append(f"Rapid multi-feed pickup ({outlier_mult:.1f}x velocity)")

    # 5. Headline Novelty (weight 0.10)
    max_recent_sim = 0.0
    if recent_headlines:
        max_recent_sim = max(
            fuzz.token_sort_ratio(primary.headline.lower(), rh.lower()) / 100.0
            for rh in recent_headlines[:50]
        )
    novelty_score = max(10.0, (1.0 - max_recent_sim) * 100.0)
    weighted_components.append((0.10, novelty_score))
    if novelty_score >= 75:
        reasons.append("Strong novelty compared with recent scripts")

    # 6. Engagement Signal (weight 0.10 — only included when authentic signal exists)
    points = max((int(e.signals.get("points") or 0) for e in cluster), default=0)
    traffic = max((int(e.signals.get("approx_traffic") or 0) for e in cluster), default=0)
    if views > 0:
        eng_score = min(100.0, math.log10(max(10, views)) * 18.0)
        weighted_components.append((0.10, eng_score))
        reasons.append(f"{views:,} public views recorded")
    elif points > 0:
        eng_score = min(100.0, points / 3.0)
        weighted_components.append((0.10, eng_score))
        reasons.append(f"{points} community upvotes")
    elif traffic > 0:
        eng_score = min(100.0, math.log10(max(100, traffic)) * 20.0)
        weighted_components.append((0.10, eng_score))
        reasons.append(f"High search volume ({traffic:,}+ searches)")

    # Dynamic weight redistribution across available signals
    total_w = sum(w for w, _s in weighted_components)
    final_score = int(round(sum(w * s for w, s in weighted_components) / max(0.01, total_w)))
    final_score = max(1, min(100, final_score))

    return final_score, outlier_mult, reasons


async def discover_and_rank_trends(
    custom_rss_urls: Sequence[str] | None = None,
    confidence_threshold: int = 70,
) -> list[NormalizedTrendSchema]:
    """
    Execute all trend providers concurrently (each fault-isolated), cluster duplicates,
    compute Trend Score + Source Confidence + Outlier Multiplier, and persist to SQLite.
    """
    with get_session() as session:
        watched = list(session.exec(select(WatchedTopic).where(WatchedTopic.enabled == True)).all())  # noqa: E712
        recent_scripts = list(session.exec(select(ScriptRecord).order_by(ScriptRecord.id.desc()).limit(40)).all())
        baselines = {
            b.creator_name.lower(): b.typical_views_1h
            for b in session.exec(select(CreatorBaseline)).all()
        }

    watched_names = [w.name for w in watched]
    recent_headlines = [r.event_summary or r.hook for r in recent_scripts]

    svc = TrendDiscoveryService()
    results = await asyncio.gather(
        svc.fetch_google_trends_rss(),
        svc.fetch_google_news_rss(watched),
        svc.fetch_youtube_channel_rss(watched),
        svc.fetch_reddit_rss(),
        svc.fetch_hackernews_api(),
        svc.fetch_custom_rss(custom_rss_urls or []),
        return_exceptions=True,
    )

    raw_entries: list[RawFeedEntry] = []
    for batch in results:
        if isinstance(batch, list):
            raw_entries.extend(batch)

    # If completely offline and no live entries were fetched, return existing cached DB items
    if not raw_entries:
        with get_session() as session:
            cached_items = list(
                session.exec(select(TrendItem).order_by(TrendItem.trend_score.desc()).limit(30)).all()
            )
            return [
                NormalizedTrendSchema(
                    id=ci.id,
                    provider=ci.provider,
                    topic=ci.topic,
                    headline=ci.headline,
                    url=ci.url,
                    published_at=ci.published_at,
                    creator=ci.creator,
                    signals=json.loads(ci.signals_json or "{}"),
                    trend_score=ci.trend_score,
                    source_confidence=ci.source_confidence,
                    outlier_multiplier=ci.outlier_multiplier,
                    reasons=json.loads(ci.reasons_json or "[]"),
                    sources_count=ci.sources_count,
                )
                for ci in cached_items
            ]

    clusters = cluster_and_deduplicate_entries(raw_entries)
    normalized_list: list[NormalizedTrendSchema] = []

    with get_session() as session:
        for cluster in clusters:
            primary = cluster[0]
            cluster_hash = hashlib.sha1(
                " ".join(tokenize_words(primary.headline, remove_stopwords=True)[:8]).encode("utf-8")
            ).hexdigest()[:12]
            trend_id = f"tr_{cluster_hash}"

            creator_name = next((e.creator for e in cluster if e.creator), "")
            base_v1h = baselines.get(creator_name.lower()) if creator_name else None

            t_score, outlier_mult, reasons = compute_explainable_trend_score(
                cluster=cluster,
                watched_names=watched_names,
                recent_headlines=recent_headlines,
                creator_baseline_views_1h=base_v1h,
            )

            raw_sources = [
                {
                    "provider": e.provider,
                    "url": e.url,
                    "headline": e.headline,
                    "published_at": e.published_at,
                    "snippet": e.snippet,
                }
                for e in cluster
            ]
            evidence_bundle = build_evidence_bundle(
                topic=primary.topic or creator_name or "Trending Story",
                headline=primary.headline,
                creator=creator_name,
                raw_sources=raw_sources,
                confidence_threshold=confidence_threshold,
            )

            merged_signals: dict[str, Any] = {}
            for e in cluster:
                merged_signals.update(e.signals)

            existing = session.get(TrendItem, trend_id)
            if existing:
                existing.trend_score = t_score
                existing.source_confidence = evidence_bundle.confidence
                existing.outlier_multiplier = outlier_mult
                existing.reasons_json = json.dumps(reasons)
                existing.evidence_bundle_json = evidence_bundle.model_dump_json()
                existing.sources_count = len(cluster)
                session.add(existing)
            else:
                session.add(
                    TrendItem(
                        id=trend_id,
                        cluster_hash=cluster_hash,
                        provider=primary.provider,
                        topic=primary.topic,
                        creator=creator_name,
                        headline=primary.headline,
                        url=primary.url,
                        published_at=primary.published_at,
                        signals_json=json.dumps(merged_signals),
                        trend_score=t_score,
                        source_confidence=evidence_bundle.confidence,
                        outlier_multiplier=outlier_mult,
                        reasons_json=json.dumps(reasons),
                        evidence_bundle_json=evidence_bundle.model_dump_json(),
                        sources_count=len(cluster),
                        status="NEW",
                    )
                )

            normalized_list.append(
                NormalizedTrendSchema(
                    id=trend_id,
                    provider=primary.provider,
                    topic=primary.topic,
                    headline=primary.headline,
                    url=primary.url,
                    published_at=primary.published_at,
                    creator=creator_name,
                    signals=merged_signals,
                    trend_score=t_score,
                    source_confidence=evidence_bundle.confidence,
                    outlier_multiplier=outlier_mult,
                    reasons=reasons,
                    sources_count=len(cluster),
                )
            )
        session.commit()

    normalized_list.sort(key=lambda x: (x.trend_score, x.source_confidence), reverse=True)
    return normalized_list
