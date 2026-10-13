"""MediaProvider base interface and implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from localshorts.services.media import MediaService


class MediaProvider(ABC):
    """Pluggable MediaProvider interface."""

    name: str = "base"

    @abstractmethod
    async def resolve_asset(self, query: str) -> dict[str, Any] | None:
        raise NotImplementedError


class AuthorizedLocalMediaProvider(MediaProvider):
    name = "authorized_local"

    async def resolve_asset(self, query: str) -> dict[str, Any] | None:
        match = MediaService().find_authorized_local_media(query)
        if not match:
            return None
        return {
            "local_path": match[0],
            "attribution": match[1],
            "license": "User Authorized",
            "provider": self.name,
        }


class WikimediaCommonsMediaProvider(MediaProvider):
    name = "wikimedia"

    async def resolve_asset(self, query: str) -> dict[str, Any] | None:
        return await MediaService().fetch_wikimedia_image(query)
