"""Modelos de dominio compartidos por todos los proveedores.

Dos capas bien separadas:

* :class:`ReleaseRecord` — registro neutro que produce un adaptador. Solo
  contiene datos que la API del proveedor entrega de forma estructurada.
* :class:`MediaResult` — modelo común normalizado que se expone por MCP.

Los campos específicos por tipo de contenido son opcionales y solo se rellenan
si el proveedor aporta el dato de forma estructurada. No se deducen
características técnicas (resolución, códec, formato, temporada...) a partir del
título o del nombre del fichero.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "AnimeMetadata",
    "BookMetadata",
    "CategoryMatch",
    "CategoryMatchSummary",
    "CategoryNamespace",
    "CategoryRef",
    "CategorySupportLevel",
    "EmptyResultAssessment",
    "ExternalIds",
    "IndexerCategorySupport",
    "IndexerInfo",
    "IndexerStats",
    "IndexerWindowSummary",
    "MangaMetadata",
    "MediaResult",
    "MediaType",
    "MediaTypeCapability",
    "ProviderCapabilities",
    "ProviderInfo",
    "ProviderSearchRequest",
    "ProviderSearchResult",
    "ProviderStatus",
    "ReleaseRecord",
    "SearchRequest",
    "SearchResponse",
    "VideoMetadata",
    "WindowStatus",
]


class MediaType(StrEnum):
    """Tipos de contenido multimedia reconocidos por el servidor."""

    MOVIE = "movie"
    SERIES = "series"
    ANIME = "anime"
    BOOK = "book"
    MANGA = "manga"
    OTHER = "other"


class CategoryMatch(StrEnum):
    """Correspondencia entre las categorías de un resultado y el filtro aplicado.

    Solo se calcula cuando la búsqueda lleva filtro de categorías. Nunca se
    deduce del título, la descripción ni el nombre del fichero.
    """

    #: Alguna categoría del resultado pertenece al filtro o a su subárbol.
    IN_SUBTREE = "in_subtree"
    #: El resultado trae categorías, pero ninguna pertenece al filtro ni a su subárbol.
    OFF_CATEGORY = "off_category"
    #: El resultado no trae categorías verificables (lista vacía o nula).
    UNCATEGORIZED = "uncategorized"


class WindowStatus(StrEnum):
    """Fiabilidad de la ventana de resultados recibida de un proveedor.

    Ningún proveedor actual puede demostrar que su ventana esté completa ni
    parcial, así que el valor normal es ``UNKNOWN``. ``POSSIBLY_INCOMPLETE`` está
    reservado para un proveedor que aporte evidencia explícita de corte (por
    ejemplo, un cursor de paginación con más páginas); nunca se deduce de la
    capacidad declarada del indexador ni del número de resultados recibidos.
    """

    #: No hay evidencia suficiente para afirmar nada sobre la completitud.
    UNKNOWN = "unknown"
    #: El proveedor ha aportado evidencia explícita de que la ventana puede estar
    #: limitada. Solo lo fija un adaptador con esa evidencia; Prowlarr no la tiene.
    POSSIBLY_INCOMPLETE = "possibly_incomplete"


class CategoryNamespace(StrEnum):
    """Namespace de una categoría.

    Prowlarr mezcla el árbol Newznab (global) con categorías propias de cada
    indexador. Un id nativo **no** es portable entre indexadores ni descendiente
    automático de una categoría Newznab, aunque su número lo parezca.
    """

    #: Categoría del árbol Newznab, compartida por todo el sistema.
    NEWZNAB = "newznab"
    #: Categoría propia de un indexador concreto (``indexer`` la identifica).
    NATIVE = "native"
    #: No se puede determinar el namespace con seguridad.
    UNKNOWN = "unknown"


class CategorySupportLevel(StrEnum):
    """Cómo se relaciona un indexador con la categoría solicitada.

    Distingue lo que el indexador declara de lo que solo se puede inferir por la
    jerarquía Newznab, para no presentar una inferencia como soporte confirmado.
    """

    #: Anuncia exactamente la categoría solicitada.
    DECLARED = "declared"
    #: No anuncia la categoría, pero sí un padre o un descendiente suyo.
    INFERRED = "inferred"
    #: Anuncia categorías, pero ninguna relacionada con la solicitada.
    NONE = "none"
    #: No hay datos de categorías del indexador.
    UNKNOWN = "unknown"


class CategoryRef(BaseModel):
    """Categoría de un proveedor, con su namespace y atribución.

    ``name`` solo se rellena cuando el proveedor lo aporta (o el árbol Newznab lo
    conoce); nunca se inventa para una categoría nativa desconocida.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    name: str | None = None
    namespace: CategoryNamespace = CategoryNamespace.UNKNOWN
    #: Indexador al que pertenece la categoría. Solo para ``namespace == native``.
    indexer: str | None = None
    indexer_id: int | None = None


class ExternalIds(BaseModel):
    """Identificadores externos que el proveedor entrega explícitamente."""

    model_config = ConfigDict(frozen=True)

    imdb: str | None = None
    tmdb: str | None = None
    tvdb: str | None = None
    tvmaze: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.imdb, self.tmdb, self.tvdb, self.tvmaze))


class VideoMetadata(BaseModel):
    """Metadatos de películas y series."""

    resolution: str | None = None
    codec: str | None = None
    language: str | None = None
    subtitles: list[str] = Field(default_factory=list)
    season: int | None = None
    episode: int | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.resolution, self.codec, self.language, self.subtitles, self.season, self.episode)
        )


class AnimeMetadata(BaseModel):
    """Metadatos específicos de anime."""

    original_title: str | None = None
    alternative_titles: list[str] = Field(default_factory=list)
    season: int | None = None
    episode: int | None = None
    is_special: bool | None = None
    language: str | None = None
    subtitles: list[str] = Field(default_factory=list)
    fansub_group: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.original_title,
                self.alternative_titles,
                self.season,
                self.episode,
                self.is_special,
                self.language,
                self.subtitles,
                self.fansub_group,
            )
        )


class BookMetadata(BaseModel):
    """Metadatos específicos de libros y publicaciones."""

    authors: list[str] = Field(default_factory=list)
    publisher: str | None = None
    publication_year: int | None = None
    language: str | None = None
    isbn: str | None = None
    file_format: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.authors,
                self.publisher,
                self.publication_year,
                self.language,
                self.isbn,
                self.file_format,
            )
        )


class MangaMetadata(BaseModel):
    """Metadatos específicos de manga."""

    original_title: str | None = None
    alternative_titles: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    volume: int | None = None
    chapter: int | None = None
    language: str | None = None
    file_format: str | None = None
    publication_status: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.original_title,
                self.alternative_titles,
                self.authors,
                self.volume,
                self.chapter,
                self.language,
                self.file_format,
                self.publication_status,
            )
        )


class ReleaseRecord(BaseModel):
    """Resultado crudo pero ya neutro, tal y como lo entrega un adaptador.

    ``structured_metadata`` transporta únicamente campos que la API del
    proveedor devuelve de forma explícita; la normalización común los mapea a
    los modelos de metadatos cuando existen.

    ``category_match`` lo calcula el adaptador (solo él conoce su árbol de
    categorías) y vale ``None`` cuando la búsqueda no llevaba filtro.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    guid: str | None = None
    #: Hash de información BitTorrent canónico (minúsculas) si el proveedor lo
    #: da en formato válido: 40 hex (BTIH v1) o 64 hex (v2). ``None`` si falta o
    #: no es válido; el valor crudo, si existe, queda en ``extra``.
    info_hash: str | None = None
    indexer: str | None = None
    indexer_id: int | None = None
    size_bytes: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    grabs: int | None = None
    publish_date: datetime | None = None
    info_url: str | None = None
    magnet_uri: str | None = None
    protocol: str | None = None
    indexer_flags: list[str] = Field(default_factory=list)
    categories: list[CategoryRef] = Field(default_factory=list)
    external_ids: ExternalIds | None = None
    category_match: CategoryMatch | None = None
    structured_metadata: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class MediaResult(BaseModel):
    """Resultado normalizado, independiente del proveedor."""

    result_id: str
    title: str
    #: GUID del proveedor para esta entrada. **No se serializa** (``exclude``):
    #: solo lo usa la detección interna de duplicados por entrada de origen, que
    #: necesita distinguir «misma entrada de un indexador» de «mismo torrent».
    #: No se interpreta como magnet ni se extraen hashes de él.
    guid: str | None = Field(default=None, exclude=True)
    #: Tipo solicitado en la búsqueda. El proveedor no clasifica semánticamente.
    media_type: MediaType
    provider: str
    indexer: str | None = None
    indexer_id: int | None = None
    categories: list[CategoryRef] = Field(default_factory=list)
    size_bytes: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    grabs: int | None = None
    publish_date: datetime | None = None
    protocol: str | None = None
    info_url: str | None = None
    #: Hash de información BitTorrent canónico (minúsculas), 40 hex (BTIH v1) o
    #: 64 hex (v2). Dos resultados comparten torrent solo si coincide; ``None``
    #: significa «no disponible o no válido», nunca «sin hash» deducido del texto.
    #: El valor crudo original sigue en ``provider_metadata["info_hash"]``.
    info_hash: str | None = None
    magnet_uri: str | None = None
    indexer_flags: list[str] = Field(default_factory=list)
    external_ids: ExternalIds | None = None
    #: Solo cuando la búsqueda llevaba filtro de categorías (ver :class:`CategoryMatch`).
    category_match: CategoryMatch | None = None
    video: VideoMetadata | None = None
    anime: AnimeMetadata | None = None
    book: BookMetadata | None = None
    manga: MangaMetadata | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class IndexerStats(BaseModel):
    """Estadísticas que el proveedor publique para un indexador."""

    average_response_time_ms: int | None = None
    queries: int | None = None
    failed_queries: int | None = None
    grabs: int | None = None
    failed_grabs: int | None = None


class IndexerInfo(BaseModel):
    """Indexador configurado en el proveedor."""

    id: int | None = None
    name: str
    enabled: bool | None = None
    protocol: str | None = None
    privacy: str | None = None
    supports_search: bool | None = None
    supports_rss: bool | None = None
    categories: list[CategoryRef] = Field(default_factory=list)
    stats: IndexerStats | None = None


class ProviderInfo(BaseModel):
    """Proveedor registrado y su estado de configuración."""

    name: str
    configured: bool
    read_only: bool = True
    missing_configuration: list[str] = Field(default_factory=list)
    capabilities_summary: str | None = None


class ProviderStatus(BaseModel):
    """Estado de conectividad del proveedor."""

    provider: str
    configured: bool
    reachable: bool
    version: str | None = None
    latency_ms: int | None = None
    indexers_enabled: int | None = None
    error: str | None = None


class IndexerCategorySupport(BaseModel):
    """Nivel de soporte de un indexador concreto para una categoría solicitada."""

    indexer: str
    indexer_id: int | None = None
    support: CategorySupportLevel = CategorySupportLevel.UNKNOWN
    #: Categorías del indexador que han motivado el nivel (declarada o inferida).
    matched_category_ids: list[int] = Field(default_factory=list)
    matched_category_names: list[str] = Field(default_factory=list)


class MediaTypeCapability(BaseModel):
    """Qué puede hacer realmente el proveedor para un tipo de contenido."""

    media_type: MediaType
    #: ``filtered`` = búsqueda con categorías verificadas; ``general`` = sin filtro.
    mode: Literal["filtered", "general"]
    category_ids: list[int] = Field(default_factory=list)
    category_names: list[str] = Field(default_factory=list)
    mapping_verified: bool
    #: Indexadores que **declaran** exactamente la categoría del filtro. Prowlarr
    #: los consulta con certeza (su correspondencia incluye la categoría pedida).
    indexers_supporting: list[str] = Field(default_factory=list)
    #: Indexadores sin la categoría pero relacionados por jerarquía (inferencia).
    #: Prowlarr **puede** consultarlos o no: la correspondencia real depende de la
    #: definición interna del indexador y no es deducible desde la API.
    indexers_inferred: list[str] = Field(default_factory=list)
    #: Indexadores que Prowlarr **consultará con certeza** en esta búsqueda. En modo
    #: ``general`` son todos los habilitados (sin filtro no descarta a ninguno); en
    #: ``filtered``, solo los de soporte declarado, porque Prowlarr descarta antes de
    #: buscar los indexadores cuya correspondencia de categorías no cubre el filtro.
    #: Participar no significa haber devuelto resultados. Los inferidos no se
    #: incluyen: su consulta no puede confirmarse.
    indexers_participating: list[str] = Field(default_factory=list)
    #: Indexadores cuyo estado de habilitación no se conoce (sin campo ``enable``).
    #: No se cuentan como participantes; se listan para no silenciar su existencia.
    indexers_unknown_state: list[str] = Field(default_factory=list)
    #: ``True`` solo si el estado de participación de **todos** los indexadores
    #: relevantes se conoce: el listado se leyó y ninguno quedó con estado
    #: indeterminado. ``False`` si el listado no se pudo leer o si algún indexador
    #: aparece en ``indexers_unknown_state``. No significa que exista participación:
    #: indica que ``indexers_participating`` es completo. Un listado vacío es estado
    #: conocido (no hay indexadores) y da ``True``.
    participation_known: bool = False
    #: Detalle por indexador del nivel de soporte (solo en modo ``filtered``).
    indexer_support: list[IndexerCategorySupport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProviderCapabilities(BaseModel):
    """Capacidades efectivas del proveedor con su configuración actual."""

    provider: str
    search_types: list[MediaTypeCapability] = Field(default_factory=list)
    categories: list[CategoryRef] = Field(default_factory=list)
    indexers: list[IndexerInfo] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProviderSearchRequest(BaseModel):
    """Petición que el servicio envía al proveedor (sin paginación)."""

    model_config = ConfigDict(frozen=True)

    query: str
    media_type: MediaType
    year: int | None = None
    categories: tuple[str, ...] = ()


class IndexerWindowSummary(BaseModel):
    """Cuántos resultados aportó cada indexador y qué se sabe de su ventana."""

    indexer: str | None = None
    indexer_id: int | None = None
    #: Resultados recibidos de ese indexador (antes de deduplicar entre indexadores).
    received_count: int = 0
    #: Fiabilidad de la ventana. ``unknown`` salvo evidencia explícita del proveedor.
    window: WindowStatus = WindowStatus.UNKNOWN
    #: Capacidad declarada por el indexador (``supportsPagination``), informativa:
    #: no demuestra que la ventana recibida esté truncada.
    supports_pagination: bool | None = None


class CategoryMatchSummary(BaseModel):
    """Recuento de resultados por estado de correspondencia de categorías."""

    #: ``False`` cuando la búsqueda no llevaba filtro y no se clasificó nada.
    applicable: bool = False
    in_subtree: int = 0
    off_category: int = 0
    uncategorized: int = 0
    #: Resultados que el proveedor no clasificó (adaptadores sin esa capacidad).
    unclassified: int = 0


class EmptyResultAssessment(BaseModel):
    """Hechos conocidos sobre una respuesta vacía.

    No afirma ausencia de coincidencias: solo reúne la evidencia disponible.
    Todos los campos son opcionales porque ``0`` consultas fallidas no demuestra
    que los indexadores hayan respondido bien.
    """

    #: Indexadores habilitados en el proveedor en el momento de la búsqueda.
    #: ``None`` si no se sabe (listado no disponible o algún indexador no informa
    #: del campo ``enable``).
    indexers_available: int | None = None
    #: Indexadores considerados (no deshabilitados) que declaran la categoría del
    #: filtro o una relacionada (compatibilidad inferida). ``None`` si no hay árbol
    #: de categorías con el que decidirlo.
    indexers_compatible_with_filter: int | None = None
    #: ``True`` si ningún indexador considerado anuncia compatibilidad con el filtro.
    no_compatible_indexers: bool | None = None
    #: Consultas fallidas reportadas para los indexadores considerados.
    reported_failed_queries: int | None = None
    #: ``True`` si el proveedor reporta fallos; ``None`` si no hay dato fiable.
    indexer_failure_reported: bool | None = None


class ProviderSearchResult(BaseModel):
    """Respuesta del proveedor antes de normalizar y paginar.

    ``total_available`` y ``truncated`` son deliberadamente opcionales: un
    proveedor solo debe rellenarlos cuando pueda demostrarlos. ``None`` significa
    "desconocido", nunca "cero" ni "completo".
    """

    results: list[ReleaseRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    applied_categories: list[CategoryRef] = Field(default_factory=list)
    used_general_search: bool = False
    #: Total real disponible, solo si el proveedor lo garantiza.
    total_available: int | None = None
    #: ``True`` solo si el proveedor aporta evidencia de corte; ``None`` si no
    #: puede determinarse (Prowlarr no aporta ninguna).
    truncated: bool | None = None
    #: Ventana recibida por indexador (nombre, cantidad y fiabilidad).
    per_indexer: list[IndexerWindowSummary] = Field(default_factory=list)
    #: Qué se sabe de una respuesta vacía (solo cuando ``results`` está vacío).
    empty_result: EmptyResultAssessment | None = None


class SearchRequest(BaseModel):
    """Petición validada que llega desde la herramienta MCP."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1, max_length=256)
    media_type: MediaType = MediaType.OTHER
    year: int | None = Field(default=None, ge=1888, le=2100)
    categories: tuple[str, ...] = ()
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)
    provider: str | None = None

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("la consulta no puede estar vacía")
        return cleaned

    @field_validator("categories", mode="before")
    @classmethod
    def _clean_categories(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            items: list[object] = [value]
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
        else:
            raise ValueError("las categorías deben ser una lista de textos (id o nombre)")
        cleaned: list[str] = []
        for item in items:
            if not isinstance(item, str):
                raise ValueError("las categorías deben ser textos (id o nombre)")
            text = item.strip()
            if text:
                cleaned.append(text)
        if len(cleaned) > 20:
            raise ValueError("se admiten como máximo 20 categorías por búsqueda")
        return tuple(dict.fromkeys(cleaned))



# ---------------------------------------------------------------------------
# P9 — identidades exactas (Etapa B) y relaciones heurísticas (Etapa C2)
# ---------------------------------------------------------------------------


class DuplicateRelation(StrEnum):
    """Fuerza de una relación de duplicado.

    De momento solo existe ``confirmed``: la Etapa B detecta exclusivamente
    identidades exactas. Las relaciones ``possible`` se añadirán en una etapa
    posterior y no se representan aquí para no prometer lo que no se calcula.
    """

    CONFIRMED = "confirmed"


class DuplicateKind(StrEnum):
    """Qué se está afirmando exactamente al agrupar dos resultados."""

    #: Mismo torrent: el ``info_hash`` canónico coincide. Implica mismo contenido
    #: y misma publicación, incluso entre indexadores distintos.
    SAME_PUBLICATION = "same_publication"
    #: Misma entrada del mismo indexador (GUID coincidente). **No** demuestra que
    #: el contenido sea idéntico: dos entradas de origen pueden apuntar a ficheros
    #: distintos y el GUID no es portable entre indexadores.
    SAME_SOURCE_ENTRY = "same_source_entry"


class DuplicateBasis(StrEnum):
    """Evidencia concreta que motiva el grupo."""

    INFO_HASH = "info_hash"
    INDEXER_GUID = "indexer_guid"


class DuplicateGroup(BaseModel):
    """Grupo de resultados que la Etapa B considera la misma identidad exacta.

    Es una vista **no destructiva**: solo referencia ``result_id`` de resultados
    que siguen presentes, en su orden original, en la respuesta. El orden de los
    miembros y el ``group_id`` son deterministas y no dependen del orden de
    entrada.
    """

    model_config = ConfigDict(frozen=True)

    #: ``dup:`` + hash de la evidencia y los miembros. Estable entre ejecuciones.
    group_id: str
    relation: DuplicateRelation = DuplicateRelation.CONFIRMED
    kind: DuplicateKind
    basis: DuplicateBasis
    #: Primer miembro en orden canónico (``members[0]``), útil como referencia.
    anchor_id: str
    #: ``result_id`` de los miembros, ordenados y sin repeticiones (mínimo 2).
    members: list[str] = Field(min_length=2)

    @property
    def size(self) -> int:
        """Número de resultados del grupo."""
        return len(self.members)


class HeuristicKind(StrEnum):
    """Qué se afirma entre los dos resultados del par."""

    #: Probablemente el mismo release, sin identidad criptográfica.
    SAME_RELEASE = "same_release"
    #: Misma obra/unidad con diferencias técnicas explícitas (otra codificación,
    #: idioma, fuente…). Nunca implica el mismo fichero.
    VARIANT = "variant"


class HeuristicRelation(StrEnum):
    """Fuerza de la relación. C2 solo produce heurísticas, nunca confirmaciones."""

    POSSIBLE = "possible"


class PairEvidence(StrEnum):
    """Evidencia que sostiene la relación."""

    #: Títulos normalizados estrictamente iguales (``same_release``).
    TITLE_NORMALIZED = "title_normalized"
    #: Títulos base iguales tras retirar los fragmentos técnicos de C1 (``variant``).
    BASE_TITLE = "base_title"
    #: Tamaños conocidos e idénticos (``same_release``).
    SIZE_EXACT = "size_exact"
    #: Al menos una dimensión estructural coincide con valor presente en ambos.
    STRUCTURE_MATCH = "structure_match"


class TechnicalDimension(StrEnum):
    """Dimensión técnica que puede diferenciar dos variantes."""

    RESOLUTION = "resolution"
    SOURCE = "source"
    CODEC = "codec"
    LANGUAGE = "language"
    AUDIO = "audio"
    SUBTITLES = "subtitles"
    AUDIO_MODE = "audio_mode"


class PairRelation(BaseModel):
    """Relación heurística entre exactamente dos resultados.

    Los identificadores se guardan en orden canónico (``left_id < right_id``), de
    modo que comparar A con B y B con A produce el mismo objeto, con el mismo
    ``relation_id``. No contiene ``MediaResult`` ni agrupa más de dos resultados.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``rel:`` + hash determinista de ``kind`` y del par ordenado.
    relation_id: str
    left_id: str
    right_id: str
    relation: HeuristicRelation = HeuristicRelation.POSSIBLE
    kind: HeuristicKind
    evidence: tuple[PairEvidence, ...] = Field(min_length=1)
    #: Dimensiones con diferencia explícita; vacío en ``same_release``.
    differences: tuple[TechnicalDimension, ...] = ()
    note: str | None = None

    @model_validator(mode="after")
    def _check_pair(self) -> Self:
        if self.left_id == self.right_id:
            raise ValueError("una relación necesita dos resultados distintos")
        if not self.left_id < self.right_id:
            raise ValueError("los identificadores deben ir en orden canónico")
        if self.kind is HeuristicKind.SAME_RELEASE and self.differences:
            raise ValueError("same_release no admite diferencias técnicas")
        if self.kind is HeuristicKind.VARIANT and not self.differences:
            raise ValueError("variant exige al menos una diferencia explícita")
        return self


class SearchResponse(BaseModel):
    """Respuesta normalizada y paginada de ``search_media``.

    Semántica de los campos de cantidad/ventana (deliberadamente conservadora):

    * ``received_count``: resultados que el proveedor entregó y que se pudieron
      normalizar y deduplicar. Es una ventana, no el total del índice.
    * ``returned_count``: resultados de esta página tras ``offset``/``limit``.
    * ``total_available``: solo cuando el proveedor garantiza el total; ``None``
      = desconocido.
    * ``has_more``: ``True`` = quedan resultados en la ventana recibida;
      ``None`` = no puede determinarse si existen más en el proveedor.
      Nunca ``False``: no se puede demostrar que no haya más.
    * ``next_offset``: ``offset`` a usar para la siguiente página de la ventana
      recibida, o ``None`` si no aplica.
    * ``truncated``: ``True`` = el proveedor ha aportado evidencia de ventana
      limitada; ``None`` = desconocido (caso de Prowlarr: no hay evidencia).
      Nunca ``False`` por ausencia de evidencia.
    """

    provider: str
    query: str
    media_type: MediaType
    year: int | None = None
    applied_categories: list[CategoryRef] = Field(default_factory=list)
    used_general_search: bool = False
    received_count: int = 0
    returned_count: int = 0
    limit: int = 0
    offset: int = 0
    has_more: bool | None = None
    next_offset: int | None = None
    total_available: int | None = None
    truncated: bool | None = None
    per_indexer: list[IndexerWindowSummary] = Field(default_factory=list)
    category_match_summary: CategoryMatchSummary | None = None
    empty_result: EmptyResultAssessment | None = None
    warnings: list[str] = Field(default_factory=list)
    #: Grupos confirmados (B) sobre los resultados **de esta página**.
    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list)
    #: Relaciones ``possible`` (C2) entre resultados de esta página, sin las
    #: parejas ya cubiertas por un grupo confirmado. Nunca crea grupos.
    heuristic_relations: list[PairRelation] = Field(default_factory=list)
    results: list[MediaResult] = Field(default_factory=list)
