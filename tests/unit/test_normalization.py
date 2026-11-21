"""Normalización de resultados: general, por tipo y casos incompletos."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import record

from multimedia_search_mcp.models import CategoryRef, ExternalIds, MediaType
from multimedia_search_mcp.normalization import build_result_id, normalize_record, normalize_records

PROVIDER = "fake"


def norm(rec, media_type: MediaType = MediaType.MOVIE):  # type: ignore[no-untyped-def]
    return normalize_record(rec, media_type=media_type, provider=PROVIDER)


class TestGeneralFields:
    def test_common_fields_are_mapped(self) -> None:
        result = norm(record())
        assert result.title == "Dune Part Two 2024 2160p"
        assert result.provider == PROVIDER
        assert result.media_type is MediaType.MOVIE
        assert result.indexer == "TestIndexer"
        assert result.indexer_id == 1
        assert result.size_bytes == 1024
        assert result.seeders == 5
        assert result.leechers == 1
        assert result.categories == [CategoryRef(id=2045, name="Movies/UHD")]

    def test_publish_date_keeps_timezone(self) -> None:
        result = norm(record(publish_date=datetime(2026, 10, 25, 7, 0, tzinfo=UTC)))
        assert result.publish_date == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)

    def test_result_id_is_stable_and_opaque(self) -> None:
        rec = record(guid="magnet:?xt=urn:btih:abc&dn=title")
        first = build_result_id(PROVIDER, rec)
        second = build_result_id(PROVIDER, rec)
        assert first == second
        assert first.startswith("prowlarr:") or first.startswith("fake:")
        assert "abc" not in first.split(":")[1]
        assert build_result_id(PROVIDER, record(guid="other")) != first

    def test_duplicate_categories_are_collapsed(self) -> None:
        rec = record(
            categories=[
                CategoryRef(id=2000, name="Movies"),
                CategoryRef(id=2000, name="Movies"),
                CategoryRef(id=2040, name="Movies/HD"),
            ]
        )
        assert [category.id for category in norm(rec).categories] == [2000, 2040]

    def test_external_ids_that_are_empty_are_dropped(self) -> None:
        assert norm(record(external_ids=ExternalIds())).external_ids is None
        ids = norm(record(external_ids=ExternalIds(imdb="tt0133093"))).external_ids
        assert ids is not None and ids.imdb == "tt0133093"

    def test_provider_metadata_is_preserved(self) -> None:
        result = norm(record(extra={"age_days": 24, "file_name": "x.iso.torrent"}))
        assert result.provider_metadata == {"age_days": 24, "file_name": "x.iso.torrent"}


class TestLinks:
    def test_magnet_is_kept(self) -> None:
        magnet = "magnet:?xt=urn:btih:ABC&dn=title"
        assert norm(record(magnet_uri=magnet)).magnet_uri == magnet

    def test_proxy_url_with_api_key_is_dropped(self) -> None:
        proxy = "http://127.0.0.1:9696/2/download?apikey=secret-value&link=xyz"
        assert norm(record(magnet_uri=proxy)).magnet_uri is None

    def test_info_url_is_sanitized(self) -> None:
        result = norm(record(info_url="https://tracker.test/view/1?apikey=secret-value&id=7"))
        assert result.info_url is not None
        assert "secret-value" not in result.info_url
        assert "apikey" not in result.info_url
        assert "id=7" in result.info_url


class TestNoInference:
    """Regla clave: no se deducen datos técnicos del título ni del fichero."""

    @pytest.mark.parametrize(
        "media_type",
        [MediaType.MOVIE, MediaType.SERIES, MediaType.ANIME, MediaType.BOOK, MediaType.MANGA],
    )
    def test_descriptive_title_does_not_produce_metadata(self, media_type: MediaType) -> None:
        rec = record(
            title="Dune.2024.2160p.UHD.BluRay.x265.DTS-HD.MA.7.1.Episode.S02E05.epub",
            extra={"file_name": "Dune.S02E05.1080p.x264.mkv"},
            structured_metadata={},
        )
        result = norm(rec, media_type)
        assert result.video is None
        assert result.anime is None
        assert result.book is None
        assert result.manga is None

    def test_only_the_group_for_the_requested_type_is_filled(self) -> None:
        rec = record(
            structured_metadata={
                "resolution": "1080p",
                "authors": ["Frank Herbert"],
                "volume": 2,
            }
        )
        movie = norm(rec, MediaType.MOVIE)
        assert movie.video is not None and movie.video.resolution == "1080p"
        assert movie.book is None and movie.manga is None

        book = norm(rec, MediaType.BOOK)
        assert book.book is not None and book.book.authors == ["Frank Herbert"]
        assert book.video is None


class TestMovieAndSeriesMetadata:
    def test_video_metadata(self) -> None:
        rec = record(
            structured_metadata={
                "resolution": "2160p",
                "codec": "x265",
                "language": "en",
                "subtitles": ["es", "en"],
                "season": 2,
                "episode": 5,
            }
        )
        video = norm(rec, MediaType.SERIES).video
        assert video is not None
        assert (video.resolution, video.codec, video.language) == ("2160p", "x265", "en")
        assert video.subtitles == ["es", "en"]
        assert (video.season, video.episode) == (2, 5)

    def test_partial_video_metadata(self) -> None:
        video = norm(record(structured_metadata={"resolution": "1080p"})).video
        assert video is not None
        assert video.resolution == "1080p"
        assert video.season is None and video.episode is None
        assert video.subtitles == []


class TestAnimeMetadata:
    def test_anime_metadata(self) -> None:
        rec = record(
            structured_metadata={
                "original_title": "進撃の巨人",
                "alternative_titles": ["Attack on Titan", "Shingeki no Kyojin"],
                "season": 4,
                "episode": 28,
                "is_special": False,
                "language": "ja",
                "subtitles": ["es"],
                "fansub_group": "SubsPlease",
            }
        )
        anime = norm(rec, MediaType.ANIME).anime
        assert anime is not None
        assert anime.original_title == "進撃の巨人"
        assert anime.alternative_titles == ["Attack on Titan", "Shingeki no Kyojin"]
        assert (anime.season, anime.episode) == (4, 28)
        assert anime.is_special is False
        assert anime.fansub_group == "SubsPlease"

    def test_special_flag_is_coerced(self) -> None:
        anime = norm(record(structured_metadata={"is_special": "true"}), MediaType.ANIME).anime
        assert anime is not None and anime.is_special is True


class TestBookAndMangaMetadata:
    def test_book_metadata(self) -> None:
        rec = record(
            structured_metadata={
                "authors": ["Frank Herbert"],
                "publisher": "Chilton",
                "publication_year": 1965,
                "language": "en",
                "isbn": "9780441013593",
                "file_format": "EPUB",
            }
        )
        book = norm(rec, MediaType.BOOK).book
        assert book is not None
        assert book.authors == ["Frank Herbert"]
        assert book.publisher == "Chilton"
        assert book.publication_year == 1965
        assert book.isbn == "9780441013593"
        assert book.file_format == "EPUB"

    def test_single_author_string_is_wrapped(self) -> None:
        book = norm(record(structured_metadata={"author": "Ursula K. Le Guin"}), MediaType.BOOK).book
        assert book is not None and book.authors == ["Ursula K. Le Guin"]

    def test_manga_metadata(self) -> None:
        rec = record(
            structured_metadata={
                "original_title": "ワンピース",
                "alternative_titles": ["One Piece"],
                "authors": ["Eiichiro Oda"],
                "volume": 108,
                "chapter": 1100,
                "language": "ja",
                "file_format": "CBZ",
                "publication_status": "ongoing",
            }
        )
        manga = norm(rec, MediaType.MANGA).manga
        assert manga is not None
        assert manga.original_title == "ワンピース"
        assert manga.volume == 108 and manga.chapter == 1100
        assert manga.file_format == "CBZ"
        assert manga.publication_status == "ongoing"

    def test_missing_metadata_leaves_group_empty(self) -> None:
        assert norm(record(structured_metadata={}), MediaType.MANGA).manga is None


class TestIncompleteResponses:
    def test_record_without_optional_fields(self) -> None:
        result = normalize_record(
            record(
                indexer=None,
                indexer_id=None,
                size_bytes=None,
                seeders=None,
                leechers=None,
                grabs=None,
                publish_date=None,
                info_url=None,
                categories=[],
            ),
            media_type=MediaType.OTHER,
            provider=PROVIDER,
        )
        assert result.size_bytes is None
        assert result.seeders is None
        assert result.publish_date is None
        assert result.categories == []
        assert result.title == "Dune Part Two 2024 2160p"

    def test_metadata_with_unexpected_types_is_coerced(self) -> None:
        rec = record(
            structured_metadata={
                "resolution": 1080,
                "season": "2",
                "subtitles": "Español",
                "alternative_titles": [1, "One Piece", None],
                "authors": "Frank Herbert",
                "publication_year": "not-a-year",
            }
        )
        video = norm(rec, MediaType.SERIES).video
        assert video is not None
        assert video.resolution == "1080"
        assert video.season == 2
        assert video.subtitles == ["Español"]

        anime = norm(rec, MediaType.ANIME).anime
        assert anime is not None
        assert anime.alternative_titles == ["1", "One Piece"]

        book = norm(rec, MediaType.BOOK).book
        assert book is not None
        assert book.authors == ["Frank Herbert"]
        assert book.publication_year is None

    def test_normalize_records_preserves_order(self) -> None:
        results = normalize_records(
            [record(title="a", guid="1"), record(title="b", guid="2")],
            media_type=MediaType.OTHER,
            provider=PROVIDER,
        )
        assert [item.title for item in results] == ["a", "b"]

    def test_title_is_stripped(self) -> None:
        assert norm(record(title="  spaced title  ")).title == "spaced title"
