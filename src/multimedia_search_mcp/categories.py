"""Tipos de contenido soportados y su correspondencia con categorías.

La correspondencia es **por proveedor**: vive en el adaptador
(``providers/prowlarr.py``) porque las categorías técnicas son suyas. Este
módulo solo declara el catálogo de tipos multimedia y las notas estáticas que
acompañan a cada uno.

Correspondencias verificadas contra Prowlarr 3.0.2 y su árbol Newznab
(``GET /api/v1/indexer/categories``):

===== ================== ============================== ============
tipo  ``type`` Prowlarr  categorías Newznab              verificado
===== ================== ============================== ============
movie ``movie``          2000 Movies                    sí
series ``tvsearch``      5000 TV                        sí
anime ``tvsearch``       5070 TV/Anime                  sí
book  ``book``           7000 Books                     sí
manga —                  sin categoría propia           no
other —                  sin filtro                     no
===== ================== ============================== ============

Manga no se mapea a Books/Comics: Prowlarr no expone ninguna categoría de manga
y dar por hecho que "manga son libros" es una equivalencia no verificada. Se
usa búsqueda general con advertencia explícita.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import CategoryMatch, CategoryNamespace, CategoryRef, MediaType

__all__ = [
    "GENERAL_SEARCH_WARNING",
    "MEDIA_TYPE_SPECS",
    "NEWZNAB_CATEGORY_MAX",
    "NEWZNAB_CATEGORY_MIN",
    "OVERLAP_NOTES",
    "SEMANTIC_FILTER_WARNING",
    "MediaTypeSpec",
    "categories_related",
    "classify_category_match",
    "is_in_newznab_subtree",
    "is_newznab_category",
    "newznab_ancestors",
    "parent_category_id",
    "spec_for",
    "supported_media_types",
]

GENERAL_SEARCH_WARNING = (
    "Búsqueda general sin filtro de categoría: el filtrado semántico por tipo de "
    "contenido no está garantizado y los resultados pueden pertenecer a otros tipos."
)

#: Advertencia global sobre qué significan las categorías del proveedor (P4).
SEMANTIC_FILTER_WARNING = (
    "Las categorías describen el filtrado que ofrece el proveedor, no una "
    "clasificación semántica del contenido: hay solapamiento (TV 5000 incluye "
    "TV/Anime 5070) y algunos resultados pueden aparecer en un tipo sin serlo."
)

OVERLAP_NOTES: dict[MediaType, str] = {
    MediaType.MOVIE: (
        "Movies (2000) es un filtro del proveedor, no una clasificación semántica: "
        "algunos indexadores mapean ahí resultados que no son películas."
    ),
    MediaType.SERIES: (
        "TV (5000) incluye sus descendientes (p. ej. 5070 TV/Anime): una búsqueda de "
        "series puede devolver anime."
    ),
    MediaType.ANIME: (
        "Anime usa 5070, descendiente de TV (5000): los mismos resultados pueden "
        "aparecer en series; la categoría no garantiza que todo sea anime."
    ),
}

#: Rango de las categorías estándar Newznab. Los indexadores añaden ids propios
#: por encima de este rango (p. ej. 130420 = id nativo + 100000) que **no** son
#: comparables con el árbol Newznab y no deben interpretarse como sus padres.
NEWZNAB_CATEGORY_MIN = 1000
NEWZNAB_CATEGORY_MAX = 8999


def is_newznab_category(category_id: int) -> bool:
    """Indica si el id pertenece al rango estándar Newznab."""
    return NEWZNAB_CATEGORY_MIN <= category_id <= NEWZNAB_CATEGORY_MAX


def parent_category_id(category_id: int) -> int | None:
    """Categoría padre según la convención Newznab (5070 -> 5000).

    Devuelve ``None`` para categorías raíz y para ids nativos/custom: no se
    inventa una jerarquía para ids que no siguen el esquema Newznab.
    """
    if not is_newznab_category(category_id) or category_id % 1000 == 0:
        return None
    return category_id - (category_id % 1000)


def newznab_ancestors(category_id: int) -> tuple[int, ...]:
    """Ancestros Newznab de una categoría (vacío si no aplica el esquema)."""
    parent = parent_category_id(category_id)
    return () if parent is None else (parent,)


def is_in_newznab_subtree(category_id: int, filter_ids: Iterable[int]) -> bool:
    """¿La categoría pertenece al filtro o a su subárbol Newznab?

    Un id custom (``130420``) nunca se considera dentro de un filtro Newznab
    (``4000``): su pertenencia real la conoce el indexador, no el árbol Newznab.
    """
    filters = set(filter_ids)
    if not filters:
        return False
    if category_id in filters:
        return True
    if not is_newznab_category(category_id):
        return False
    return any(ancestor in filters for ancestor in newznab_ancestors(category_id))


def categories_related(left: int, right: int) -> bool:
    """Dos categorías están relacionadas si son iguales o una es padre de otra."""
    if left == right:
        return True
    return parent_category_id(left) == right or parent_category_id(right) == left


def classify_category_match(
    categories: Iterable[CategoryRef], filter_ids: Iterable[int]
) -> CategoryMatch | None:
    """Clasifica un resultado respecto al filtro de categorías aplicado.

    * sin filtro -> ``None`` (no aplica clasificación);
    * sin categorías -> ``uncategorized`` (Prowlarr puede conservarlas);
    * alguna categoría Newznab en el filtro o su subárbol -> ``in_subtree``;
    * categorías presentes pero ninguna dentro -> ``off_category``.

    Solo cuentan las categorías con ``namespace == newznab``: una categoría nativa
    o de namespace desconocido **nunca** pertenece a un filtro Newznab, aunque su
    número coincida. Si no se pudo determinar el namespace (árbol no disponible),
    el adaptador no llama a esta función: devuelve resultados sin clasificar.
    No se deduce nada del título.
    """
    filters = {category_id for category_id in filter_ids}
    if not filters:
        return None

    refs = list(categories)
    if not refs:
        return CategoryMatch.UNCATEGORIZED

    for ref in refs:
        if ref.namespace is not CategoryNamespace.NEWZNAB:
            continue
        if is_in_newznab_subtree(ref.id, filters):
            return CategoryMatch.IN_SUBTREE
    return CategoryMatch.OFF_CATEGORY


@dataclass(frozen=True, slots=True)
class MediaTypeSpec:
    """Descripción de un tipo de contenido multimedia."""

    media_type: MediaType
    label: str
    #: Tipo de búsqueda Newznab que entiende el proveedor (``None`` = búsqueda básica).
    provider_search_type: str | None
    #: Categorías de proveedor verificadas. Vacío = sin correspondencia fiable.
    category_ids: tuple[int, ...]
    mapping_verified: bool
    notes: tuple[str, ...] = ()

    @property
    def uses_general_search(self) -> bool:
        return not self.category_ids


MEDIA_TYPE_SPECS: dict[MediaType, MediaTypeSpec] = {
    MediaType.MOVIE: MediaTypeSpec(
        media_type=MediaType.MOVIE,
        label="Películas",
        provider_search_type="movie",
        category_ids=(2000,),
        mapping_verified=True,
        notes=(
            "Prowlarr no devuelve metadatos técnicos estructurados (resolución, códec, "
            "idioma, subtítulos) en los resultados de búsqueda.",
            OVERLAP_NOTES[MediaType.MOVIE],
        ),
    ),
    MediaType.SERIES: MediaTypeSpec(
        media_type=MediaType.SERIES,
        label="Series",
        provider_search_type="tvsearch",
        category_ids=(5000,),
        mapping_verified=True,
        notes=(
            "Prowlarr no identifica temporadas ni episodios de forma semántica; no se "
            "deducen del título.",
            OVERLAP_NOTES[MediaType.SERIES],
        ),
    ),
    MediaType.ANIME: MediaTypeSpec(
        media_type=MediaType.ANIME,
        label="Anime",
        provider_search_type="tvsearch",
        category_ids=(5070,),
        mapping_verified=True,
        notes=(
            "Anime se filtra por la subcategoría Newznab TV/Anime (5070); los "
            "indexadores pueden exponer además categorías propias de anime con otros "
            "identificadores.",
            "Prowlarr no aporta título original, títulos alternativos ni grupo de "
            "subtitulado; no se extraen del título.",
            OVERLAP_NOTES[MediaType.ANIME],
        ),
    ),
    MediaType.BOOK: MediaTypeSpec(
        media_type=MediaType.BOOK,
        label="Libros",
        provider_search_type="book",
        category_ids=(7000,),
        mapping_verified=True,
        notes=(
            "Books (7000) agrupa eBooks, revistas, cómics y material técnico; Prowlarr "
            "no aporta autor, editorial, ISBN ni formato estructurado.",
        ),
    ),
    MediaType.MANGA: MediaTypeSpec(
        media_type=MediaType.MANGA,
        label="Manga",
        provider_search_type=None,
        category_ids=(),
        mapping_verified=False,
        notes=(
            "Prowlarr no dispone de categoría de manga verificada; se realiza búsqueda "
            "general y el filtrado por tipo no está garantizado.",
            "Books/Comics (7030) existe en el árbol Newznab pero no se usa: asumir que "
            "manga equivale a cómics/libros no está verificado.",
            "Prowlarr no aporta volumen, capítulo ni autor de forma estructurada.",
        ),
    ),
    MediaType.OTHER: MediaTypeSpec(
        media_type=MediaType.OTHER,
        label="Otros",
        provider_search_type=None,
        category_ids=(),
        mapping_verified=False,
    ),
}


def spec_for(media_type: MediaType) -> MediaTypeSpec:
    """Devuelve la especificación de un tipo de contenido."""
    try:
        return MEDIA_TYPE_SPECS[media_type]
    except KeyError as exc:  # pragma: no cover - cubierto por la validación de MediaType
        raise ValueError(f"Tipo de contenido no soportado: {media_type}") from exc


def supported_media_types() -> tuple[MediaType, ...]:
    """Tipos declarados, en orden estable."""
    return tuple(MEDIA_TYPE_SPECS)
