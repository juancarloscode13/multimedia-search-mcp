"""Pruebas de protocolo: herramientas MCP reales sobre un proveedor falso.

Usa el cliente oficial del SDK v2 (``mcp.Client``) contra el servidor en
memoria: se ejercita el protocolo completo (list_tools + call_tool), no solo las
funciones Python.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from conftest import TEST_API_KEY, FakeProvider, make_service, record
from mcp import Client

from multimedia_search_mcp.errors import ProviderAuthError, ProviderUnavailableError
from multimedia_search_mcp.models import IndexerInfo, ProviderStatus
from multimedia_search_mcp.server import create_server

EXPECTED_TOOLS = {
    "search_media",
    "list_providers",
    "list_indexers",
    "get_provider_status",
    "get_search_capabilities",
}


def client_for(provider: FakeProvider) -> Client:
    server = create_server(make_service(provider))
    return Client(server)


async def call(client: Client, name: str, arguments: dict | None = None) -> dict:  # type: ignore[type-arg]
    result = await client.call_tool(name, arguments or {})
    assert result.structured_content is not None, result.content
    return result.structured_content


class TestToolDiscovery:
    async def test_exposes_the_five_tools(self, fake_provider: FakeProvider) -> None:
        async with client_for(fake_provider) as client:
            tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS

    async def test_tool_descriptions_are_documented(
        self, fake_provider: FakeProvider
    ) -> None:
        async with client_for(fake_provider) as client:
            tools = await client.list_tools()
        for tool in tools.tools:
            assert tool.description and len(tool.description) > 20, tool.name

    async def test_search_media_schema_has_the_expected_parameters(
        self, fake_provider: FakeProvider
    ) -> None:
        async with client_for(fake_provider) as client:
            tools = await client.list_tools()
        schema = next(tool for tool in tools.tools if tool.name == "search_media").input_schema
        assert set(schema["properties"]) == {
            "query",
            "media_type",
            "year",
            "categories",
            "limit",
            "offset",
            "provider",
        }
        assert "movie" in json.dumps(schema)

    async def test_only_read_only_tools_are_exposed(
        self, fake_provider: FakeProvider
    ) -> None:
        forbidden = ("download", "add", "delete", "remove", "grab", "pause", "resume", "config")
        async with client_for(fake_provider) as client:
            tools = await client.list_tools()
        for tool in tools.tools:
            assert not any(word in tool.name.lower() for word in forbidden), tool.name


class TestSearchMedia:
    async def test_successful_search(
        self, fake_provider: FakeProvider
    ) -> None:
        async with client_for(fake_provider) as client:
            payload = await call(
                client, "search_media", {"query": "dune", "media_type": "movie", "limit": 5}
            )
        assert payload["ok"] is True
        assert payload["provider"] == "fake"
        assert payload["media_type"] == "movie"
        assert payload["returned_count"] == 1
        assert payload["received_count"] == 1
        assert payload["total_available"] is None
        assert payload["has_more"] is None
        assert payload["results"][0]["title"] == "Dune Part Two 2024 2160p"
        assert payload["results"][0]["result_id"].startswith("fake:")

    async def test_arguments_reach_the_provider(
        self, fake_provider: FakeProvider
    ) -> None:
        async with client_for(fake_provider) as client:
            await call(
                client,
                "search_media",
                {
                    "query": "one piece",
                    "media_type": "manga",
                    "year": 1997,
                    "limit": 2,
                    "offset": 0,
                    "categories": ["7030"],
                },
            )
        request = fake_provider.requests[0]
        assert request.query == "one piece"
        assert request.media_type.value == "manga"
        assert request.year == 1997
        assert request.categories == ("7030",)

    async def test_invalid_parameters_return_structured_error(
        self, fake_provider: FakeProvider
    ) -> None:
        async with client_for(fake_provider) as client:
            payload = await call(client, "search_media", {"query": "x", "limit": 0})
        assert payload["ok"] is False
        assert payload["error"]["type"] == "InvalidRequestError"
        assert "limit" in payload["error"]["message"]

    async def test_provider_error_is_reported_without_secrets(self) -> None:
        provider = FakeProvider(
            secrets=[TEST_API_KEY],
            search_error=ProviderAuthError("fake", f"autenticación rechazada, clave {TEST_API_KEY}"),
        )
        async with client_for(provider) as client:
            payload = await call(client, "search_media", {"query": "x"})
        assert payload["ok"] is False
        assert payload["error"]["type"] == "ProviderAuthError"
        assert payload["error"]["provider"] == "fake"
        assert TEST_API_KEY not in json.dumps(payload)

    async def test_response_never_contains_the_api_key(self) -> None:
        leaking = record(
            info_url=f"https://tracker.test/view/1?apikey={TEST_API_KEY}",
            extra={"file_name": "x.torrent"},
        )
        provider = FakeProvider(records=[leaking])
        async with client_for(provider) as client:
            payload = await call(client, "search_media", {"query": "x"})
        serialized = json.dumps(payload)
        assert TEST_API_KEY not in serialized
        assert "apikey" not in serialized.lower()

    async def test_secret_declared_by_the_provider_is_redacted(self) -> None:
        """El servidor pide los secretos al servicio, no a Prowlarr."""
        leaking = record(title=f"titulo con {TEST_API_KEY} dentro")
        provider = FakeProvider(records=[leaking], secrets=[TEST_API_KEY])
        async with client_for(provider) as client:
            payload = await call(client, "search_media", {"query": "x"})
        assert TEST_API_KEY not in json.dumps(payload)
        assert "***" in json.dumps(payload)

    async def test_general_search_flag_is_exposed(self) -> None:
        provider = FakeProvider(records=[record()], used_general_search=True, warnings=["aviso"])
        async with client_for(provider) as client:
            payload = await call(client, "search_media", {"query": "x", "media_type": "manga"})
        assert payload["used_general_search"] is True
        assert payload["warnings"][0] == "aviso"
        # Sin filtro de categorías no se clasifica nada.
        assert payload["category_match_summary"] is None
        assert "category_match" not in payload["results"][0]


class TestOtherTools:
    async def test_list_providers(self, fake_provider: FakeProvider) -> None:
        async with client_for(fake_provider) as client:
            payload = await call(client, "list_providers")
        assert payload["ok"] is True
        assert payload["providers"][0]["name"] == "fake"
        assert payload["providers"][0]["read_only"] is True

    async def test_list_indexers(self) -> None:
        provider = FakeProvider(
            indexers=[IndexerInfo(id=1, name="Nyaa.si", enabled=True, privacy="public")]
        )
        async with client_for(provider) as client:
            payload = await call(client, "list_indexers")
        assert payload["ok"] is True
        assert payload["count"] == 1
        assert payload["indexers"][0]["name"] == "Nyaa.si"

    async def test_get_provider_status(self) -> None:
        provider = FakeProvider(
            status=ProviderStatus(
                provider="fake", configured=True, reachable=True, version="3.0.2.5780", latency_ms=5
            )
        )
        async with client_for(provider) as client:
            payload = await call(client, "get_provider_status")
        assert payload["status"]["reachable"] is True
        assert payload["status"]["version"] == "3.0.2.5780"

    async def test_status_of_unreachable_provider_is_not_an_error(self) -> None:
        provider = FakeProvider(
            status=ProviderStatus(
                provider="fake", configured=True, reachable=False, error="sin conexión"
            )
        )
        async with client_for(provider) as client:
            payload = await call(client, "get_provider_status")
        assert payload["ok"] is True
        assert payload["status"]["reachable"] is False

    async def test_get_search_capabilities(self) -> None:
        provider = FakeProvider()
        async with client_for(provider) as client:
            payload = await call(client, "get_search_capabilities")
        assert payload["ok"] is True
        assert payload["provider"] == "fake"
        assert payload["search_types"] == []

    async def test_unknown_provider_returns_error(self) -> None:
        async with client_for(FakeProvider()) as client:
            payload = await call(client, "get_search_capabilities", {"provider": "otro"})
        assert payload["ok"] is False
        assert payload["error"]["type"] == "UnknownProviderError"

    async def test_provider_unavailable_is_reported(
        self, fake_provider: FakeProvider
    ) -> None:
        fake_provider._search_error = ProviderUnavailableError("fake", "Prowlarr no responde")
        async with client_for(fake_provider) as client:
            payload = await call(client, "search_media", {"query": "x"})
        assert payload["error"]["type"] == "ProviderUnavailableError"
