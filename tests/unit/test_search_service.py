"""Servicio de búsqueda: validación, paginación, selección de proveedor."""

from __future__ import annotations

import pytest
from conftest import FakeProvider, make_service, record
from pydantic import ValidationError

from multimedia_search_mcp.errors import ConfigurationError, InvalidRequestError, UnknownProviderError
from multimedia_search_mcp.models import CategoryMatch, CategoryRef, MediaType, SearchRequest
from multimedia_search_mcp.providers.registry import ProviderRegistry
from multimedia_search_mcp.services.search_service import MultimediaSearchService


class TestValidation:
    def test_query_is_normalized(self) -> None:
        assert SearchRequest(query="  dune   part  two  ").query == "dune part two"

    @pytest.mark.parametrize("query", ["", "   ", "\n\t"])
    def test_empty_query_is_rejected(self, query: str) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query=query)

    @pytest.mark.parametrize("limit", [0, -1, 101])
    def test_limit_bounds(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="x", limit=limit)

    def test_offset_bounds(self) -> None:
        assert SearchRequest(query="x", offset=0).offset == 0
        with pytest.raises(ValidationError):
            SearchRequest(query="x", offset=-1)

    @pytest.mark.parametrize("year", [1800, 2200])
    def test_year_bounds(self, year: int) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="x", year=year)

    def test_invalid_media_type(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="x", media_type="documentary")  # type: ignore[arg-type]

    def test_categories_are_cleaned(self) -> None:
        request = SearchRequest(query="x", categories=(" 2040 ", "", "2040", "Movies/HD"))
        assert request.categories == ("2040", "Movies/HD")

    def test_too_many_categories(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="x", categories=tuple(str(i) for i in range(21)))

    def test_non_string_categories(self) -> None:
        with pytest.raises(ValidationError):
            SearchRequest(query="x", categories=(2040,))  # type: ignore[arg-type]


class TestPagination:
    async def test_limit_and_offset_are_applied_by_the_service(self) -> None:
        records = [record(title=f"r{i}", guid=f"g{i}") for i in range(10)]
        service = make_service(FakeProvider(records=records))

        response = await service.search(SearchRequest(query="x", limit=3, offset=2))
        assert [item.title for item in response.results] == ["r2", "r3", "r4"]
        # `total_available` queda desconocido: 10 es la ventana, no el total.
        assert response.total_available is None
        assert response.received_count == 10
        assert response.returned_count == 3
        assert response.has_more is True
        assert response.next_offset == 5

    async def test_end_of_window_does_not_claim_completeness(self) -> None:
        records = [record(title=f"r{i}", guid=f"g{i}") for i in range(5)]
        service = make_service(FakeProvider(records=records))
        response = await service.search(SearchRequest(query="x", limit=10, offset=0))
        assert response.returned_count == 5
        assert response.has_more is None
        assert response.next_offset is None
        assert any("no se puede garantizar" in warning for warning in response.warnings)

    async def test_offset_beyond_results(self) -> None:
        service = make_service(FakeProvider(records=[record()]))
        response = await service.search(SearchRequest(query="x", offset=5))
        assert response.results == []
        assert response.has_more is None
        assert any("offset" in warning for warning in response.warnings)

    async def test_empty_provider_result(self) -> None:
        service = make_service(FakeProvider(records=[]))
        response = await service.search(SearchRequest(query="x"))
        assert response.results == []
        assert response.received_count == 0
        assert response.has_more is None
        assert any("Respuesta vacía" in warning for warning in response.warnings)


class TestNormalizationIntegration:
    async def test_results_are_normalized(self) -> None:
        service = make_service(FakeProvider(records=[record()]))
        response = await service.search(SearchRequest(query="dune", media_type=MediaType.MOVIE))
        result = response.results[0]
        assert result.provider == "fake"
        assert result.media_type is MediaType.MOVIE
        assert result.result_id.startswith("fake:")

    async def test_duplicate_results_are_removed(self) -> None:
        duplicated = [record(guid="same"), record(guid="same"), record(guid="other")]
        service = make_service(FakeProvider(records=duplicated))
        response = await service.search(SearchRequest(query="x"))
        assert response.returned_count == 2

    async def test_received_count_is_counted_after_deduplication(self) -> None:
        """La ventana expuesta no puede incluir duplicados."""
        duplicated = [record(guid="same"), record(guid="same"), record(guid="other")]
        service = make_service(FakeProvider(records=duplicated))

        first = await service.search(SearchRequest(query="x", limit=1, offset=0))
        assert first.received_count == 2
        assert first.has_more is True
        assert first.next_offset == 1

        second = await service.search(SearchRequest(query="x", limit=1, offset=1))
        assert second.returned_count == 1
        assert second.has_more is None

    async def test_service_forwards_media_type_and_categories(self) -> None:
        provider = FakeProvider(records=[record()])
        service = make_service(provider)
        await service.search(
            SearchRequest(
                query="dune",
                media_type=MediaType.BOOK,
                year=1965,
                categories=("Books/EBook",),
            )
        )
        sent = provider.requests[0]
        assert sent.media_type is MediaType.BOOK
        assert sent.year == 1965
        assert sent.categories == ("Books/EBook",)

    async def test_provider_warnings_and_flags_are_preserved(self) -> None:
        provider = FakeProvider(
            records=[record(category_match=CategoryMatch.IN_SUBTREE)],
            warnings=["aviso uno", "aviso dos"],
            used_general_search=True,
            applied_categories=[CategoryRef(id=2000, name="Movies")],
        )
        service = make_service(provider)
        response = await service.search(SearchRequest(query="x"))
        assert response.warnings[:2] == ["aviso uno", "aviso dos"]
        assert response.used_general_search is True
        assert response.applied_categories == [CategoryRef(id=2000, name="Movies")]
        assert response.category_match_summary is not None
        assert response.category_match_summary.in_subtree == 1


class TestProviderSelection:
    async def test_unknown_provider(self) -> None:
        service = make_service(FakeProvider())
        with pytest.raises(UnknownProviderError):
            await service.search(SearchRequest(query="x", provider="inexistente"))

    async def test_provider_must_be_configured(self) -> None:
        class Disabled(FakeProvider):
            name = "disabled"

        service = make_service(Disabled(configured=False, missing=["PROWLARR_API_KEY"]))
        with pytest.raises(ConfigurationError) as excinfo:
            await service.search(SearchRequest(query="x", provider="disabled"))
        assert "PROWLARR_API_KEY" in str(excinfo.value)

    async def test_default_provider_without_configuration(self) -> None:
        service = make_service(FakeProvider(configured=False))
        with pytest.raises(ConfigurationError):
            await service.search(SearchRequest(query="x"))

    async def test_status_of_unconfigured_provider_is_returned(self) -> None:
        service = make_service(FakeProvider(configured=False))
        status = await service.get_provider_status()
        assert status.configured is False

    def test_security_secrets_are_collected_from_all_providers(self) -> None:
        class Second(FakeProvider):
            name = "second"

        service = make_service(
            FakeProvider(secrets=["clave-uno"]),
            Second(secrets=["clave-dos", "clave-uno"]),
        )
        assert service.security_secrets() == ("clave-uno", "clave-dos")

    def test_security_secrets_are_empty_without_configuration(self) -> None:
        assert make_service(FakeProvider()).security_secrets() == ()

    async def test_list_providers_never_needs_network(self) -> None:
        service = make_service(FakeProvider(configured=False, missing=["X"]))
        providers = service.list_providers()
        assert len(providers) == 1
        assert providers[0].configured is False


class TestLifecycle:
    async def test_aclose_closes_all_providers(self) -> None:
        class Second(FakeProvider):
            name = "second"

        first = FakeProvider()
        second = Second()
        service = MultimediaSearchService(ProviderRegistry([first, second]))
        await service.aclose()
        assert first.closed and second.closed
