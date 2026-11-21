"""Pruebas de integración contra una instancia real de Prowlarr.

Se ejecutan con::

    .venv/bin/python -m pytest -m integration -v

Se omiten automáticamente si no hay configuración o si Prowlarr no responde.
No se simulan resultados: solo se comprueba lo que devuelve la instancia real,
con términos de búsqueda de software libre (Linux).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from multimedia_search_mcp.config import Settings
from multimedia_search_mcp.dedup import find_exact_duplicates
from multimedia_search_mcp.errors import InvalidRequestError, ProviderError, ProviderRequestError
from multimedia_search_mcp.models import (
    CategoryNamespace,
    DuplicateBasis,
    IndexerInfo,
    MediaType,
    SearchRequest,
)
from multimedia_search_mcp.providers.prowlarr import (
    ProwlarrClient,
    ProwlarrProvider,
    flatten_categories,
)
from multimedia_search_mcp.providers.registry import ProviderRegistry
from multimedia_search_mcp.services.search_service import MultimediaSearchService

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Settings:
    loaded = Settings.load()
    if not loaded.prowlarr.configured:
        pytest.skip("Prowlarr no está configurado (.env ausente o incompleto)")
    # Las búsquedas agregadas esperan a todos los indexadores: un timeout de 10 s
    # puede saltar por un indexador lento. En integración se da más margen.
    prowlarr = loaded.prowlarr.model_copy(
        update={"timeout_seconds": max(loaded.prowlarr.timeout_seconds, 45.0)}
    )
    return loaded.model_copy(update={"prowlarr": prowlarr})


@pytest.fixture
async def live_client(settings: Settings) -> AsyncIterator[ProwlarrClient]:
    client = ProwlarrClient(settings.prowlarr)
    try:
        await client.get_system_status()
    except ProviderError as exc:
        await client.aclose()
        pytest.skip(f"Prowlarr no responde: {exc}")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def provider(settings: Settings, live_client: ProwlarrClient) -> AsyncIterator[ProwlarrProvider]:
    instance = ProwlarrProvider(settings.prowlarr, client=live_client)
    yield instance


@pytest.fixture
async def service(settings: Settings, live_client: ProwlarrClient) -> AsyncIterator[MultimediaSearchService]:
    registry = ProviderRegistry([ProwlarrProvider(settings.prowlarr, client=live_client)])
    yield MultimediaSearchService(registry)


class TestConnection:
    async def test_authentication_and_status(self, live_client: ProwlarrClient) -> None:
        status = await live_client.get_system_status()
        assert status.get("appName") == "Prowlarr"
        assert status.get("version")

    async def test_provider_status_is_reachable(self, provider: ProwlarrProvider) -> None:
        status = await provider.get_status()
        assert status.configured is True
        assert status.reachable is True
        assert status.error is None
        assert status.version
        assert status.latency_ms is not None


class TestIndexers:
    async def test_list_indexers(self, provider: ProwlarrProvider) -> None:
        indexers = await provider.list_indexers()
        assert indexers, "Prowlarr no tiene indexadores configurados"
        for indexer in indexers:
            assert indexer.name
            assert indexer.enabled is not None
        assert any(indexer.enabled for indexer in indexers)

    async def test_indexer_stats_are_optional(self, live_client: ProwlarrClient) -> None:
        stats = await live_client.get_indexer_stats()
        assert "indexers" in stats


class TestCategories:
    async def test_categories_tree(self, live_client: ProwlarrClient) -> None:
        raw = await live_client.get_categories()
        tree = flatten_categories(raw)
        assert len(tree) > 50
        assert {2000, 5000, 5070, 7000}.issubset(tree)
        assert tree[5070] == "TV/Anime"

    async def test_names_received_from_api_are_consistent(self, live_client: ProwlarrClient) -> None:
        tree = flatten_categories(await live_client.get_categories())
        assert tree[2000] == "Movies"
        assert tree[5000] == "TV"


class TestLiveSearch:
    async def test_linux_search(self, service: MultimediaSearchService) -> None:
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.OTHER, limit=5)
        )
        assert response.provider == "prowlarr"
        assert response.returned_count <= 5
        assert response.received_count >= response.returned_count
        assert response.results, "La búsqueda de 'debian' no ha devuelto resultados"
        for result in response.results:
            assert result.title
            assert result.provider == "prowlarr"
            assert result.indexer
            assert result.result_id.startswith("prowlarr:")
            assert result.size_bytes is None or result.size_bytes >= 0
            assert result.seeders is None or result.seeders >= 0
            # Sin filtro de categorías no se clasifica.
            assert result.category_match is None

    async def test_pagination_is_applied_client_side(
        self, service: MultimediaSearchService
    ) -> None:
        first = await service.search(
            SearchRequest(query="debian", media_type=MediaType.OTHER, limit=3, offset=0)
        )
        second = await service.search(
            SearchRequest(query="debian", media_type=MediaType.OTHER, limit=3, offset=3)
        )
        assert first.returned_count == min(3, first.received_count)
        # El total real no se conoce: no se expone la ventana como total.
        assert first.total_available is None
        assert second.total_available is None
        assert first.received_count == second.received_count
        if first.received_count > 3:
            assert first.has_more is True
            assert first.next_offset == 3
            assert [r.result_id for r in first.results] != [r.result_id for r in second.results]

    async def test_filtered_search_reports_categories(
        self, service: MultimediaSearchService
    ) -> None:
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.MOVIE, limit=3)
        )
        assert response.used_general_search is False
        assert [category.id for category in response.applied_categories] == [2000]
        assert response.total_available is None
        # Cada resultado trae su estado de categoría respecto al filtro 2000.
        for result in response.results:
            assert result.category_match in {"in_subtree", "off_category", "uncategorized"}
        assert response.category_match_summary is not None
        assert response.category_match_summary.applicable is True

    async def test_manga_uses_general_search(self, service: MultimediaSearchService) -> None:
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.MANGA, limit=3)
        )
        assert response.used_general_search is True
        assert response.applied_categories == []
        assert any("no está garantizado" in warning for warning in response.warnings)

    async def test_year_is_reported_as_unsupported(
        self, service: MultimediaSearchService
    ) -> None:
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.MOVIE, year=2024, limit=1)
        )
        assert any("year" in warning for warning in response.warnings)

    async def test_unavailable_indexer_translates_to_provider_error(
        self, live_client: ProwlarrClient
    ) -> None:
        with pytest.raises(ProviderRequestError):
            await live_client.search(query="linux", indexer_ids=[999_999])

    async def test_empty_query_with_unknown_category_is_empty_not_error(
        self, live_client: ProwlarrClient
    ) -> None:
        results = await live_client.search(query="linux", categories=[9999])
        assert results == []


class TestCapabilities:
    async def test_capabilities_against_real_instance(
        self, provider: ProwlarrProvider
    ) -> None:
        capabilities = await provider.get_capabilities()
        by_type = {capability.media_type: capability for capability in capabilities.search_types}
        assert by_type[MediaType.MOVIE].mode == "filtered"
        assert by_type[MediaType.SERIES].mode == "filtered"
        assert by_type[MediaType.ANIME].mode == "filtered"
        assert by_type[MediaType.BOOK].mode == "filtered"
        assert by_type[MediaType.MANGA].mode == "general"
        assert len(capabilities.categories) > 50
        assert capabilities.indexers
        # Todos los tipos exponen namespace en sus categorías.
        assert all(
            category.namespace == "newznab" for category in capabilities.categories
        )

    async def test_general_types_list_participating_indexers(
        self, provider: ProwlarrProvider
    ) -> None:
        """P5: una búsqueda general no puede quedarse con indexers_supporting y [] sin más."""
        capabilities = await provider.get_capabilities()
        by_type = {capability.media_type: capability for capability in capabilities.search_types}
        for media_type in (MediaType.MANGA, MediaType.OTHER):
            capability = by_type[media_type]
            assert capability.mode == "general"
            assert capability.participation_known is True
            assert capability.indexers_participating, f"{media_type} sin participantes"
            assert capability.indexers_supporting == []

    async def test_filtered_types_report_declared_or_inferred(
        self, provider: ProwlarrProvider
    ) -> None:
        """P8/P5: soporte declarado o inferido, nunca ambos mezclados."""
        capabilities = await provider.get_capabilities()
        by_type = {capability.media_type: capability for capability in capabilities.search_types}
        for media_type in (MediaType.MOVIE, MediaType.SERIES, MediaType.ANIME, MediaType.BOOK):
            capability = by_type[media_type]
            assert capability.indexer_support
            declared = {
                item.indexer
                for item in capability.indexer_support
                if item.support == "declared"
            }
            inferred = {
                item.indexer
                for item in capability.indexer_support
                if item.support == "inferred"
            }
            assert set(capability.indexers_supporting) == declared
            assert set(capability.indexers_inferred) == inferred

    async def test_real_result_categories_carry_a_namespace(
        self, service: MultimediaSearchService
    ) -> None:
        """P6: con datos reales, cada categoría es newznab o nativa atribuida."""
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.OTHER, limit=5)
        )
        assert response.results
        for result in response.results:
            for category in result.categories:
                assert category.namespace in {"newznab", "native", "unknown"}
                if category.namespace == "native":
                    assert category.indexer == result.indexer
                    assert category.indexer_id == result.indexer_id

    async def test_filtered_participation_is_declared_only(
        self, provider: ProwlarrProvider
    ) -> None:
        """P5: con filtro solo participan los indexadores que declaran la categoría.

        La expectativa se deriva del listado real de la instancia: no se fijan
        nombres ni un inventario concreto de indexadores, de modo que la prueba
        sigue siendo válida cuando se añaden o se quitan fuentes.

        Requiere una instancia real de Prowlarr con al menos un indexador
        habilitado que declare alguna de las categorías comprobadas.
        """
        indexers = await provider.list_indexers()
        enabled = [indexer for indexer in indexers if indexer.enabled is True]
        if not enabled:
            pytest.skip("La instancia no tiene indexadores habilitados")

        def declares(indexer: IndexerInfo, category_id: int) -> bool:
            # Solo una categoría Newznab exacta cuenta como soporte declarado:
            # un id nativo con el mismo número no es portable entre indexadores.
            return any(
                category.id == category_id and category.namespace == CategoryNamespace.NEWZNAB
                for category in indexer.categories
            )

        checks = (
            (MediaType.MOVIE, 2000),
            (MediaType.ANIME, 5070),
        )
        expected_by_type = {
            media_type: {indexer.name for indexer in enabled if declares(indexer, category_id)}
            for media_type, category_id in checks
        }
        if not any(expected_by_type.values()):
            pytest.skip("Ningún indexador habilitado declara las categorías comprobadas")

        capabilities = await provider.get_capabilities()
        by_type = {capability.media_type: capability for capability in capabilities.search_types}
        not_enabled = {indexer.name for indexer in indexers if indexer.enabled is not True}

        for media_type, _category_id in checks:
            capability = by_type[media_type]
            expected = expected_by_type[media_type]
            assert capability.mode == "filtered"
            # Los que declaran la categoría aparecen, y solo ellos.
            assert capability.indexers_supporting == sorted(expected)
            # Participar es exactamente lo declarado: los inferidos por jerarquía
            # no se dan por consultados.
            assert capability.indexers_participating == capability.indexers_supporting
            assert not set(capability.indexers_supporting) & set(capability.indexers_inferred)
            # Un indexador deshabilitado o con estado desconocido no participa.
            assert not set(capability.indexers_participating) & not_enabled

    async def test_native_category_filter_is_rejected_live(
        self, service: MultimediaSearchService
    ) -> None:
        """P7: un id nativo real no se envía como filtro global."""
        with pytest.raises(InvalidRequestError) as excinfo:
            await service.search(
                SearchRequest(
                    query="debian", media_type=MediaType.OTHER, categories=("130420",)
                )
            )
        assert "nativa" in str(excinfo.value).lower()


class TestSecretHygiene:
    async def test_api_key_never_appears_in_a_real_search(
        self, settings: Settings, service: MultimediaSearchService
    ) -> None:
        key = settings.prowlarr.api_key_value
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.OTHER, limit=10)
        )
        serialized = response.model_dump_json()
        assert key not in serialized
        assert "apikey" not in serialized.lower()
        json.loads(serialized)  # serializable para MCP


class TestInfoHashLive:
    async def test_live_results_expose_canonical_info_hash(
        self, service: MultimediaSearchService
    ) -> None:
        """P9 Etapa A: el hash llega como campo y coincide con el dato crudo."""
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.OTHER, limit=10)
        )
        assert response.results
        for result in response.results:
            raw = result.provider_metadata.get("info_hash")
            if result.info_hash is None:
                assert raw in (None, "", " ") or not raw
                continue
            assert len(result.info_hash) in (40, 64)
            assert result.info_hash == result.info_hash.lower()
            assert raw is not None
            assert result.info_hash == raw.strip().lower()


class TestDedupLive:
    async def test_exact_detection_is_non_destructive_over_live_results(
        self, service: MultimediaSearchService
    ) -> None:
        """P9 Etapa B: los grupos no alteran ni pierden resultados reales."""
        response = await service.search(
            SearchRequest(query="debian", media_type=MediaType.OTHER, limit=10)
        )
        assert response.results
        snapshot = [result.result_id for result in response.results]

        groups = find_exact_duplicates(response.results)

        assert [result.result_id for result in response.results] == snapshot
        known = set(snapshot)
        hashes = {result.result_id: result.info_hash for result in response.results}
        for group in groups:
            assert group.size >= 2
            assert set(group.members) <= known
            if group.basis is DuplicateBasis.INFO_HASH:
                assert len({hashes[member] for member in group.members}) == 1
