"""Conversión tolerante de valores que llegan de APIs externas.

Un único sitio para las coerciones evita que cada capa decida por su cuenta qué
significa "vacío", "0" o "true" en datos de terceros.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["as_bool", "as_info_hash", "as_int", "as_str", "as_str_list"]

#: BitTorrent v1 usa SHA-1 (20 bytes -> 40 hex); v2 (BEP 52) usa SHA-256 (64 hex).
#: Prowlarr entrega el valor ya en hexadecimal; no se convierte base32 ni se
#: reinterpreta ningún otro formato.
_INFO_HASH_RE = re.compile(r"^(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$")

_TRUE_STRINGS = frozenset({"true", "yes", "1"})
_FALSE_STRINGS = frozenset({"false", "no", "0"})


def as_str(value: Any) -> str | None:
    """Texto limpio o ``None``. Nunca convierte a partir de estructuras."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def as_int(value: Any) -> int | None:
    """Entero o ``None``. Rechaza bool, floats no enteros y texto no numérico."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def as_bool(value: Any, *, lenient: bool = False) -> bool | None:
    """Booleano o ``None``.

    ``lenient=True`` acepta también cadenas tipo ``"true"``/``"1"``; úsalo solo
    cuando el proveedor documenta esos valores. Por defecto solo se acepta un
    booleano real, para no inventar estado a partir de texto libre.
    """
    if isinstance(value, bool):
        return value
    if lenient and isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
    return None


def as_info_hash(value: Any) -> str | None:
    """Hash de información BitTorrent canónico, o ``None`` si no es válido.

    Solo se acepta lo que Prowlarr entrega realmente: hexadecimal de 40
    caracteres (BTIH v1) o de 64 (BTIH v2, BEP 52), con espacios alrededor
    tolerados y cualquier combinación de mayúsculas y minúsculas. El valor se
    devuelve en minúsculas.

    No se "arregla" un valor inválido: un hash truncado, con caracteres fuera
    del alfabeto hexadecimal, en base32 o de otra longitud devuelve ``None`` en
    lugar de parecer un hash. El valor original, si existía, se conserva en
    ``provider_metadata``.
    """
    text = as_str(value)
    if text is None or _INFO_HASH_RE.match(text) is None:
        return None
    return text.lower()


def as_str_list(value: Any) -> list[str]:
    """Lista de textos no vacíos; un texto suelto se envuelve en una lista."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        items: list[str] = []
        for item in value:
            text = as_str(item)
            if text is not None:
                items.append(text)
        return items
    return []
