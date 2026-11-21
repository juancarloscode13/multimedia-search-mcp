"""Normalización común de resultados, independiente del proveedor.

Convierte un :class:`ReleaseRecord` (neutro pero crudo) en un
:class:`MediaResult` (normalizado) sin conocer qué API lo ha producido.

Reglas deliberadas:

* Solo se rellenan campos que llegan de forma estructurada. Nunca se deduce
  resolución, códec, temporada, volumen ni formato a partir del título o del
  nombre del fichero: un dato inventado es peor que un dato ausente.
* Los campos específicos viven en grupos opcionales (``video``, ``anime``,
  ``book``, ``manga``) que el tipo de contenido selecciona.
* Los enlaces se sanean (Prowlarr incluye ``?apikey=...`` en sus enlaces de
  proxy, que aquí se descartan).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from .coercion import as_bool, as_info_hash, as_int, as_str, as_str_list
from .models import (
    AnimeMetadata,
    BookMetadata,
    CategoryRef,
    ExternalIds,
    MangaMetadata,
    MediaResult,
    MediaType,
    ReleaseRecord,
    VideoMetadata,
)
from .security import sanitize_url

__all__ = ["build_result_id", "normalize_record", "normalize_records"]

_MAGNET_PREFIX = "magnet:?"


def build_result_id(provider: str, record: ReleaseRecord) -> str:
    """Identificador estable y sin secretos para un resultado.

    Se usa un hash del GUID del proveedor (que en Prowlarr es el magnet completo,
    con decenas de trackers) en lugar del GUID en claro.
    """
    basis = "|".join(
        (
            provider,
            str(record.indexer_id) if record.indexer_id is not None else "",
            record.guid or "",
            record.title,
        )
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return f"{provider}:{digest[:16]}"


def _clean_url(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = sanitize_url(value.strip())
    return cleaned or None


def _pick(metadata: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in metadata and metadata[key] is not None:
            return metadata[key]
    return None


def _build_video(metadata: Mapping[str, Any]) -> VideoMetadata | None:
    video = VideoMetadata(
        resolution=as_str(_pick(metadata, "resolution")),
        codec=as_str(_pick(metadata, "codec", "video_codec")),
        language=as_str(_pick(metadata, "language")),
        subtitles=as_str_list(_pick(metadata, "subtitles")),
        season=as_int(_pick(metadata, "season")),
        episode=as_int(_pick(metadata, "episode")),
    )
    return None if video.is_empty else video


def _build_anime(metadata: Mapping[str, Any]) -> AnimeMetadata | None:
    anime = AnimeMetadata(
        original_title=as_str(_pick(metadata, "original_title")),
        alternative_titles=as_str_list(_pick(metadata, "alternative_titles")),
        season=as_int(_pick(metadata, "season")),
        episode=as_int(_pick(metadata, "episode")),
        is_special=as_bool(_pick(metadata, "is_special", "special"), lenient=True),
        language=as_str(_pick(metadata, "language")),
        subtitles=as_str_list(_pick(metadata, "subtitles")),
        fansub_group=as_str(_pick(metadata, "fansub_group", "release_group")),
    )
    return None if anime.is_empty else anime


def _build_book(metadata: Mapping[str, Any]) -> BookMetadata | None:
    book = BookMetadata(
        authors=as_str_list(_pick(metadata, "authors", "author")),
        publisher=as_str(_pick(metadata, "publisher")),
        publication_year=as_int(_pick(metadata, "publication_year", "year")),
        language=as_str(_pick(metadata, "language")),
        isbn=as_str(_pick(metadata, "isbn")),
        file_format=as_str(_pick(metadata, "file_format", "format")),
    )
    return None if book.is_empty else book


def _build_manga(metadata: Mapping[str, Any]) -> MangaMetadata | None:
    manga = MangaMetadata(
        original_title=as_str(_pick(metadata, "original_title")),
        alternative_titles=as_str_list(_pick(metadata, "alternative_titles")),
        authors=as_str_list(_pick(metadata, "authors", "author")),
        volume=as_int(_pick(metadata, "volume")),
        chapter=as_int(_pick(metadata, "chapter")),
        language=as_str(_pick(metadata, "language")),
        file_format=as_str(_pick(metadata, "file_format", "format")),
        publication_status=as_str(_pick(metadata, "publication_status", "status")),
    )
    return None if manga.is_empty else manga


def _normalized_categories(record: ReleaseRecord) -> list[CategoryRef]:
    # Clave (namespace, id): una categoría nativa y una Newznab pueden compartir
    # número y son categorías distintas.
    seen: set[tuple[str, int]] = set()
    categories: list[CategoryRef] = []
    for category in record.categories:
        key = (category.namespace.value, category.id)
        if key in seen:
            continue
        seen.add(key)
        categories.append(category)
    return categories


def normalize_record(
    record: ReleaseRecord,
    *,
    media_type: MediaType,
    provider: str,
) -> MediaResult:
    """Normaliza un registro crudo al modelo común de resultados multimedia."""
    metadata: Mapping[str, Any] = record.structured_metadata

    magnet = record.magnet_uri
    if magnet is not None and not magnet.startswith(_MAGNET_PREFIX):
        # Un enlace de proxy no es un magnet: no se propaga como tal.
        magnet = None

    external_ids = record.external_ids
    if external_ids is not None and external_ids.is_empty:
        external_ids = None

    video = anime = book = manga = None
    if media_type is MediaType.MOVIE or media_type is MediaType.SERIES:
        video = _build_video(metadata)
    elif media_type is MediaType.ANIME:
        anime = _build_anime(metadata)
    elif media_type is MediaType.BOOK:
        book = _build_book(metadata)
    elif media_type is MediaType.MANGA:
        manga = _build_manga(metadata)

    return MediaResult(
        result_id=build_result_id(provider, record),
        title=record.title.strip(),
        guid=record.guid,
        media_type=media_type,
        provider=provider,
        indexer=record.indexer,
        indexer_id=record.indexer_id,
        categories=_normalized_categories(record),
        size_bytes=record.size_bytes,
        seeders=record.seeders,
        leechers=record.leechers,
        grabs=record.grabs,
        publish_date=record.publish_date,
        protocol=record.protocol,
        info_url=_clean_url(record.info_url),
        # Idempotente: un adaptador que ya canonicalizó no cambia; uno que no,
        # queda corregido aquí (y un valor inválido nunca se propaga como hash).
        info_hash=as_info_hash(record.info_hash),
        magnet_uri=_clean_url(magnet),
        indexer_flags=list(record.indexer_flags),
        external_ids=external_ids,
        category_match=record.category_match,
        video=video,
        anime=anime,
        book=book,
        manga=manga,
        provider_metadata=dict(record.extra),
    )


def normalize_records(
    records: Iterable[ReleaseRecord],
    *,
    media_type: MediaType,
    provider: str,
) -> list[MediaResult]:
    """Normaliza una colección de registros respetando el orden recibido."""
    return [normalize_record(record, media_type=media_type, provider=provider) for record in records]
