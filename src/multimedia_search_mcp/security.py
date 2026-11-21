"""Utilidades de saneado de secretos.

Prowlarr devuelve enlaces de proxy (``magnetUrl``/``downloadUrl``) que incluyen
``?apikey=<API Key>``. Esos enlaces se descartan o se limpian antes de salir del
proceso, y además se aplica un saneado final sobre toda respuesta del servidor
como red de seguridad.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["REDACTED", "redact", "redact_text", "sanitize_url"]

REDACTED = "***"
SECRET_QUERY_PARAMS = frozenset(
    {"apikey", "api_key", "api-key", "x-api-key", "token", "password", "passkey"}
)
_MIN_SECRET_LENGTH = 4
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|magnet):[^\s\"'<>\\]+")


def _is_url(value: str) -> bool:
    return "://" in value or value.startswith(("magnet:", "urn:"))


def sanitize_url(value: str) -> str:
    """Elimina credenciales de una URL (parámetros sensibles y userinfo)."""
    if not value or not _is_url(value):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value

    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in query_pairs if k.lower() not in SECRET_QUERY_PARAMS]
    query = urlencode(filtered, doseq=True) if len(filtered) != len(query_pairs) else parts.query

    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def redact_text(value: str, secrets: Iterable[str]) -> str:
    """Sustituye secretos y limpia cualquier URL embebida en el texto."""
    text = _URL_PATTERN.sub(lambda match: sanitize_url(match.group(0)), value)
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LENGTH:
            text = text.replace(secret, REDACTED)
    return text


def redact(value: Any, secrets: Iterable[str]) -> Any:
    """Saneado recursivo de estructuras ya serializables (dict/list/str)."""
    secret_list = [s for s in secrets if s]
    return _redact(value, secret_list)


def _redact(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, Mapping):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    return value
