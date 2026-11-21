"""Interfaz abstracta de proveedores de búsqueda.

El servidor MCP y el servicio de búsqueda dependen **solo** de esta interfaz.
Añadir un proveedor nuevo consiste en implementar :class:`SearchProvider` y
registrarlo; no hay que tocar el núcleo (``server.py`` / ``services/``).

Contrato:

* Todas las operaciones son de **consulta** (equivalente GET). Nada de añadir,
  borrar, descargar ni modificar configuración del proveedor.
* Los errores se expresan con las excepciones de :mod:`.errors`, ya saneadas.
* ``search`` devuelve el conjunto completo que el proveedor entrega para la
  consulta; la paginación (``limit``/``offset``) la aplica el servicio.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..models import (
    IndexerInfo,
    ProviderCapabilities,
    ProviderInfo,
    ProviderSearchRequest,
    ProviderSearchResult,
    ProviderStatus,
)

__all__ = ["SearchProvider"]


class SearchProvider(ABC):
    """Proveedor de búsqueda multimedia de solo lectura."""

    #: Identificador estable usado en configuración y en las respuestas.
    name: ClassVar[str] = ""
    #: Descripción breve para ``list_providers``.
    capabilities_summary: ClassVar[str | None] = None

    @property
    @abstractmethod
    def configured(self) -> bool:
        """Indica si el proveedor tiene la configuración mínima para operar."""

    @property
    @abstractmethod
    def missing_configuration(self) -> list[str]:
        """Variables de entorno que faltan (sin valores, solo nombres)."""

    @abstractmethod
    async def search(self, request: ProviderSearchRequest) -> ProviderSearchResult:
        """Ejecuta una búsqueda y devuelve resultados neutros sin paginar."""

    @abstractmethod
    async def list_indexers(self) -> list[IndexerInfo]:
        """Lista los indexadores configurados en el proveedor."""

    @abstractmethod
    async def get_status(self) -> ProviderStatus:
        """Comprueba conectividad, autenticación y disponibilidad."""

    @abstractmethod
    async def get_capabilities(self) -> ProviderCapabilities:
        """Tipos de búsqueda y categorías realmente admitidos ahora mismo."""

    def secret_values(self) -> tuple[str, ...]:
        """Valores del proveedor que deben redactarse antes de salir del proceso.

        Por defecto no hay ninguno. Un adaptador con credenciales debe devolverlas
        aquí para que la capa MCP no necesite saber de qué proveedor se trata.
        """
        return ()

    async def aclose(self) -> None:
        """Libera recursos. Las implementaciones sin recursos no hacen nada."""
        return None

    def describe(self) -> ProviderInfo:
        """Metadatos del proveedor para ``list_providers``."""
        return ProviderInfo(
            name=self.name,
            configured=self.configured,
            read_only=True,
            missing_configuration=self.missing_configuration,
            capabilities_summary=self.capabilities_summary,
        )

    async def __aenter__(self) -> "SearchProvider":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
