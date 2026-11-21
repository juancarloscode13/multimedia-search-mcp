"""Correspondencia entre tipos multimedia y categorías del proveedor."""

from __future__ import annotations

import pytest
from conftest import category_tree

from multimedia_search_mcp.categories import (
    GENERAL_SEARCH_WARNING,
    MEDIA_TYPE_SPECS,
    spec_for,
    supported_media_types,
)
from multimedia_search_mcp.config import ProwlarrSettings
from multimedia_search_mcp.errors import InvalidRequestError
from multimedia_search_mcp.models import CategoryNamespace, CategoryRef, MediaType
from multimedia_search_mcp.providers.prowlarr import (
    ProwlarrProvider,
    flatten_categories,
    parent_category_id,
)


def tree() -> dict[int, str]:
    return flatten_categories(category_tree())


@pytest.fixture
def provider(prowlarr_settings: ProwlarrSettings) -> ProwlarrProvider:
    # No se crea cliente HTTP: _resolve_categories es pura.
    return ProwlarrProvider(prowlarr_settings)


class TestCatalogue:
    def test_all_documented_types_exist(self) -> None:
        assert supported_media_types() == (
            MediaType.MOVIE,
            MediaType.SERIES,
            MediaType.ANIME,
            MediaType.BOOK,
            MediaType.MANGA,
            MediaType.OTHER,
        )

    @pytest.mark.parametrize(
        ("media_type", "expected"),
        [
            (MediaType.MOVIE, (2000,)),
            (MediaType.SERIES, (5000,)),
            (MediaType.ANIME, (5070,)),
            (MediaType.BOOK, (7000,)),
        ],
    )
    def test_verified_mappings(self, media_type: MediaType, expected: tuple[int, ...]) -> None:
        spec = MEDIA_TYPE_SPECS[media_type]
        assert spec.category_ids == expected
        assert spec.mapping_verified is True
        assert spec.uses_general_search is False

    def test_manga_has_no_verified_category(self) -> None:
        spec = MEDIA_TYPE_SPECS[MediaType.MANGA]
        assert spec.category_ids == ()
        assert spec.mapping_verified is False
        assert spec.uses_general_search is True
        assert any("manga" in note.lower() for note in spec.notes)

    def test_anime_is_not_mapped_to_series_categories(self) -> None:
        assert MEDIA_TYPE_SPECS[MediaType.ANIME].category_ids != MEDIA_TYPE_SPECS[MediaType.SERIES].category_ids

    def test_spec_for_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError):
            spec_for("documentary")  # type: ignore[arg-type]


class TestFlatten:
    def test_flatten_includes_parents_and_children(self) -> None:
        flat = tree()
        assert flat[2000] == "Movies"
        assert flat[2045] == "Movies/UHD"
        assert flat[5070] == "TV/Anime"
        assert flat[7000] == "Books"

    def test_parent_category_id_convention(self) -> None:
        assert parent_category_id(5070) == 5000
        assert parent_category_id(2045) == 2000
        assert parent_category_id(5000) is None
        # Un id nativo/custom no tiene padre Newznab: no se inventa (130420 != 4000).
        assert parent_category_id(140679) is None
        assert parent_category_id(130420) is None


class TestResolveCategories:
    def test_movie_uses_verified_category(self, provider: ProwlarrProvider) -> None:
        ids, applied, warnings = provider._resolve_categories(
            spec_for(MediaType.MOVIE), (), tree()
        )
        assert ids == [2000]
        assert [category.id for category in applied] == [2000]
        assert applied[0].name == "Movies"
        assert applied[0].namespace is CategoryNamespace.NEWZNAB
        assert warnings == []

    def test_anime_uses_tv_anime(self, provider: ProwlarrProvider) -> None:
        ids, _, _ = provider._resolve_categories(spec_for(MediaType.ANIME), (), tree())
        assert ids == [5070]

    def test_manga_falls_back_to_general_search(self, provider: ProwlarrProvider) -> None:
        ids, applied, warnings = provider._resolve_categories(
            spec_for(MediaType.MANGA), (), tree()
        )
        assert ids == [] and applied == [] and warnings == []

    def test_missing_category_in_tree_degrades_to_general(self, provider: ProwlarrProvider) -> None:
        flat = tree()
        del flat[7000]
        ids, _, warnings = provider._resolve_categories(spec_for(MediaType.BOOK), (), flat)
        assert ids == []
        assert any("7000" in warning for warning in warnings)

    def test_user_numeric_category_wins(self, provider: ProwlarrProvider) -> None:
        ids, applied, _ = provider._resolve_categories(
            spec_for(MediaType.MOVIE), ("2040", "2045"), tree()
        )
        assert ids == [2040, 2045]
        assert [category.name for category in applied] == ["Movies/HD", "Movies/UHD"]

    def test_user_named_category_is_resolved(self, provider: ProwlarrProvider) -> None:
        ids, _, _ = provider._resolve_categories(spec_for(MediaType.OTHER), ("PC/ISO",), tree())
        assert ids == [4020]

    def test_user_category_name_is_case_insensitive(self, provider: ProwlarrProvider) -> None:
        ids, _, _ = provider._resolve_categories(spec_for(MediaType.OTHER), ("pc/iso",), tree())
        assert ids == [4020]

    def test_partial_leaf_name_must_be_unambiguous(self, provider: ProwlarrProvider) -> None:
        with pytest.raises(InvalidRequestError):
            # "HD" existe en Movies/HD y TV/HD.
            provider._resolve_categories(spec_for(MediaType.OTHER), ("HD",), tree())

    def test_unknown_category_name_raises(self, provider: ProwlarrProvider) -> None:
        with pytest.raises(InvalidRequestError) as excinfo:
            provider._resolve_categories(spec_for(MediaType.OTHER), ("PC/Misc",), tree())
        assert "PC/Misc" in str(excinfo.value)

    def test_duplicate_categories_are_collapsed(self, provider: ProwlarrProvider) -> None:
        ids, _, _ = provider._resolve_categories(
            spec_for(MediaType.OTHER), ("4020", "4020", "PC/ISO"), tree()
        )
        assert ids == [4020]

    def test_general_search_warning_constant_is_used(self) -> None:
        assert "no está garantizado" in GENERAL_SEARCH_WARNING
