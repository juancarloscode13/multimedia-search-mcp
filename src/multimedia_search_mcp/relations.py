"""Comparación heurística por pares entre dos resultados (P9, Etapa C2).

Módulo **puro**: sin red, sin proveedores, sin estado global, sin mutaciones. Solo
lee dos ``MediaResult`` y devuelve, como mucho, una relación entre ese par.

Alcance deliberadamente estrecho:

* ``possible / same_release``: mismo título normalizado, tamaño conocido e
  idéntico, sin contradicciones ni diferencias técnicas y sin ambigüedad.
* ``possible / variant``: mismo **título base** (el normalizado sin los
  fragmentos técnicos que C1 ya reconoció) con al menos una diferencia técnica
  explícita.

No hay grupos, transitividad, puntuaciones ni umbrales. Cada llamada compara
exactamente dos resultados: si A y B son variantes y B y C también, A y C solo se
relacionan si su propia comparación lo justifica.

Por qué existe el título base: ``normalize_title`` conserva resolución, fuente,
códec, idioma… así que dos releases que solo difieren en un indicador técnico
tienen títulos normalizados distintos. Comparar el título base —eliminando
únicamente los fragmentos que ``IndicatorMatch.text`` identifica como técnicos—
permite reconocer la variante sin relajar nada más: cualquier diferencia en un
token no reconocido por C1 rompe la igualdad del título base y no hay relación.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from .indicators import Indicator, IndicatorState, ReleaseIndicators, extract_indicators
from .models import (
    HeuristicKind,
    HeuristicRelation,
    MediaResult,
    PairEvidence,
    PairRelation,
    TechnicalDimension,
)

__all__ = [
    "HeuristicKind",
    "HeuristicRelation",
    "PairEvidence",
    "PairRelation",
    "TechnicalDimension",
    "base_title",
    "compare_pair",
]


#: Atributos técnicos que se retiran del título base y que pueden diferenciar
#: variantes. Los estructurales (año, temporada, episodio, volumen, capítulo) no
#: se retiran nunca: identifican la unidad.
_TECHNICAL_ATTRIBUTES: tuple[tuple[TechnicalDimension, str], ...] = (
    (TechnicalDimension.RESOLUTION, "resolution"),
    (TechnicalDimension.SOURCE, "source"),
    (TechnicalDimension.CODEC, "codec"),
    (TechnicalDimension.LANGUAGE, "language"),
    (TechnicalDimension.AUDIO, "audio"),
    (TechnicalDimension.SUBTITLES, "subtitles"),
    (TechnicalDimension.AUDIO_MODE, "audio_mode"),
)

_STRUCTURAL_ATTRIBUTES = ("year", "season", "episode", "volume", "chapter")
#: Dimensiones que identifican la unidad dentro de una obra (sin el año).
_UNIT_ATTRIBUTES = ("season", "episode", "volume", "chapter")


def compare_pair(left: MediaResult, right: MediaResult) -> PairRelation | None:
    """Compara dos resultados y devuelve la relación heurística, o ``None``.

    Reglas comunes:

    * ``result_id`` distintos y ``info_hash`` no compartido (si coinciden, B ya
      los confirma y una heurística sería redundante);
    * ningún indicador estructural ambiguo;
    * sin contradicciones estructurales (dos valores presentes y distintos);
    * unidad estructural compatible (aplica a ambas relaciones: un mismo título
      normalizado puede extraer años distintos si C1 neutralizó un corchete).

    A partir de ahí, título normalizado idéntico lleva a ``same_release`` y, si no
    lo es, la igualdad de título base con diferencias técnicas lleva a
    ``variant``. Todo lo demás devuelve ``None``.
    """
    if left.result_id == right.result_id:
        return None
    if _shares_info_hash(left, right):
        return None

    left_indicators = extract_indicators(left.title)
    right_indicators = extract_indicators(right.title)

    if _structural_ambiguity(left_indicators, right_indicators):
        return None
    if _structural_contradiction(left_indicators, right_indicators):
        return None
    # La unidad debe ser compatible para ambas reglas: el mismo título normalizado
    # puede extraer años distintos (C1 neutraliza los corchetes con etiqueta), así
    # que no basta con comparar títulos.
    if not _structural_compatible(left_indicators, right_indicators):
        return None

    if (
        left_indicators.normalized_title
        and left_indicators.normalized_title == right_indicators.normalized_title
    ):
        return _same_release(left, right, left_indicators, right_indicators)

    return _variant(left, right, left_indicators, right_indicators)


def base_title(indicators: ReleaseIndicators) -> str:
    """Título normalizado sin los fragmentos técnicos reconocidos por C1.

    Solo se retiran coincidencias ya identificadas por C1 (``IndicatorMatch``);
    nada más se elimina. El reparto es determinista: cada coincidencia reclama la
    primera aparición completa de su texto, en tokens, que no esté ya ocupada, de
    modo que los solapamientos se resuelven por orden fijo de atributos y de
    aparición (el primero que llega se queda el fragmento).
    """
    tokens = indicators.normalized_title.split()
    if not tokens:
        return ""

    taken = [False] * len(tokens)
    for _, attribute in _TECHNICAL_ATTRIBUTES:
        indicator: Indicator[str] = getattr(indicators, attribute)
        for match in indicator.matches:
            _reserve_tokens(tokens, taken, match.text)

    return " ".join(token for token, used in zip(tokens, taken, strict=True) if not used)


# -- reglas ----------------------------------------------------------------


def _same_release(
    left: MediaResult,
    right: MediaResult,
    left_indicators: ReleaseIndicators,
    right_indicators: ReleaseIndicators,
) -> PairRelation | None:
    """Título idéntico, tamaño exacto y ninguna diferencia ni ambigüedad.

    La compatibilidad estructural ya la ha comprobado ``compare_pair``: aquí no
    se repite para que ambas reglas compartan exactamente el mismo criterio.
    """
    if not left_indicators.normalized_title:
        return None
    if _technical_ambiguity(left_indicators, right_indicators):
        return None
    if _technical_differences(left_indicators, right_indicators):
        return None
    if left.size_bytes is None or right.size_bytes is None:
        return None
    if left.size_bytes != right.size_bytes:
        return None

    evidence = [PairEvidence.TITLE_NORMALIZED, PairEvidence.SIZE_EXACT]
    if _shares_any_structure(left_indicators, right_indicators):
        evidence.append(PairEvidence.STRUCTURE_MATCH)
    return _build_relation(left, right, HeuristicKind.SAME_RELEASE, evidence, ())


def _variant(
    left: MediaResult,
    right: MediaResult,
    left_indicators: ReleaseIndicators,
    right_indicators: ReleaseIndicators,
) -> PairRelation | None:
    """Título base idéntico, unidad compatible y ≥1 diferencia técnica explícita."""
    left_base = base_title(left_indicators)
    if not left_base or left_base != base_title(right_indicators):
        return None
    if _technical_ambiguity(left_indicators, right_indicators):
        return None

    differences = _technical_differences(left_indicators, right_indicators)
    if not differences:
        return None

    evidence = [PairEvidence.BASE_TITLE]
    if _shares_any_structure(left_indicators, right_indicators):
        evidence.append(PairEvidence.STRUCTURE_MATCH)
    note = None
    if left.size_bytes != right.size_bytes:
        note = "Los tamaños difieren; no impide una variante del mismo contenido."
    return _build_relation(left, right, HeuristicKind.VARIANT, evidence, differences, note)


# -- comprobaciones auxiliares --------------------------------------------


def _shares_info_hash(left: MediaResult, right: MediaResult) -> bool:
    left_hash = left.info_hash
    right_hash = right.info_hash
    return left_hash is not None and left_hash == right_hash


def _structures(
    indicators: ReleaseIndicators,
) -> dict[str, Indicator[int]]:
    return {name: getattr(indicators, name) for name in _STRUCTURAL_ATTRIBUTES}


def _structural_ambiguity(left: ReleaseIndicators, right: ReleaseIndicators) -> bool:
    for indicators in (left, right):
        for indicator in _structures(indicators).values():
            if indicator.is_ambiguous:
                return True
    return False


def _structural_contradiction(left: ReleaseIndicators, right: ReleaseIndicators) -> bool:
    left_values, right_values = _structures(left), _structures(right)
    for name in _STRUCTURAL_ATTRIBUTES:
        first, second = left_values[name], right_values[name]
        if first.state is IndicatorState.PRESENT and second.state is IndicatorState.PRESENT:
            if set(first.values) != set(second.values):
                return True
    return False


def _structural_compatible(left: ReleaseIndicators, right: ReleaseIndicators) -> bool:
    """Unidad compatible: dimensión presente en ambos con el mismo valor o ausente en ambos.

    El año admite present/absent solo si otra dimensión de unidad coincide con
    valor en ambos; así no se emparejan obras de años distintos ni se inventa el
    año que falta.

    Nota: como el título base solo retira fragmentos *técnicos*, los tokens
    estructurales siguen en él, de modo que hoy estos casos ya quedan filtrados
    por la igualdad de base. La comprobación se mantiene como invariante
    explícita por si en el futuro la identidad de unidad cambia de fuente.
    """
    left_values, right_values = _structures(left), _structures(right)

    for name in _UNIT_ATTRIBUTES:
        first, second = left_values[name], right_values[name]
        if first.state is not second.state:
            return False
        if first.state is IndicatorState.PRESENT and set(first.values) != set(second.values):
            return False

    year_left, year_right = left_values["year"], right_values["year"]
    if year_left.state is IndicatorState.PRESENT and year_right.state is IndicatorState.PRESENT:
        if set(year_left.values) != set(year_right.values):
            return False
    elif year_left.state is not year_right.state:
        if not _shares_unit_evidence(left, right):
            return False
    return True


def _shares_unit_evidence(left: ReleaseIndicators, right: ReleaseIndicators) -> bool:
    left_values, right_values = _structures(left), _structures(right)
    return any(
        left_values[name].state is IndicatorState.PRESENT
        and right_values[name].state is IndicatorState.PRESENT
        and set(left_values[name].values) == set(right_values[name].values)
        for name in _UNIT_ATTRIBUTES
    )


def _shares_any_structure(left: ReleaseIndicators, right: ReleaseIndicators) -> bool:
    left_values, right_values = _structures(left), _structures(right)
    return any(
        left_values[name].state is IndicatorState.PRESENT
        and right_values[name].state is IndicatorState.PRESENT
        for name in _STRUCTURAL_ATTRIBUTES
    )


def _technical_ambiguity(left: ReleaseIndicators, right: ReleaseIndicators) -> bool:
    for indicators in (left, right):
        for _, attribute in _TECHNICAL_ATTRIBUTES:
            if getattr(indicators, attribute).is_ambiguous:
                return True
    return False


def _technical_differences(
    left: ReleaseIndicators, right: ReleaseIndicators
) -> tuple[TechnicalDimension, ...]:
    """Dimensiones con valores presentes y distintos en ambos resultados.

    La ausencia nunca cuenta como diferencia y una dimensión ambigua tampoco: no
    se puede justificar con ella.
    """
    differences: list[TechnicalDimension] = []
    for dimension, attribute in _TECHNICAL_ATTRIBUTES:
        first: Indicator[str] = getattr(left, attribute)
        second: Indicator[str] = getattr(right, attribute)
        if first.state is IndicatorState.PRESENT and second.state is IndicatorState.PRESENT:
            if set(first.values) != set(second.values):
                differences.append(dimension)
    return tuple(differences)


def _reserve_tokens(tokens: Sequence[str], taken: list[bool], text: str) -> None:
    """Marca la primera aparición completa de ``text`` (en tokens) no ocupada."""
    needle = text.split()
    if not needle:
        return
    width = len(needle)
    for start in range(len(tokens) - width + 1):
        window = tokens[start : start + width]
        if window != needle:
            continue
        if any(taken[start : start + width]):
            continue
        for index in range(start, start + width):
            taken[index] = True
        return


def _build_relation(
    left: MediaResult,
    right: MediaResult,
    kind: HeuristicKind,
    evidence: list[PairEvidence],
    differences: tuple[TechnicalDimension, ...],
    note: str | None = None,
) -> PairRelation:
    left_id, right_id = sorted((left.result_id, right.result_id))
    digest = hashlib.sha256(f"{kind.value}|{left_id}|{right_id}".encode("utf-8")).hexdigest()
    return PairRelation(
        relation_id=f"rel:{digest[:16]}",
        left_id=left_id,
        right_id=right_id,
        kind=kind,
        evidence=tuple(evidence),
        differences=differences,
        note=note,
    )
