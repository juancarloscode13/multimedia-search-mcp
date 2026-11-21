"""Adaptador Prowlarr: búsqueda, indexadores, estado y capacidades."""

from __future__ import annotations

import httpx
import pytest
from conftest import (
    TEST_API_KEY,
    category_tree,
    indexer_stats_payload,
    indexers_payload,
    make_client,
    prowlarr_release,
)

from multimedia_search_mcp.config import ProwlarrSettings
from multimedia_search_mcp.errors import ProviderAuthError
from multimedia_search_mcp.models import MediaType, ProviderSearchRequest
from multimedia_search_mcp.providers.prowlarr import ProwlarrProvider

SEARCH_PREFIX = "/api/v1/search"


class Router:
    """Transporte simulado que enruta por ruta y registra las peticiones."""

    def __init__(self, *, releases: list[dict] | None = None, stats_status: int = 200) -> None:
        self.releases = releases or []
        self.stats_status = stats_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/v1/system/status":
            return httpx.Response(200, json={"version": "3.0.2.5780", "appName": "Prowlarr"})
        if path == "/api/v1/indexer":
            return httpx.Response(200, json=indexers_payload())
        if path == "/api/v1/indexerstats":
            if self.stats_status != 200:
                return httpx.Response(self.stats_status, json={"message": "boom"})
            return httpx.Response(200, json=indexer_stats_payload())
        if path == "/api/v1/indexer/categories":
            return httpx.Response(200, json=category_tree())
        if path == SEARCH_PREFIX:
            return httpx.Response(200, json=self.releases)
        return httpx.Response(404, json={"message": "not found"})

    def search_requests(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path == SEARCH_PREFIX]


def build(settings: ProwlarrSettings, router: Router) -> ProwlarrProvider:
    return ProwlarrProvider(settings, client=make_client(router, settings))


@pytest.fixture
def router() -> Router:
    return Router(releases=[prowlarr_release(), prowlarr_release(guid="magnet:?xt=2", title="otro")])


class TestSearch:
    async def test_movie_search_sends_verified_category(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="dune", media_type=MediaType.MOVIE)
        )

        request = router.search_requests()[0]
        assert request.url.params.get_list("categories") == ["2000"]
        assert request.url.params["type"] == "movie"
        assert result.used_general_search is False
        assert [category.id for category in result.applied_categories] == [2000]
        assert len(result.results) == 2

    async def test_manga_search_uses_general_search_with_warning(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="one piece", media_type=MediaType.MANGA)
        )

        request = router.search_requests()[0]
        assert "categories" not in request.url.params
        assert result.used_general_search is True
        assert any("no está garantizado" in warning for warning in result.warnings)
        assert any("manga" in warning.lower() for warning in result.warnings)

    async def test_year_is_reported_as_unsupported(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="dune", media_type=MediaType.MOVIE, year=2024)
        )
        assert any("year" in warning for warning in result.warnings)

    async def test_user_categories_replace_type_mapping(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(
                query="dune", media_type=MediaType.MOVIE, categories=("Books/EBook",)
            )
        )
        request = router.search_requests()[0]
        assert request.url.params.get_list("categories") == ["7020"]
        assert [category.name for category in result.applied_categories] == ["Books/EBook"]

    async def test_empty_result_is_reported_without_claiming_absence(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = build(prowlarr_settings, Router(releases=[]))
        result = await provider.search(
            ProviderSearchRequest(query="nada", media_type=MediaType.BOOK)
        )
        assert result.results == []
        # Nunca se presenta la ventana como total ni se afirma completitud.
        assert result.total_available is None
        assert result.truncated is None
        assert result.empty_result is not None
        assessment = result.empty_result
        assert assessment.indexers_available == 2  # IndexerCaido está deshabilitado
        assert assessment.indexers_compatible_with_filter == 1  # Nyaa anuncia Books
        assert assessment.no_compatible_indexers is False
        assert assessment.reported_failed_queries == 1
        assert assessment.indexer_failure_reported is True
        # La ventana por indexador se informa aunque no haya resultados.
        assert {summary.indexer for summary in result.per_indexer} == {"Nyaa.si", "LinuxTracker"}
        assert all(summary.received_count == 0 for summary in result.per_indexer)

    async def test_result_without_title_is_skipped_and_reported(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(releases=[prowlarr_release(title=""), prowlarr_release(guid="magnet:?xt=b")])
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="linux", media_type=MediaType.OTHER)
        )
        assert len(result.results) == 1
        assert any("descartados" in warning for warning in result.warnings)

    async def test_proxy_magnet_url_is_not_exposed(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(guid="not-a-magnet", magnetUrl="http://h/download?apikey=REAL&l=x")
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="linux", media_type=MediaType.OTHER)
        )
        assert result.results[0].magnet_uri is None

    async def test_categories_without_name_are_kept_by_id(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(categories=[{"id": 140679, "subCategories": []}])
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="linux", media_type=MediaType.OTHER)
        )
        category = result.results[0].categories[0]
        assert category.id == 140679 and category.name is None

    async def test_category_tree_is_cached(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        await provider.search(ProviderSearchRequest(query="a", media_type=MediaType.MOVIE))
        await provider.search(ProviderSearchRequest(query="b", media_type=MediaType.SERIES))
        tree_requests = [
            request for request in router.requests if request.url.path.endswith("/indexer/categories")
        ]
        assert len(tree_requests) == 1

    async def test_struct_metadata_is_empty_because_prowlarr_does_not_provide_it(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(title="Dune.2024.2160p.UHD.BluRay.x265")
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="dune", media_type=MediaType.MOVIE)
        )
        assert result.results[0].structured_metadata == {}


class TestIndexers:
    async def test_indexers_include_state_and_stats(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        indexers = await provider.list_indexers()

        assert [indexer.name for indexer in indexers] == ["Nyaa.si", "LinuxTracker", "IndexerCaido"]
        nyaa = indexers[0]
        assert nyaa.enabled is True
        assert nyaa.privacy == "public"
        assert nyaa.stats is not None and nyaa.stats.failed_queries == 1
        assert {category.id for category in nyaa.categories} >= {2000, 5000, 7000}

    async def test_stats_failure_does_not_break_listing(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = build(prowlarr_settings, Router(stats_status=500))
        indexers = await provider.list_indexers()
        assert len(indexers) == 3
        assert all(indexer.stats is None for indexer in indexers)

    async def test_indexer_listing_failure_does_not_mark_provider_unreachable(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/system/status":
                return httpx.Response(200, json={"version": "3.0.2.5780"})
            return httpx.Response(500)

        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(handler, prowlarr_settings)
        status = await provider.get_status()
        assert status.reachable is True
        assert status.indexers_enabled is None


class TestStatus:
    async def test_reachable_status(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        status = await provider.get_status()
        assert status.reachable is True
        assert status.version == "3.0.2.5780"
        # IndexerCaido tiene enable=false.
        assert status.indexers_enabled == 2
        assert status.latency_ms is not None

    async def test_auth_failure_is_reported_not_raised(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(lambda request: httpx.Response(401), prowlarr_settings)
        status = await provider.get_status()
        assert status.reachable is False
        assert status.error is not None and "401" in status.error
        assert TEST_API_KEY not in status.error

    async def test_unconfigured_provider_status(self) -> None:
        provider = ProwlarrProvider(ProwlarrSettings(base_url=None, api_key=None))
        status = await provider.get_status()
        assert status.configured is False
        assert status.reachable is False
        assert "PROWLARR_API_KEY" in (status.error or "")

    async def test_unconfigured_provider_search_raises(self) -> None:
        from multimedia_search_mcp.errors import ConfigurationError

        provider = ProwlarrProvider(ProwlarrSettings(base_url=None, api_key=None))
        with pytest.raises(ConfigurationError):
            await provider.search(
                ProviderSearchRequest(query="x", media_type=MediaType.OTHER)
            )


class TestCapabilities:
    async def test_capabilities_report_modes_and_support(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        capabilities = await provider.get_capabilities()

        by_type = {capability.media_type: capability for capability in capabilities.search_types}
        assert by_type[MediaType.MOVIE].mode == "filtered"
        assert by_type[MediaType.MOVIE].category_ids == [2000]
        assert by_type[MediaType.MOVIE].category_names == ["Movies"]
        assert "Nyaa.si" in by_type[MediaType.MOVIE].indexers_supporting
        assert "LinuxTracker" not in by_type[MediaType.MOVIE].indexers_supporting

        # Anime (5070): Nyaa solo anuncia el padre TV (5000) => inferido, no declarado.
        assert by_type[MediaType.ANIME].mode == "filtered"
        assert by_type[MediaType.ANIME].indexers_supporting == []
        assert by_type[MediaType.ANIME].indexers_inferred == ["Nyaa.si"]
        # Inferido no es participación confirmada: Prowlarr puede o no consultarlo.
        assert by_type[MediaType.ANIME].indexers_participating == []
        assert any(
            "inferida" in warning for warning in by_type[MediaType.ANIME].warnings
        )

        assert by_type[MediaType.SERIES].indexers_supporting == ["Nyaa.si"]

        assert by_type[MediaType.MANGA].mode == "general"
        assert by_type[MediaType.MANGA].mapping_verified is False
        assert any("no está garantizado" in warning for warning in by_type[MediaType.MANGA].warnings)
        # P5: una búsqueda general participa con los indexadores habilitados.
        assert by_type[MediaType.MANGA].indexers_supporting == []
        assert by_type[MediaType.MANGA].indexers_participating == ["LinuxTracker", "Nyaa.si"]
        assert by_type[MediaType.MANGA].participation_known is True

    async def test_capabilities_list_available_categories(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        capabilities = await provider.get_capabilities()
        ids = {category.id for category in capabilities.categories}
        assert {2000, 5000, 5070, 7000}.issubset(ids)
        assert capabilities.provider == "prowlarr"
        assert len(capabilities.indexers) == 3

    async def test_disabled_indexers_are_not_counted_as_support(
        self, prowlarr_settings: ProwlarrSettings, router: Router
    ) -> None:
        provider = build(prowlarr_settings, router)
        capabilities = await provider.get_capabilities()
        by_type = {capability.media_type: capability for capability in capabilities.search_types}
        assert "IndexerCaido" not in by_type[MediaType.SERIES].indexers_supporting

    async def test_indexer_listing_failure_degrades_capabilities(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Sin listado de indexadores, las capacidades lo declaran en vez de fallar."""
        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(lambda request: httpx.Response(500), prowlarr_settings)
        capabilities = await provider.get_capabilities()
        assert capabilities.search_types
        assert all(not cap.participation_known for cap in capabilities.search_types)
        assert all(cap.indexers_participating == [] for cap in capabilities.search_types)
        assert any(
            "desconoce qué indexadores participan" in warning
            for warning in capabilities.warnings
        )


class TestDegradation:
    async def test_category_endpoint_failure_does_not_abort_search(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/indexer/categories":
                return httpx.Response(500)
            if request.url.path == SEARCH_PREFIX:
                return httpx.Response(200, json=[prowlarr_release()])
            return httpx.Response(404)

        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(handler, prowlarr_settings)

        result = await provider.search(
            ProviderSearchRequest(query="linux", media_type=MediaType.MOVIE)
        )
        assert len(result.results) == 1
        assert any("árbol de categorías" in warning for warning in result.warnings)

    async def test_category_endpoint_failure_does_not_break_indexer_listing(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/indexer":
                return httpx.Response(200, json=indexers_payload())
            if request.url.path == "/api/v1/indexerstats":
                return httpx.Response(200, json=indexer_stats_payload())
            return httpx.Response(500)

        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(handler, prowlarr_settings)
        indexers = await provider.list_indexers()
        assert [indexer.name for indexer in indexers] == ["Nyaa.si", "LinuxTracker", "IndexerCaido"]

    async def test_indexer_without_enable_field_is_not_reported_as_enabled(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        indexers = indexers_payload()
        indexers[1].pop("enable")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/indexer":
                return httpx.Response(200, json=indexers)
            if request.url.path == "/api/v1/indexerstats":
                return httpx.Response(200, json=indexer_stats_payload())
            if request.url.path == "/api/v1/indexer/categories":
                return httpx.Response(200, json=category_tree())
            if request.url.path == "/api/v1/system/status":
                return httpx.Response(200, json={"version": "3.0.2.5780"})
            return httpx.Response(404)

        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(handler, prowlarr_settings)

        listed = await provider.list_indexers()
        linux = next(indexer for indexer in listed if indexer.name == "LinuxTracker")
        assert linux.enabled is None

        status = await provider.get_status()
        assert status.indexers_enabled == 1  # solo Nyaa.si declara enable=true

        capabilities = await provider.get_capabilities()
        assert any("enable" in warning for warning in capabilities.warnings)


class TestRedactionInRecords:
    async def test_api_key_never_reaches_records(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(infoUrl="https://t/view/1?apikey=REAL_KEY&id=1")
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="linux", media_type=MediaType.OTHER)
        )
        record = result.results[0]
        assert record.info_url is not None and "REAL_KEY" not in record.info_url
        dumped = record.model_dump_json()
        assert TEST_API_KEY not in dumped
        assert "apikey" not in dumped.lower()

    async def test_auth_error_message_has_no_key(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(lambda request: httpx.Response(401), prowlarr_settings)
        with pytest.raises(ProviderAuthError) as excinfo:
            await provider.list_indexers()
        assert TEST_API_KEY not in str(excinfo.value)
