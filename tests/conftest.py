"""Fixtures y utilidades compartidas por las pruebas."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from multimedia_search_mcp.config import ProwlarrSettings, Settings
from multimedia_search_mcp.models import (
    CategoryRef,
    EmptyResultAssessment,
    IndexerInfo,
    IndexerWindowSummary,
    MediaType,
    ProviderCapabilities,
    ProviderSearchRequest,
    ProviderSearchResult,
    ProviderStatus,
    ReleaseRecord,
)
from multimedia_search_mcp.providers.base import SearchProvider
from multimedia_search_mcp.providers.prowlarr import ProwlarrClient
from multimedia_search_mcp.providers.registry import ProviderRegistry
from multimedia_search_mcp.services.search_service import MultimediaSearchService

TEST_API_KEY = "test-api-key-0123456789abcdef"
TEST_BASE_URL = "http://prowlarr.test:9696"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def prowlarr_settings() -> ProwlarrSettings:
    return ProwlarrSettings(
        base_url=TEST_BASE_URL,
        api_key=SecretStr(TEST_API_KEY),
        timeout_seconds=5.0,
    )


@pytest.fixture
def settings(prowlarr_settings: ProwlarrSettings) -> Settings:
    return Settings(prowlarr=prowlarr_settings, env_file=None)


def make_client(handler: Handler, settings: ProwlarrSettings) -> ProwlarrClient:
    """Cliente Prowlarr con transporte HTTP simulado (sin red)."""
    return ProwlarrClient(settings, transport=httpx.MockTransport(handler))


def category_tree() -> list[dict[str, Any]]:
    """Árbol de categorías reducido, con la misma forma que el real."""
    return json.loads(json.dumps(_CATEGORY_TREE))


_CATEGORY_TREE: list[dict[str, Any]] = [
    {
        "id": 2000,
        "name": "Movies",
        "subCategories": [
            {"id": 2040, "name": "Movies/HD", "subCategories": []},
            {"id": 2045, "name": "Movies/UHD", "subCategories": []},
        ],
    },
    {
        "id": 3000,
        "name": "Audio",
        "subCategories": [{"id": 3030, "name": "Audio/Audiobook", "subCategories": []}],
    },
    {
        "id": 4000,
        "name": "PC",
        "subCategories": [
            {"id": 4010, "name": "PC/0day", "subCategories": []},
            {"id": 4020, "name": "PC/ISO", "subCategories": []},
        ],
    },
    {
        "id": 5000,
        "name": "TV",
        "subCategories": [
            {"id": 5030, "name": "TV/SD", "subCategories": []},
            {"id": 5040, "name": "TV/HD", "subCategories": []},
            {"id": 5070, "name": "TV/Anime", "subCategories": []},
        ],
    },
    {
        "id": 7000,
        "name": "Books",
        "subCategories": [
            {"id": 7020, "name": "Books/EBook", "subCategories": []},
            {"id": 7030, "name": "Books/Comics", "subCategories": []},
        ],
    },
    {"id": 8000, "name": "Other", "subCategories": []},
]


def indexers_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "name": "Nyaa.si",
            "enable": True,
            "protocol": "torrent",
            "privacy": "public",
            "supportsSearch": True,
            "supportsRss": True,
            "capabilities": {
                "categories": [
                    {"id": 5000, "name": "TV"},
                    {"id": 2000, "name": "Movies"},
                    {"id": 7000, "name": "Books"},
                    {"id": 140679, "name": "Anime"},
                ]
            },
        },
        {
            "id": 2,
            "name": "LinuxTracker",
            "enable": True,
            "protocol": "torrent",
            "privacy": "public",
            "supportsSearch": True,
            "supportsRss": True,
            "capabilities": {"categories": [{"id": 4000, "name": "PC"}]},
        },
        {
            "id": 3,
            "name": "IndexerCaido",
            "enable": False,
            "protocol": "torrent",
            "privacy": "private",
            "supportsSearch": False,
            "supportsRss": False,
            "capabilities": {"categories": [{"id": 5000, "name": "TV"}]},
        },
    ]


def indexer_stats_payload() -> dict[str, Any]:
    return {
        "indexers": [
            {
                "indexerId": 1,
                "indexerName": "Nyaa.si",
                "averageResponseTime": 1774,
                "numberOfQueries": 6,
                "numberOfFailedQueries": 1,
                "numberOfGrabs": 0,
                "numberOfFailedGrabs": 0,
            },
            {
                "indexerId": 2,
                "indexerName": "LinuxTracker",
                "averageResponseTime": 1400,
                "numberOfQueries": 6,
                "numberOfFailedQueries": 0,
                "numberOfGrabs": 2,
                "numberOfFailedGrabs": 0,
            },
        ],
        "userAgents": [],
        "hosts": [],
    }


def prowlarr_release(**overrides: Any) -> dict[str, Any]:
    """Resultado con la forma exacta que devuelve Prowlarr 3.x."""
    release: dict[str, Any] = {
        "guid": "magnet:?xt=urn:btih:D12CF502B1FE28D78E8123AA03A531A7E841C1A0&dn=debian.iso",
        "age": 24,
        "size": 4402341478,
        "grabs": 29,
        "indexerId": 2,
        "indexer": "LinuxTracker",
        "title": "debian-13.0-amd64.iso",
        "sortTitle": "debian 13 0 amd64 iso",
        "imdbId": 0,
        "tmdbId": 0,
        "tvdbId": 0,
        "tvMazeId": 0,
        "publishDate": "2026-10-25T07:00:00Z",
        "infoUrl": "https://linuxtracker.org/torrents/d12cf502b1fe28d78e8123aa03a531a7e841c1a0/",
        "indexerFlags": ["freeleech"],
        "categories": [
            {"id": 4020, "name": "PC/ISO", "subCategories": []},
            {"id": 102153, "subCategories": []},
        ],
        "magnetUrl": "http://127.0.0.1:9696/2/download?apikey=REAL_KEY_HERE&link=abc",
        "protocol": "torrent",
        "fileName": "debian-13.0-amd64.iso.torrent",
        "seeders": 6,
        "leechers": 0,
        "infoHash": "D12CF502B1FE28D78E8123AA03A531A7E841C1A0",
    }
    release.update(overrides)
    return release


def record(**overrides: Any) -> ReleaseRecord:
    data: dict[str, Any] = {
        "title": "Dune Part Two 2024 2160p",
        "guid": "guid-1",
        "indexer": "TestIndexer",
        "indexer_id": 1,
        "size_bytes": 1024,
        "seeders": 5,
        "leechers": 1,
        "categories": [CategoryRef(id=2045, name="Movies/UHD")],
    }
    data.update(overrides)
    return ReleaseRecord(**data)


class FakeProvider(SearchProvider):
    """Proveedor de prueba: sin red, comportamiento controlado."""

    name = "fake"
    capabilities_summary = "Proveedor falso para pruebas"

    def __init__(
        self,
        *,
        records: Sequence[ReleaseRecord] = (),
        warnings: Sequence[str] = (),
        used_general_search: bool = False,
        applied_categories: Sequence[CategoryRef] = (),
        configured: bool = True,
        missing: Sequence[str] = (),
        secrets: Sequence[str] = (),
        total_available: int | None = None,
        truncated: bool | None = None,
        per_indexer: Sequence[IndexerWindowSummary] = (),
        empty_result: EmptyResultAssessment | None = None,
        search_error: Exception | None = None,
        status: ProviderStatus | None = None,
        indexers: Sequence[IndexerInfo] = (),
    ) -> None:
        self._records = list(records)
        self._warnings = list(warnings)
        self._used_general = used_general_search
        self._applied = list(applied_categories)
        self._configured = configured
        self._missing = list(missing)
        self._secrets = list(secrets)
        self._total_available = total_available
        self._truncated = truncated
        self._per_indexer = list(per_indexer)
        self._empty_result = empty_result
        self._search_error = search_error
        self._status = status
        self._indexers = list(indexers)
        self.requests: list[ProviderSearchRequest] = []
        self.closed = False

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def missing_configuration(self) -> list[str]:
        return self._missing

    def secret_values(self) -> tuple[str, ...]:
        return tuple(self._secrets)

    async def search(self, request: ProviderSearchRequest) -> ProviderSearchResult:
        self.requests.append(request)
        if self._search_error is not None:
            raise self._search_error
        return ProviderSearchResult(
            results=self._records,
            warnings=self._warnings,
            applied_categories=self._applied,
            used_general_search=self._used_general,
            total_available=self._total_available,
            truncated=self._truncated,
            per_indexer=self._per_indexer,
            empty_result=self._empty_result,
        )

    async def list_indexers(self) -> list[IndexerInfo]:
        return list(self._indexers)

    async def get_status(self) -> ProviderStatus:
        if self._status is not None:
            return self._status
        return ProviderStatus(
            provider=self.name,
            configured=self._configured,
            reachable=True,
            version="test",
        )

    async def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            search_types=[],
            categories=[],
            indexers=list(self._indexers),
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider(records=[record()])


def make_service(*providers: SearchProvider) -> MultimediaSearchService:
    return MultimediaSearchService(ProviderRegistry(list(providers)))


@pytest.fixture
def media_types() -> list[MediaType]:
    return list(MediaType)
