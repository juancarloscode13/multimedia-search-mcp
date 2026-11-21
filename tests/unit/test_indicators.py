"""P9 Etapa C1: extracción conservadora de indicadores y sus modelos internos."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from multimedia_search_mcp.indicators import (
    Indicator,
    IndicatorMatch,
    IndicatorState,
    ReleaseIndicators,
    extract_indicators,
)
from multimedia_search_mcp.models import MediaResult

PRESENT = IndicatorState.PRESENT
ABSENT = IndicatorState.ABSENT
AMBIGUOUS = IndicatorState.AMBIGUOUS


def values(indicator: Indicator[Any]) -> list[Any]:
    return list(indicator.values)


class TestStructuralIndicators:
    def test_year(self) -> None:
        result = extract_indicators("The.Matrix.1999.1080p.BluRay")
        assert result.year.state is PRESENT
        assert values(result.year) == [1999]

    def test_year_range(self) -> None:
        assert values(extract_indicators("Show.2024").year) == [2024]
        assert values(extract_indicators("Show.1901").year) == [1901]

    def test_resolution_is_not_a_year(self) -> None:
        assert extract_indicators("Movie.1080p.2160p").year.state is ABSENT

    def test_year_outside_range_is_ignored(self) -> None:
        assert extract_indicators("Movie.1899").year.state is ABSENT
        assert extract_indicators("Movie.2100").year.state is ABSENT

    def test_year_inside_longer_number_is_ignored(self) -> None:
        assert extract_indicators("Movie.20199").year.state is ABSENT

    def test_scan_tag_dpi_is_not_a_year(self) -> None:
        result = extract_indicators("[Scan SP-1920] Movie 2001")
        assert result.year.state is PRESENT
        assert values(result.year) == [2001]

    def test_1920_as_a_real_movie_year(self) -> None:
        result = extract_indicators("Movie (1920)")
        assert result.year.state is PRESENT
        assert values(result.year) == [1920]

    def test_bare_1920_next_to_a_resolution_is_a_year(self) -> None:
        """Sin ``x`` no es una dimensión: ``1920`` es año, no ancho de vídeo."""
        result = extract_indicators("Movie.1920.1080p")
        assert values(result.year) == [1920]
        assert values(result.resolution) == ["1080p"]

    def test_video_dimension_is_not_a_year(self) -> None:
        result = extract_indicators("Movie.1920x1080")
        assert result.year.state is ABSENT
        assert result.season.state is ABSENT
        assert result.episode.state is ABSENT

    @pytest.mark.parametrize(
        "title",
        [
            "[Group1998] Some Show",
            "[SP-1920] Some Show",
            "[7B51F66C] Some Show",
            "[Release 2024 Group] Some Show",
        ],
    )
    def test_numbers_inside_labelled_tags_are_not_years(self, title: str) -> None:
        assert extract_indicators(title).year.state is ABSENT

    def test_numeric_only_tag_is_still_a_year(self) -> None:
        assert values(extract_indicators("Some Show [2024]").year) == [2024]

    def test_unbracketed_technical_marker_is_not_a_year(self) -> None:
        assert extract_indicators("Scan SP 1920 Manga").year.state is ABSENT
        assert values(extract_indicators("Scan SP 1920 Movie 2001").year) == [2001]

    def test_scan_dpi_does_not_break_a_real_year(self) -> None:
        result = extract_indicators("Batman (2001) [Scan SP-1920] [Manga FR]")
        assert result.year.state is PRESENT
        assert values(result.year) == [2001]

    def test_year_range_is_ambiguous_not_chosen(self) -> None:
        result = extract_indicators("Naruto (2003-2015) (Digital)")
        assert result.year.state is AMBIGUOUS
        assert values(result.year) == [2003, 2015]

    @pytest.mark.parametrize("title", ["Show.S02", "Show.S2", "Show season 2", "Show Season2"])
    def test_season_formats(self, title: str) -> None:
        assert values(extract_indicators(title).season) == [2]

    @pytest.mark.parametrize("title", ["Show.E05", "Show ep 5", "Show Episode 5", "Show.Episode5"])
    def test_episode_formats(self, title: str) -> None:
        assert values(extract_indicators(title).episode) == [5]

    def test_combined_sxxeyy(self) -> None:
        result = extract_indicators("One Piece - S23E05")
        assert values(result.season) == [23]
        assert values(result.episode) == [5]

    def test_combined_nxnn(self) -> None:
        result = extract_indicators("Show.2x05.1080p")
        assert values(result.season) == [2]
        assert values(result.episode) == [5]

    def test_resolution_pair_is_not_a_season(self) -> None:
        """``1920x1080`` no es temporada/episodio."""
        result = extract_indicators("Movie.1920x1080.pixels")
        assert result.season.state is ABSENT
        assert result.episode.state is ABSENT

    def test_volume(self) -> None:
        assert values(extract_indicators("Manga Vol. 03 [Digital]").volume) == [3]
        assert values(extract_indicators("Manga Volume 12").volume) == [12]

    def test_chapter(self) -> None:
        assert values(extract_indicators("Manga Chapter 105").chapter) == [105]
        assert values(extract_indicators("Manga Ch. 105").chapter) == [105]
        assert values(extract_indicators("Manga ch105").chapter) == [105]

    @pytest.mark.parametrize(
        "title",
        ["One Piece 1080", "Show 5 6 7", "Movie 3", "Release 2024 1080p"],
    )
    def test_bare_numbers_are_not_structural(self, title: str) -> None:
        result = extract_indicators(title)
        assert result.season.state is ABSENT
        assert result.episode.state is ABSENT
        assert result.volume.state is ABSENT
        assert result.chapter.state is ABSENT

    def test_absent_indicators_are_absent_not_inferred(self) -> None:
        result = extract_indicators("Some Random Title")
        assert result.year.state is ABSENT
        assert result.season.state is ABSENT
        assert result.episode.state is ABSENT
        assert result.year.values == ()


class TestAmbiguity:
    def test_conflicting_resolution(self) -> None:
        result = extract_indicators("Movie.720p.1080p")
        assert result.resolution.state is AMBIGUOUS
        assert values(result.resolution) == ["1080p", "720p"]
        assert result.resolution.single is None

    def test_conflicting_season_and_episode(self) -> None:
        result = extract_indicators("Movie.S01E02.S02E03")
        assert result.season.state is AMBIGUOUS
        assert values(result.season) == [1, 2]
        assert result.episode.state is AMBIGUOUS
        assert values(result.episode) == [2, 3]

    def test_conflicting_source(self) -> None:
        result = extract_indicators("Movie.BluRay.WEB-DL")
        assert result.source.state is AMBIGUOUS
        assert values(result.source) == ["bluray", "web-dl"]

    def test_conflicting_codec(self) -> None:
        result = extract_indicators("Movie.x264.x265")
        assert result.codec.state is AMBIGUOUS
        assert values(result.codec) == ["h264", "hevc"]

    def test_conflicting_year(self) -> None:
        result = extract_indicators("Show.1999.2024")
        assert result.year.state is AMBIGUOUS
        assert values(result.year) == [1999, 2024]

    def test_equivalent_repeats_stay_present(self) -> None:
        assert extract_indicators("Movie.1080p.1080p").resolution.state is PRESENT
        assert values(extract_indicators("Movie.1080p.1080p").resolution) == ["1080p"]

    def test_equivalent_codec_names_stay_present(self) -> None:
        result = extract_indicators("Movie.x265.HEVC")
        assert result.codec.state is PRESENT
        assert values(result.codec) == ["hevc"]

    def test_equivalent_subtitle_names_stay_present(self) -> None:
        result = extract_indicators("Movie.SUB.SUBS")
        assert result.subtitles.state is PRESENT
        assert values(result.subtitles) == ["sub"]

    def test_set_valued_attributes_admit_several_values(self) -> None:
        result = extract_indicators("Show.ENG.JPN.AAC.FLAC.DUB")
        assert result.language.state is PRESENT
        assert values(result.language) == ["eng", "jpn"]
        assert result.audio.state is PRESENT
        assert values(result.audio) == ["aac", "flac"]
        assert result.audio_mode.state is PRESENT


class TestTechnicalIndicators:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("480p", "480p"),
            ("720p", "720p"),
            ("1080p", "1080p"),
            ("2160p", "2160p"),
            ("4K", "2160p"),
        ],
    )
    def test_resolutions(self, token: str, expected: str) -> None:
        assert values(extract_indicators(f"Movie.{token}").resolution) == [expected]

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("WEB-DL", "web-dl"),
            ("WEBDL", "web-dl"),
            ("WEBRip", "webrip"),
            ("BluRay", "bluray"),
            ("Blu-Ray", "bluray"),
            ("BDRip", "bdrip"),
            ("DVD", "dvd"),
            ("DVDRip", "dvd"),
        ],
    )
    def test_sources(self, token: str, expected: str) -> None:
        assert values(extract_indicators(f"Movie.{token}.1080p").source) == [expected]

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("x264", "h264"),
            ("H.264", "h264"),
            ("AVC", "h264"),
            ("x265", "hevc"),
            ("H.265", "hevc"),
            ("HEVC", "hevc"),
            ("AV1", "av1"),
        ],
    )
    def test_codecs(self, token: str, expected: str) -> None:
        assert values(extract_indicators(f"Movie.{token}.1080p").codec) == [expected]

    @pytest.mark.parametrize("token", ["AAC", "FLAC", "DTS", "AC3"])
    def test_audio(self, token: str) -> None:
        assert values(extract_indicators(f"Movie.{token}").audio) == [token.lower()]

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("ESP", "esp"),
            ("ENG", "eng"),
            ("JPN", "jpn"),
            ("MULTi", "multi"),
            ("VOSTFR", "vostfr"),
        ],
    )
    def test_languages(self, token: str, expected: str) -> None:
        assert values(extract_indicators(f"Movie.{token}").language) == [expected]

    @pytest.mark.parametrize("token", ["SUB", "SUBS"])
    def test_subtitles(self, token: str) -> None:
        assert values(extract_indicators(f"Movie.{token}").subtitles) == ["sub"]

    def test_audio_modes(self) -> None:
        assert values(extract_indicators("Movie.DUB").audio_mode) == ["dub"]
        assert values(extract_indicators("Movie.Dual Audio").audio_mode) == ["dual"]
        both = extract_indicators("Movie.DUB.Dual Audio")
        assert both.audio_mode.state is PRESENT
        assert values(both.audio_mode) == ["dual", "dub"]

    def test_full_release_title(self) -> None:
        result = extract_indicators(
            "[SubsPlease] Sousou no Frieren - 12 (1080p) [HEVC].mkv"
        )
        assert result.normalized_title == "subsplease sousou no frieren 12 1080p hevc mkv"
        assert values(result.resolution) == ["1080p"]
        assert values(result.codec) == ["hevc"]
        assert result.year.state is ABSENT


class TestWordBoundaries:
    @pytest.mark.parametrize(
        ("title", "attribute"),
        [
            ("Movie.AV10", "codec"),
            ("Movie.dtsearch", "codec"),
            ("Movie.engram", "language"),
            ("Movie.multiplayer", "language"),
            ("Movie.subsidy", "subtitles"),
            ("Movie.flac2", "audio"),
            ("Movie.WEB-DLX", "source"),
            ("Movie.1080px", "resolution"),
        ],
    )
    def test_substrings_do_not_match(self, title: str, attribute: str) -> None:
        result = extract_indicators(title)
        indicator = getattr(result, attribute)
        assert indicator.state is ABSENT, f"{attribute} no debería coincidir en {title}"

    def test_unknown_indicators_are_preserved_in_normalized_title(self) -> None:
        result = extract_indicators("Some.Show.REPACK.PROPER.1080p")
        assert "repack" in result.normalized_title
        assert "proper" in result.normalized_title
        assert values(result.resolution) == ["1080p"]


class TestUnicodeAndEdges:
    def test_japanese_title(self) -> None:
        result = extract_indicators("鬼滅の刃 1080p")
        assert result.normalized_title == "鬼滅の刃 1080p"
        assert values(result.resolution) == ["1080p"]

    def test_chinese_title(self) -> None:
        result = extract_indicators("進撃の巨人 第01話 1080p")
        assert "進撃の巨人" in result.normalized_title
        assert values(result.resolution) == ["1080p"]

    def test_korean_title_with_season(self) -> None:
        result = extract_indicators("진격의 거인 S04E28 1080p")
        assert result.normalized_title.startswith("진격의 거인")
        assert values(result.season) == [4]
        assert values(result.episode) == [28]

    @pytest.mark.parametrize("title", [None, "", "   ", "---"])
    def test_empty_titles(self, title: str | None) -> None:
        result = extract_indicators(title)
        assert result.normalized_title == ""
        for attribute in (
            "year",
            "season",
            "episode",
            "volume",
            "chapter",
            "resolution",
            "source",
            "codec",
            "audio",
            "language",
            "subtitles",
            "audio_mode",
        ):
            assert getattr(result, attribute).state is ABSENT

    def test_normalized_input_gives_same_result(self) -> None:
        raw = "The.Matrix.1999.1080p.BluRay"
        from_normalized = extract_indicators(extract_indicators(raw).normalized_title)
        assert from_normalized.model_dump() == extract_indicators(raw).model_dump()


class TestProvenance:
    def test_matches_record_pattern_and_text(self) -> None:
        result = extract_indicators("Show.1999.S02E05")
        assert result.year.matches == (IndicatorMatch(pattern="year_4digit", text="1999"),)
        assert result.season.matches[0].pattern == "sxxeyy"
        assert result.season.matches[0].text == "s02e05"
        assert result.episode.matches[0].text == "s02e05"

    def test_word_patterns_are_identified(self) -> None:
        result = extract_indicators("Manga Volume 3 Chapter 12")
        assert result.volume.matches[0].pattern == "volume_word"
        assert result.chapter.matches[0].pattern == "chapter_word"

    def test_all_matches_are_kept_even_when_deduped(self) -> None:
        result = extract_indicators("Movie.1080p.1080P")
        assert values(result.resolution) == ["1080p"]
        assert len(result.resolution.matches) == 2


class TestIndicatorModel:
    def test_absent_by_default(self) -> None:
        indicator = Indicator[int]()
        assert indicator.state is ABSENT
        assert indicator.values == ()
        assert indicator.matches == ()
        assert indicator.is_absent is True
        assert indicator.single is None

    def test_present_requires_values(self) -> None:
        with pytest.raises(ValidationError):
            Indicator[int](state=PRESENT)

    def test_ambiguous_requires_two_values(self) -> None:
        with pytest.raises(ValidationError):
            Indicator[int](state=AMBIGUOUS, values=[1])

    def test_absent_rejects_values(self) -> None:
        with pytest.raises(ValidationError):
            Indicator[int](state=ABSENT, values=[1])

    def test_single_value_helper(self) -> None:
        indicator = Indicator[int](state=PRESENT, values=[2])
        assert indicator.single == 2
        multi = Indicator[int](state=AMBIGUOUS, values=[1, 2])
        assert multi.single is None
        assert multi.is_ambiguous is True

    def test_models_are_frozen(self) -> None:
        indicator = Indicator[int](state=PRESENT, values=[1])
        with pytest.raises(ValidationError):
            indicator.state = ABSENT  # type: ignore[misc]

    def test_release_indicators_is_detached_from_public_models(self) -> None:
        assert not (set(ReleaseIndicators.model_fields) & set(MediaResult.model_fields))
        assert "indicators" not in MediaResult.model_fields
        assert "normalized_title" not in MediaResult.model_fields


class TestScope:
    def test_module_does_not_depend_on_deduplication_or_records(self) -> None:
        source = Path(inspect.getfile(extract_indicators)).read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        forbidden = {"dedup", "normalization", "providers", "httpx", "asyncio"}
        assert not (imported & forbidden), f"imports prohibidos: {imported & forbidden}"
        assert not any(name.startswith(("httpx", "asyncio")) for name in imported)


class TestDeepImmutability:
    def test_collections_are_tuples(self) -> None:
        indicator = extract_indicators("Movie.1080p").resolution
        assert isinstance(indicator.values, tuple)
        assert isinstance(indicator.matches, tuple)

    def test_values_cannot_be_mutated(self) -> None:
        indicator = extract_indicators("Movie.1080p").resolution
        with pytest.raises(AttributeError):
            indicator.values.append("720p")  # type: ignore[attr-defined]
        with pytest.raises(TypeError):
            indicator.values[0] = "720p"  # type: ignore[index]

    def test_matches_cannot_be_mutated(self) -> None:
        indicator = extract_indicators("Movie.1080p").resolution
        with pytest.raises(AttributeError):
            indicator.matches.append(  # type: ignore[attr-defined]
                IndicatorMatch(pattern="x", text="y")
            )
        with pytest.raises(TypeError):
            indicator.matches[0] = IndicatorMatch(pattern="x", text="y")  # type: ignore[index]

    def test_match_model_is_frozen(self) -> None:
        match = IndicatorMatch(pattern="p", text="t")
        with pytest.raises(ValidationError):
            match.pattern = "otro"  # type: ignore[misc]

    def test_empty_indicator_collections_are_immutable(self) -> None:
        indicator = Indicator[int]()
        assert indicator.values == ()
        assert indicator.matches == ()

    def test_release_indicators_have_no_list_fields(self) -> None:
        result = extract_indicators("Movie.1999.1080p.x265")
        for name in ReleaseIndicators.model_fields:
            assert not isinstance(getattr(result, name), list), name
