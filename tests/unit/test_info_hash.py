"""P9 Etapa A: ``info_hash`` como campo de primera clase.

No hay detección de duplicados: solo canonicalización, validación y exposición
del dato que el proveedor entrega.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import category_tree, make_client, prowlarr_release, record

from multimedia_search_mcp.coercion import as_info_hash
from multimedia_search_mcp.config import ProwlarrSettings
from multimedia_search_mcp.models import (
    MediaType,
    ProviderSearchRequest,
    ReleaseRecord,
)
from multimedia_search_mcp.normalization import build_result_id, normalize_record
from multimedia_search_mcp.providers.prowlarr import ProwlarrProvider

HASH_LOWER = "d12cf502b1fe28d78e8123aa03a531a7e841c1a0"
HASH_UPPER = HASH_LOWER.upper()
HASH_V2 = "A" * 32 + "b" * 32  # 64 hex: BitTorrent v2 (BEP 52)


class TestCanonicalHash:
    def test_valid_lowercase_is_kept(self) -> None:
        assert as_info_hash(HASH_LOWER) == HASH_LOWER

    def test_valid_uppercase_is_lowercased(self) -> None:
        assert as_info_hash(HASH_UPPER) == HASH_LOWER

    def test_mixed_case_is_lowercased(self) -> None:
        assert as_info_hash("D12cf502B1FE28d78E8123aa03A531a7e841C1a0") == HASH_LOWER

    def test_surrounding_spaces_are_ignored(self) -> None:
        assert as_info_hash(f"  {HASH_UPPER}\n") == HASH_LOWER

    def test_v2_length_is_accepted_and_lowercased(self) -> None:
        assert as_info_hash(HASH_V2) == HASH_V2.lower()

    @pytest.mark.parametrize("value", [None, "", "   ", 0, [], {}, True])
    def test_missing_values_return_none(self, value: Any) -> None:
        assert as_info_hash(value) is None

    @pytest.mark.parametrize(
        "value",
        [
            HASH_LOWER[:-1],  # 39 hex
            HASH_LOWER + "0",  # 41 hex
            HASH_LOWER[:-1] + "g",  # carácter no hexadecimal
            "0x" + HASH_LOWER,  # prefijo, no hex puro
            "MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43U",  # base32: no se convierte
            "not-a-hash",
            HASH_LOWER + " extra",
        ],
    )
    def test_malformed_values_are_rejected(self, value: str) -> None:
        """Un valor inválido nunca se "arregla" para parecer un hash."""
        assert as_info_hash(value) is None


class TestModels:
    def test_release_record_defaults_to_none(self) -> None:
        assert ReleaseRecord(title="x").info_hash is None

    def test_media_result_carries_the_field(self) -> None:
        result = normalize_record(
            record(info_hash=HASH_UPPER), media_type=MediaType.MOVIE, provider="prowlarr"
        )
        assert result.info_hash == HASH_LOWER

    def test_media_result_without_hash(self) -> None:
        result = normalize_record(record(), media_type=MediaType.MOVIE, provider="prowlarr")
        assert result.info_hash is None


class _ReleaseRouter:
    """Transporte simulado: árbol de categorías + un único resultado."""

    def __init__(self, release: dict[str, Any]) -> None:
        self.release = release
        self.search_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/indexer/categories":
            return httpx.Response(200, json=category_tree())
        if path == "/api/v1/search":
            self.search_calls += 1
            return httpx.Response(200, json=[self.release])
        if path == "/api/v1/indexer":
            return httpx.Response(200, json=[])
        return httpx.Response(404)


async def _search_one(
    settings: ProwlarrSettings, release: dict[str, Any]
) -> tuple[Any, _ReleaseRouter]:
    router = _ReleaseRouter(release)
    provider = ProwlarrProvider(settings, client=make_client(router, settings))
    result = await provider.search(
        ProviderSearchRequest(query="debian", media_type=MediaType.OTHER)
    )
    return result, router


class TestProviderMapping:
    async def test_uppercase_infohash_is_canonicalised(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        result, _ = await _search_one(prowlarr_settings, prowlarr_release())
        assert result.results[0].info_hash == HASH_LOWER

    async def test_raw_value_is_preserved_in_extra(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        """El campo canónico no sustituye al dato original del proveedor."""
        result, _ = await _search_one(prowlarr_settings, prowlarr_release())
        normalized = normalize_record(
            result.results[0], media_type=MediaType.OTHER, provider="prowlarr"
        )
        assert normalized.provider_metadata["info_hash"] == HASH_UPPER
        assert normalized.info_hash == HASH_LOWER

    async def test_invalid_hash_is_not_promoted_but_raw_survives(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release(infoHash="no-es-un-hash")
        result, _ = await _search_one(prowlarr_settings, release)
        item = result.results[0]
        assert item.info_hash is None
        assert item.extra["info_hash"] == "no-es-un-hash"

    async def test_release_without_hash_is_compatible(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        release = prowlarr_release()
        release.pop("infoHash")
        result, router = await _search_one(prowlarr_settings, release)
        assert router.search_calls == 1
        assert result.results[0].info_hash is None
        assert "info_hash" not in result.results[0].extra

    async def test_null_hash_is_compatible(
        self, prowlarr_settings: ProwlarrSettings
    ) -> None:
        result, _ = await _search_one(prowlarr_settings, prowlarr_release(infoHash=None))
        assert result.results[0].info_hash is None


class TestNoBehaviourChanges:
    def test_result_id_does_not_depend_on_info_hash(self) -> None:
        without = record()
        with_hash = record(info_hash=HASH_UPPER)
        assert build_result_id("prowlarr", without) == build_result_id("prowlarr", with_hash)

    def test_normalized_result_id_is_unchanged(self) -> None:
        without = normalize_record(record(), media_type=MediaType.MOVIE, provider="prowlarr")
        with_hash = normalize_record(
            record(info_hash=HASH_LOWER), media_type=MediaType.MOVIE, provider="prowlarr"
        )
        assert without.result_id == with_hash.result_id
        assert with_hash.info_hash == HASH_LOWER

    def test_canonicalisation_is_idempotent(self) -> None:
        once = as_info_hash(HASH_UPPER)
        assert once is not None
        assert as_info_hash(once) == once
