"""Errores controlados del servidor.

Todas las excepciones de este módulo transportan mensajes ya saneados: nunca
incluyen la API Key ni cabeceras de autenticación. El detalle técnico original
de httpx se descarta a propósito (``raise ... from None``) para que no pueda
filtrarse en logs, trazas ni respuestas MCP.
"""

from __future__ import annotations

__all__ = [
    "ConfigurationError",
    "InvalidRequestError",
    "MultimediaSearchError",
    "ProviderAuthError",
    "ProviderError",
    "ProviderRequestError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "UnknownProviderError",
]


class MultimediaSearchError(Exception):
    """Base de todos los errores previstos y seguros de mostrar."""


class ConfigurationError(MultimediaSearchError):
    """Falta configuración obligatoria o no es válida."""


class InvalidRequestError(MultimediaSearchError):
    """Parámetros de la petición no válidos (validación de entrada)."""


class UnknownProviderError(MultimediaSearchError):
    """Se ha solicitado un proveedor que no está registrado."""


class ProviderError(MultimediaSearchError):
    """Error genérico de un proveedor."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        self.message = message
        super().__init__(f"[{provider}] {message}")


class ProviderAuthError(ProviderError):
    """El proveedor ha rechazado las credenciales (401/403)."""


class ProviderTimeoutError(ProviderError):
    """El proveedor no ha respondido dentro del tiempo configurado."""


class ProviderUnavailableError(ProviderError):
    """No se puede contactar con el proveedor o está caído (5xx)."""


class ProviderRequestError(ProviderError):
    """El proveedor ha rechazado la petición (4xx con detalle)."""


class ProviderResponseError(ProviderError):
    """La respuesta del proveedor no tiene el formato esperado."""
