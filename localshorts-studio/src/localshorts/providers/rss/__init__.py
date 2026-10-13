"""TrendProvider base interface and provider exports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from localshorts.models.db_models import WatchedTopic
from localshorts.services.trends import RawFeedEntry, TrendDiscoveryService


class TrendProvider(ABC):
    """Pluggable TrendProvider interface."""

    name: str = "base"

    @abstractmethod
    async def fetch(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        raise NotImplementedError


class GoogleTrendsProvider(TrendProvider):
    name = "google_trends"

    async def fetch(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        return await TrendDiscoveryService().fetch_google_trends_rss()


class GoogleNewsProvider(TrendProvider):
    name = "google_news"

    async def fetch(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        return await TrendDiscoveryService().fetch_google_news_rss(watched)


class YouTubeChannelRSSProvider(TrendProvider):
    name = "youtube_rss"

    async def fetch(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        return await TrendDiscoveryService().fetch_youtube_channel_rss(watched)


class RedditPublicRSSProvider(TrendProvider):
    name = "reddit_rss"

    async def fetch(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        return await TrendDiscoveryService().fetch_reddit_rss()


class HackerNewsTrendProvider(TrendProvider):
    name = "hackernews"

    async def fetch(self, watched: Sequence[WatchedTopic]) -> list[RawFeedEntry]:
        return await TrendDiscoveryService().fetch_hackernews_api()
