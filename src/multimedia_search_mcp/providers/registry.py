"""Registro de proveedores disponibles."""

from __future__ import annotations

from collections.abc import Iterator


from ..errors import ConfigurationError, UnknownProviderError
from ..models import ProviderInfo
from .base import SearchProvider

__all__ = ["ProviderRegistry"]


class ProviderRegistry:
    """Colección ordenada de proveedores indexada por nombre."""

    def __init__(self, providers: list[SearchProvider] | None = None) -> None:
        self._providers: dict[str, SearchProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: SearchProvider) -> None:
        """Registra un proveedor. El nombre debe ser único y no vacío."""
        name = provider.name
        if not name:
            raise ValueError("Un proveedor debe declarar un nombre")
        if name in self._providers:
            raise ValueError(f"Proveedor duplicado: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> SearchProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            available = ", ".join(self._providers) or "ninguno"
            raise UnknownProviderError(
                f"Proveedor desconocido: {name}. Disponibles: {available}"
            ) from exc

    def default(self) -> SearchProvider:
        """Proveedor a usar cuando el cliente no especifica ninguno.

        Se prefiere el primero configurado, en el orden de registro. Si hay
        varios configurados, la selección explícita sigue siendo posible con el
        parámetro ``provider``.
        """
        for provider in self._providers.values():
            if provider.configured:
                return provider
        names = ", ".join(self._providers) or "ninguno"
        raise ConfigurationError(
            "No hay ningún proveedor configurado. Revisa las variables de entorno "
            f"(proveedores registrados: {names})."
        )

    def describe(self) -> list[ProviderInfo]:
        return [provider.describe() for provider in self._providers.values()]

    def names(self) -> list[str]:
        return list(self._providers)

    def __iter__(self) -> Iterator[SearchProvider]:
        return iter(self._providers.values())

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers
