"""Cliente HTTP de Prowlarr: parámetros, cabeceras y manejo de errores."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import TEST_API_KEY, make_client

from multimedia_search_mcp.config import ProwlarrSettings
from multimedia_search_mcp.errors import (
    ConfigurationError,
    ProviderAuthError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from multimedia_search_mcp.providers.prowlarr import ProwlarrClient


def json_handler(status: int, payload: Any):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


class TestAuthentication:
    async def test_api_key_is_sent_as_header(self, prowlarr_settings: ProwlarrSettings) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["key"] = request.headers.get("X-Api-Key", "")
            captured["url"] = str(request.url)
            return httpx.Response(200, json=[])

        client = make_client(handler, prowlarr_settings)
        await client.search(query="debian")
        assert captured["key"] == TEST_API_KEY
        assert "apikey" not in captured["url"]
        assert TEST_API_KEY not in captured["url"]


class TestRequestParameters:
    async def test_categories_are_repeated_not_comma_joined(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=[])

        client = make_client(handler, prowlarr_settings)
        await client.search(query="dune", search_type="movie", categories=[2000, 2045])

        request = captured[0]
        assert request.url.params.get_list("categories") == ["2000", "2045"]
        assert "categories=2000%2C2045" not in str(request.url)
        assert request.url.params["query"] == "dune"
        assert request.url.params["type"] == "movie"

    async def test_no_category_parameter_when_empty(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=[])

        client = make_client(handler, prowlarr_settings)
        await client.search(query="linux", categories=[])
        assert "categories" not in captured[0].url.params
        assert "type" not in captured[0].url.params

    async def test_indexer_ids_are_repeated(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=[])

        client = make_client(handler, prowlarr_settings)
        await client.search(query="linux", indexer_ids=[1, 2])
        assert captured[0].url.params.get_list("indexerIds") == ["1", "2"]

    async def test_limit_and_offset_are_not_sent(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """Prowlarr no recorta el resultado agregado: se pagina en el servicio."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=[])

        client = make_client(handler, prowlarr_settings)
        await client.search(query="linux")
        assert "limit" not in captured[0].url.params
        assert "offset" not in captured[0].url.params


class TestErrorMapping:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_errors(self, prowlarr_settings: ProwlarrSettings, status: int) -> None:
        client = make_client(json_handler(status, {}), prowlarr_settings)
        with pytest.raises(ProviderAuthError) as excinfo:
            await client.get_system_status()
        message = str(excinfo.value)
        assert str(status) in message
        assert TEST_API_KEY not in message

    async def test_bad_request_includes_sanitized_detail(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        payload = {
            "title": "One or more validation errors occurred.",
            "errors": {"categories": ["The value '5000,5070' is not valid."]},
            "description": f"detalle con clave {TEST_API_KEY} y ruta /var/lib/prowlarr",
        }
        client = make_client(json_handler(400, payload), prowlarr_settings)
        with pytest.raises(ProviderRequestError) as excinfo:
            await client.search(query="x", categories=[5000, 5070])
        message = str(excinfo.value)
        assert "not valid" in message
        assert TEST_API_KEY not in message

    async def test_not_found(self, prowlarr_settings: ProwlarrSettings) -> None:
        client = make_client(json_handler(404, {}), prowlarr_settings)
        with pytest.raises(ProviderResponseError):
            await client.get_categories()

    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_server_errors(self, prowlarr_settings: ProwlarrSettings, status: int) -> None:
        client = make_client(json_handler(status, {}), prowlarr_settings)
        with pytest.raises(ProviderUnavailableError):
            await client.get_indexers()

    async def test_timeout(self, prowlarr_settings: ProwlarrSettings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("agotado", request=request)

        client = make_client(handler, prowlarr_settings)
        with pytest.raises(ProviderTimeoutError):
            await client.get_system_status()

    async def test_connection_error(self, prowlarr_settings: ProwlarrSettings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sin ruta", request=request)

        client = make_client(handler, prowlarr_settings)
        with pytest.raises(ProviderUnavailableError):
            await client.get_system_status()

    async def test_http_error_does_not_leak_key(self, prowlarr_settings: ProwlarrSettings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError(f"fallo con {TEST_API_KEY}", request=request)

        client = make_client(handler, prowlarr_settings)
        with pytest.raises(ProviderUnavailableError) as excinfo:
            await client.get_system_status()
        assert TEST_API_KEY not in str(excinfo.value)


class TestUnexpectedResponses:
    async def test_invalid_json(self, prowlarr_settings: ProwlarrSettings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>no json</html>")

        client = make_client(handler, prowlarr_settings)
        with pytest.raises(ProviderResponseError):
            await client.get_indexers()

    async def test_object_instead_of_list(self, prowlarr_settings: ProwlarrSettings) -> None:
        client = make_client(json_handler(200, {"indexers": []}), prowlarr_settings)
        with pytest.raises(ProviderResponseError):
            await client.get_indexers()

    async def test_list_with_invalid_items(self, prowlarr_settings: ProwlarrSettings) -> None:
        client = make_client(json_handler(200, ["texto"]), prowlarr_settings)
        with pytest.raises(ProviderResponseError):
            await client.search(query="x")

    async def test_list_instead_of_object(self, prowlarr_settings: ProwlarrSettings) -> None:
        client = make_client(json_handler(200, []), prowlarr_settings)
        with pytest.raises(ProviderResponseError):
            await client.get_system_status()

    async def test_empty_results_are_valid(self, prowlarr_settings: ProwlarrSettings) -> None:
        client = make_client(json_handler(200, []), prowlarr_settings)
        assert await client.search(query="nada") == []


class TestClientLifecycle:
    async def test_close_is_idempotent(self, prowlarr_settings: ProwlarrSettings) -> None:
        client: ProwlarrClient = make_client(json_handler(200, []), prowlarr_settings)
        await client.aclose()

    async def test_requires_configuration(self) -> None:
        with pytest.raises(ConfigurationError):
            ProwlarrClient(ProwlarrSettings(base_url=None, api_key=None))

    async def test_json_payload_is_parsed(self, prowlarr_settings: ProwlarrSettings) -> None:
        payload = {"version": "3.0.2.5780", "appName": "Prowlarr"}
        client = make_client(
            lambda request: httpx.Response(200, text=json.dumps(payload)),
            prowlarr_settings,
        )
        assert (await client.get_system_status())["version"] == "3.0.2.5780"
