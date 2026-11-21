"""Adaptadores de proveedores."""

from __future__ import annotations

from .base import SearchProvider
from .prowlarr import ProwlarrProvider
from .registry import ProviderRegistry

__all__ = ["ProwlarrProvider", "ProviderRegistry", "SearchProvider"]
