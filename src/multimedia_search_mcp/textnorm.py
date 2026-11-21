"""Normalización textual de títulos (P9, Etapa C1).

Función única, pura, determinista e idempotente. **No** elimina información
semántica: años, temporadas, episodios, volúmenes, capítulos, resoluciones,
códecs, idiomas, fuentes, ediciones ni indicadores de subtítulos sobreviven a la
normalización. Lo único que cambia es la forma: mayúsculas, acentos
tipográficos, anchos y separadores.

No translitera, no traduce y no consulta nada externo: dos títulos que se
normalizan igual solo comparten forma, no significado.

Criterio de conservación de caracteres: se mantienen letras, números y marcas
combinantes Unicode (categorías ``L*``, ``N*`` y ``M*``), lo que preserva
japonés, chino, coreano, hangul, devanagari y demás escrituras. Cualquier otro
carácter (puntuación, símbolos, emoji) actúa como separador.
"""

from __future__ import annotations

import unicodedata

__all__ = ["normalize_title"]

_KEPT_CATEGORIES = frozenset({"L", "N", "M"})


def normalize_title(title: str | None) -> str:
    """Normaliza un título para comparación textual.

    Pasos, en orden: NFKC (compone y unifica anchos), ``casefold``, sustitución
    de todo carácter no alfanumérico por espacio y colapso de espacios.

    Reglas de borde:

    * ``None`` → ``""``.
    * Cadena vacía o solo espacios → ``""``.
    * Se conservan los caracteres Unicode de escrituras no latinas; no se
      transliteran ni se traducen.
    * El resultado nunca contiene espacios al inicio o al final ni dos
      consecutivos, y normalizarlo de nuevo no cambia nada (idempotencia).

    No se elimina ningún token: los indicadores desconocidos (``REPACK``,
    ``PROPER``…) permanecen en el resultado.
    """
    if title is None:
        return ""

    text = unicodedata.normalize("NFKC", title).casefold()

    characters: list[str] = []
    for character in text:
        if unicodedata.category(character)[0] in _KEPT_CATEGORIES:
            characters.append(character)
        else:
            characters.append(" ")

    return " ".join("".join(characters).split())
