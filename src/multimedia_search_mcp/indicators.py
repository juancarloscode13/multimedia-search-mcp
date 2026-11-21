"""Extracción conservadora de indicadores de releases (P9, Etapa C1).

Módulo **interno**: no forma parte del contrato de MCP y no se expone en
``MediaResult`` ni en ``SearchResponse``. Prepara datos para etapas posteriores;
aquí **no** se comparan resultados, no se agrupan duplicados y no se puntúa nada.

Dos decisiones estructuran el módulo:

* La extracción trabaja sobre ``normalize_title`` (NFKC + casefold + separadores)
  y usa **límites de palabra** en todos los patrones, para no confundir
  fragmentos como ``AV10``, ``engram`` o ``multiplayer`` con indicadores.
* Un atributo puede estar presente, ausente o **ambiguo**. Si un atributo de un
  solo valor aparece con valores distintos (``720p.1080p``), no se elige uno:
  el estado es ``ambiguous`` y se conservan todos los valores. Los atributos que
  pueden repetirse legítimamente (idiomas, audio, subtítulos) se consideran
  conjuntos: varios valores son ``present``, no una contradicción.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .textnorm import normalize_title

__all__ = [
    "Indicator",
    "IndicatorMatch",
    "IndicatorState",
    "ReleaseIndicators",
    "extract_indicators",
]

ValueT = TypeVar("ValueT")


class IndicatorState(StrEnum):
    """Estado de un atributo extraído."""

    #: Reconocido con un único valor (o varios, en atributos de conjunto).
    PRESENT = "present"
    #: No aparece ningún indicador. Nunca se infiere.
    ABSENT = "absent"
    #: Aparece con valores distintos y excluyentes: no se elige uno.
    AMBIGUOUS = "ambiguous"


class IndicatorMatch(BaseModel):
    """Procedencia de un valor: patrón que lo reconoció y fragmento literal."""

    model_config = ConfigDict(frozen=True)

    pattern: str
    text: str


class Indicator(BaseModel, Generic[ValueT]):
    """Valor, ausencia o ambigüedad de un atributo, con su procedencia.

    ``values`` está ordenado y sin duplicados equivalentes (``x265`` y ``HEVC``
    producen el mismo valor canónico). ``matches`` conserva todas las apariciones
    reconocidas, en el orden en que se evaluaron los patrones.
    """

    model_config = ConfigDict(frozen=True)

    state: IndicatorState = IndicatorState.ABSENT
    #: Tuplas, no listas: ``frozen=True`` impide reasignar campos, pero una lista
    #: seguiría siendo mutable por dentro (``values.append(...)``).
    values: tuple[ValueT, ...] = ()
    matches: tuple[IndicatorMatch, ...] = ()

    @model_validator(mode="after")
    def _check_state(self) -> Self:
        if self.state is IndicatorState.ABSENT and (self.values or self.matches):
            raise ValueError("un indicador ausente no puede tener valores ni coincidencias")
        if self.state is IndicatorState.PRESENT and not self.values:
            raise ValueError("un indicador presente necesita al menos un valor")
        if self.state is IndicatorState.AMBIGUOUS and len(self.values) < 2:
            raise ValueError("un indicador ambiguo necesita al menos dos valores")
        return self

    @property
    def is_absent(self) -> bool:
        """No se reconoció el atributo."""
        return self.state is IndicatorState.ABSENT

    @property
    def is_ambiguous(self) -> bool:
        """Se reconocieron valores excluyentes y no se eligió ninguno."""
        return self.state is IndicatorState.AMBIGUOUS

    @property
    def single(self) -> ValueT | None:
        """Valor único si el estado es ``present`` con un solo valor."""
        if self.state is IndicatorState.PRESENT and len(self.values) == 1:
            return self.values[0]
        return None


class ReleaseIndicators(BaseModel):
    """Indicadores estructurales y técnicos extraídos de un título.

    Todos los campos son opcionales por construcción: el modelo representa
    explícitamente ``absent`` y ``ambiguous``. ``normalized_title`` es el texto
    exacto sobre el que se aplicaron los patrones, para que la procedencia sea
    auditable.
    """

    model_config = ConfigDict(frozen=True)

    normalized_title: str = ""

    # Estructurales (un solo valor legítimo).
    year: Indicator[int] = Field(default_factory=lambda: Indicator[int]())
    season: Indicator[int] = Field(default_factory=lambda: Indicator[int]())
    episode: Indicator[int] = Field(default_factory=lambda: Indicator[int]())
    volume: Indicator[int] = Field(default_factory=lambda: Indicator[int]())
    chapter: Indicator[int] = Field(default_factory=lambda: Indicator[int]())

    # Técnicos de un solo valor.
    resolution: Indicator[str] = Field(default_factory=lambda: Indicator[str]())
    source: Indicator[str] = Field(default_factory=lambda: Indicator[str]())
    codec: Indicator[str] = Field(default_factory=lambda: Indicator[str]())

    # Técnicos de conjunto: varias apariciones pueden ser legítimas.
    audio: Indicator[str] = Field(default_factory=lambda: Indicator[str]())
    language: Indicator[str] = Field(default_factory=lambda: Indicator[str]())
    subtitles: Indicator[str] = Field(default_factory=lambda: Indicator[str]())
    audio_mode: Indicator[str] = Field(default_factory=lambda: Indicator[str]())


# -- patrones --------------------------------------------------------------

_SXXEYY = re.compile(r"\bs(\d{1,2})e(\d{1,3})\b")
_NXNN = re.compile(r"\b(\d{1,2})x(\d{1,3})\b")
_SEASON_WORD = re.compile(r"\bseason\s*(\d{1,2})\b")
_SEASON_BARE = re.compile(r"\bs(\d{1,2})\b")
_EPISODE_WORD = re.compile(r"\b(?:episode|ep)\s*(\d{1,3})\b")
_EPISODE_BARE = re.compile(r"\be(\d{1,3})\b")
_VOLUME = re.compile(r"\b(?:volume|vol)\s*(\d{1,3})\b")
_CHAPTER = re.compile(r"\b(?:chapter|chap|ch)\s*(\d{1,4})\b")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

#: Grupos entre corchetes (etiquetas de release) y detección de letras en ellos.
_BRACKETED = re.compile(r"\[[^\]]*\]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
#: Marcadores técnicos tras los que un número no es un año (``SP-1920`` = DPI).
_TECHNICAL_MARKERS = frozenset({"sp", "dpi", "px", "scan", "res", "resolution"})

_RESOLUTION = re.compile(r"\b(480p|720p|1080p|2160p|4k)\b")
_SOURCE = re.compile(
    r"\b(web dl|webdl|webrip|web rip|blu ray|bluray|bdrip|bd rip|dvdrip|dvd)\b"
)
_CODEC_H264 = re.compile(r"\b(x264|x 264|h264|h 264|avc)\b")
_CODEC_HEVC = re.compile(r"\b(x265|x 265|h265|h 265|hevc)\b")
_CODEC_AV1 = re.compile(r"\bav1\b")
_AUDIO = re.compile(r"\b(aac|flac|dts|ac3)\b")
_LANGUAGE = re.compile(r"\b(eng|esp|jpn|multi|vostfr)\b")
_SUBTITLES = re.compile(r"\b(subs|sub)\b")
_AUDIO_DUB = re.compile(r"\bdub\b")
_AUDIO_DUAL = re.compile(r"\bdual audio\b")

#: ``4K`` es la misma resolución que ``2160p``: se unifica al valor numérico.
_RESOLUTION_VALUES = {
    "480p": "480p",
    "720p": "720p",
    "1080p": "1080p",
    "2160p": "2160p",
    "4k": "2160p",
}
#: Variantes tipográficas de la misma fuente. ``DVDRip`` se agrupa con ``DVD``.
_SOURCE_VALUES = {
    "web dl": "web-dl",
    "webdl": "web-dl",
    "webrip": "webrip",
    "web rip": "webrip",
    "blu ray": "bluray",
    "bluray": "bluray",
    "bdrip": "bdrip",
    "bd rip": "bdrip",
    "dvdrip": "dvd",
    "dvd": "dvd",
}
#: ``x264``/``AVC`` son H.264 y ``x265``/``HEVC`` son H.265: mismo códec.
_CODEC_H264_VALUES = {"x264": "h264", "x 264": "h264", "h264": "h264", "h 264": "h264", "avc": "h264"}
_CODEC_HEVC_VALUES = {"x265": "hevc", "x 265": "hevc", "h265": "hevc", "h 265": "hevc", "hevc": "hevc"}
_SUBTITLE_VALUES = {"sub": "sub", "subs": "sub"}


def extract_indicators(title: str | None) -> ReleaseIndicators:
    """Extrae indicadores estructurales y técnicos de un título de release.

    No interpreta números sueltos (``One Piece 1080`` no aporta episodio), no
    elige valores contradictorios y no completa datos ausentes. La entrada puede
    ser el título original o ya normalizado: normalizar es idempotente.
    """
    text = normalize_title(title)
    if not text:
        return ReleaseIndicators(normalized_title="")

    season_entries: list[tuple[int, IndicatorMatch]] = []
    episode_entries: list[tuple[int, IndicatorMatch]] = []

    for match in _SXXEYY.finditer(text):
        season_entries.append(
            (int(match.group(1)), IndicatorMatch(pattern="sxxeyy", text=match.group(0)))
        )
        episode_entries.append(
            (int(match.group(2)), IndicatorMatch(pattern="sxxeyy", text=match.group(0)))
        )
    for match in _NXNN.finditer(text):
        season_entries.append(
            (int(match.group(1)), IndicatorMatch(pattern="nxnn", text=match.group(0)))
        )
        episode_entries.append(
            (int(match.group(2)), IndicatorMatch(pattern="nxnn", text=match.group(0)))
        )
    season_entries.extend(_int_matches(_SEASON_WORD, text, "season_word"))
    season_entries.extend(_int_matches(_SEASON_BARE, text, "season_bare"))
    episode_entries.extend(_int_matches(_EPISODE_WORD, text, "episode_word"))
    episode_entries.extend(_int_matches(_EPISODE_BARE, text, "episode_bare"))

    # El año se busca en un texto donde se han neutralizado las etiquetas entre
    # corchetes que contienen letras (``[Scan SP-1920]``, ``[Group1998]``): son
    # identificadores, no años. Un corchete solo numérico (``[2024]``) se
    # conserva. El resto de atributos se extraen del texto completo.
    year_text = normalize_title(_mask_labelled_brackets(title or ""))
    year_entries: list[tuple[int, IndicatorMatch]] = []
    for match in _YEAR.finditer(year_text):
        prefix = year_text[: match.start()].rstrip()
        previous = prefix.rsplit(" ", 1)[-1] if prefix else ""
        if previous in _TECHNICAL_MARKERS:
            continue  # ``SP 1920`` (DPI), ``scan 1999`` (resolución de escaneo)...
        year_entries.append(
            (int(match.group(1)), IndicatorMatch(pattern="year_4digit", text=match.group(0)))
        )

    return ReleaseIndicators(
        normalized_title=text,
        year=_int_indicator(year_entries),
        season=_int_indicator(season_entries),
        episode=_int_indicator(episode_entries),
        volume=_int_indicator(_int_matches(_VOLUME, text, "volume_word")),
        chapter=_int_indicator(_int_matches(_CHAPTER, text, "chapter_word")),
        resolution=_str_indicator(
            _str_matches(_RESOLUTION, text, "resolution", _RESOLUTION_VALUES)
        ),
        source=_str_indicator(_str_matches(_SOURCE, text, "source", _SOURCE_VALUES)),
        codec=_str_indicator(
            _str_matches(_CODEC_H264, text, "codec_h264", _CODEC_H264_VALUES)
            + _str_matches(_CODEC_HEVC, text, "codec_hevc", _CODEC_HEVC_VALUES)
            + _str_matches(_CODEC_AV1, text, "codec_av1", {"av1": "av1"})
        ),
        audio=_str_indicator(_str_matches(_AUDIO, text, "audio"), single_valued=False),
        language=_str_indicator(_str_matches(_LANGUAGE, text, "language"), single_valued=False),
        subtitles=_str_indicator(
            _str_matches(_SUBTITLES, text, "subtitles", _SUBTITLE_VALUES),
            single_valued=False,
        ),
        audio_mode=_str_indicator(
            _str_matches(_AUDIO_DUB, text, "audio_dub", {"dub": "dub"})
            + _str_matches(_AUDIO_DUAL, text, "audio_dual", {"dual audio": "dual"}),
            single_valued=False,
        ),
    )


# -- ayudas internas -------------------------------------------------------


def _int_matches(
    pattern: re.Pattern[str], text: str, pattern_id: str
) -> list[tuple[int, IndicatorMatch]]:
    return [
        (int(match.group(1)), IndicatorMatch(pattern=pattern_id, text=match.group(0)))
        for match in pattern.finditer(text)
    ]


def _str_matches(
    pattern: re.Pattern[str],
    text: str,
    pattern_id: str,
    canonical: dict[str, str] | None = None,
) -> list[tuple[str, IndicatorMatch]]:
    entries: list[tuple[str, IndicatorMatch]] = []
    for match in pattern.finditer(text):
        raw = match.group(0)
        value = canonical.get(raw, raw) if canonical is not None else raw
        entries.append((value, IndicatorMatch(pattern=pattern_id, text=raw)))
    return entries


def _mask_labelled_brackets(title: str) -> str:
    """Sustituye por espacios los corchetes con etiqueta textual.

    ``[Scan SP-1920]`` o ``[Group1998]`` son identificadores de release, no
    años. Un corchete solo numérico (``[2024]``) se conserva porque sí suele ser
    el año. Solo afecta a la detección de año.
    """

    def replace(match: re.Match[str]) -> str:
        content = match.group(0)
        return " " * len(content) if _LETTER.search(content) else content

    return _BRACKETED.sub(replace, title)


def _dedupe(
    entries: list[tuple[Any, IndicatorMatch]],
) -> tuple[tuple[Any, ...], tuple[IndicatorMatch, ...]]:
    """Valores únicos ordenados; se conservan todas las coincidencias."""
    unique = tuple(sorted({value for value, _ in entries}))
    matches = tuple(match for _, match in entries)
    return unique, matches


def _state(values: tuple[Any, ...], *, single_valued: bool) -> IndicatorState:
    if not values:
        return IndicatorState.ABSENT
    if single_valued and len(values) > 1:
        return IndicatorState.AMBIGUOUS
    return IndicatorState.PRESENT


def _int_indicator(
    entries: list[tuple[int, IndicatorMatch]], *, single_valued: bool = True
) -> Indicator[int]:
    values, matches = _dedupe(entries)
    return Indicator[int](
        state=_state(values, single_valued=single_valued),
        values=tuple(int(value) for value in values),
        matches=matches,
    )


def _str_indicator(
    entries: list[tuple[str, IndicatorMatch]], *, single_valued: bool = True
) -> Indicator[str]:
    values, matches = _dedupe(entries)
    return Indicator[str](
        state=_state(values, single_valued=single_valued),
        values=tuple(str(value) for value in values),
        matches=matches,
    )
