"""Fase 2: namespaces de categoría (P6), validación (P7), soporte (P8), participación (P5)
y solapamiento semántico (P4)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import make_client, prowlarr_release

from multimedia_search_mcp.categories import (
    SEMANTIC_FILTER_WARNING,
    classify_category_match,
    spec_for,
)
from multimedia_search_mcp.config import ProwlarrSettings
from multimedia_search_mcp.errors import InvalidRequestError
from multimedia_search_mcp.models import (
    CategoryMatch,
    CategoryNamespace,
    CategoryRef,
    CategorySupportLevel,
    MediaType,
    ProviderSearchRequest,
)
from multimedia_search_mcp.providers.prowlarr import ProwlarrProvider

TREE = [
    {
        "id": 2000,
        "name": "Movies",
        "subCategories": [{"id": 2040, "name": "Movies/HD", "subCategories": []}],
    },
    {
        "id": 4000,
        "name": "PC",
        "subCategories": [{"id": 4020, "name": "PC/ISO", "subCategories": []}],
    },
    {
        "id": 5000,
        "name": "TV",
        "subCategories": [{"id": 5070, "name": "TV/Anime", "subCategories": []}],
    },
    {"id": 7000, "name": "Books", "subCategories": []},
    {"id": 8000, "name": "Other", "subCategories": []},
]


def indexer(
    indexer_id: int,
    name: str,
    category_ids: list[int],
    *,
    enabled: bool | None = True,
    include_categories: bool = True,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": indexer_id,
        "name": name,
        "protocol": "torrent",
        "privacy": "public",
        "supportsSearch": True,
        "supportsRss": True,
    }
    if enabled is not None:  # None = la API no informa del campo `enable`
        raw["enable"] = enabled
    if include_categories:
        raw["capabilities"] = {"categories": [{"id": category_id} for category_id in category_ids]}
    return raw


class Router:
    """Transporte simulado para capacidades y validación de filtros."""

    def __init__(
        self,
        *,
        releases: list[dict[str, Any]] | None = None,
        indexers: list[dict[str, Any]] | None = None,
        categories_status: int = 200,
    ) -> None:
        self.releases = releases or []
        self.indexers = (
            indexers
            if indexers is not None
            else [
                indexer(1, "Nyaa.si", [2000, 5000, 7000, 140679]),
                indexer(2, "LinuxTracker", [4000]),
            ]
        )
        self.categories_status = categories_status
        self.search_calls = 0
        self.category_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/search":
            self.search_calls += 1
            return httpx.Response(200, json=self.releases)
        if path == "/api/v1/indexer/categories":
            self.category_calls += 1
            if self.categories_status != 200:
                return httpx.Response(self.categories_status, json={"message": "boom"})
            return httpx.Response(200, json=TREE)
        if path == "/api/v1/indexer":
            return httpx.Response(200, json=self.indexers)
        if path == "/api/v1/indexerstats":
            return httpx.Response(200, json={"indexers": []})
        if path == "/api/v1/system/status":
            return httpx.Response(200, json={"version": "3.0.2.5780"})
        return httpx.Response(404)


def build(settings: ProwlarrSettings, router: Router) -> ProwlarrProvider:
    return ProwlarrProvider(settings, client=make_client(router, settings))


async def capabilities(
    settings: ProwlarrSettings, router: Router
) -> dict[MediaType, Any]:
    provider = build(settings, router)
    result = await provider.get_capabilities()
    return {capability.media_type: capability for capability in result.search_types}


# ---------------------------------------------------------------------------
# P6 — namespaces
# ---------------------------------------------------------------------------


class TestNamespaces:
    async def test_tree_categories_are_newznab(self, prowlarr_settings: ProwlarrSettings) -> None:
        provider = build(prowlarr_settings, Router())
        result = await provider.get_capabilities()
        assert result.categories
        for category in result.categories:
            assert category.namespace is CategoryNamespace.NEWZNAB
            assert category.name
            assert category.indexer is None

    async def test_result_newznab_category(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(categories=[{"id": 2040, "name": "Movies/HD"}])
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.MOVIE)
        )
        category = result.results[0].categories[0]
        assert category.namespace is CategoryNamespace.NEWZNAB
        assert category.indexer is None and category.indexer_id is None
        assert category.name == "Movies/HD"

    async def test_result_native_category_is_attributed_to_its_indexer(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(
            indexer="Nyaa.si", indexerId=1, categories=[{"id": 130420}]
        )
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.OTHER)
        )
        category = result.results[0].categories[0]
        assert category.namespace is CategoryNamespace.NATIVE
        assert category.indexer == "Nyaa.si"
        assert category.indexer_id == 1
        # Sin nombre en la respuesta y fuera del árbol: no se inventa.
        assert category.name is None

    async def test_native_category_keeps_the_name_the_provider_gives(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(categories=[{"id": 140679, "name": "Anime"}])
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.OTHER)
        )
        category = result.results[0].categories[0]
        assert category.namespace is CategoryNamespace.NATIVE
        assert category.name == "Anime"

    async def test_unmapped_mid_range_id_is_unknown(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(categories=[{"id": 50_000}])
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.OTHER)
        )
        assert result.results[0].categories[0].namespace is CategoryNamespace.UNKNOWN

    async def test_same_numeric_id_in_different_namespaces_is_distinguishable(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        newznab = CategoryRef(id=4000, namespace=CategoryNamespace.NEWZNAB)
        native = CategoryRef(
            id=4000, namespace=CategoryNamespace.NATIVE, indexer="OtroIndexer"
        )
        filter_ids = (4000,)
        assert classify_category_match([newznab], filter_ids) is CategoryMatch.IN_SUBTREE
        # El mismo número como categoría nativa no cuenta como Newznab.
        assert classify_category_match([native], filter_ids) is CategoryMatch.OFF_CATEGORY

    async def test_native_ids_from_two_indexers_are_not_portable(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        first = prowlarr_release(indexer="Nyaa.si", indexerId=1, categories=[{"id": 130420}])
        second = prowlarr_release(
            indexer="Otro", indexerId=7, guid="magnet:?xt=2", categories=[{"id": 130420}]
        )
        provider = build(prowlarr_settings, Router(releases=[first, second]))
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.OTHER)
        )
        one, other = (item.categories[0] for item in result.results)
        assert one.id == other.id == 130420
        assert (one.indexer, one.indexer_id) == ("Nyaa.si", 1)
        assert (other.indexer, other.indexer_id) == ("Otro", 7)

    async def test_mixed_result_keeps_both_namespaces(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(
            indexer="Nyaa.si",
            indexerId=1,
            categories=[{"id": 2000, "name": "Movies"}, {"id": 130420}],
        )
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.MOVIE)
        )
        namespaces = [category.namespace for category in result.results[0].categories]
        assert namespaces == [CategoryNamespace.NEWZNAB, CategoryNamespace.NATIVE]
        assert result.results[0].category_match is CategoryMatch.IN_SUBTREE

    async def test_only_native_categories_are_off_category(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """P2 se conserva: 130420 no pertenece al filtro Newznab 4000."""
        release = prowlarr_release(
            indexer="Nyaa.si", indexerId=1, categories=[{"id": 130420}]
        )
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(
                query="x", media_type=MediaType.OTHER, categories=("4020",)
            )
        )
        assert result.results[0].category_match is CategoryMatch.OFF_CATEGORY

    async def test_indexer_catalog_is_namespaced(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = build(prowlarr_settings, Router())
        indexers = await provider.list_indexers()
        nyaa = next(indexer for indexer in indexers if indexer.name == "Nyaa.si")
        namespaces = {category.id: category.namespace for category in nyaa.categories}
        assert namespaces[5000] is CategoryNamespace.NEWZNAB
        assert namespaces[140679] is CategoryNamespace.NATIVE
        native = next(category for category in nyaa.categories if category.id == 140679)
        assert native.indexer == "Nyaa.si" and native.indexer_id == 1


# ---------------------------------------------------------------------------
# P7 — validación de filtros
# ---------------------------------------------------------------------------


class TestCategoryValidation:
    async def test_valid_newznab_id_is_accepted(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router()
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.MOVIE, categories=("2040",))
        )
        assert [category.id for category in result.applied_categories] == [2040]
        assert result.applied_categories[0].namespace is CategoryNamespace.NEWZNAB
        assert router.search_calls == 1
        assert not any("sin validar" in warning for warning in result.warnings)

    async def test_valid_id_uses_the_cached_tree(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router()
        provider = build(prowlarr_settings, router)
        for _ in range(2):
            await provider.search(
                ProviderSearchRequest(
                    query="x", media_type=MediaType.MOVIE, categories=("2040",)
                )
            )
        assert router.category_calls == 1  # segunda búsqueda usa la caché

    async def test_inexistent_newznab_id_is_rejected_without_searching(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router()
        provider = build(prowlarr_settings, router)
        with pytest.raises(InvalidRequestError) as excinfo:
            await provider.search(
                ProviderSearchRequest(
                    query="x", media_type=MediaType.MOVIE, categories=("9999",)
                )
            )
        assert "9999" in str(excinfo.value)
        assert router.search_calls == 0

    async def test_native_id_without_indexer_is_rejected(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router()
        provider = build(prowlarr_settings, router)
        with pytest.raises(InvalidRequestError) as excinfo:
            await provider.search(
                ProviderSearchRequest(
                    query="x", media_type=MediaType.ANIME, categories=("130420",)
                )
            )
        message = str(excinfo.value)
        assert "nativa" in message and "indexador" in message
        assert router.search_calls == 0

    async def test_native_id_cannot_be_scoped_to_an_indexer(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """El filtro de Prowlarr es global: no se finge una asociación segura."""
        provider = build(prowlarr_settings, Router())
        with pytest.raises(InvalidRequestError) as excinfo:
            await provider.search(
                ProviderSearchRequest(
                    query="x", media_type=MediaType.ANIME, categories=("140679",)
                )
            )
        assert "todos los indexadores" in str(excinfo.value)

    async def test_arbitrary_number_is_rejected(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router()
        provider = build(prowlarr_settings, router)
        with pytest.raises(InvalidRequestError):
            await provider.search(
                ProviderSearchRequest(
                    query="x", media_type=MediaType.OTHER, categories=("12345",)
                )
            )
        assert router.search_calls == 0

    async def test_tree_unavailable_degrades_with_warning(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(categories_status=500)
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(
                query="x", media_type=MediaType.MOVIE, categories=("2040",)
            )
        )
        assert [category.id for category in result.applied_categories] == [2040]
        assert any("sin validar" in warning for warning in result.warnings)
        assert router.search_calls == 1

    async def test_tree_unavailable_rejects_names(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = build(prowlarr_settings, Router(categories_status=500))
        with pytest.raises(InvalidRequestError) as excinfo:
            await provider.search(
                ProviderSearchRequest(
                    query="x", media_type=MediaType.MOVIE, categories=("Movies/HD",)
                )
            )
        assert "no está disponible" in str(excinfo.value)

    async def test_name_is_resolved_against_the_tree(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = build(prowlarr_settings, Router())
        result = await provider.search(
            ProviderSearchRequest(
                query="x", media_type=MediaType.MOVIE, categories=("Movies/HD",)
            )
        )
        assert [category.id for category in result.applied_categories] == [2040]

    async def test_tree_unavailable_still_rejects_native_ids(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Un id nativo no se admite ni siquiera en degradación."""
        router = Router(categories_status=500)
        provider = build(prowlarr_settings, router)
        with pytest.raises(InvalidRequestError) as excinfo:
            await provider.search(
                ProviderSearchRequest(
                    query="x", media_type=MediaType.ANIME, categories=("130420",)
                )
            )
        assert "nativa" in str(excinfo.value).lower()
        assert router.search_calls == 0

    async def test_ambiguous_exact_name_is_rejected(self) -> None:
        tree = {2000: "Movies", 2001: "movies"}
        assert ProwlarrProvider._match_category_name("Movies", tree) is None
        assert ProwlarrProvider._match_category_name("movies", {2000: "Movies"}) == 2000

    async def test_tree_unavailable_does_not_classify_results(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Sin árbol no se puede afirmar el namespace: no se clasifica."""
        router = Router(
            releases=[prowlarr_release(categories=[{"id": 2040, "name": "Movies/HD"}])],
            categories_status=500,
        )
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(
                query="x", media_type=MediaType.MOVIE, categories=("2040",)
            )
        )
        assert result.results[0].category_match is None
        assert result.results[0].categories[0].namespace is CategoryNamespace.UNKNOWN

    async def test_category_names_are_omitted_without_tree(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        by_type = await capabilities(prowlarr_settings, Router(categories_status=500))
        movie = by_type[MediaType.MOVIE]
        assert movie.category_ids == [2000]
        assert movie.category_names == []  # nunca cadenas vacías


# ---------------------------------------------------------------------------
# P5 — participación vs soporte
# ---------------------------------------------------------------------------


class TestParticipation:
    async def test_general_search_lists_participating_indexers(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        by_type = await capabilities(prowlarr_settings, Router())
        manga = by_type[MediaType.MANGA]
        assert manga.mode == "general"
        assert manga.indexers_supporting == []
        assert manga.indexers_participating == ["LinuxTracker", "Nyaa.si"]
        assert manga.participation_known is True

    async def test_general_search_does_not_claim_results(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        by_type = await capabilities(prowlarr_settings, Router())
        assert any(
            "no implica que vayan a devolver resultados" in warning
            for warning in by_type[MediaType.MANGA].warnings
        )

    async def test_filtered_search_separates_support_from_participation(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        by_type = await capabilities(prowlarr_settings, Router())
        movie = by_type[MediaType.MOVIE]
        assert movie.indexers_supporting == ["Nyaa.si"]
        assert movie.indexers_participating == ["Nyaa.si"]

    async def test_unsupported_category_has_no_participants(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(2, "LinuxTracker", [4000])])
        by_type = await capabilities(prowlarr_settings, router)
        movie = by_type[MediaType.MOVIE]
        assert movie.mode == "filtered"
        assert movie.indexers_supporting == []
        assert movie.indexers_inferred == []
        assert movie.indexers_participating == []
        assert any("Ningún indexador habilitado declara" in w for w in movie.warnings)

    async def test_participation_unknown_without_indexer_list(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/indexer/categories":
                return httpx.Response(200, json=TREE)
            if request.url.path == "/api/v1/indexer":
                return httpx.Response(500)
            return httpx.Response(404)

        provider = ProwlarrProvider(prowlarr_settings)
        provider._client = make_client(handler, prowlarr_settings)
        result = await provider.get_capabilities()

        assert all(not cap.participation_known for cap in result.search_types)
        assert all(cap.indexers_participating == [] for cap in result.search_types)
        assert any("se desconoce qué indexadores participan" in w for w in result.warnings)

    async def test_enabled_indexers_without_results_still_participate(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """La participación es de la configuración, no de los resultados."""
        router = Router(releases=[])  # ninguna búsqueda devuelve nada
        by_type = await capabilities(prowlarr_settings, router)
        assert by_type[MediaType.MANGA].indexers_participating == ["LinuxTracker", "Nyaa.si"]


# ---------------------------------------------------------------------------
# P8 — soporte declarado vs inferido
# ---------------------------------------------------------------------------


class TestSupportLevels:
    async def test_declared_when_the_category_is_announced_exactly(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(1, "Nyaa.si", [5000])])
        by_type = await capabilities(prowlarr_settings, router)
        series = by_type[MediaType.SERIES]
        support = series.indexer_support[0]
        assert support.support is CategorySupportLevel.DECLARED
        assert support.matched_category_ids == [5000]
        assert series.indexers_supporting == ["Nyaa.si"]
        assert series.indexers_inferred == []

    async def test_inferred_when_only_a_descendant_is_announced(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(1, "Nyaa.si", [5070])])
        by_type = await capabilities(prowlarr_settings, router)
        series = by_type[MediaType.SERIES]
        assert series.indexers_supporting == []
        assert series.indexers_inferred == ["Nyaa.si"]
        assert series.indexer_support[0].matched_category_ids == [5070]

    async def test_inferred_when_only_a_parent_is_announced(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(1, "Nyaa.si", [5000])])
        by_type = await capabilities(prowlarr_settings, router)
        anime = by_type[MediaType.ANIME]
        assert anime.indexers_supporting == []
        assert anime.indexers_inferred == ["Nyaa.si"]
        assert anime.indexer_support[0].support is CategorySupportLevel.INFERRED
        assert any("inferida" in warning for warning in anime.warnings)

    async def test_indexer_without_categories_is_unknown(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(3, "SinCategorias", [], include_categories=False)])
        by_type = await capabilities(prowlarr_settings, router)
        movie = by_type[MediaType.MOVIE]
        assert movie.indexer_support[0].support is CategorySupportLevel.UNKNOWN
        assert movie.indexers_supporting == []
        assert any("compatibilidad es desconocida" in w for w in movie.warnings)

    async def test_unrelated_category_is_none_and_not_participating(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(2, "LinuxTracker", [4000])])
        by_type = await capabilities(prowlarr_settings, router)
        anime = by_type[MediaType.ANIME]
        assert anime.indexer_support[0].support is CategorySupportLevel.NONE
        assert anime.indexers_participating == []

    async def test_no_false_positive_for_declared_support(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[
                indexer(1, "Nyaa.si", [2000]),
                indexer(2, "LinuxTracker", [4000]),
            ]
        )
        by_type = await capabilities(prowlarr_settings, router)
        series = by_type[MediaType.SERIES]
        assert series.indexers_supporting == []
        assert series.indexers_participating == []

    async def test_id_outside_the_tree_is_not_inferred_as_newznab(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Un id 2500 (fuera del árbol) no es descendiente Newznab de 2000."""
        router = Router(
            indexers=[indexer(1, "IndexerRaro", [2500])],
            releases=[prowlarr_release(categories=[{"id": 2500}])],
        )
        by_type = await capabilities(prowlarr_settings, router)
        movie = by_type[MediaType.MOVIE]
        assert movie.indexer_support[0].support is CategorySupportLevel.NONE
        assert movie.indexers_inferred == []

        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.MOVIE)
        )
        assert result.results[0].categories[0].namespace is CategoryNamespace.UNKNOWN
        assert result.results[0].category_match is CategoryMatch.OFF_CATEGORY

    async def test_duplicate_categories_are_kept_per_namespace(self) -> None:
        """Mismo número en dos namespaces = dos categorías distintas."""
        from multimedia_search_mcp.models import ReleaseRecord
        from multimedia_search_mcp.normalization import normalize_record

        record = ReleaseRecord(
            title="x",
            categories=[
                CategoryRef(id=4000, namespace=CategoryNamespace.NEWZNAB),
                CategoryRef(
                    id=4000, namespace=CategoryNamespace.NATIVE, indexer="Nyaa.si"
                ),
            ],
        )
        result = normalize_record(record, media_type=MediaType.OTHER, provider="prowlarr")
        assert len(result.categories) == 2
        assert {category.namespace for category in result.categories} == {
            CategoryNamespace.NEWZNAB,
            CategoryNamespace.NATIVE,
        }

    async def test_disabled_indexer_is_never_counted(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[indexer(1, "Nyaa.si", [5000]), indexer(9, "Apagado", [5000], enabled=False)]
        )
        by_type = await capabilities(prowlarr_settings, router)
        assert by_type[MediaType.SERIES].indexers_supporting == ["Nyaa.si"]


# ---------------------------------------------------------------------------
# P4 — solapamiento semántico
# ---------------------------------------------------------------------------


class TestSemanticOverlap:
    async def test_capabilities_warn_about_semantic_filtering(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = build(prowlarr_settings, Router())
        result = await provider.get_capabilities()
        assert SEMANTIC_FILTER_WARNING in result.warnings

    async def test_each_type_explains_its_overlap(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        by_type = await capabilities(prowlarr_settings, Router())
        assert any("no una clasificación semántica" in w for w in by_type[MediaType.MOVIE].warnings)
        assert any("puede devolver anime" in w for w in by_type[MediaType.SERIES].warnings)
        assert any("descendiente de TV" in w for w in by_type[MediaType.ANIME].warnings)

    async def test_search_warns_once_about_overlap(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        provider = build(prowlarr_settings, Router(releases=[prowlarr_release()]))
        result = await provider.search(
            ProviderSearchRequest(query="one piece", media_type=MediaType.ANIME)
        )
        overlap = [w for w in result.warnings if "descendiente de TV" in w]
        assert len(overlap) == 1

    async def test_content_is_not_classified_from_the_title(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Un título con pinta de serie no altera el tipo ni las categorías."""
        release = prowlarr_release(
            title="One Piece S01E1080 1080p", categories=[{"id": 2000, "name": "Movies"}]
        )
        provider = build(prowlarr_settings, Router(releases=[release]))
        result = await provider.search(
            ProviderSearchRequest(query="one piece", media_type=MediaType.MOVIE)
        )
        record = result.results[0]
        assert record.structured_metadata == {}
        assert record.category_match is CategoryMatch.IN_SUBTREE
        assert record.categories[0].namespace is CategoryNamespace.NEWZNAB


# ---------------------------------------------------------------------------
# P5 — participación de indexadores
# ---------------------------------------------------------------------------


class TestIndexerParticipation:
    async def test_filtered_declaring_indexer_participates(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(1, "Nyaa.si", [2000])])
        movie = (await capabilities(prowlarr_settings, router))[MediaType.MOVIE]
        assert movie.indexers_supporting == ["Nyaa.si"]
        assert movie.indexers_participating == ["Nyaa.si"]
        assert any("participar no implica" in w for w in movie.warnings)

    async def test_filtered_non_declaring_indexer_does_not_participate(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(2, "LinuxTracker", [4000])])
        movie = (await capabilities(prowlarr_settings, router))[MediaType.MOVIE]
        assert movie.indexers_supporting == []
        assert movie.indexers_inferred == []
        assert movie.indexers_participating == []
        assert movie.indexer_support[0].support is CategorySupportLevel.NONE

    async def test_indexer_without_categories_is_not_a_participant(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(3, "SinCategorias", [], include_categories=False)])
        movie = (await capabilities(prowlarr_settings, router))[MediaType.MOVIE]
        assert movie.indexer_support[0].support is CategorySupportLevel.UNKNOWN
        assert movie.indexers_participating == []
        assert movie.indexers_unknown_state == []  # su `enable` sí se conoce

    async def test_uncategorized_results_still_identifiable(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """P2 sigue intacto: sin categorías el resultado es `uncategorized`."""
        router = Router(
            indexers=[indexer(1, "Nyaa.si", [2000])],
            releases=[prowlarr_release(categories=[])],
        )
        provider = build(prowlarr_settings, router)
        result = await provider.search(
            ProviderSearchRequest(query="x", media_type=MediaType.MOVIE)
        )
        assert result.results[0].category_match is CategoryMatch.UNCATEGORIZED
        assert router.search_calls == 1

    async def test_disabled_indexer_never_participates(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[
                indexer(1, "Nyaa.si", [2000]),
                indexer(3, "Apagado", [2000], enabled=False),
            ]
        )
        movie = (await capabilities(prowlarr_settings, router))[MediaType.MOVIE]
        assert movie.indexers_participating == ["Nyaa.si"]
        assert movie.indexers_supporting == ["Nyaa.si"]
        assert "Apagado" not in movie.indexers_participating

    async def test_unknown_enable_state_is_listed_but_not_a_participant(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(indexers=[indexer(9, "SinEstado", [2000], enabled=None)])
        movie = (await capabilities(prowlarr_settings, router))[MediaType.MOVIE]
        assert movie.indexers_participating == []
        assert movie.indexers_supporting == []
        assert movie.indexers_unknown_state == ["SinEstado"]
        assert movie.participation_known is False
        assert any("estado de habilitación desconocido" in w for w in movie.warnings)

    async def test_general_search_lists_only_confirmed_enabled(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[
                indexer(1, "Nyaa.si", [2000]),
                indexer(2, "LinuxTracker", [4000]),
                indexer(3, "Apagado", [5000], enabled=False),
                indexer(9, "SinEstado", [], enabled=None),
            ]
        )
        manga = (await capabilities(prowlarr_settings, router))[MediaType.MANGA]
        assert manga.mode == "general"
        assert manga.indexers_participating == ["LinuxTracker", "Nyaa.si"]
        assert manga.indexers_unknown_state == ["SinEstado"]
        assert manga.indexers_supporting == []
        assert manga.indexers_inferred == []

    async def test_indexer_listing_unavailable_means_unknown_participation(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        class Boom(Router):
            def __call__(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/api/v1/indexer":
                    return httpx.Response(500, json={"message": "boom"})
                return super().__call__(request)

        provider = build(prowlarr_settings, Boom())
        result = await provider.get_capabilities()
        for capability in result.search_types:
            assert capability.participation_known is False
            assert capability.indexers_participating == []
            assert capability.indexers_unknown_state == []

    async def test_support_inferred_is_not_confirmed_participation(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Anime 5070: un indexador que solo anuncia TV (5000) no es participante confirmado."""
        router = Router(indexers=[indexer(4, "SoloPadre", [5000])])
        anime = (await capabilities(prowlarr_settings, router))[MediaType.ANIME]
        assert anime.indexers_supporting == []
        assert anime.indexers_inferred == ["SoloPadre"]
        assert anime.indexers_participating == []
        assert any("no se puede confirmar" in w for w in anime.warnings)

    async def test_tree_unavailable_does_not_claim_unknown_categories(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(categories_status=500)
        movie = (await capabilities(prowlarr_settings, router))[MediaType.MOVIE]
        assert movie.indexers_participating == []
        assert movie.indexer_support == []
        assert any("Sin árbol" in w for w in movie.warnings)
        assert not any("no publican categorías" in w for w in movie.warnings)


# ---------------------------------------------------------------------------
# Cierre de Fase 2 — contrato de `participation_known`
# ---------------------------------------------------------------------------


class TestParticipationKnown:
    """`participation_known` = el estado de TODOS los indexadores relevantes se conoce."""

    async def test_all_enabled_is_known_and_complete(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[indexer(1, "Nyaa.si", [2000]), indexer(2, "LinuxTracker", [4000])]
        )
        by_type = await capabilities(prowlarr_settings, router)
        for capability in by_type.values():
            assert capability.participation_known is True
            assert capability.indexers_unknown_state == []
            assert set(capability.indexers_participating) <= {"LinuxTracker", "Nyaa.si"}

        assert by_type[MediaType.MOVIE].indexers_participating == ["Nyaa.si"]  # filtrada
        assert by_type[MediaType.MANGA].indexers_participating == [  # general
            "LinuxTracker",
            "Nyaa.si",
        ]

    async def test_one_unknown_state_makes_it_false(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[
                indexer(1, "Nyaa.si", [2000]),
                indexer(9, "SinEstado", [2000], enabled=None),
            ]
        )
        by_type = await capabilities(prowlarr_settings, router)
        for capability in by_type.values():
            assert capability.participation_known is False
            assert capability.indexers_unknown_state == ["SinEstado"]
        assert by_type[MediaType.MOVIE].indexers_participating == ["Nyaa.si"]
        assert by_type[MediaType.MANGA].indexers_participating == ["Nyaa.si"]

    async def test_mixed_states_keep_only_confirmed_participants(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[
                indexer(1, "Nyaa.si", [2000]),
                indexer(3, "Apagado", [2000], enabled=False),
                indexer(9, "SinEstado", [], enabled=None),
            ]
        )
        by_type = await capabilities(prowlarr_settings, router)
        movie = by_type[MediaType.MOVIE]
        assert movie.indexers_participating == ["Nyaa.si"]
        assert movie.indexers_unknown_state == ["SinEstado"]
        assert movie.participation_known is False
        assert by_type[MediaType.MANGA].participation_known is False

    async def test_unreadable_listing_is_unknown(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        class Boom(Router):
            def __call__(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/api/v1/indexer":
                    return httpx.Response(500, json={"message": "boom"})
                return super().__call__(request)

        result = await build(prowlarr_settings, Boom()).get_capabilities()
        for capability in result.search_types:
            assert capability.participation_known is False
            assert capability.indexers_participating == []
            # No se enumera lo que no se pudo leer.
            assert capability.indexers_unknown_state == []

    async def test_no_indexers_is_a_known_state(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        result = await build(prowlarr_settings, Router(indexers=[])).get_capabilities()
        assert any(
            "no tiene indexadores con habilitación confirmada" in w
            for w in result.warnings
        )
        for capability in result.search_types:
            assert capability.participation_known is True
            assert capability.indexers_participating == []
            assert capability.indexers_unknown_state == []

    async def test_unknown_state_is_preserved_in_every_type(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        router = Router(
            indexers=[
                indexer(9, "SinEstado", [2000], enabled=None),
                indexer(8, "OtroSinEstado", [5000], enabled=None),
            ]
        )
        by_type = await capabilities(prowlarr_settings, router)
        expected = ["OtroSinEstado", "SinEstado"]
        for capability in by_type.values():
            assert capability.indexers_unknown_state == expected
            assert capability.participation_known is False
            assert capability.indexers_participating == []
