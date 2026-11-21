"""Interfaz abstracta de proveedores y registro."""

from __future__ import annotations

import pytest
from conftest import FakeProvider

from multimedia_search_mcp.errors import ConfigurationError, UnknownProviderError
from multimedia_search_mcp.models import ProviderSearchRequest
from multimedia_search_mcp.providers.base import SearchProvider
from multimedia_search_mcp.providers.registry import ProviderRegistry


class TestAbstractInterface:
    def test_provider_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            SearchProvider()  # type: ignore[abstract]

    def test_incomplete_implementation_is_rejected(self) -> None:
        class Incomplete(SearchProvider):
            name = "incomplete"

            @property
            def configured(self) -> bool:
                return True

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_fake_provider_satisfies_contract(self, fake_provider: FakeProvider) -> None:
        assert isinstance(fake_provider, SearchProvider)
        assert fake_provider.configured is True
        assert fake_provider.missing_configuration == []
        info = fake_provider.describe()
        assert info.name == "fake"
        assert info.read_only is True
        assert info.configured is True

    def test_provider_reports_missing_configuration(self) -> None:
        provider = FakeProvider(configured=False, missing=["PROWLARR_API_KEY"])
        descriptor = provider.describe()
        assert descriptor.configured is False
        assert descriptor.missing_configuration == ["PROWLARR_API_KEY"]

    async def test_async_context_manager_closes_provider(self) -> None:
        provider = FakeProvider()
        async with provider as entered:
            assert entered is provider
        assert provider.closed is True

    async def test_search_receives_provider_request(self, fake_provider: FakeProvider) -> None:
        request = ProviderSearchRequest(query="dune", media_type="movie")  # type: ignore[arg-type]
        result = await fake_provider.search(request)
        assert fake_provider.requests == [request]
        assert len(result.results) == 1
        assert result.total_available is None


class TestRegistry:
    def test_register_and_get(self, fake_provider: FakeProvider) -> None:
        registry = ProviderRegistry([fake_provider])
        assert registry.get("fake") is fake_provider
        assert registry.names() == ["fake"]
        assert len(registry) == 1
        assert "fake" in registry

    def test_duplicate_name_is_rejected(self, fake_provider: FakeProvider) -> None:
        registry = ProviderRegistry([fake_provider])
        with pytest.raises(ValueError):
            registry.register(FakeProvider())

    def test_provider_without_name_is_rejected(self) -> None:
        class Nameless(FakeProvider):
            name = ""

        with pytest.raises(ValueError):
            ProviderRegistry([Nameless()])

    def test_unknown_provider_raises_with_available_names(self, fake_provider: FakeProvider) -> None:
        registry = ProviderRegistry([fake_provider])
        with pytest.raises(UnknownProviderError) as excinfo:
            registry.get("prowlarr")
        assert "fake" in str(excinfo.value)

    def test_default_requires_a_configured_provider(self) -> None:
        registry = ProviderRegistry([FakeProvider(configured=False, missing=["X"])])
        with pytest.raises(ConfigurationError):
            registry.default()

    def test_default_picks_first_configured_provider(self) -> None:
        class Disabled(FakeProvider):
            name = "disabled"

        configured = FakeProvider()
        registry = ProviderRegistry([Disabled(configured=False, missing=["X"]), configured])
        assert registry.default() is configured

    def test_describe_returns_all_providers(self) -> None:
        class Other(FakeProvider):
            name = "other"

        descriptors = ProviderRegistry([FakeProvider(), Other()]).describe()
        assert [descriptor.name for descriptor in descriptors] == ["fake", "other"]
