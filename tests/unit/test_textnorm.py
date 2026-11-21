"""P9 Etapa C1: normalización textual de títulos."""

from __future__ import annotations

import pytest

from multimedia_search_mcp.textnorm import normalize_title


class TestExamples:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("The.Matrix.1999.1080p.BluRay", "the matrix 1999 1080p bluray"),
            ("THE MATRIX (1999) 1080P BluRay", "the matrix 1999 1080p bluray"),
            ("One Piece - S23E05", "one piece s23e05"),
            ("鬼滅の刃 1080p", "鬼滅の刃 1080p"),
        ],
    )
    def test_reference_examples(self, raw: str, expected: str) -> None:
        assert normalize_title(raw) == expected


class TestCaseAndSeparators:
    def test_casefold(self) -> None:
        assert normalize_title("MoViE TiTlE") == "movie title"

    def test_german_sharp_s(self) -> None:
        assert normalize_title("Straße") == normalize_title("STRASSE")

    def test_punctuation_becomes_space(self) -> None:
        assert normalize_title("Movie: The [Final] Cut!") == "movie the final cut"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("A.B_C-D/E\\F|G", "a b c d e f g"),
            ("A. B_ C- D/ E\\ F| G", "a b c d e f g"),
            ("A\tB\nC\r\nD", "a b c d"),
        ],
    )
    def test_separators_are_uniform(self, raw: str, expected: str) -> None:
        assert normalize_title(raw) == expected

    def test_consecutive_spaces_collapse(self) -> None:
        assert normalize_title("  Movie     Title   ") == "movie title"

    def test_leading_and_trailing_punctuation_is_dropped(self) -> None:
        assert normalize_title("[Group] Title (2024)") == "group title 2024"

    def test_apostrophe_and_hyphen_split_words(self) -> None:
        assert normalize_title("L'Enfant") == "l enfant"
        assert normalize_title("Spider-Man") == "spider man"

    def test_indicators_are_not_removed(self) -> None:
        assert normalize_title("Movie.1999.S02E05.Vol.03.Ch.4.1080p.x265.FLAC.VOSTFR.SUB") == (
            "movie 1999 s02e05 vol 03 ch 4 1080p x265 flac vostfr sub"
        )


class TestUnicode:
    def test_nfkc_full_width(self) -> None:
        assert normalize_title("ＭＯＶＩＥ　１９９９") == "movie 1999"

    def test_nfkc_ligature(self) -> None:
        assert normalize_title("ﬁnal") == "final"

    def test_nfkc_composed_accents(self) -> None:
        assert normalize_title("Cafe\u0301") == normalize_title("Café") == "café"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("僕のヒーローアカデミア", "僕のヒーローアカデミア"),
            ("進撃の巨人", "進撃の巨人"),
            ("진격의 거인", "진격의 거인"),
            ("हिन्दी फ़िल्म", "हिन्दी फ़िल्म"),
        ],
    )
    def test_scripts_are_preserved(self, raw: str, expected: str) -> None:
        assert normalize_title(raw) == expected

    def test_unknown_unicode_is_not_lost(self) -> None:
        assert normalize_title("Movie ★★ 2024") == "movie 2024"
        assert "★★★★" not in normalize_title("Movie ★★ 2024")

    def test_no_transliteration(self) -> None:
        assert normalize_title("鬼滅の刃") != "kimetsu no yaiba"


class TestEdgeCases:
    @pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
    def test_empty_inputs(self, raw: str | None) -> None:
        assert normalize_title(raw) == ""

    def test_result_has_no_outer_or_double_spaces(self) -> None:
        result = normalize_title("  ---Movie---   Title---  ")
        assert result == result.strip()
        assert "  " not in result

    @pytest.mark.parametrize(
        "raw",
        [
            "The.Matrix.1999.1080p.BluRay",
            "鬼滅の刃 1080p",
            "Weird___---   title",
            "Straße",
            "हिन्दी फ़िल्म",
            "",
        ],
    )
    def test_idempotent(self, raw: str) -> None:
        once = normalize_title(raw)
        assert normalize_title(once) == once
