"""Servidor MCP: definición de las herramientas expuestas.

SDK oficial ``mcp`` **2.x**: ``FastMCP`` se llama ahora ``MCPServer`` y se
importa de ``mcp.server.mcpserver`` (en 2.x, ``mcp.server.fastmcp`` lanza
``ModuleNotFoundError`` a propósito).

Las herramientas son de solo consulta: ninguna modifica Prowlarr, descarga
contenido ni abre enlaces.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ValidationError

from . import __version__
from .errors import InvalidRequestError, MultimediaSearchError, ProviderError
from .models import MediaType, SearchRequest, SearchResponse
from .security import redact
from .services.search_service import MultimediaSearchService

__all__ = ["create_server", "SERVER_INSTRUCTIONS"]

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Servidor de búsqueda multimedia de solo lectura. Busca releases en los "
    "proveedores configurados (actualmente Prowlarr) y normaliza los resultados. "
    "Nunca descarga contenido ni modifica el proveedor. Los datos técnicos solo "
    "aparecen si el proveedor los entrega de forma estructurada."
)


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


def _dump_search(response: SearchResponse) -> dict[str, Any]:
    """Volcado de la búsqueda con ``null`` explícito en los campos desconocidos.

    En ``search_media`` un ``null`` significa "no se puede saber" (total,
    truncamiento, has_more), así que no debe desaparecer del JSON. Los resultados
    individuales sí se compactan omitiendo sus campos vacíos.
    """
    payload = response.model_dump(mode="json", exclude_none=False, exclude={"results"})
    payload["results"] = [
        result.model_dump(mode="json", exclude_none=True) for result in response.results
    ]
    return payload


def _dump_all(models: Sequence[BaseModel]) -> list[dict[str, Any]]:
    return [_dump(model) for model in models]


def create_server(service: MultimediaSearchService) -> MCPServer:
    """Crea el servidor MCP con las cinco herramientas de consulta.

    No recibe ``Settings`` ni conoce ningún proveedor concreto: los secretos que
    debe redactar se los pide al servicio.
    """

    secrets = service.security_secrets()

    def _safe(payload: dict[str, Any]) -> dict[str, Any]:
        # Red de seguridad: ningún secreto sale del proceso.
        return redact(payload, secrets)  # type: ignore[no-any-return]

    def _error_payload(exc: Exception) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        if isinstance(exc, ProviderError):
            payload["provider"] = exc.provider
        return _safe({"ok": False, "error": payload})

    @asynccontextmanager
    async def lifespan(_server: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await service.aclose()

    server = MCPServer(
        name="multimedia-search",
        title="Multimedia Search",
        description="Búsqueda multimedia de solo lectura con proveedores intercambiables.",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
        lifespan=lifespan,
    )

    @server.tool()
    async def search_media(
        query: str,
        media_type: MediaType = MediaType.OTHER,
        year: int | None = None,
        categories: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Busca contenido multimedia en los proveedores configurados.

        Args:
            query: Título o términos de búsqueda.
            media_type: movie, series, anime, book, manga u other.
            year: Año de estreno/publicación (solo si el proveedor lo soporta).
            categories: Filtro explícito de categorías del proveedor: ids
                numéricos Newznab o nombres exactos del árbol (p. ej. "2040" o
                "Movies/HD"). Los ids se validan contra el árbol antes de buscar;
                las categorías nativas de un indexador (p. ej. 130420) se rechazan
                porque el filtro de Prowlarr es global. Si el árbol no está
                disponible, los ids se envían sin validar y se avisa.
            limit: Resultados por página (1-100, por defecto 20).
            offset: Desplazamiento dentro de la ventana recibida (por defecto 0).
            provider: Proveedor a usar (por defecto, el primero configurado).

        Semántica de la respuesta (deliberadamente conservadora):
            received_count: resultados recibidos del proveedor (una ventana, no el
                total del índice).
            returned_count: resultados de esta página tras limit/offset local.
            has_more: true si quedan resultados en la ventana recibida; null si no
                puede determinarse. Nunca false: no se puede demostrar que no haya
                más. next_offset indica la página siguiente cuando aplica.
            total_available: null cuando el proveedor no garantiza el total.
            truncated: true solo si el proveedor aporta evidencia de corte; null si
                no se sabe (caso de Prowlarr: no hay evidencia).
            per_indexer: resultados y fiabilidad de la ventana por indexador.
            category_match: por resultado, in_subtree / off_category /
                uncategorized (solo con filtro de categorías); resumen en
                category_match_summary. Los resultados sin categoría se conservan
                pero no se presentan como coincidencia confirmada.

        Una lista vacía no garantiza que no existan coincidencias.
        """
        try:
            request = SearchRequest(
                query=query,
                media_type=media_type,
                year=year,
                categories=tuple(categories or ()),
                limit=limit,
                offset=offset,
                provider=provider,
            )
        except ValidationError as exc:
            return _error_payload(InvalidRequestError(_validation_message(exc)))

        try:
            response = await service.search(request)
        except MultimediaSearchError as exc:
            return _error_payload(exc)
        except Exception as exc:  # pragma: no cover - defensa ante fallos inesperados
            logger.exception("Error inesperado en search_media")
            return _error_payload(exc)

        return _safe({"ok": True, **_dump_search(response)})

    @server.tool()
    async def list_providers() -> dict[str, Any]:
        """Lista los proveedores registrados y si están configurados.

        No realiza peticiones de red.
        """
        providers = service.list_providers()
        return _safe({"ok": True, "providers": _dump_all(providers)})

    @server.tool()
    async def list_indexers(provider: str | None = None) -> dict[str, Any]:
        """Lista los indexadores configurados en el proveedor y su estado.

        Incluye estadísticas cuando el proveedor las publica (son opcionales).
        """
        try:
            selected, indexers = await service.list_indexers(provider)
        except MultimediaSearchError as exc:
            return _error_payload(exc)
        except Exception as exc:  # pragma: no cover
            logger.exception("Error inesperado en list_indexers")
            return _error_payload(exc)

        return _safe(
            {
                "ok": True,
                "provider": selected.name,
                "count": len(indexers),
                "indexers": _dump_all(indexers),
            }
        )

    @server.tool()
    async def get_provider_status(provider: str | None = None) -> dict[str, Any]:
        """Comprueba conectividad, autenticación y disponibilidad del proveedor.

        Devuelve ``reachable: false`` con el motivo si no responde; no lanza
        error salvo que el proveedor solicitado no exista.
        """
        try:
            status = await service.get_provider_status(provider)
        except MultimediaSearchError as exc:
            return _error_payload(exc)
        except Exception as exc:  # pragma: no cover
            logger.exception("Error inesperado en get_provider_status")
            return _error_payload(exc)

        return _safe({"ok": True, "status": _dump(status)})

    @server.tool()
    async def get_search_capabilities(provider: str | None = None) -> dict[str, Any]:
        """Devuelve los tipos de búsqueda y categorías que admite la configuración actual.

        Por tipo de contenido indica el modo (``filtered``/``general``), las
        categorías aplicadas y, por indexador:

            indexers_supporting: anuncia exactamente la categoría (soporte declarado).
            indexers_inferred: no la anuncia, pero sí un padre o descendiente
                (compatibilidad inferida por jerarquía, no confirmada: Prowlarr
                puede consultarlo o no).
            indexers_participating: a quiénes consultará Prowlarr con certeza
                (en modo general, los habilitados; con filtro, solo los que
                declaran la categoría). Participar no implica devolver resultados.
            indexers_unknown_state: habilitación desconocida; no cuentan como
                participantes.
            participation_known: true solo si se conoce el estado de participación
                de todos los indexadores relevantes (listado leído y ninguno en
                indexers_unknown_state); no implica que haya participantes.
            indexer_support: nivel por indexador (declared/inferred/none/unknown)
                con las categorías que lo motivan.

        Las capacidades describen el filtrado del proveedor, no una clasificación
        semántica: movie/series/anime pueden solaparse (TV 5000 incluye 5070).
        """
        try:
            capabilities = await service.get_capabilities(provider)
        except MultimediaSearchError as exc:
            return _error_payload(exc)
        except Exception as exc:  # pragma: no cover
            logger.exception("Error inesperado en get_search_capabilities")
            return _error_payload(exc)

        return _safe({"ok": True, **_dump(capabilities)})

    return server


def _validation_message(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(item) for item in error["loc"]) or "parámetros"
        parts.append(f"{location}: {error['msg']}")
    return "Parámetros no válidos -> " + "; ".join(parts)
