"""Semántica de resultados: estados de categoría (P2), ventana/totales (P1) y vacíos (P3).

Estas pruebas fijan el contrato de honestidad del servidor: nunca se afirma un
total que no se conoce, nunca se presenta un resultado sin categoría como
coincidencia confirmada y nunca se interpreta una lista vacía como certeza.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import (
    FakeProvider,
    category_tree,
    indexer_stats_payload,
    indexers_payload,
    make_client,
    prowlarr_release,
    record,
)

from multimedia_search_mcp.categories import classify_category_match
from multimedia_search_mcp.config import ProwlarrSettings
from multimedia_search_mcp.models import (
    CategoryMatch,
    CategoryNamespace,
    CategoryRef,
    CategoryMatchSummary,
    MediaType,
    ProviderSearchRequest,
    SearchRequest,
    WindowStatus,
)
from multimedia_search_mcp.providers.prowlarr import ProwlarrProvider
from multimedia_search_mcp.providers.registry import ProviderRegistry
from multimedia_search_mcp.services.search_service import MultimediaSearchService

FILTER_MOVIES = (2000,)
FILTER_PC = (4000,)
FILTER_ANIME = (5070,)


class PayloadRouter:
    """Transporte simulado configurable por prueba."""

    def __init__(
        self,
        *,
        releases: list[dict[str, Any]] | None = None,
        indexers: list[dict[str, Any]] | None = None,
        stats: dict[str, Any] | None = None,
        stats_status: int = 200,
    ) -> None:
        self.releases = releases if releases is not None else []
        self.indexers = indexers if indexers is not None else indexers_payload()
        self.stats = stats if stats is not None else indexer_stats_payload()
        self.stats_status = stats_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/search":
            return httpx.Response(200, json=self.releases)
        if path == "/api/v1/indexer/categories":
            return httpx.Response(200, json=category_tree())
        if path == "/api/v1/indexer":
            return httpx.Response(200, json=self.indexers)
        if path == "/api/v1/indexerstats":
            if self.stats_status != 200:
                return httpx.Response(self.stats_status, json={"message": "boom"})
            return httpx.Response(200, json=self.stats)
        if path == "/api/v1/system/status":
            return httpx.Response(200, json={"version": "3.0.2.5780"})
        return httpx.Response(404)


def build_provider(settings: ProwlarrSettings, router: PayloadRouter) -> ProwlarrProvider:
    return ProwlarrProvider(settings, client=make_client(router, settings))


def build_service(provider: ProwlarrProvider) -> MultimediaSearchService:
    return MultimediaSearchService(ProviderRegistry([provider]))


def releases_from(indexer: str, indexer_id: int, count: int, **overrides: Any) -> list[dict[str, Any]]:
    return [
        prowlarr_release(
            indexer=indexer,
            indexerId=indexer_id,
            guid=f"magnet:?xt=urn:btih:{indexer_id}-{i}",
            title=f"{indexer} release {i}",
            **overrides,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# P2 — correspondencia de categorías
# ---------------------------------------------------------------------------


class TestCategoryClassification:
    @staticmethod
    def refs(*ids: int, namespace: CategoryNamespace = CategoryNamespace.NEWZNAB) -> list[CategoryRef]:
        return [CategoryRef(id=category_id, namespace=namespace) for category_id in ids]

    @pytest.mark.parametrize(
        ("categories", "expected"),
        [
            ([2000], CategoryMatch.IN_SUBTREE),  # la propia categoría del filtro
            ([2040], CategoryMatch.IN_SUBTREE),  # descendiente Newznab
            ([2045], CategoryMatch.IN_SUBTREE),  # descendiente Newznab
            ([3000], CategoryMatch.OFF_CATEGORY),  # categoría ajena
            ([2000, 3000], CategoryMatch.IN_SUBTREE),  # basta una dentro
            ([], CategoryMatch.UNCATEGORIZED),
        ],
    )
    def test_states(self, categories: list[int], expected: CategoryMatch) -> None:
        assert classify_category_match(self.refs(*categories), FILTER_MOVIES) is expected

    def test_parent_of_filter_is_not_in_subtree(self) -> None:
        # Filtro TV/Anime (5070): la categoría padre TV (5000) no pertenece a su subárbol.
        assert classify_category_match(self.refs(5000), FILTER_ANIME) is CategoryMatch.OFF_CATEGORY
        assert classify_category_match(self.refs(5070), FILTER_ANIME) is CategoryMatch.IN_SUBTREE

    def test_custom_id_is_not_automatically_in_newznab_subtree(self) -> None:
        """Caso exigido: Nyaa 130420 no pertenece automáticamente a Newznab 4000."""
        assert classify_category_match(self.refs(130420, namespace=CategoryNamespace.NATIVE), FILTER_PC) is CategoryMatch.OFF_CATEGORY
        # Tampoco se inventa jerarquía para otros ids nativos.
        assert classify_category_match(self.refs(102153, namespace=CategoryNamespace.NATIVE), FILTER_PC) is CategoryMatch.OFF_CATEGORY
        assert classify_category_match(self.refs(140679, namespace=CategoryNamespace.NATIVE), FILTER_ANIME) is CategoryMatch.OFF_CATEGORY

    def test_no_filter_means_no_classification(self) -> None:
        # Sin filtro la clasificación no aplica: no se fuerza un estado.
        assert classify_category_match(self.refs(), ()) is None
        assert classify_category_match(self.refs(2000), ()) is None


class TestCategoryMatchInProvider:
    async def test_result_categories_are_classified_and_preserved(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(
            releases=[
                prowlarr_release(categories=[{"id": 2040, "name": "Movies/HD"}]),
                prowlarr_release(guid="magnet:?xt=1", categories=[{"id": 3000, "name": "Audio"}]),
                prowlarr_release(guid="magnet:?xt=2", categories=[]),
                prowlarr_release(guid="magnet:?xt=3", categories=None),
            ]
        )
        provider = build_provider(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.MOVIE)
        )

        matches = [record.category_match for record in result.results]
        assert matches == [
            CategoryMatch.IN_SUBTREE,
            CategoryMatch.OFF_CATEGORY,
            CategoryMatch.UNCATEGORIZED,
            CategoryMatch.UNCATEGORIZED,
        ]
        # Las categorías originales se conservan tal cual las devolvió Prowlarr.
        assert [category.id for category in result.results[0].categories] == [2040]
        assert result.results[1].categories[0].name == "Audio"
        assert result.results[2].categories == []

    async def test_uncategorized_results_are_kept_not_dropped(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[prowlarr_release(categories=[])])
        provider = build_provider(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.MOVIE)
        )
        assert len(result.results) == 1

    async def test_no_category_filter_does_not_classify(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[prowlarr_release(categories=[{"id": 2040}])])
        provider = build_provider(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.OTHER)
        )
        assert result.used_general_search is True
        assert result.applied_categories == []
        assert result.results[0].category_match is None


class TestCategorySummaryAndWarnings:
    async def test_mixed_results_produce_one_warning_per_state(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(
            releases=[
                prowlarr_release(categories=[{"id": 2040, "name": "Movies/HD"}]),
                prowlarr_release(guid="magnet:?xt=a", categories=[{"id": 3000, "name": "Audio"}]),
                prowlarr_release(guid="magnet:?xt=b", categories=[]),
                prowlarr_release(guid="magnet:?xt=c", categories=[]),
            ]
        )
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.MOVIE, limit=10)
        )

        summary = response.category_match_summary
        assert summary is not None
        assert summary.applicable is True
        assert (summary.in_subtree, summary.off_category, summary.uncategorized) == (1, 1, 2)

        uncategorized_warnings = [w for w in response.warnings if "2 de 4" in w]
        assert len(uncategorized_warnings) == 1
        assert "no sea posible confirmar" in uncategorized_warnings[0]
        off_warnings = [w for w in response.warnings if "no pertenecen al filtro" in w]
        assert len(off_warnings) == 1

    async def test_no_filter_has_no_summary_and_no_category_warnings(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[prowlarr_release(categories=[])])
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.OTHER, limit=10)
        )
        assert response.category_match_summary is None
        assert not any("categorías verificables" in warning for warning in response.warnings)
        assert response.results[0].category_match is None

    async def test_summary_counts_unclassified_for_adapters_without_support(self) -> None:
        provider = FakeProvider(
            records=[record(category_match=None)],
            applied_categories=[CategoryRef(id=2000, name="Movies")],
        )
        service = MultimediaSearchService(ProviderRegistry([provider]))
        response = await service.search(SearchRequest(query="x"))
        summary = response.category_match_summary
        assert isinstance(summary, CategoryMatchSummary)
        assert summary.unclassified == 1
        assert any("no pudieron clasificarse" in warning for warning in response.warnings)


# ---------------------------------------------------------------------------
# P1 — ventana, totales y truncamiento
# ---------------------------------------------------------------------------


class TestWindowSemantics:
    async def test_small_response_does_not_claim_completeness(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        releases = releases_from("Nyaa.si", 1, 3) + releases_from("LinuxTracker", 2, 5)
        service = build_service(build_provider(prowlarr_settings, PayloadRouter(releases=releases)))
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=10)
        )

        assert response.received_count == 8
        assert response.returned_count == 8
        assert response.total_available is None
        assert response.truncated is None
        assert response.has_more is None
        assert response.next_offset is None
        assert any("no se puede garantizar" in warning for warning in response.warnings)

    async def test_75_and_25_are_not_treated_as_universal_limits(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Cantidades observadas en la auditoría: no se codifican como límites."""
        releases = releases_from("Nyaa.si", 1, 75) + releases_from("LinuxTracker", 2, 25)
        service = build_service(build_provider(prowlarr_settings, PayloadRouter(releases=releases)))
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=100)
        )

        assert response.received_count == 100
        assert response.total_available is None
        assert response.truncated is None  # los indexadores simulados no declaran paginación
        assert response.has_more is None
        widths = {summary.indexer: summary.received_count for summary in response.per_indexer}
        assert widths == {"Nyaa.si": 75, "LinuxTracker": 25}

    async def test_pagination_support_is_reported_but_not_evidence_of_truncation(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        indexers = indexers_payload()
        indexers[0]["supportsPagination"] = True
        releases = releases_from("Nyaa.si", 1, 3) + releases_from("LinuxTracker", 2, 5)
        service = build_service(
            build_provider(
                prowlarr_settings, PayloadRouter(releases=releases, indexers=indexers)
            )
        )
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=10)
        )

        by_name = {summary.indexer: summary for summary in response.per_indexer}
        # El dato declarado se conserva...
        assert by_name["Nyaa.si"].supports_pagination is True
        # ...pero no se usa como prueba de corte.
        assert by_name["Nyaa.si"].window is WindowStatus.UNKNOWN
        assert by_name["LinuxTracker"].window is WindowStatus.UNKNOWN
        assert response.truncated is None
        assert not any("puede estar limitada" in warning for warning in response.warnings)

    async def test_paginating_indexer_with_zero_results_does_not_imply_truncation(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        indexers = indexers_payload()
        for raw in indexers:
            raw["supportsPagination"] = True
        service = build_service(
            build_provider(
                prowlarr_settings,
                PayloadRouter(releases=[], indexers=indexers, stats=indexer_stats_payload()),
            )
        )
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.MOVIE)
        )

        assert response.truncated is None
        assert all(summary.window is WindowStatus.UNKNOWN for summary in response.per_indexer)
        assert all(summary.supports_pagination is True for summary in response.per_indexer)
        assert not any("puede estar limitada" in warning for warning in response.warnings)

    async def test_supports_pagination_keeps_the_declared_value(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        indexers = indexers_payload()
        indexers[0]["supportsPagination"] = True
        indexers[1]["supportsPagination"] = False
        indexers[2]["enable"] = True
        indexers[2].pop("supportsPagination", None)
        service = build_service(
            build_provider(
                prowlarr_settings,
                PayloadRouter(releases=releases_from("Nyaa.si", 1, 2), indexers=indexers),
            )
        )
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER)
        )

        by_name = {summary.indexer: summary for summary in response.per_indexer}
        assert by_name["Nyaa.si"].supports_pagination is True
        assert by_name["LinuxTracker"].supports_pagination is False
        assert by_name["IndexerCaido"].supports_pagination is None
        # Ningún valor de la capacidad altera el estado de la ventana.
        assert all(summary.window is WindowStatus.UNKNOWN for summary in response.per_indexer)
        assert response.truncated is None

    async def test_local_pagination_still_works_with_pagination_support(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        indexers = indexers_payload()
        indexers[0]["supportsPagination"] = True
        service = build_service(
            build_provider(
                prowlarr_settings,
                PayloadRouter(releases=releases_from("Nyaa.si", 1, 4), indexers=indexers),
            )
        )
        first = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=2)
        )
        second = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=2, offset=2)
        )

        assert first.returned_count == 2
        assert first.has_more is True
        assert first.next_offset == 2
        assert second.returned_count == 2
        assert second.has_more is None
        assert first.truncated is None and second.truncated is None

    async def test_explicit_truncation_evidence_is_still_honoured(self) -> None:
        """El contrato permite truncated=True si un proveedor lo demuestra."""
        provider = FakeProvider(records=[record()], truncated=True)
        response = await MultimediaSearchService(ProviderRegistry([provider])).search(
            SearchRequest(query="x")
        )
        assert response.truncated is True
        assert any("puede estar limitada" in warning for warning in response.warnings)

    async def test_limit_and_offset_within_window(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        releases = releases_from("Nyaa.si", 1, 8)
        service = build_service(build_provider(prowlarr_settings, PayloadRouter(releases=releases)))

        page = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=3, offset=2)
        )
        assert [item.title for item in page.results] == [
            "Nyaa.si release 2",
            "Nyaa.si release 3",
            "Nyaa.si release 4",
        ]
        assert page.returned_count == 3
        assert page.has_more is True
        assert page.next_offset == 5

    async def test_limit_one(self, prowlarr_settings: ProwlarrSettings) -> None:
        releases = releases_from("Nyaa.si", 1, 4)
        service = build_service(build_provider(prowlarr_settings, PayloadRouter(releases=releases)))
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=1)
        )
        assert response.returned_count == 1
        assert response.received_count == 4
        assert response.has_more is True
        assert response.next_offset == 1

    async def test_offset_beyond_window(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        releases = releases_from("Nyaa.si", 1, 4)
        service = build_service(build_provider(prowlarr_settings, PayloadRouter(releases=releases)))
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, offset=10)
        )
        assert response.returned_count == 0
        assert response.has_more is None
        assert any("offset 10" in warning for warning in response.warnings)

    async def test_empty_response_leaves_every_quantity_unknown(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        service = build_service(
            build_provider(prowlarr_settings, PayloadRouter(releases=[]))
        )
        response = await service.search(SearchRequest(query="linux", media_type=MediaType.OTHER))
        assert response.received_count == 0
        assert response.returned_count == 0
        assert response.total_available is None
        assert response.truncated is None
        assert response.has_more is None

    async def test_provider_total_is_only_reported_when_guaranteed(self) -> None:
        """Prowlarr nunca garantiza el total; un proveedor que sí lo haga lo expone."""
        provider = FakeProvider(records=[record()], total_available=42)
        response = await MultimediaSearchService(ProviderRegistry([provider])).search(
            SearchRequest(query="x")
        )
        assert response.total_available == 42

    async def test_has_more_is_never_false(self, prowlarr_settings: ProwlarrSettings) -> None:
        releases = releases_from("Nyaa.si", 1, 2)
        service = build_service(build_provider(prowlarr_settings, PayloadRouter(releases=releases)))
        for offset in (0, 2, 5):
            response = await service.search(
                SearchRequest(query="linux", media_type=MediaType.OTHER, offset=offset)
            )
            assert response.has_more is not False

    async def test_supports_pagination_is_null_when_the_field_is_absent(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        indexers = indexers_payload()
        for raw in indexers:
            raw.pop("supportsPagination", None)
        service = build_service(
            build_provider(
                prowlarr_settings,
                PayloadRouter(releases=releases_from("Nyaa.si", 1, 2), indexers=indexers),
            )
        )
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER)
        )
        assert all(
            summary.supports_pagination is None for summary in response.per_indexer
        )
        assert response.truncated is None

    async def test_per_indexer_separates_indexers_with_the_same_name(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        indexers = indexers_payload() + [
            {"id": 9, "name": "Nyaa.si", "enable": True, "capabilities": {"categories": []}}
        ]
        releases = releases_from("Nyaa.si", 1, 2) + releases_from("Nyaa.si", 9, 3)
        service = build_service(
            build_provider(
                prowlarr_settings, PayloadRouter(releases=releases, indexers=indexers)
            )
        )
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=20)
        )

        nyaa = [s for s in response.per_indexer if s.indexer == "Nyaa.si"]
        assert len(nyaa) == 2
        assert {s.indexer_id for s in nyaa} == {1, 9}
        assert sorted(s.received_count for s in nyaa) == [2, 3]
        assert response.received_count == 5

    async def test_per_indexer_sum_can_exceed_received_after_dedupe(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Un indexador puede repetir filas: `per_indexer` cuenta lo recibido."""
        duplicate = prowlarr_release(
            indexer="Nyaa.si", indexerId=1, guid="magnet:?xt=dup", title="dup"
        )
        service = build_service(
            build_provider(prowlarr_settings, PayloadRouter(releases=[duplicate, duplicate]))
        )
        response = await service.search(
            SearchRequest(query="linux", media_type=MediaType.OTHER, limit=20)
        )

        assert response.received_count == 1
        nyaa = next(s for s in response.per_indexer if s.indexer == "Nyaa.si")
        assert nyaa.received_count == 2


# ---------------------------------------------------------------------------
# P3 — respuestas vacías y fallos silenciosos
# ---------------------------------------------------------------------------


class TestEmptyResponseAssessment:
    async def test_empty_with_compatible_indexers(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[], stats=indexer_stats_payload())
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="dune", media_type=MediaType.MOVIE)
        )

        assert response.empty_result is not None
        assert response.empty_result.indexers_compatible_with_filter == 1
        assert response.empty_result.no_compatible_indexers is False
        joined = " ".join(response.warnings)
        assert "Respuesta vacía" in joined
        assert "no se puede garantizar" in joined
        assert "sin resultados" not in joined.lower()

    async def test_empty_when_no_indexer_supports_the_category(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[], stats=indexer_stats_payload())
        provider = build_provider(prowlarr_settings, router)
        # Filtro explícito que ningún indexador simulado anuncia (6000 XXX).
        result = await provider.search(
            ProviderSearchRequest(
                query="dune", media_type=MediaType.MOVIE, categories=("8000",)
            )
        )
        assert result.empty_result is not None
        assert result.empty_result.indexers_compatible_with_filter == 0
        assert result.empty_result.no_compatible_indexers is True

        service = build_service(provider)
        response = await service.search(
            SearchRequest(query="dune", media_type=MediaType.MOVIE, categories=("8000",))
        )
        assert any("Ningún indexador" in warning for warning in response.warnings)

    async def test_empty_with_unknown_error_state(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[], stats_status=500)
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.MOVIE)
        )

        assert response.empty_result is not None
        assert response.empty_result.reported_failed_queries is None
        assert response.empty_result.indexer_failure_reported is None
        assert any("No hay datos de errores" in warning for warning in response.warnings)

    async def test_zero_failed_queries_is_not_proof_of_health(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        stats = {
            "indexers": [
                {
                    "indexerId": 1,
                    "indexerName": "Nyaa.si",
                    "numberOfQueries": 10,
                    "numberOfFailedQueries": 0,
                },
                {
                    "indexerId": 2,
                    "indexerName": "LinuxTracker",
                    "numberOfQueries": 10,
                    "numberOfFailedQueries": 0,
                },
            ]
        }
        router = PayloadRouter(releases=[], stats=stats)
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.MOVIE)
        )

        assert response.empty_result is not None
        assert response.empty_result.reported_failed_queries == 0
        # 0 fallos no se convierte en "todos sanos".
        assert response.empty_result.indexer_failure_reported is None
        assert any("cero consultas fallidas" in warning for warning in response.warnings)

    async def test_unknown_error_state_is_not_reported_as_zero_failures(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Sin /indexerstats no se puede hablar de "cero consultas fallidas"."""
        router = PayloadRouter(releases=[], stats_status=500)
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.MOVIE)
        )

        assert response.empty_result is not None
        assert response.empty_result.reported_failed_queries is None
        joined = " ".join(response.warnings)
        assert "No hay datos de errores" in joined
        assert "cero consultas fallidas" not in joined

    async def test_indexers_without_enable_field_do_not_produce_invented_facts(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Sin campo `enable` no se puede afirmar 0 indexadores ni "ninguno compatible"."""
        indexers = indexers_payload()
        for raw in indexers:
            raw.pop("enable", None)
        router = PayloadRouter(releases=[], indexers=indexers, stats=indexer_stats_payload())
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.MOVIE)
        )

        assert response.empty_result is not None
        assert response.empty_result.indexers_available is None
        # La compatibilidad sí puede calcularse con los candidatos listados.
        assert response.empty_result.indexers_compatible_with_filter == 1
        assert response.empty_result.no_compatible_indexers is False
        joined = " ".join(response.warnings)
        assert "Ningún indexador" not in joined
        assert "No se puede determinar cuántos indexadores" in joined

    async def test_failures_are_reported_when_known(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[], stats=indexer_stats_payload())
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.MOVIE)
        )
        assert response.empty_result is not None
        assert response.empty_result.indexer_failure_reported is True
        assert any("consultas fallidas" in warning for warning in response.warnings)

    async def test_results_with_compatibility_warnings_are_preserved(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = PayloadRouter(releases=[prowlarr_release()])
        service = build_service(build_provider(prowlarr_settings, router))
        response = await service.search(
            SearchRequest(query="x", media_type=MediaType.BOOK)
        )
        assert response.results
        # Las advertencias del adaptador (limitaciones del tipo) siguen presentes.
        assert any("Books (7000)" in warning for warning in response.warnings)
        assert not any("Respuesta vacía" in warning for warning in response.warnings)
