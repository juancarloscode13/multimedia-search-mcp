"""P9 Etapa C2: comparación heurística por pares."""

from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError

from multimedia_search_mcp.dedup import find_exact_duplicates
from multimedia_search_mcp.indicators import extract_indicators
from multimedia_search_mcp.models import MediaType, MediaResult
from multimedia_search_mcp.relations import (
    HeuristicKind,
    HeuristicRelation,
    PairEvidence,
    PairRelation,
    TechnicalDimension,
    base_title,
    compare_pair,
)

HASH = "0ebe910cb906735255e0a2fda0fe123f6d3c903b"
_counter = itertools.count()

NINJA_1080 = "[Erai-raws] Ninja Batman [1080p][Multiple Subtitle].mkv"
NINJA_720 = "[Erai-raws] Ninja Batman [720p][Multiple Subtitle].mkv"
H265 = "Batman Ninja Vs Yakuza League 2025 MULTi 1080p AMZN WEB-DL DDP5.1 H.265-Tsundere-Raws"
H264 = "Batman Ninja Vs Yakuza League 2025 MULTi 1080p AMZN WEB-DL DDP5.1 H.264-Tsundere-Raws"


def media(
    title: str,
    *,
    size: int | None = 1000,
    info_hash: str | None = None,
    result_id: str | None = None,
    provider: str = "prowlarr",
) -> MediaResult:
    return MediaResult(
        result_id=result_id or f"prowlarr:{next(_counter):016x}",
        title=title,
        media_type=MediaType.OTHER,
        provider=provider,
        size_bytes=size,
        info_hash=info_hash,
    )


def kind_of(relation: PairRelation | None) -> str | None:
    return None if relation is None else relation.kind.value


def diffs(relation: PairRelation) -> list[str]:
    return [dimension.value for dimension in relation.differences]


class TestSameReleasePositive:
    def test_identical_title_and_size(self) -> None:
        relation = compare_pair(media("Some Release"), media("Some Release"))
        assert relation is not None
        assert relation.kind is HeuristicKind.SAME_RELEASE
        assert relation.relation is HeuristicRelation.POSSIBLE
        assert set(relation.evidence) == {PairEvidence.TITLE_NORMALIZED, PairEvidence.SIZE_EXACT}
        assert relation.differences == ()

    def test_punctuation_and_case_differences(self) -> None:
        relation = compare_pair(
            media("The.Matrix.1999.1080p.BluRay"),
            media("THE MATRIX (1999) 1080P BluRay"),
        )
        assert kind_of(relation) == "same_release"

    def test_absent_indicators_in_both(self) -> None:
        relation = compare_pair(media("Plain Title"), media("plain   title"))
        assert kind_of(relation) == "same_release"

    def test_structural_indicators_compatible(self) -> None:
        relation = compare_pair(
            media("Show S02E05 1080p"), media("Show S02E05 1080p")
        )
        assert relation is not None
        assert PairEvidence.STRUCTURE_MATCH in relation.evidence

    def test_real_duplicate_same_hash_is_left_to_b(self) -> None:
        """Sin hash compartido, mismo título y tamaño: heurística de release."""
        relation = compare_pair(
            media("ニンジャバットマン 第01-02巻 [Batman Ninja vol 01-02]", size=110624768),
            media("ニンジャバットマン 第01-02巻 [Batman Ninja vol 01-02]", size=110624768),
        )
        assert kind_of(relation) == "same_release"


class TestSameReleaseNegative:
    def test_different_titles(self) -> None:
        assert compare_pair(media("Movie One"), media("Movie Two")) is None

    @pytest.mark.parametrize(("left", "right"), [("", ""), ("", "Movie"), ("Movie", "")])
    def test_empty_titles(self, left: str, right: str) -> None:
        assert compare_pair(media(left), media(right)) is None

    def test_different_sizes(self) -> None:
        assert compare_pair(media("Movie", size=1000), media("Movie", size=2000)) is None

    @pytest.mark.parametrize(("left", "right"), [(None, 1000), (1000, None), (None, None)])
    def test_unknown_size(self, left: int | None, right: int | None) -> None:
        assert compare_pair(media("Movie", size=left), media("Movie", size=right)) is None

    def test_different_year(self) -> None:
        assert compare_pair(media("Movie 1999"), media("Movie 2024")) is None

    def test_different_season(self) -> None:
        assert compare_pair(media("Show S01E01"), media("Show S02E01")) is None

    def test_different_episode(self) -> None:
        assert compare_pair(media("Show S01E01"), media("Show S01E02")) is None

    def test_different_volume(self) -> None:
        assert compare_pair(media("Manga Vol. 01"), media("Manga Vol. 02")) is None

    def test_different_chapter(self) -> None:
        assert compare_pair(media("Manga Ch. 1"), media("Manga Ch. 2")) is None

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Movie 1080p", "Movie 720p"),
            ("Movie WEB-DL", "Movie BluRay"),
            ("Movie x264", "Movie x265"),
            ("Movie ENG", "Movie ESP"),
        ],
    )
    def test_technical_differences_are_not_same_release(self, left: str, right: str) -> None:
        relation = compare_pair(media(left), media(right))
        assert relation is None or relation.kind is not HeuristicKind.SAME_RELEASE

    def test_ambiguous_indicators_block_same_release(self) -> None:
        assert compare_pair(media("Movie 720p 1080p"), media("Movie 720p 1080p")) is None

    def test_ambiguous_year_blocks_relation(self) -> None:
        title = "Naruto (2003-2015) 1080p"
        assert compare_pair(media(title), media(title)) is None


class TestVariantPositive:
    def test_resolution_difference_real_example(self) -> None:
        relation = compare_pair(media(NINJA_1080, size=500), media(NINJA_720, size=200))
        assert relation is not None
        assert relation.kind is HeuristicKind.VARIANT
        assert diffs(relation) == ["resolution"]
        assert PairEvidence.BASE_TITLE in relation.evidence

    def test_source_difference(self) -> None:
        relation = compare_pair(
            media("Movie 2024 1080p WEB-DL"), media("Movie 2024 1080p BluRay")
        )
        assert relation is not None
        assert relation.kind is HeuristicKind.VARIANT
        assert diffs(relation) == ["source"]

    def test_codec_difference_real_example(self) -> None:
        relation = compare_pair(media(H265), media(H264))
        assert relation is not None
        assert relation.kind is HeuristicKind.VARIANT
        assert diffs(relation) == ["codec"]

    def test_language_difference(self) -> None:
        relation = compare_pair(media("Movie 1080p ENG"), media("Movie 1080p ESP"))
        assert relation is not None
        assert relation.kind is HeuristicKind.VARIANT
        assert diffs(relation) == ["language"]

    def test_audio_difference(self) -> None:
        relation = compare_pair(media("Movie 1080p AAC"), media("Movie 1080p FLAC"))
        assert relation is not None
        assert relation.kind is HeuristicKind.VARIANT
        assert diffs(relation) == ["audio"]

    def test_audio_mode_difference(self) -> None:
        relation = compare_pair(media("Movie 1080p DUB"), media("Movie 1080p Dual Audio"))
        assert relation is not None
        assert relation.kind is HeuristicKind.VARIANT
        assert diffs(relation) == ["audio_mode"]

    def test_several_differences_at_once(self) -> None:
        relation = compare_pair(
            media("Movie 2024 1080p BluRay x264 ENG"),
            media("Movie 2024 2160p WEB-DL x265 ESP"),
        )
        assert relation is not None
        assert set(diffs(relation)) == {"resolution", "source", "codec", "language"}

    def test_same_size_is_allowed_for_variants(self) -> None:
        relation = compare_pair(
            media("Movie 1080p", size=1000), media("Movie 720p", size=1000)
        )
        assert kind_of(relation) == "variant"
        assert relation is not None and relation.note is None

    def test_different_size_does_not_block_variant(self) -> None:
        relation = compare_pair(
            media("Movie 1080p", size=4000), media("Movie 720p", size=1000)
        )
        assert kind_of(relation) == "variant"
        assert relation is not None and relation.note is not None


class TestVariantNegative:
    def test_different_episodes(self) -> None:
        assert compare_pair(media("Show S23E04 1080p"), media("Show S23E05 720p")) is None

    def test_different_seasons(self) -> None:
        assert compare_pair(media("Show S02E01 1080p"), media("Show S03E01 720p")) is None

    def test_different_volumes(self) -> None:
        assert compare_pair(media("Manga Vol. 01 1080p"), media("Manga Vol. 02 720p")) is None

    def test_different_chapters(self) -> None:
        assert compare_pair(media("Manga Ch. 100 1080p"), media("Manga Ch. 101 720p")) is None

    def test_different_years(self) -> None:
        assert compare_pair(media("Movie 2024 1080p"), media("Movie 2025 720p")) is None

    def test_similar_titles_with_different_units(self) -> None:
        assert compare_pair(
            media("[Grp] Show EP01 1080p"), media("[Grp] Show EP02 720p")
        ) is None

    def test_absence_is_not_a_difference(self) -> None:
        assert compare_pair(media("Movie 1080p"), media("Movie")) is None

    def test_absence_of_subtitles_is_not_a_difference(self) -> None:
        assert compare_pair(media("Movie 1080p SUB"), media("Movie 1080p")) is None

    def test_ambiguous_difference_does_not_justify_variant(self) -> None:
        assert compare_pair(media("Movie 720p 1080p"), media("Movie 2160p")) is None

    def test_group_tag_difference_breaks_the_base(self) -> None:
        assert compare_pair(
            media("[GroupA] Movie 1080p"), media("[GroupB] Movie 720p")
        ) is None

    def test_unrecognised_difference_is_not_technical(self) -> None:
        assert compare_pair(
            media("Movie REPACK 1080p"), media("Movie PROPER 720p")
        ) is None

    def test_same_base_without_differences(self) -> None:
        assert compare_pair(media("Movie 1080p"), media("Movie 1080p")) is not None
        # Sin diferencia técnica no hay variante: aquí es same_release por título.
        assert compare_pair(media("Movie"), media("Movie")) is not None
        assert kind_of(compare_pair(media("Movie"), media("Movie"))) == "same_release"

    def test_title_made_only_of_technical_tokens(self) -> None:
        assert base_title(extract_indicators("1080p WEB-DL")) == ""
        assert compare_pair(media("1080p"), media("720p")) is None

    def test_episode_notation_difference_is_not_a_variant(self) -> None:
        assert compare_pair(media("One Piece EP1160 1080p"), media("One Piece S01E1160 720p")) is None


class TestSizeIndependence:
    def test_same_release_requires_known_identical_size(self) -> None:
        assert compare_pair(media("Movie", size=1), media("Movie", size=2)) is None
        assert compare_pair(media("Movie", size=None), media("Movie", size=1)) is None
        assert kind_of(compare_pair(media("Movie", size=1), media("Movie", size=1))) == "same_release"

    def test_variant_does_not_require_equal_sizes(self) -> None:
        assert kind_of(
            compare_pair(media("Movie 1080p", size=9), media("Movie 720p", size=1))
        ) == "variant"

    def test_size_alone_never_justifies_variant(self) -> None:
        assert compare_pair(media("Movie", size=9), media("Movie", size=1)) is None


class TestInvariance:
    def test_order_does_not_matter(self) -> None:
        left, right = media(NINJA_1080), media(NINJA_720)
        assert compare_pair(left, right) == compare_pair(right, left)

    def test_relation_identifiers_are_stable(self) -> None:
        left, right = media(H265), media(H264)
        first = compare_pair(left, right)
        second = compare_pair(right, left)
        third = compare_pair(left, right)
        assert first is not None and second is not None and third is not None
        assert first.relation_id == second.relation_id == third.relation_id
        assert first.relation_id.startswith("rel:")

    def test_pair_is_canonically_ordered(self) -> None:
        relation = compare_pair(media("Movie 1080p", result_id="zzz"), media("Movie 720p", result_id="aaa"))
        assert relation is not None
        assert (relation.left_id, relation.right_id) == ("aaa", "zzz")

    def test_inputs_are_not_modified(self) -> None:
        left = media(H265, info_hash=None)
        right = media(H264)
        before = (left.model_dump(), right.model_dump())
        compare_pair(left, right)
        assert (left.model_dump(), right.model_dump()) == before

    def test_same_result_id_never_relates(self) -> None:
        first = media("Movie 1080p", result_id="same")
        second = media("Movie 720p", result_id="same")
        assert compare_pair(first, second) is None

    def test_relation_holds_exactly_two_members(self) -> None:
        relation = compare_pair(media("Movie 1080p"), media("Movie 720p"))
        assert relation is not None
        assert {relation.left_id, relation.right_id} == {
            relation.left_id,
            relation.right_id,
        }
        assert relation.left_id != relation.right_id

    def test_no_transitive_grouping(self) -> None:
        """Tres variantes encadenadas no producen una estructura de grupo."""
        a, b, c = media("Show 1080p"), media("Show 720p"), media("Show 480p")
        ab, bc, ac = compare_pair(a, b), compare_pair(b, c), compare_pair(a, c)
        assert kind_of(ab) == kind_of(bc) == kind_of(ac) == "variant"
        ids = {ab.relation_id, bc.relation_id, ac.relation_id}  # type: ignore[union-attr]
        assert len(ids) == 3
        assert not hasattr(compare_pair(a, b), "members")


class TestSeparationFromConfirmed:
    def test_shared_info_hash_blocks_heuristics(self) -> None:
        assert compare_pair(
            media("Movie 1080p", info_hash=HASH), media("Movie 720p", info_hash=HASH)
        ) is None
        assert compare_pair(
            media("Movie", info_hash=HASH), media("Movie", info_hash=HASH)
        ) is None

    def test_dedup_keeps_confirming_the_same_pair(self) -> None:
        left = media("Movie", info_hash=HASH)
        right = media("Movie", info_hash=HASH, size=1000)
        assert compare_pair(left, right) is None
        groups = find_exact_duplicates([left, right])
        assert len(groups) == 1
        assert groups[0].kind.value == "same_publication"

    def test_heuristics_never_claim_confirmation(self) -> None:
        relation = compare_pair(media("Movie 1080p"), media("Movie 720p"))
        assert relation is not None
        assert relation.relation is HeuristicRelation.POSSIBLE


class TestRealisticFalsePositives:
    def test_same_manga_name_different_hashes_and_units(self) -> None:
        assert compare_pair(
            media("Bleach - 068 [1080p]", info_hash=HASH),
            media("Bleach - 069 [1080p]"),
        ) is None

    def test_shared_numbering_is_not_identity(self) -> None:
        assert compare_pair(
            media("Show 2x05 1080p"), media("Other Show 2x05 720p")
        ) is None

    def test_different_versions_of_the_same_work(self) -> None:
        assert compare_pair(
            media("Blade Runner 1982 Final Cut 1080p"),
            media("Blade Runner 1982 Director's Cut 720p"),
        ) is None

    def test_same_title_different_season_pack(self) -> None:
        assert compare_pair(
            media("Show S01 Complete 1080p"), media("Show S01E01 1080p")
        ) is None


class TestRelationModel:
    def test_requires_distinct_identifiers(self) -> None:
        with pytest.raises(ValidationError):
            PairRelation(
                relation_id="rel:x",
                left_id="a",
                right_id="a",
                kind=HeuristicKind.SAME_RELEASE,
                evidence=(PairEvidence.TITLE_NORMALIZED,),
            )

    def test_requires_canonical_order(self) -> None:
        with pytest.raises(ValidationError):
            PairRelation(
                relation_id="rel:x",
                left_id="z",
                right_id="a",
                kind=HeuristicKind.SAME_RELEASE,
                evidence=(PairEvidence.TITLE_NORMALIZED,),
            )

    def test_same_release_rejects_differences(self) -> None:
        with pytest.raises(ValidationError):
            PairRelation(
                relation_id="rel:x",
                left_id="a",
                right_id="b",
                kind=HeuristicKind.SAME_RELEASE,
                evidence=(PairEvidence.TITLE_NORMALIZED,),
                differences=(TechnicalDimension.RESOLUTION,),
            )

    def test_variant_requires_differences(self) -> None:
        with pytest.raises(ValidationError):
            PairRelation(
                relation_id="rel:x",
                left_id="a",
                right_id="b",
                kind=HeuristicKind.VARIANT,
                evidence=(PairEvidence.BASE_TITLE,),
            )

    def test_is_frozen(self) -> None:
        relation = compare_pair(media("Movie 1080p"), media("Movie 720p"))
        assert relation is not None
        with pytest.raises(ValidationError):
            relation.kind = HeuristicKind.SAME_RELEASE  # type: ignore[misc]


class TestBaseTitle:
    def test_removes_only_recognised_technical_tokens(self) -> None:
        indicators = extract_indicators(
            "[Erai-raws] Ninja Batman [1080p][Multiple Subtitle].mkv"
        )
        assert base_title(indicators) == "erai raws ninja batman multiple subtitle mkv"

    def test_keeps_structural_and_unknown_tokens(self) -> None:
        indicators = extract_indicators("Show S02E05 REPACK 1080p x265")
        assert base_title(indicators) == "show s02e05 repack"

    def test_empty_when_everything_is_technical(self) -> None:
        assert base_title(extract_indicators("1080p WEB-DL x265")) == ""

    def test_repeated_tokens_are_removed_once_each(self) -> None:
        indicators = extract_indicators("Movie 1080p 1080p")
        assert base_title(indicators) == "movie"


class TestSpanResolution:
    """Resolución determinista de fragmentos técnicos en el título base."""

    def test_overlap_is_resolved_by_first_claim(self) -> None:
        from multimedia_search_mcp.relations import _reserve_tokens

        tokens = ["a", "b", "c"]
        taken = [False, False, False]
        _reserve_tokens(tokens, taken, "a b")
        _reserve_tokens(tokens, taken, "b c")
        assert taken == [True, True, False]

    def test_repeated_match_does_not_remove_twice(self) -> None:
        from multimedia_search_mcp.relations import _reserve_tokens

        tokens = ["x", "1080p", "y"]
        taken = [False, False, False]
        _reserve_tokens(tokens, taken, "1080p")
        _reserve_tokens(tokens, taken, "1080p")
        assert taken == [False, True, False]

    def test_match_not_present_is_ignored(self) -> None:
        from multimedia_search_mcp.relations import _reserve_tokens

        tokens = ["movie", "1080p"]
        taken = [False, False]
        _reserve_tokens(tokens, taken, "2160p")
        assert taken == [False, False]

    def test_partial_token_is_not_removed(self) -> None:
        from multimedia_search_mcp.relations import _reserve_tokens

        tokens = ["1080px"]
        taken = [False]
        _reserve_tokens(tokens, taken, "1080p")
        assert taken == [False]


class TestStructuralAgreementInSameRelease:
    """Las reglas estructurales también se aplican a ``same_release``."""

    def test_year_inside_labelled_bracket_blocks_same_release(self) -> None:
        """C1 neutraliza el año en corchetes: mismo texto, extracción distinta."""
        bracketed = media("[Group 2024] Movie")
        plain = media("Group 2024 Movie")
        assert (
            extract_indicators(bracketed.title).normalized_title
            == extract_indicators(plain.title).normalized_title
        )
        assert extract_indicators(bracketed.title).year.is_absent
        assert extract_indicators(plain.title).year.values == (2024,)

        assert compare_pair(bracketed, plain) is None
        assert compare_pair(plain, bracketed) is None

    def test_year_present_absent_with_unit_evidence_is_allowed(self) -> None:
        """Año ambiguo por contexto pero temporada/episodio coinciden: admisible."""
        bracketed = media("[Group 2024] Show S01E01")
        plain = media("Group 2024 Show S01E01")
        assert extract_indicators(bracketed.title).year.is_absent

        relation = compare_pair(bracketed, plain)
        assert kind_of(relation) == "same_release"

    def test_valid_same_release_cases_still_work(self) -> None:
        assert kind_of(compare_pair(media("Movie 2024"), media("Movie (2024)"))) == "same_release"
        assert kind_of(compare_pair(media("Some Release"), media("Some Release"))) == "same_release"

    def test_variant_behaviour_is_unchanged(self) -> None:
        relation = compare_pair(media(NINJA_1080), media(NINJA_720))
        assert relation is not None and relation.kind is HeuristicKind.VARIANT

    def test_year_context_difference_blocks_variant_without_unit_evidence(self) -> None:
        bracketed = media("[Group 2024] Movie 1080p")
        plain = media("Group 2024 Movie 720p")
        assert compare_pair(bracketed, plain) is None
