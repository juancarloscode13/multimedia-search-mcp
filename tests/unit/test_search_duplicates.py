"""C3: integración de duplicados confirmados (B) y relaciones heurísticas (C2)."""

from __future__ import annotations

import itertools
from typing import Any

from conftest import FakeProvider, make_service, record

from multimedia_search_mcp.dedup import find_exact_duplicates
from multimedia_search_mcp.models import (
    DuplicateBasis,
    DuplicateGroup,
    DuplicateKind,
    DuplicateRelation,
    MediaResult,
    MediaType,
    SearchRequest,
    SearchResponse,
)
from multimedia_search_mcp.relations import compare_pair
from multimedia_search_mcp.services.search_service import MultimediaSearchService

HASH_A = "0ebe910cb906735255e0a2fda0fe123f6d3c903b"
HASH_B = "d12cf502b1fe28d78e8123aa03a531a7e841c1a0"


def release(
    title: str,
    *,
    size: int = 1000,
    info_hash: str | None = None,
    guid: str | None = None,
    indexer: str = "Nyaa.si",
    indexer_id: int = 1,
) -> Any:
    return record(
        title=title,
        guid=guid or f"guid:{title}:{size}:{info_hash}",
        info_hash=info_hash,
        indexer=indexer,
        indexer_id=indexer_id,
        size_bytes=size,
    )


async def search(records: list[Any], **request: Any) -> SearchResponse:
    service = make_service(FakeProvider(records=records))
    return await service.search(SearchRequest(query="x", **request))


def media(title: str, *, result_id: str, info_hash: str | None = None) -> MediaResult:
    return MediaResult(
        result_id=result_id,
        title=title,
        media_type=MediaType.OTHER,
        provider="prowlarr",
        size_bytes=1000,
        info_hash=info_hash,
    )


class TestBasicIntegration:
    async def test_same_info_hash_produces_a_confirmed_group(self) -> None:
        response = await search(
            [release("Movie 1080p", info_hash=HASH_A), release("Movie (otra vista)", info_hash=HASH_A)]
        )
        assert len(response.duplicate_groups) == 1
        group = response.duplicate_groups[0]
        assert group.kind is DuplicateKind.SAME_PUBLICATION
        assert group.basis is DuplicateBasis.INFO_HASH
        assert set(group.members) == {result.result_id for result in response.results}
        assert response.heuristic_relations == []

    async def test_heuristic_pair_produces_a_relation(self) -> None:
        response = await search([release("Movie 1080p"), release("Movie 720p")])
        assert response.duplicate_groups == []
        assert len(response.heuristic_relations) == 1
        relation = response.heuristic_relations[0]
        assert relation.kind.value == "variant"
        assert [dimension.value for dimension in relation.differences] == ["resolution"]

    async def test_unrelated_results_produce_empty_lists(self) -> None:
        response = await search([release("Alpha 1080p"), release("Beta 1080p")])
        assert response.duplicate_groups == []
        assert response.heuristic_relations == []

    async def test_empty_page(self) -> None:
        response = await search([])
        assert response.duplicate_groups == []
        assert response.heuristic_relations == []

    async def test_single_result(self) -> None:
        response = await search([release("Only One")])
        assert response.duplicate_groups == []
        assert response.heuristic_relations == []


class TestSuppression:
    async def test_pair_grouped_by_b_has_no_heuristic_relation(self) -> None:
        shared_guid = "guid-compartido"
        response = await search(
            [
                release("Movie 1080p", guid=shared_guid),
                release("Movie 720p", guid=shared_guid),
            ]
        )
        assert len(response.duplicate_groups) == 1
        assert response.duplicate_groups[0].basis is DuplicateBasis.INDEXER_GUID
        assert response.heuristic_relations == []

    async def test_three_member_group_suppresses_all_three_pairs(self) -> None:
        shared_guid = "guid-tres"
        response = await search(
            [
                release("Show 1080p", guid=shared_guid),
                release("Show 720p", guid=shared_guid),
                release("Show 480p", guid=shared_guid),
            ]
        )
        assert len(response.duplicate_groups) == 1
        assert response.duplicate_groups[0].size == 3
        assert response.heuristic_relations == []

    async def test_independent_relation_is_preserved(self) -> None:
        response = await search(
            [
                release("Movie 1080p", guid="g1"),
                release("Movie 720p", guid="g1"),
                release("Other 1080p", guid="g2"),
                release("Other 720p", guid="g3"),
            ]
        )
        assert len(response.duplicate_groups) == 1
        assert len(response.heuristic_relations) == 1
        relation = response.heuristic_relations[0]
        outsiders = {response.results[2].result_id, response.results[3].result_id}
        assert {relation.left_id, relation.right_id} == outsiders

    def test_suppression_does_not_depend_on_the_basis(self) -> None:
        left = media("Movie 1080p", result_id="prowlarr:1")
        right = media("Movie 720p", result_id="prowlarr:2")
        assert compare_pair(left, right) is not None

        for basis in (DuplicateBasis.INFO_HASH, DuplicateBasis.INDEXER_GUID):
            group = DuplicateGroup(
                group_id=f"dup:{basis.value}",
                relation=DuplicateRelation.CONFIRMED,
                kind=DuplicateKind.SAME_PUBLICATION
                if basis is DuplicateBasis.INFO_HASH
                else DuplicateKind.SAME_SOURCE_ENTRY,
                basis=basis,
                anchor_id=left.result_id,
                members=sorted([left.result_id, right.result_id]),
            )
            assert MultimediaSearchService._heuristic_relations([left, right], [group]) == []
            # Sin el grupo, la relación sí existe.
            assert len(MultimediaSearchService._heuristic_relations([left, right], [])) == 1

    async def test_relation_with_one_member_of_a_group_is_kept(self) -> None:
        """La supresión es por pareja: no se propaga a terceros."""
        shared = "guid-dos"
        response = await search(
            [
                release("Movie 1080p", guid=shared),
                release("Movie 720p", guid=shared),
                release("Movie 2160p", guid="guid-solo"),
            ]
        )
        assert len(response.duplicate_groups) == 1
        # El tercero no está en el grupo: sus relaciones con cada miembro sí se emiten.
        assert len(response.heuristic_relations) == 2
        group_members = set(response.duplicate_groups[0].members)
        solo = next(result.result_id for result in response.results if result.result_id not in group_members)
        for relation in response.heuristic_relations:
            assert solo in {relation.left_id, relation.right_id}


class TestNoTransitivity:
    async def test_relations_match_direct_pairwise_comparisons(self) -> None:
        response = await search(
            [
                release("Show 1080p", guid="a"),
                release("Show 720p", guid="b"),
                release("Show 480p", guid="c"),
            ]
        )
        expected = {
            relation.relation_id
            for left, right in itertools.combinations(response.results, 2)
            if (relation := compare_pair(left, right)) is not None
        }
        assert {relation.relation_id for relation in response.heuristic_relations} == expected

    async def test_no_heuristic_groups_are_created(self) -> None:
        response = await search(
            [
                release("Show 1080p", guid="a"),
                release("Show 720p", guid="b"),
                release("Show 480p", guid="c"),
            ]
        )
        assert response.duplicate_groups == []
        assert len(response.heuristic_relations) == 3
        for relation in response.heuristic_relations:
            assert not hasattr(relation, "members")
            assert relation.left_id != relation.right_id

    async def test_confirmed_groups_are_not_merged_with_relations(self) -> None:
        response = await search(
            [
                release("Movie 1080p", info_hash=HASH_A, guid="g1"),
                release("Movie 1080p (copia)", info_hash=HASH_A, guid="g2"),
                release("Movie 720p", guid="g3"),
            ]
        )
        assert len(response.duplicate_groups) == 1
        assert response.duplicate_groups[0].kind is DuplicateKind.SAME_PUBLICATION
        assert len(response.duplicate_groups[0].members) == 2
        # Las relaciones con el tercero no se han absorbido en el grupo.
        for relation in response.heuristic_relations:
            assert not set(response.duplicate_groups[0].members) >= {relation.left_id, relation.right_id}


class TestPaginationScope:
    async def test_duplicates_are_detected_within_the_page(self) -> None:
        response = await search(
            [
                release("Filler 1"),
                release("Movie 1080p", info_hash=HASH_A),
                release("Movie 1080p (copia)", info_hash=HASH_A),
                release("Filler 2"),
            ],
            limit=2,
            offset=1,
        )
        assert [result.title for result in response.results] == ["Movie 1080p", "Movie 1080p (copia)"]
        assert len(response.duplicate_groups) == 1

    async def test_related_results_in_different_pages_are_not_compared(self) -> None:
        records = [
            release("Movie 1080p", guid="a"),
            release("Filler", guid="b"),
            release("Movie 720p", guid="c"),
        ]
        first = await search(records, limit=2, offset=0)
        second = await search(records, limit=2, offset=2)
        assert first.heuristic_relations == []
        assert second.heuristic_relations == []
        assert first.duplicate_groups == [] and second.duplicate_groups == []

    async def test_all_references_belong_to_the_page(self) -> None:
        response = await search(
            [
                release("Movie 1080p", info_hash=HASH_A, guid="g1"),
                release("Movie 1080p (copia)", info_hash=HASH_A, guid="g2"),
                release("Other 1080p", guid="g3"),
                release("Other 720p", guid="g4"),
            ],
            limit=3,
        )
        page_ids = {result.result_id for result in response.results}
        for group in response.duplicate_groups:
            assert set(group.members) <= page_ids
        for relation in response.heuristic_relations:
            assert {relation.left_id, relation.right_id} <= page_ids

    async def test_pagination_fields_are_unchanged(self) -> None:
        records = [release(f"Title {index}", guid=f"g{index}") for index in range(10)]
        response = await search(records, limit=4, offset=2)
        assert response.received_count == 10
        assert response.returned_count == 4
        assert response.limit == 4
        assert response.offset == 2
        assert response.has_more is True
        assert response.next_offset == 6
        assert response.total_available is None
        assert response.truncated is None


class TestStability:
    async def test_ordering_is_deterministic_and_sorted(self) -> None:
        records = [
            release("Movie 1080p", info_hash=HASH_A, guid="g1"),
            release("Movie 1080p (copia)", info_hash=HASH_A, guid="g2"),
            release("Other 1080p", guid="g3"),
            release("Other 720p", guid="g4"),
        ]
        first = await search(records)
        second = await search(records)
        assert [group.group_id for group in first.duplicate_groups] == sorted(
            group.group_id for group in first.duplicate_groups
        )
        assert [relation.relation_id for relation in first.heuristic_relations] == sorted(
            relation.relation_id for relation in first.heuristic_relations
        )
        assert [group.group_id for group in first.duplicate_groups] == [
            group.group_id for group in second.duplicate_groups
        ]
        assert [relation.relation_id for relation in first.heuristic_relations] == [
            relation.relation_id for relation in second.heuristic_relations
        ]

    async def test_results_keep_their_order(self) -> None:
        records = [release(f"Title {index}", guid=f"g{index}") for index in range(5)]
        response = await search(records, limit=3, offset=1)
        assert [result.title for result in response.results] == ["Title 1", "Title 2", "Title 3"]

    async def test_results_are_not_mutated(self) -> None:
        records = [
            release("Movie 1080p", info_hash=HASH_A, guid="g1"),
            release("Movie 720p", guid="g2"),
        ]
        response = await search(records)
        assert response.results[0].title == "Movie 1080p"
        assert response.results[0].info_hash == HASH_A
        assert response.results[1].title == "Movie 720p"
        assert "guid" not in response.results[0].model_dump()

    async def test_b_and_c2_stay_independent(self) -> None:
        records = [
            release("Movie 1080p", info_hash=HASH_A, guid="g1"),
            release("Movie 1080p (copia)", info_hash=HASH_A, guid="g2"),
            release("Other 1080p", guid="g3"),
            release("Other 720p", guid="g4"),
        ]
        response = await search(records)
        page = list(response.results)
        assert {group.group_id for group in find_exact_duplicates(page)} == {
            group.group_id for group in response.duplicate_groups
        }
        direct = [
            relation
            for left, right in itertools.combinations(page, 2)
            if (relation := compare_pair(left, right)) is not None
        ]
        covered = {
            pair
            for group in response.duplicate_groups
            for pair in itertools.combinations(sorted(group.members), 2)
        }
        expected = {
            relation.relation_id
            for relation in direct
            if (relation.left_id, relation.right_id) not in covered
        }
        assert {relation.relation_id for relation in response.heuristic_relations} == expected


class TestSerialization:
    def test_empty_lists_are_serialised_as_empty_lists(self) -> None:
        from multimedia_search_mcp.server import _dump_search

        payload = _dump_search(SearchResponse(provider="fake", query="x", media_type=MediaType.OTHER))
        assert payload["duplicate_groups"] == []
        assert payload["heuristic_relations"] == []

    async def test_payload_contains_both_fields_without_internal_data(self) -> None:
        from multimedia_search_mcp.server import _dump_search

        response = await search(
            [
                release("Movie 1080p", info_hash=HASH_A, guid="g1"),
                release("Movie 1080p (copia)", info_hash=HASH_A, guid="g2"),
            ]
        )
        payload = _dump_search(response)
        assert "duplicate_groups" in payload and "heuristic_relations" in payload
        assert payload["duplicate_groups"][0]["kind"] == "same_publication"
        for key in ("guid", "indicators", "provider_metadata_hash"):
            assert key not in payload["results"][0]

    async def test_public_fields_are_preserved(self) -> None:
        previous = {
            "provider",
            "query",
            "media_type",
            "year",
            "applied_categories",
            "used_general_search",
            "received_count",
            "returned_count",
            "limit",
            "offset",
            "has_more",
            "next_offset",
            "total_available",
            "truncated",
            "per_indexer",
            "category_match_summary",
            "empty_result",
            "warnings",
            "results",
        }
        fields = set(SearchResponse.model_fields)
        assert previous <= fields
        assert fields - previous == {"duplicate_groups", "heuristic_relations"}
