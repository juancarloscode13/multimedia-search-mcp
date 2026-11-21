"""P9 Etapa B: detección de duplicados exactos (hash y GUID de indexador)."""

from __future__ import annotations

import itertools

import pytest
from conftest import record

from multimedia_search_mcp.dedup import (
    find_exact_duplicates,
    group_by_info_hash,
    group_by_source_entry,
    same_indexer,
)
from multimedia_search_mcp.models import (
    DuplicateBasis,
    DuplicateKind,
    MediaType,
    MediaResult,
)
from multimedia_search_mcp.normalization import normalize_record

HASH_A = "0ebe910cb906735255e0a2fda0fe123f6d3c903b"  # caso real de Nyaa (Bleach 068)
HASH_B = "d12cf502b1fe28d78e8123aa03a531a7e841c1a0"  # caso real (LinuxTracker)

_counter = itertools.count()


def media(
    title: str | None = None,
    *,
    info_hash: str | None = None,
    guid: str | None = None,
    indexer: str | None = "Nyaa.si",
    indexer_id: int | None = 1,
    provider: str = "prowlarr",
) -> MediaResult:
    """``MediaResult`` realista; GUID único salvo que se indique."""
    suffix = next(_counter)
    resolved_title = title or f"Release {suffix}"
    return normalize_record(
        record(
            title=resolved_title,
            guid=guid if guid is not None else f"guid:{resolved_title}:{suffix}",
            info_hash=info_hash,
            indexer=indexer,
            indexer_id=indexer_id,
            size_bytes=1024 + suffix,
        ),
        media_type=MediaType.OTHER,
        provider=provider,
    )


class TestInfoHashRule:
    def test_same_hash_across_indexers(self) -> None:
        first = media("Same.Release", info_hash=HASH_A, indexer="Nyaa.si", indexer_id=1)
        second = media("Same.Release", info_hash=HASH_A, indexer="LinuxTracker", indexer_id=2)
        groups = find_exact_duplicates([first, second])
        assert len(groups) == 1
        group = groups[0]
        assert group.kind is DuplicateKind.SAME_PUBLICATION
        assert group.basis is DuplicateBasis.INFO_HASH
        assert group.members == sorted([first.result_id, second.result_id])

    def test_same_hash_within_one_indexer(self) -> None:
        first = media("Dup", info_hash=HASH_A)
        second = media("Dup (copy)", info_hash=HASH_A)
        assert first.indexer == second.indexer
        groups = group_by_info_hash([first, second])
        assert len(groups) == 1
        assert groups[0].size == 2

    def test_hash_case_is_canonicalised(self) -> None:
        first = media("Case", info_hash=HASH_A)
        second = media("Case", info_hash=HASH_A.upper())
        groups = find_exact_duplicates([first, second])
        assert len(groups) == 1
        assert groups[0].basis is DuplicateBasis.INFO_HASH

    def test_missing_hash_never_matches(self) -> None:
        first = media("No hash", info_hash=None)
        second = media("No hash either", info_hash=None)
        assert find_exact_duplicates([first, second]) == []

    def test_invalid_hash_never_matches(self) -> None:
        first = media("Bad", info_hash="not-a-hash")
        second = media("Bad too", info_hash="not-a-hash")
        assert find_exact_duplicates([first, second]) == []

    def test_different_hashes_are_unrelated(self) -> None:
        assert find_exact_duplicates(
            [media("One", info_hash=HASH_A), media("Two", info_hash=HASH_B)]
        ) == []

    def test_three_or_more_with_same_hash(self) -> None:
        results = [media(f"Pack {index}", info_hash=HASH_A) for index in range(4)]
        groups = find_exact_duplicates(results)
        assert len(groups) == 1
        assert groups[0].members == sorted(result.result_id for result in results)

    def test_distinct_result_ids_same_hash(self) -> None:
        first = media("Distinct", info_hash=HASH_A)
        second = media("Distinct", info_hash=HASH_A)
        assert first.result_id != second.result_id
        groups = find_exact_duplicates([first, second])
        assert groups[0].members == sorted([first.result_id, second.result_id])


class TestSourceEntryRule:
    def test_same_guid_and_indexer(self) -> None:
        first = media("Entry", guid="guid-shared")
        second = media("Entry (otra vista)", guid="guid-shared")
        groups = group_by_source_entry([first, second])
        assert len(groups) == 1
        assert groups[0].kind is DuplicateKind.SAME_SOURCE_ENTRY
        assert groups[0].basis is DuplicateBasis.INDEXER_GUID

    def test_same_guid_different_indexer_is_not_related(self) -> None:
        first = media("Entry", guid="guid-shared", indexer="Nyaa.si", indexer_id=1)
        second = media("Entry", guid="guid-shared", indexer="LinuxTracker", indexer_id=2)
        assert group_by_source_entry([first, second]) == []

    def test_same_guid_different_provider_is_not_related(self) -> None:
        first = media("Entry", guid="guid-shared", provider="prowlarr")
        second = media("Entry", guid="guid-shared", provider="otro")
        assert group_by_source_entry([first, second]) == []

    @pytest.mark.parametrize("guid", [None, "", "   "])
    def test_empty_or_missing_guid_never_matches(self, guid: str | None) -> None:
        first = media("No guid", guid=guid)
        second = media("No guid", guid=guid)
        assert group_by_source_entry([first, second]) == []

    def test_same_indexer_different_guid(self) -> None:
        first = media("A", guid="guid-1")
        second = media("B", guid="guid-2")
        assert group_by_source_entry([first, second]) == []

    def test_missing_indexer_id_falls_back_to_name(self) -> None:
        # Títulos distintos: si no, ambos serían la misma fila (mismo result_id).
        first = media("Entry", guid="guid-shared", indexer_id=None)
        second = media("Entry (otra vista)", guid="guid-shared", indexer_id=None)
        groups = group_by_source_entry([first, second])
        assert len(groups) == 1

    def test_indexer_identity_absent_is_not_confirmed(self) -> None:
        first = media("Entry", guid="guid-shared", indexer=None, indexer_id=None)
        second = media("Entry", guid="guid-shared", indexer=None, indexer_id=None)
        assert group_by_source_entry([first, second]) == []

    def test_conflicting_indexer_ids_are_not_confirmed(self) -> None:
        first = media("Entry", guid="guid-shared", indexer="Nyaa.si", indexer_id=1)
        second = media("Entry", guid="guid-shared", indexer="Nyaa.si", indexer_id=9)
        assert group_by_source_entry([first, second]) == []
        assert same_indexer(first, second) is False

    def test_same_id_but_contradictory_name_is_not_confirmed(self) -> None:
        first = media("Entry", guid="guid-shared", indexer="Nyaa.si", indexer_id=1)
        second = media("Entry", guid="guid-shared", indexer="Otra", indexer_id=1)
        assert same_indexer(first, second) is False
        assert group_by_source_entry([first, second]) == []


class TestGroups:
    def test_groups_are_deterministic_regardless_of_order(self) -> None:
        results = [
            media("A", info_hash=HASH_A),
            media("B", info_hash=HASH_A),
            media("C", info_hash=HASH_B),
            media("D", info_hash=HASH_B),
            media("E", guid="guid-e"),
            media("F", guid="guid-e"),
        ]
        forward = find_exact_duplicates(results)
        backward = find_exact_duplicates(list(reversed(results)))
        assert [group.group_id for group in forward] == [
            group.group_id for group in backward
        ]
        assert [group.members for group in forward] == [
            group.members for group in backward
        ]

    def test_member_is_not_repeated_within_a_group(self) -> None:
        first = media("Repeat", info_hash=HASH_A)
        second = media("Repeat", info_hash=HASH_A)
        groups = find_exact_duplicates([first, second, first, second])
        assert len(groups) == 1
        assert groups[0].members == sorted({first.result_id, second.result_id})

    def test_same_result_can_be_in_two_groups_with_different_basis(self) -> None:
        anchor = media("Anchor", info_hash=HASH_A, guid="guid-x")
        same_torrent = media("Torrent", info_hash=HASH_A, guid="guid-y")
        same_entry = media("Entry", info_hash=HASH_B, guid="guid-x")
        groups = find_exact_duplicates([anchor, same_torrent, same_entry])
        assert len(groups) == 2

        hash_group = next(g for g in groups if g.basis is DuplicateBasis.INFO_HASH)
        entry_group = next(g for g in groups if g.basis is DuplicateBasis.INDEXER_GUID)
        assert anchor.result_id in hash_group.members
        assert anchor.result_id in entry_group.members
        # Los pares son distintos: no se repite la misma relación.
        assert same_torrent.result_id not in entry_group.members
        assert same_entry.result_id not in hash_group.members

    def test_redundant_guid_group_is_not_emitted(self) -> None:
        first = media("Both", info_hash=HASH_A, guid="guid-same")
        second = media("Both (otra vista)", info_hash=HASH_A, guid="guid-same")
        groups = group_by_source_entry([first, second])
        assert len(groups) == 1  # aislado sí existe…
        merged = find_exact_duplicates([first, second])
        assert [group.basis for group in merged] == [DuplicateBasis.INFO_HASH]

    def test_groups_do_not_remove_or_reorder_results(self) -> None:
        results = [
            media("First", info_hash=HASH_A),
            media("Second", info_hash=HASH_B),
            media("Third", info_hash=HASH_A),
        ]
        snapshot = [result.result_id for result in results]
        find_exact_duplicates(results)
        assert [result.result_id for result in results] == snapshot
        assert len(results) == 3

    def test_single_member_groups_are_not_created(self) -> None:
        assert find_exact_duplicates([media("Only", info_hash=HASH_A)]) == []
        assert find_exact_duplicates([]) == []

    def test_basis_is_never_mixed_within_a_group(self) -> None:
        groups = find_exact_duplicates(
            [
                media("H1", info_hash=HASH_A),
                media("H2", info_hash=HASH_A),
                media("G1", guid="guid-g"),
                media("G2", guid="guid-g"),
            ]
        )
        by_basis = {group.basis: group for group in groups}
        assert set(by_basis) == {DuplicateBasis.INFO_HASH, DuplicateBasis.INDEXER_GUID}
        assert by_basis[DuplicateBasis.INFO_HASH].kind is DuplicateKind.SAME_PUBLICATION
        assert by_basis[DuplicateBasis.INDEXER_GUID].kind is DuplicateKind.SAME_SOURCE_ENTRY


class TestRealNyaaCase:
    def test_real_hash_on_two_entries_of_the_same_indexer(self) -> None:
        """El torrente real de Bleach 068 duplicado en Nyaa (dos entradas)."""
        first = media("[Naruto-Kun.Hu] Bleach - 068 [1080p].mkv", info_hash=HASH_A)
        second = media(
            "[Naruto-Kun.Hu] Bleach - 068 [1080p].mkv [copia]",
            info_hash=HASH_A,
        )
        groups = find_exact_duplicates([first, second])
        assert len(groups) == 1
        assert groups[0].kind is DuplicateKind.SAME_PUBLICATION
        assert groups[0].size == 2

    def test_byte_identical_rows_share_result_id_and_form_no_group(self) -> None:
        """Prowlarr repite filas idénticas: ya las colapsa `_dedupe` antes."""
        first = media("Identical", info_hash=HASH_A, guid="guid-identical")
        second = first.model_copy()
        assert first.result_id == second.result_id
        assert find_exact_duplicates([first, second]) == []


class TestPublicSurface:
    def test_guid_is_not_serialised(self) -> None:
        """El GUID es interno: no cambia el esquema público de los resultados."""
        result = media("Hidden", guid="guid-visible")
        assert result.guid == "guid-visible"
        assert "guid" not in result.model_dump()
        assert "guid" not in result.model_dump(mode="json", exclude_none=True)


class TestIndexerIdentityInvariants:
    def test_non_transitive_chain_never_forms_a_group(self) -> None:
        """A~B y B~C pero A≁C: no puede salir un grupo con los tres."""
        results = [
            media("A", guid="guid-chain", indexer="Alpha", indexer_id=1),
            media("B", guid="guid-chain", indexer="Alpha", indexer_id=None),
            media("C", guid="guid-chain", indexer="Alpha", indexer_id=2),
        ]
        assert same_indexer(results[0], results[1]) is True
        assert same_indexer(results[1], results[2]) is True
        assert same_indexer(results[0], results[2]) is False

        assert group_by_source_entry(results) == []
        assert find_exact_duplicates(results) == []

    def test_every_emitted_group_is_pairwise_compatible(self) -> None:
        results = [
            media("A1", guid="guid-mix", indexer="Alpha", indexer_id=1),
            media("A2", guid="guid-mix", indexer="Alpha", indexer_id=1),
            media("B1", guid="guid-mix", indexer="Beta", indexer_id=2),
            media("B2", guid="guid-mix", indexer="Beta", indexer_id=2),
            media("C", guid="guid-mix", indexer="Alpha", indexer_id=None),
        ]
        groups = find_exact_duplicates(results)
        by_id = {result.result_id: result for result in results}
        assert groups
        for group in groups:
            members = [by_id[result_id] for result_id in group.members]
            for left, right in itertools.combinations(members, 2):
                assert same_indexer(left, right) is True
        # C (sin id, nombre Alpha) se une al único grupo llamado Alpha.
        titles = sorted(sorted(by_id[m].title for m in group.members) for group in groups)
        assert titles == [["A1", "A2", "C"], ["B1", "B2"]]

    def test_ambiguous_name_is_not_attributed_to_one_id(self) -> None:
        """B no puede atribuirse a Alpha id 1 ni id 2: queda fuera de todo grupo."""
        results = [
            media("A", guid="guid-chain", indexer="Alpha", indexer_id=1),
            media("A2", guid="guid-chain", indexer="Alpha", indexer_id=1),
            media("B", guid="guid-chain", indexer="Alpha", indexer_id=None),
            media("C", guid="guid-chain", indexer="Alpha", indexer_id=2),
        ]
        groups = group_by_source_entry(results)
        assert len(groups) == 1
        assert results[2].result_id not in groups[0].members
        assert groups[0].size == 2

    def test_name_only_results_join_a_unique_id_group(self) -> None:
        results = [
            media("A", guid="guid-solo", indexer="Alpha", indexer_id=1),
            media("B", guid="guid-solo", indexer="Alpha", indexer_id=None),
            media("C", guid="guid-solo", indexer="Alpha", indexer_id=None),
        ]
        groups = group_by_source_entry(results)
        assert len(groups) == 1
        assert groups[0].members == sorted(result.result_id for result in results)

    def test_contradictory_names_block_the_whole_id_group(self) -> None:
        results = [
            media("A", guid="guid-bad", indexer="Alpha", indexer_id=1),
            media("B", guid="guid-bad", indexer="Beta", indexer_id=1),
        ]
        assert same_indexer(results[0], results[1]) is False
        assert group_by_source_entry(results) == []

    def test_non_transitive_scenario_is_stable_under_permutation(self) -> None:
        results = [
            media("A", guid="guid-perm", indexer="Alpha", indexer_id=1),
            media("B", guid="guid-perm", indexer="Alpha", indexer_id=None),
            media("C", guid="guid-perm", indexer="Alpha", indexer_id=2),
        ]
        signatures = set()
        for permutation in itertools.permutations(results):
            groups = group_by_source_entry(list(permutation))
            signatures.add(
                tuple(sorted((group.group_id, tuple(group.members)) for group in groups))
            )
        assert len(signatures) == 1


class TestGroupIdUniqueness:
    def test_independent_groups_with_same_guid_have_distinct_ids(self) -> None:
        """Mismo proveedor y GUID, dos grupos independientes por indexador."""
        results = [
            media("A1", guid="guid-two", indexer="Alpha", indexer_id=1),
            media("A2", guid="guid-two", indexer="Alpha", indexer_id=1),
            media("B1", guid="guid-two", indexer="Beta", indexer_id=2),
            media("B2", guid="guid-two", indexer="Beta", indexer_id=2),
        ]
        groups = group_by_source_entry(results)
        assert len(groups) == 2
        assert len({group.group_id for group in groups}) == 2
        assert {frozenset(group.members) for group in groups} == {
            frozenset(results[:2][i].result_id for i in range(2)),
            frozenset(results[2:][i].result_id for i in range(2)),
        }

    def test_group_ids_are_stable_when_input_is_reversed(self) -> None:
        results = [
            media("A1", guid="guid-two", indexer="Alpha", indexer_id=1),
            media("A2", guid="guid-two", indexer="Alpha", indexer_id=1),
            media("B1", guid="guid-two", indexer="Beta", indexer_id=2),
            media("B2", guid="guid-two", indexer="Beta", indexer_id=2),
        ]
        forward = group_by_source_entry(results)
        backward = group_by_source_entry(list(reversed(results)))
        assert [(g.group_id, g.members) for g in forward] == [
            (g.group_id, g.members) for g in backward
        ]

    def test_group_ids_differ_between_hash_and_guid_basis(self) -> None:
        results = [
            media("X1", info_hash=HASH_A, guid="guid-same"),
            media("X2", info_hash=HASH_A, guid="guid-same"),
        ]
        groups = find_exact_duplicates(results)
        assert len(groups) == 1  # el grupo GUID se suprime por redundante
        assert groups[0].basis is DuplicateBasis.INFO_HASH
