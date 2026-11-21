"""Detección de duplicados exactos entre resultados ya normalizados (P9, Etapa B).

Módulo **puro**: sin HTTP, sin proveedores concretos, sin estado global y sin
mutar los resultados. Solo lee ``MediaResult`` y produce ``DuplicateGroup``.

Alcance de esta etapa:

* mismo torrent -> ``same_publication`` (``info_hash`` canónico coincidente);
* misma entrada de un indexador -> ``same_source_entry`` (GUID + indexador).

No hay similitud de títulos, ni descriptores, ni puntuaciones, ni relaciones
``possible``: nada de eso se calcula aquí. La exposición pública de los grupos
tampoco se decide en este módulo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence

from .coercion import as_info_hash
from .models import (
    DuplicateBasis,
    DuplicateGroup,
    DuplicateKind,
    DuplicateRelation,
    MediaResult,
)

__all__ = [
    "find_exact_duplicates",
    "group_by_info_hash",
    "group_by_source_entry",
    "same_indexer",
]


def find_exact_duplicates(results: Sequence[MediaResult]) -> list[DuplicateGroup]:
    """Agrupa resultados que son la misma identidad exacta.

    Devuelve primero los grupos por ``info_hash`` y después los grupos por
    entrada de origen, cada bloque ordenado por ``group_id``. Un resultado puede
    aparecer en dos grupos de *tipos* distintos (mismo torrent y misma entrada de
    origen) solo si la evidencia lo justifica; un grupo por GUID cuyos miembros
    ya comparten hash no se emite, porque no añade ninguna relación nueva.

    No elimina, fusiona ni reordena los resultados de entrada.
    """
    unique = _unique(results)
    by_hash = group_by_info_hash(unique)
    by_entry = group_by_source_entry(unique)

    hash_of: dict[str, str | None] = {
        result.result_id: as_info_hash(result.info_hash) for result in unique
    }
    kept_entries = [
        group
        for group in by_entry
        if not _fully_covered_by_one_hash(group, hash_of)
    ]
    return by_hash + kept_entries


def group_by_info_hash(results: Iterable[MediaResult]) -> list[DuplicateGroup]:
    """Grupos de resultados con el mismo ``info_hash`` canónico (mismo torrent).

    Los hashes ausentes o inválidos nunca generan coincidencias: ``as_info_hash``
    devuelve ``None`` y se descartan.
    """
    buckets: dict[str, list[str]] = {}
    for result in _unique(results):
        info_hash = as_info_hash(result.info_hash)
        if info_hash is None:
            continue
        buckets.setdefault(info_hash, []).append(result.result_id)

    groups = [
        _build_group(
            kind=DuplicateKind.SAME_PUBLICATION,
            basis=DuplicateBasis.INFO_HASH,
            key=info_hash,
            members=members,
        )
        for info_hash, members in buckets.items()
        if len(members) >= 2
    ]
    return sorted(groups, key=lambda group: group.group_id)


def group_by_source_entry(results: Iterable[MediaResult]) -> list[DuplicateGroup]:
    """Grupos con el mismo GUID dentro del mismo indexador y proveedor.

    El GUID se compara literalmente (sin interpretarlo como magnet ni extraer
    hashes de él) y solo entre resultados de un mismo proveedor e indexador con
    identidad inequívoca (ver :func:`same_indexer`).

    La compatibilidad de indexador **no es transitiva** (A con id 1 y nombre
    «Alpha», B sin id y nombre «Alpha», C con id 2 y nombre «Alpha»: A~B y B~C
    pero A≁C), así que no se usan componentes conexas: cada grupo debe ser
    pairwise compatible y un nombre ambiguo queda fuera.
    """
    unique = _unique(results)
    candidates = [result for result in unique if _clean_guid(result) is not None]

    buckets: dict[tuple[str, str], list[MediaResult]] = {}
    for result in candidates:
        provider = _clean(result.provider)
        guid = _clean_guid(result)
        assert provider is not None and guid is not None
        buckets.setdefault((provider, guid), []).append(result)

    groups: list[DuplicateGroup] = []
    for (provider, guid), bucket in buckets.items():
        for members in _compatible_partitions(bucket):
            if len(members) < 2:
                continue
            result_ids = sorted(result.result_id for result in members)
            groups.append(
                _build_group(
                    kind=DuplicateKind.SAME_SOURCE_ENTRY,
                    basis=DuplicateBasis.INDEXER_GUID,
                    # La clave no se expone: el GUID puede ser largo o contener
                    # trackers. Incluye los miembros para que dos grupos
                    # independientes del mismo GUID nunca compartan id.
                    key=f"{provider}|{guid}|{','.join(result_ids)}",
                    members=result_ids,
                )
            )
    return sorted(groups, key=lambda group: group.group_id)


def same_indexer(left: MediaResult, right: MediaResult) -> bool:
    """¿Se puede afirmar que ambos resultados vienen del mismo indexador?

    Reglas conservadoras:

    * el proveedor debe coincidir y estar informado en ambos;
    * si los dos traen ``indexer_id``, deben coincidir y los nombres, si existen
      en ambos, no pueden contradecirlo;
    * si a alguno le falta el ``indexer_id``, se comparan los nombres, que deben
      existir en ambos (dentro del mismo proveedor el nombre identifica el
      indexador);
    * cualquier dato ausente o contradictorio devuelve ``False``: no se confirma
      la relación sin identidad inequívoca.
    """
    left_provider = _clean(left.provider)
    right_provider = _clean(right.provider)
    if left_provider is None or left_provider != right_provider:
        return False

    left_name = _clean(left.indexer)
    right_name = _clean(right.indexer)
    if left.indexer_id is not None and right.indexer_id is not None:
        if left.indexer_id != right.indexer_id:
            return False
        if left_name is None or right_name is None:
            return True
        return left_name == right_name
    return left_name is not None and left_name == right_name


# -- ayudas internas -------------------------------------------------------


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().casefold()
    return text or None


def _clean_guid(result: MediaResult) -> str | None:
    if result.guid is None:
        return None
    text = result.guid.strip()
    return text or None


def _unique(results: Iterable[MediaResult]) -> list[MediaResult]:
    """Primera aparición de cada ``result_id``; conserva el orden de entrada."""
    seen: set[str] = set()
    unique: list[MediaResult] = []
    for result in results:
        if result.result_id in seen:
            continue
        seen.add(result.result_id)
        unique.append(result)
    return unique


def _build_group(
    *,
    kind: DuplicateKind,
    basis: DuplicateBasis,
    key: str,
    members: Sequence[str],
) -> DuplicateGroup:
    ordered = sorted(dict.fromkeys(members))
    digest = hashlib.sha256(
        f"{kind.value}|{basis.value}|{key}".encode("utf-8")
    ).hexdigest()
    return DuplicateGroup(
        group_id=f"dup:{digest[:16]}",
        relation=DuplicateRelation.CONFIRMED,
        kind=kind,
        basis=basis,
        anchor_id=ordered[0],
        members=ordered,
    )


def _fully_covered_by_one_hash(
    group: DuplicateGroup, hash_of: dict[str, str | None]
) -> bool:
    """El grupo por GUID no aporta nada si todos sus miembros comparten hash."""
    hashes = {hash_of.get(member) for member in group.members}
    return len(hashes) == 1 and None not in hashes


def _compatible_partitions(bucket: Sequence[MediaResult]) -> list[list[MediaResult]]:
    """Reparte los candidatos en grupos donde **todos** los pares son compatibles.

    No se agrupa por componente conexa: la compatibilidad de indexador no es
    transitiva y una cadena afirmaría una relación inexistente. Reparto:

    1. Con ``indexer_id``: un grupo por id, y solo si todos los nombres presentes
       coinciden. Nombres contradictorios para un mismo id descartan ese grupo
       (evidencia inconsistente, no se confirma nada).
    2. Sin ``indexer_id``: se agrupan por nombre. Se anexan a un grupo con id
       únicamente si el nombre está asociado a **un solo** id del conjunto; si
       está asociado a varios, el nombre es ambiguo y esos resultados quedan
       fuera. Sin nombre ni id, tampoco participan.
    """
    with_id: dict[int, list[MediaResult]] = {}
    without_id: dict[str, list[MediaResult]] = {}
    for result in bucket:
        name = _clean(result.indexer)
        if result.indexer_id is not None:
            with_id.setdefault(result.indexer_id, []).append(result)
        elif name is not None:
            without_id.setdefault(name, []).append(result)

    groups: list[list[MediaResult]] = []
    id_group_of: dict[int, list[MediaResult]] = {}
    ids_by_name: dict[str, set[int]] = {}
    for indexer_id, members in with_id.items():
        names = {
            clean for clean in (_clean(member.indexer) for member in members) if clean
        }
        if len(names) > 1:
            continue  # id con nombres contradictorios: no se confirma
        group = list(members)
        id_group_of[indexer_id] = group
        groups.append(group)
        if names:
            ids_by_name.setdefault(next(iter(names)), set()).add(indexer_id)

    for name, members in without_id.items():
        owners = ids_by_name.get(name, set())
        if len(owners) == 1:
            id_group_of[next(iter(owners))].extend(members)
        elif not owners:
            # Nadie con id reclama ese nombre: grupo propio por nombre.
            groups.append(list(members))
        # Varios ids con el mismo nombre: ambiguo, no se atribuye.

    return groups
