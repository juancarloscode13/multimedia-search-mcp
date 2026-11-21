"""Ejecutable del servidor MCP.

Uso:
    python -m multimedia_search_mcp        # transporte stdio (Hermes)
    multimedia-search-mcp                  # script instalado

Los mensajes de diagnóstico se escriben en stderr: stdout está reservado para el
protocolo MCP.
"""

from __future__ import annotations

import logging
import sys

from .app import build_service
from .config import Settings
from .server import create_server

__all__ = ["main"]


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.load()
    service = build_service(settings)
    server = create_server(service)
    try:
        server.run("stdio")
    except KeyboardInterrupt:  # pragma: no cover - cierre manual
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
