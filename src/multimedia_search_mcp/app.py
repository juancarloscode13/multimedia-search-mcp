"""Punto de composición: configuración -> registro -> servicio -> servidor MCP.

Se mantiene aparte de :mod:`.server` para que las herramientas MCP no sepan
cómo se construye el proveedor, y de :mod:`.providers` para que el adaptador no
sepa nada de MCP.
"""

from __future__ import annotations

from .config import ProwlarrSettings, Settings
from .providers.prowlarr import ProwlarrProvider
from .providers.registry import ProviderRegistry
from .services.search_service import MultimediaSearchService

__all__ = ["build_registry", "build_service"]


def build_registry(prowlarr: ProwlarrSettings) -> ProviderRegistry:
    """Registra los proveedores disponibles.

    Para añadir un proveedor nuevo basta con instanciarlo aquí (o inyectarlo en
    el registro desde el llamante) sin tocar el servicio ni el servidor MCP.
    """
    return ProviderRegistry([ProwlarrProvider(prowlarr)])


def build_service(settings: Settings) -> MultimediaSearchService:
    return MultimediaSearchService(build_registry(settings.prowlarr))
