"""Proveedor Prowlarr (solo lectura).

Notas de implementación verificadas contra **Prowlarr 3.0.2.5780**:

* ``GET /api/v1/search`` acepta ``query``, ``type``, ``indexerIds``,
  ``categories``, ``limit`` y ``offset``.
* Los parámetros de lista (``categories``, ``indexerIds``) deben enviarse
  **repetidos** (``categories=2000&categories=5070``). Si se envían separados
  por comas, la API responde ``400`` con un error de validación.
* ``limit``/``offset``: el cliente **no** los envía. Aunque la API los acepta y los
  reenvía a los indexadores que soportan paginación, no recorta el resultado agregado
  (con ``limit=2`` devolvió 87 elementos), así que la paginación se aplica en el
  servicio, sobre la lista completa.
* ``type`` cambia la estrategia de búsqueda (``movie``, ``tvsearch``, ``book``,
  ``music``); cualquier otro valor cae en la búsqueda básica.
* La API **no expone** ``year``: ``SearchResource`` solo tiene Query, Type,
  IndexerIds, Categories, Limit y Offset.
* Los enlaces ``magnetUrl``/``downloadUrl`` son proxies de Prowlarr e incluyen
  ``?apikey=...``. Nunca se propagan: se descartan y solo se conserva el magnet
  real cuando el GUID lo es.
* Un ``indexerIds`` inexistente devuelve ``400``; una categoría inexistente
  devuelve ``200`` con lista vacía (no se puede distinguir de "sin resultados").
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from ..categories import (
    GENERAL_SEARCH_WARNING,
    MEDIA_TYPE_SPECS,
    SEMANTIC_FILTER_WARNING,
    MediaTypeSpec,
    categories_related,
    classify_category_match,
    parent_category_id as _parent_category_id,
    spec_for,
)
from ..coercion import as_bool, as_info_hash, as_int, as_str, as_str_list
from ..config import ProwlarrSettings
from ..errors import (
    ConfigurationError,
    InvalidRequestError,
    ProviderAuthError,
    ProviderError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ..models import (
    CategoryNamespace,
    CategoryRef,
    CategorySupportLevel,
    EmptyResultAssessment,
    ExternalIds,
    IndexerCategorySupport,
    IndexerInfo,
    IndexerStats,
    IndexerWindowSummary,
    MediaType,
    MediaTypeCapability,
    ProviderCapabilities,
    ProviderSearchRequest,
    ProviderSearchResult,
    ProviderStatus,
    ReleaseRecord,
    WindowStatus,
)
from ..security import redact_text, sanitize_url
from .base import SearchProvider

__all__ = [
    "CATEGORIES_PATH",
    "INDEXER_PATH",
    "INDEXER_STATS_PATH",
    "PROVIDER_NAME",
    "SEARCH_PATH",
    "STATUS_PATH",
    "ProwlarrClient",
    "ProwlarrProvider",
    "flatten_categories",
    "parent_category_id",
]

PROVIDER_NAME = "prowlarr"
SEARCH_PATH = "/api/v1/search"
INDEXER_PATH = "/api/v1/indexer"
INDEXER_STATS_PATH = "/api/v1/indexerstats"
CATEGORIES_PATH = "/api/v1/indexer/categories"
STATUS_PATH = "/api/v1/system/status"

CATEGORY_CACHE_TTL_SECONDS = 300.0
MAX_ERROR_DETAIL = 300

#: A partir de este id, Prowlarr publica categorías propias de indexadores
#: (observado: 100017, 130420, 140679). Los ids Newznab están en el árbol.
CUSTOM_CATEGORY_MIN = 100_000


def _category_namespace(category_id: int, tree: Mapping[int, str]) -> CategoryNamespace:
    """Namespace de una categoría según el árbol Newznab del proveedor.

    * está en el árbol -> ``newznab`` (global);
    * no está y alcanza el rango de ids propios de Prowlarr -> ``native``;
    * cualquier otro caso -> ``unknown`` (no se adivina).

    Un id nativo **nunca** se trata como Newznab por coincidencia numérica.
    """
    if category_id in tree:
        return CategoryNamespace.NEWZNAB
    if category_id >= CUSTOM_CATEGORY_MIN:
        return CategoryNamespace.NATIVE
    return CategoryNamespace.UNKNOWN


def _build_category_ref(
    category_id: int,
    name: str | None,
    tree: Mapping[int, str],
    *,
    indexer_name: str | None = None,
    indexer_id: int | None = None,
) -> CategoryRef:
    """Construye la categoría con namespace y atribución al indexador."""
    namespace = _category_namespace(category_id, tree)
    native = namespace is CategoryNamespace.NATIVE
    return CategoryRef(
        id=category_id,
        # El nombre solo se toma del proveedor o del árbol: nunca se inventa.
        name=name or tree.get(category_id),
        namespace=namespace,
        indexer=indexer_name if native else None,
        indexer_id=indexer_id if native else None,
    )


def _parse_datetime(value: Any) -> datetime | None:
    text = as_str(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def parent_category_id(category_id: int) -> int | None:
    """Reexportado desde :mod:`.categories` para compatibilidad de imports."""
    return _parent_category_id(category_id)


class ProwlarrClient:
    """Cliente HTTP mínimo de la API v1 de Prowlarr (solo GET)."""

    def __init__(
        self,
        settings: ProwlarrSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings.require_configured()
        base_url = settings.base_url
        if base_url is None:  # pragma: no cover - require_configured lo garantiza
            raise ConfigurationError("Falta PROWLARR_BASE_URL")

        self._api_key = settings.api_key_value
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Api-Key": self._api_key, "Accept": "application/json"},
            timeout=settings.timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(
        self,
        path: str,
        params: Sequence[tuple[str, str]] | None = None,
    ) -> Any:
        try:
            response = await self._client.get(path, params=list(params) if params else None)
        except httpx.TimeoutException:
            raise ProviderTimeoutError(
                PROVIDER_NAME, f"tiempo de espera agotado al llamar a {path}"
            ) from None
        except httpx.HTTPError:
            raise ProviderUnavailableError(
                PROVIDER_NAME, f"no se puede conectar con Prowlarr ({path})"
            ) from None

        status = response.status_code
        if status in (401, 403):
            raise ProviderAuthError(
                PROVIDER_NAME,
                f"autenticación rechazada ({status}). Revisa PROWLARR_API_KEY.",
            )
        if status in (400, 422):
            detail = self._error_detail(response)
            message = f"la API ha rechazado la petición ({status})"
            raise ProviderRequestError(
                PROVIDER_NAME, f"{message}: {detail}" if detail else message
            )
        if status == 404:
            raise ProviderResponseError(PROVIDER_NAME, f"recurso no disponible ({path})")
        if status >= 500:
            raise ProviderUnavailableError(
                PROVIDER_NAME, f"Prowlarr ha respondido con un error interno ({status})"
            )
        if status >= 400:
            raise ProviderResponseError(PROVIDER_NAME, f"respuesta inesperada ({status})")

        try:
            return response.json()
        except ValueError:
            raise ProviderResponseError(
                PROVIDER_NAME, "la respuesta no es JSON válido"
            ) from None

    def _error_detail(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return ""
        if not isinstance(payload, Mapping):
            return ""
        parts: list[str] = []
        for key in ("message", "title", "description", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        errors = payload.get("errors")
        if isinstance(errors, Mapping):
            for field, messages in errors.items():
                if isinstance(messages, list):
                    joined = "; ".join(str(message) for message in messages)
                    parts.append(f"{field}: {joined}")
        detail = " | ".join(parts)
        # Redactar ANTES de truncar: cortar primero podría dejar media clave fuera.
        return redact_text(detail, [self._api_key])[:MAX_ERROR_DETAIL]

    async def get_system_status(self) -> Mapping[str, Any]:
        payload = await self._get_json(STATUS_PATH)
        return self._require_mapping(payload, STATUS_PATH)

    async def get_indexers(self) -> list[Mapping[str, Any]]:
        payload = await self._get_json(INDEXER_PATH)
        return self._require_mapping_list(payload, INDEXER_PATH)

    async def get_indexer_stats(self) -> Mapping[str, Any]:
        payload = await self._get_json(INDEXER_STATS_PATH)
        return self._require_mapping(payload, INDEXER_STATS_PATH)

    async def get_categories(self) -> list[Mapping[str, Any]]:
        payload = await self._get_json(CATEGORIES_PATH)
        return self._require_mapping_list(payload, CATEGORIES_PATH)

    async def search(
        self,
        *,
        query: str,
        search_type: str | None = None,
        categories: Sequence[int] = (),
        indexer_ids: Sequence[int] | None = None,
    ) -> list[Mapping[str, Any]]:
        """Búsqueda agregada. Devuelve el conjunto completo (sin recortar)."""
        params: list[tuple[str, str]] = [("query", query)]
        if search_type:
            params.append(("type", search_type))
        if indexer_ids:
            params.extend(("indexerIds", str(indexer_id)) for indexer_id in indexer_ids)
        # Claves repetidas: la API rechaza listas separadas por comas.
        params.extend(("categories", str(category_id)) for category_id in categories)

        payload = await self._get_json(SEARCH_PATH, params)
        return self._require_mapping_list(payload, SEARCH_PATH)

    @staticmethod
    def _require_mapping(payload: Any, path: str) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ProviderResponseError(
                PROVIDER_NAME, f"se esperaba un objeto JSON en {path}"
            )
        return payload

    @staticmethod
    def _require_mapping_list(payload: Any, path: str) -> list[Mapping[str, Any]]:
        if not isinstance(payload, list):
            raise ProviderResponseError(
                PROVIDER_NAME, f"se esperaba una lista JSON en {path}"
            )
        items: list[Mapping[str, Any]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise ProviderResponseError(
                    PROVIDER_NAME, f"elemento inesperado en la respuesta de {path}"
                )
            items.append(item)
        return items


class ProwlarrProvider(SearchProvider):
    """Implementación de :class:`SearchProvider` sobre la API de Prowlarr."""

    name = PROVIDER_NAME
    capabilities_summary = (
        "Búsqueda de releases vía Prowlarr API v1 (solo GET), con filtrado por "
        "categorías Newznab."
    )

    def __init__(
        self,
        settings: ProwlarrSettings,
        *,
        client: ProwlarrClient | None = None,
        category_cache_ttl: float = CATEGORY_CACHE_TTL_SECONDS,
    ) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._category_cache_ttl = category_cache_ttl
        self._category_tree: dict[int, str] | None = None
        self._category_tree_at = 0.0
        self._category_tree_error: str | None = None
        self._indexers: list[Mapping[str, Any]] | None = None
        self._indexers_at = 0.0

    # -- estado ---------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._settings.configured

    @property
    def missing_configuration(self) -> list[str]:
        return self._settings.missing_variables

    def secret_values(self) -> tuple[str, ...]:
        if not self.configured:
            return ()
        return (self._settings.api_key_value,)

    def _require_client(self) -> ProwlarrClient:
        if not self.configured:
            self._settings.require_configured()
        if self._client is None:
            self._client = ProwlarrClient(self._settings)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # -- operaciones MCP -------------------------------------------------

    async def get_status(self) -> ProviderStatus:
        if not self.configured:
            missing = ", ".join(self.missing_configuration)
            return ProviderStatus(
                provider=self.name,
                configured=False,
                reachable=False,
                error=f"Configuración incompleta: faltan {missing}",
            )

        client = self._require_client()
        started = time.perf_counter()
        try:
            payload = await client.get_system_status()
            latency_ms = int((time.perf_counter() - started) * 1000)
            enabled = await self._count_enabled_indexers(client)
        except ProviderError as exc:
            return ProviderStatus(
                provider=self.name,
                configured=True,
                reachable=False,
                error=exc.message,
            )

        version = as_str(payload.get("version"))
        return ProviderStatus(
            provider=self.name,
            configured=True,
            reachable=True,
            version=version,
            latency_ms=latency_ms,
            indexers_enabled=enabled,
        )

    async def _count_enabled_indexers(self, client: ProwlarrClient) -> int | None:
        """Cuenta solo los indexadores que declaran explícitamente ``enable: true``."""
        try:
            indexers = await client.get_indexers()
        except ProviderError:
            return None
        return sum(1 for item in indexers if item.get("enable") is True)

    async def list_indexers(self) -> list[IndexerInfo]:
        client = self._require_client()
        raw_indexers = await client.get_indexers()
        stats_by_id = await self._indexer_stats(client)
        tree = await self._load_category_tree(client)

        indexers: list[IndexerInfo] = []
        for raw in raw_indexers:
            name = as_str(raw.get("name"))
            if name is None:
                continue
            indexer_id = as_int(raw.get("id"))
            indexers.append(
                IndexerInfo(
                    id=indexer_id,
                    name=name,
                    enabled=as_bool(raw.get("enable")),
                    protocol=as_str(raw.get("protocol")),
                    privacy=as_str(raw.get("privacy")),
                    supports_search=as_bool(raw.get("supportsSearch")),
                    supports_rss=as_bool(raw.get("supportsRss")),
                    categories=self._indexer_categories(raw, tree),
                    stats=stats_by_id.get(indexer_id) if indexer_id is not None else None,
                )
            )
        return indexers

    async def _indexer_stats(self, client: ProwlarrClient) -> dict[int, IndexerStats]:
        """Estadísticas best-effort: si fallan, el listado sigue siendo válido."""
        try:
            payload = await client.get_indexer_stats()
        except ProviderError:
            return {}

        stats: dict[int, IndexerStats] = {}
        entries = payload.get("indexers")
        if not isinstance(entries, list):
            return stats
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            indexer_id = as_int(entry.get("indexerId"))
            if indexer_id is None:
                continue
            stats[indexer_id] = IndexerStats(
                average_response_time_ms=as_int(entry.get("averageResponseTime")),
                queries=as_int(entry.get("numberOfQueries")),
                failed_queries=as_int(entry.get("numberOfFailedQueries")),
                grabs=as_int(entry.get("numberOfGrabs")),
                failed_grabs=as_int(entry.get("numberOfFailedGrabs")),
            )
        return stats

    async def get_capabilities(self) -> ProviderCapabilities:
        client = self._require_client()
        tree = await self._load_category_tree(client)

        warnings: list[str] = [SEMANTIC_FILTER_WARNING]
        indexers_error: str | None = None
        try:
            indexers = await self.list_indexers()
        except ProviderError as exc:
            # Sin listado no se puede afirmar qué indexadores participan.
            indexers = []
            indexers_error = exc.message

        # Solo los indexadores con `enable: true` confirmado cuentan como
        # participantes: Prowlarr descarta antes de buscar los que no cubren el
        # filtro y un estado desconocido no permite afirmar participación.
        confirmed_indexers = [indexer for indexer in indexers if indexer.enabled is True]
        unknown_state = sorted({indexer.name for indexer in indexers if indexer.enabled is None})
        # `participation_known` responde a una única pregunta: ¿se conoce el estado
        # de participación de TODOS los indexadores relevantes? Es `false` si no se
        # pudo leer el listado o si alguno no informa su `enable`. Un listado leído
        # y vacío es un estado conocido: no hay indexadores cuyo estado ignorar.
        participation_known = indexers_error is None and not unknown_state
        if indexers_error is not None:
            warnings.append(
                "No se ha podido leer el listado de indexadores "
                f"({indexers_error}): se desconoce qué indexadores participan."
            )
        elif not confirmed_indexers:
            warnings.append(
                "Prowlarr no tiene indexadores con habilitación confirmada: cualquier "
                "búsqueda devolverá resultados vacíos."
            )
        if unknown_state:
            warnings.append(
                f"{len(unknown_state)} indexador(es) no informan del campo 'enable': su "
                "participación es desconocida y no se cuentan como participantes."
            )
        if not tree:
            warnings.append(
                "No se ha podido leer el árbol de categorías de Prowlarr; las "
                "correspondencias no se han verificado en esta ejecución."
            )

        search_types: list[MediaTypeCapability] = []
        for spec in MEDIA_TYPE_SPECS.values():
            declared_ids = list(spec.category_ids)
            present = [
                category_id for category_id in declared_ids if not tree or category_id in tree
            ]
            mode: Literal["filtered", "general"] = "filtered" if present else "general"
            capability_warnings = list(spec.notes)
            if unknown_state:
                capability_warnings.append(
                    f"{len(unknown_state)} indexador(es) con estado de habilitación "
                    "desconocido no se cuentan como participantes."
                )

            if mode == "filtered":
                support = (
                    [
                        self._support_level(indexer, present, tree_available=True)
                        for indexer in confirmed_indexers
                    ]
                    if tree
                    else []
                )
                declared = sorted(
                    {
                        item.indexer
                        for item in support
                        if item.support is CategorySupportLevel.DECLARED
                    }
                )
                inferred = sorted(
                    {
                        item.indexer
                        for item in support
                        if item.support is CategorySupportLevel.INFERRED
                    }
                )
                no_categories = [
                    item.indexer
                    for item in support
                    if item.support is CategorySupportLevel.UNKNOWN
                ]
                # Prowlarr descarta antes de buscar los indexadores cuya
                # correspondencia no cubre el filtro (verificado con
                # `indexerstats`): solo los declarados se consultan con certeza.
                participating = declared
                if not tree:
                    declared, inferred, no_categories = [], [], []
                    participating = []
                    capability_warnings.append(
                        "Sin árbol de categorías no se puede verificar qué indexadores "
                        "declaran la categoría ni confirmar su participación en el filtro."
                    )
                elif not declared:
                    detail = (
                        f"{len(inferred)} indexador(es) solo se relacionan por jerarquía "
                        "(compatibilidad inferida) y Prowlarr podría consultarlos o no: no "
                        "se puede confirmar desde la API."
                        if inferred
                        else "el filtrado devolverá resultados vacíos hasta añadir uno adecuado."
                    )
                    capability_warnings.append(
                        "Ningún indexador habilitado declara exactamente esta categoría; "
                        + detail
                    )
                elif inferred:
                    capability_warnings.append(
                        f"{len(inferred)} indexador(es) más podrían ser consultados por "
                        "compatibilidad inferida, pero su consulta no se puede confirmar "
                        "desde la API."
                    )
                if participating:
                    capability_warnings.append(
                        f"Prowlarr consultará {len(participating)} indexador(es) con soporte "
                        "declarado; participar no implica que devuelvan resultados."
                    )
                if no_categories:
                    capability_warnings.append(
                        f"{len(no_categories)} indexador(es) no publican categorías: su "
                        "compatibilidad es desconocida y no se cuentan como participantes."
                    )
            else:
                support = []
                declared = []
                inferred = []
                participating = sorted({indexer.name for indexer in confirmed_indexers})
                capability_warnings.insert(0, GENERAL_SEARCH_WARNING)
                if participating:
                    capability_warnings.append(
                        "Sin filtro de categoría participan todos los indexadores "
                        "habilitados; eso no implica que vayan a devolver resultados."
                    )

            search_types.append(
                MediaTypeCapability(
                    media_type=spec.media_type,
                    mode=mode,
                    category_ids=present,
                    category_names=[
                        tree[category_id] for category_id in present if category_id in tree
                    ],
                    mapping_verified=spec.mapping_verified,
                    indexers_supporting=declared,
                    indexers_inferred=inferred,
                    indexers_participating=participating,
                    indexers_unknown_state=unknown_state,
                    participation_known=participation_known,
                    indexer_support=support,
                    warnings=capability_warnings,
                )
            )

        categories = [
            CategoryRef(id=category_id, name=name, namespace=CategoryNamespace.NEWZNAB)
            for category_id, name in sorted(tree.items())
        ]
        return ProviderCapabilities(
            provider=self.name,
            search_types=search_types,
            categories=categories,
            indexers=indexers,
            warnings=warnings,
        )

    async def search(self, request: ProviderSearchRequest) -> ProviderSearchResult:
        client = self._require_client()
        spec = spec_for(request.media_type)
        tree = await self._load_category_tree(client)

        category_ids, applied, category_warnings = self._resolve_categories(
            spec, request.categories, tree
        )
        warnings: list[str] = []
        if self._category_tree_error:
            warnings.append(
                "No se ha podido leer el árbol de categorías de Prowlarr ("
                f"{self._category_tree_error}); el filtro por categoría se envía sin "
                "verificar en esta ejecución."
            )
        if request.year is not None:
            warnings.append(
                "Prowlarr no expone el parámetro 'year' en /api/v1/search: el año "
                "solicitado no se aplica como filtro."
            )
        warnings.extend(category_warnings)
        warnings.extend(spec.notes)

        used_general_search = not category_ids
        if used_general_search:
            warnings.insert(0, GENERAL_SEARCH_WARNING)

        raw_results = await client.search(
            query=request.query,
            search_type=spec.provider_search_type,
            categories=category_ids,
        )

        filter_ids = tuple(category_ids)
        records: list[ReleaseRecord] = []
        skipped = 0
        for raw in raw_results:
            record = self._to_record(raw, tree, filter_ids)
            if record is None:
                skipped += 1
                continue
            records.append(record)

        if skipped:
            warnings.append(
                f"{skipped} resultado(s) descartados por no incluir título utilizable."
            )

        raw_indexers = await self._load_indexers(client)
        per_indexer = self._window_summaries(raw_indexers, records)
        empty_result = (
            None
            if records
            else await self._assess_empty_result(client, raw_indexers, filter_ids, tree)
        )

        return ProviderSearchResult(
            results=records,
            warnings=_dedupe(warnings),
            applied_categories=applied,
            used_general_search=used_general_search,
            # La API no informa del total del índice: `len(records)` sería una
            # ventana, no el total, y presentarlo como total sería falso.
            total_available=None,
            # `truncated` solo debe ser True con evidencia de corte. La API de
            # Prowlarr no aporta ninguna (ni el total, ni el tamaño de página, ni
            # un indicador de "hay más"), así que queda desconocido: `None`.
            truncated=None,
            per_indexer=per_indexer,
            empty_result=empty_result,
        )

    # -- ventana de resultados y diagnósticos ----------------------------

    async def _load_indexers(self, client: ProwlarrClient) -> list[Mapping[str, Any]]:
        """Metadatos de indexadores con caché corta (solo lectura)."""
        now = time.monotonic()
        cached = self._indexers
        if cached is not None and now - self._indexers_at < self._category_cache_ttl:
            return cached
        try:
            indexers = await client.get_indexers()
        except ProviderError:
            return []
        self._indexers = indexers
        self._indexers_at = time.monotonic()
        return indexers

    def _window_summaries(
        self,
        raw_indexers: Sequence[Mapping[str, Any]],
        records: Sequence[ReleaseRecord],
    ) -> list[IndexerWindowSummary]:
        """Resultados recibidos por indexador y fiabilidad de su ventana.

        ``supportsPagination`` se conserva como **dato declarado** por el
        proveedor, no como evidencia de corte: que un indexador admita paginar no
        demuestra que esta búsqueda haya devuelto solo una página ni que se hayan
        omitido resultados, y tampoco dice nada si el indexador devolvió cero
        resultados. Por eso ``window`` queda siempre en ``unknown`` y el
        truncamiento no se deduce de él. Recibir N resultados tampoco demuestra
        que se haya alcanzado ningún límite: no se codifica ningún número.
        """
        counts: dict[tuple[int | None, str], int] = {}
        for record in records:
            key = (record.indexer_id, record.indexer or "")
            counts[key] = counts.get(key, 0) + 1

        summaries: list[IndexerWindowSummary] = []
        seen: set[tuple[int | None, str]] = set()
        for raw in raw_indexers:
            indexer_name = as_str(raw.get("name"))
            if indexer_name is None or raw.get("enable") is False:
                continue
            indexer_id = as_int(raw.get("id"))
            key = (indexer_id, indexer_name)
            summaries.append(
                IndexerWindowSummary(
                    indexer=indexer_name,
                    indexer_id=indexer_id,
                    received_count=counts.get(key, 0),
                    # Sin evidencia sobre la ventana recibida, no se afirma nada.
                    window=WindowStatus.UNKNOWN,
                    supports_pagination=as_bool(raw.get("supportsPagination")),
                )
            )
            seen.add(key)

        # Resultados de indexadores que no aparecen en el listado (p. ej. sin
        # identificador): se informan sin afirmar nada sobre su ventana.
        for (indexer_id, indexer_name), count in sorted(
            counts.items(), key=lambda item: (item[0][0] or 0, item[0][1])
        ):
            if (indexer_id, indexer_name) in seen:
                continue
            summaries.append(
                IndexerWindowSummary(
                    indexer=indexer_name or None,
                    indexer_id=indexer_id,
                    received_count=count,
                )
            )
        return summaries

    async def _assess_empty_result(
        self,
        client: ProwlarrClient,
        raw_indexers: Sequence[Mapping[str, Any]],
        filter_ids: Sequence[int],
        tree: Mapping[int, str],
    ) -> EmptyResultAssessment:
        """Reúne evidencia sobre una respuesta vacía. No afirma ausencia de datos.

        Un indexador **sin campo ``enable``** no es un indexador deshabilitado: se
        trata como candidato y, si existe alguno en ese estado, no se afirma
        cuántos indexadores están habilitados. Sin árbol de categorías no se puede
        decidir compatibilidad: se deja desconocida.
        """
        candidates = [raw for raw in raw_indexers if raw.get("enable") is not False]
        unknown_state = [raw for raw in raw_indexers if raw.get("enable") is None]
        available = (
            None if (not raw_indexers or unknown_state) else len(candidates)
        )

        compatible: int | None = None
        no_compatible: bool | None = None
        if filter_ids and raw_indexers and tree:
            # "Compatible" = declara la categoría o una relacionada por jerarquía
            # (inferida). La distinción fina está en get_search_capabilities.
            compatible = sum(
                1
                for raw in candidates
                if _relation_level(self._indexer_categories(raw, tree), filter_ids)
                in (CategorySupportLevel.DECLARED, CategorySupportLevel.INFERRED)
            )
            no_compatible = compatible == 0

        reported: int | None = None
        failure: bool | None = None
        try:
            stats = await client.get_indexer_stats()
        except ProviderError:
            stats = None
        if stats is not None:
            entries = stats.get("indexers")
            if isinstance(entries, list):
                candidate_ids = {
                    indexer_id
                    for raw in candidates
                    if (indexer_id := as_int(raw.get("id"))) is not None
                }
                total = 0
                counted = False
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        continue
                    entry_id = as_int(entry.get("indexerId"))
                    if candidate_ids and entry_id not in candidate_ids:
                        continue
                    failed = as_int(entry.get("numberOfFailedQueries"))
                    if failed is None:
                        continue
                    total += failed
                    counted = True
                if counted:
                    reported = total
                    # Cero fallos NO demuestra salud: solo se afirma el True.
                    failure = True if total > 0 else None

        return EmptyResultAssessment(
            indexers_available=available,
            indexers_compatible_with_filter=compatible,
            no_compatible_indexers=no_compatible,
            reported_failed_queries=reported,
            indexer_failure_reported=failure,
        )

    # -- categorías ------------------------------------------------------

    async def _load_category_tree(self, client: ProwlarrClient) -> dict[int, str]:
        """Árbol de categorías con caché.

        Un fallo al leer el árbol **no** aborta la operación: se degrada a árbol
        vacío y se informa por advertencia. Una API caída se detectará al hacer
        la búsqueda real, con el error correcto.
        """
        now = time.monotonic()
        cached = self._category_tree
        if cached and now - self._category_tree_at < self._category_cache_ttl:
            return cached
        try:
            raw = await client.get_categories()
        except ProviderError as exc:
            self._category_tree_error = exc.message
            return {}

        tree = flatten_categories(raw)
        self._category_tree = tree
        self._category_tree_at = time.monotonic()
        self._category_tree_error = None
        return tree

    def _resolve_categories(
        self,
        spec: MediaTypeSpec,
        requested: Sequence[str],
        tree: Mapping[int, str],
    ) -> tuple[list[int], list[CategoryRef], list[str]]:
        """Decide el filtro de categorías y las advertencias asociadas."""
        warnings: list[str] = []

        if requested:
            resolved, validation_warnings = self._resolve_requested_categories(
                requested, tree
            )
            warnings.extend(validation_warnings)
            return (
                resolved,
                [
                    _build_category_ref(category_id, None, tree)
                    for category_id in resolved
                ],
                warnings,
            )

        if spec.uses_general_search:
            return [], [], warnings

        present = [category_id for category_id in spec.category_ids if not tree or category_id in tree]
        missing = [category_id for category_id in spec.category_ids if tree and category_id not in tree]
        if missing:
            warnings.append(
                "Las categorías "
                + ", ".join(str(category_id) for category_id in missing)
                + " no existen en este Prowlarr; se ignora esa parte del filtro."
            )
        if not present:
            warnings.append(
                "Ninguna categoría verificada para este tipo está disponible en el "
                "proveedor: se usa búsqueda general."
            )
            return [], [], warnings
        return present, [
            _build_category_ref(category_id, None, tree) for category_id in present
        ], warnings

    def _resolve_requested_categories(
        self,
        requested: Sequence[str],
        tree: Mapping[int, str],
    ) -> tuple[list[int], list[str]]:
        """Valida las categorías pedidas antes de consultar al proveedor.

        Con árbol disponible: solo se aceptan ids/nombres Newznab presentes en él.
        Sin árbol: no se puede verificar, así que se aceptan ids numéricos con una
        advertencia explícita (nunca se afirma que se hayan validado).
        """
        warnings: list[str] = []
        resolved: list[int] = []
        unresolved: list[str] = []
        native: list[int] = []
        unknown_ids: list[int] = []

        for token in requested:
            cleaned = token.strip()
            if not cleaned:
                continue
            number = as_int(cleaned)
            if number is not None:
                # Un id nativo se rechaza SIEMPRE, también sin árbol: su rango lo
                # identifica y el filtro de Prowlarr es global.
                if number >= CUSTOM_CATEGORY_MIN:
                    native.append(number)
                    continue
                if not tree:
                    # Degradación: no se puede validar un id Newznab sin árbol.
                    resolved.append(number)
                    continue
                if number in tree:
                    resolved.append(number)
                else:
                    unknown_ids.append(number)
                continue
            if not tree:
                raise InvalidRequestError(
                    "No se puede resolver la categoría "
                    f"'{cleaned}' por nombre: el árbol de categorías del proveedor no "
                    "está disponible. Usa un id numérico Newznab (p. ej. 2040)."
                )
            match = self._match_category_name(cleaned, tree)
            if match is None:
                unresolved.append(cleaned)
            else:
                resolved.append(match)

        if native:
            sample = ", ".join(str(category_id) for category_id in native[:5])
            raise InvalidRequestError(
                f"Categoría(s) nativa(s) no admitida(s) como filtro: {sample}. "
                "Prowlarr aplica el filtro de categorías a todos los indexadores y un id "
                "nativo solo tiene sentido dentro de su indexador, así que no se puede "
                "usar de forma segura ni siquiera cuando el árbol no está disponible. "
                "Usa una categoría Newznab (p. ej. 5070) o busca por texto."
            )
        if unknown_ids:
            sample = ", ".join(str(category_id) for category_id in unknown_ids[:5])
            raise InvalidRequestError(
                f"Categoría(s) inexistente(s) en el árbol Newznab del proveedor: {sample}. "
                "Consulta get_search_capabilities para ver las categorías disponibles."
            )
        if unresolved:
            sample = ", ".join(f"'{item}'" for item in unresolved[:5])
            raise InvalidRequestError(
                f"Categoría(s) no reconocida(s): {sample}. Usa un id numérico del árbol "
                "de categorías del proveedor o un nombre exacto (p. ej. 'Movies/HD')."
            )

        if not tree and resolved:
            warnings.append(
                "No se ha podido verificar el filtro de categorías contra el árbol del "
                "proveedor: los ids se envían sin validar."
            )
        # Elimina duplicados conservando el orden.
        return list(dict.fromkeys(resolved)), warnings

    @staticmethod
    def _match_category_name(name: str, tree: Mapping[int, str]) -> int | None:
        target = name.casefold()
        exact = [cid for cid, label in tree.items() if label.casefold() == target]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None  # ambiguo: se trata como no reconocido
        leaf = [cid for cid, label in tree.items() if label.rsplit("/", 1)[-1].casefold() == target]
        if len(leaf) == 1:
            return leaf[0]
        return None

    def _support_level(
        self,
        indexer: IndexerInfo,
        category_ids: Sequence[int],
        *,
        tree_available: bool,
    ) -> IndexerCategorySupport:
        """Nivel de soporte declarado/inferido de un indexador para un filtro.

        No se presenta una inferencia jerárquica como soporte confirmado: solo
        cuenta como ``declared`` el indexador que anuncia exactamente una de las
        categorías solicitadas. Sin árbol de categorías no se puede afirmar nada:
        el nivel es ``unknown``.
        """
        if not tree_available:
            return IndexerCategorySupport(
                indexer=indexer.name,
                indexer_id=indexer.id,
                support=CategorySupportLevel.UNKNOWN,
            )

        if not indexer.categories:
            return IndexerCategorySupport(
                indexer=indexer.name,
                indexer_id=indexer.id,
                support=CategorySupportLevel.UNKNOWN,
            )

        advertised = {
            category.id: category
            for category in indexer.categories
            if category.namespace is CategoryNamespace.NEWZNAB
        }
        level = _relation_level(advertised.values(), category_ids)
        if level is CategorySupportLevel.UNKNOWN:
            # Publica categorías, pero ninguna comparable con el árbol Newznab.
            level = CategorySupportLevel.NONE
        if level is CategorySupportLevel.NONE:
            return IndexerCategorySupport(
                indexer=indexer.name,
                indexer_id=indexer.id,
                support=level,
            )

        targets = set(category_ids)
        if level is CategorySupportLevel.DECLARED:
            matched = sorted(set(advertised) & targets)
        elif level is CategorySupportLevel.INFERRED:
            matched = sorted(
                {
                    advertised_id
                    for advertised_id in advertised
                    if any(categories_related(advertised_id, target) for target in targets)
                }
            )
        else:
            matched = []

        names = [advertised[category_id].name or "" for category_id in matched]
        return IndexerCategorySupport(
            indexer=indexer.name,
            indexer_id=indexer.id,
            support=level,
            matched_category_ids=matched,
            matched_category_names=[name for name in names if name],
        )

    def _indexer_categories(
        self, raw: Mapping[str, Any], tree: Mapping[int, str]
    ) -> list[CategoryRef]:
        capabilities = raw.get("capabilities")
        if not isinstance(capabilities, Mapping):
            return []
        entries = capabilities.get("categories")
        if not isinstance(entries, list):
            return []
        indexer_name = as_str(raw.get("name"))
        indexer_id = as_int(raw.get("id"))
        categories: list[CategoryRef] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            category_id = as_int(entry.get("id"))
            if category_id is None:
                continue
            categories.append(
                _build_category_ref(
                    category_id,
                    as_str(entry.get("name")),
                    tree,
                    indexer_name=indexer_name,
                    indexer_id=indexer_id,
                )
            )
        return categories

    # -- mapeo de resultados ---------------------------------------------

    def _to_record(
        self,
        raw: Mapping[str, Any],
        tree: Mapping[int, str],
        filter_ids: Sequence[int] = (),
    ) -> ReleaseRecord | None:
        title = as_str(raw.get("title"))
        if title is None:
            return None

        guid = as_str(raw.get("guid"))
        magnet = guid if guid and guid.startswith("magnet:?") else None
        if magnet is None:
            candidate = as_str(raw.get("magnetUrl"))
            # Solo se acepta un magnet real; los proxies de Prowlarr llevan apikey.
            if candidate and candidate.startswith("magnet:?"):
                magnet = candidate

        categories = self._result_categories(raw, tree)

        return ReleaseRecord(
            title=title,
            guid=guid,
            # Campo de primera clase: canónico o None. El valor crudo de la API
            # se conserva en `extra` (provider_metadata) sin tocar.
            info_hash=as_info_hash(raw.get("infoHash")),
            indexer=as_str(raw.get("indexer")),
            indexer_id=as_int(raw.get("indexerId")),
            size_bytes=as_int(raw.get("size")),
            seeders=as_int(raw.get("seeders")),
            leechers=as_int(raw.get("leechers")),
            grabs=as_int(raw.get("grabs")),
            publish_date=_parse_datetime(raw.get("publishDate")),
            info_url=sanitize_url(as_str(raw.get("infoUrl")) or "") or None,
            magnet_uri=magnet,
            protocol=as_str(raw.get("protocol")),
            indexer_flags=as_str_list(raw.get("indexerFlags")),
            categories=categories,
            external_ids=self._external_ids(raw),
            # Solo se clasifica con filtro y árbol disponible: sin árbol no se
            # puede afirmar el namespace de ninguna categoría.
            category_match=(
                classify_category_match(categories, filter_ids)
                if filter_ids and tree
                else None
            ),
            # Prowlarr no entrega metadatos técnicos estructurados; no se
            # rellenan para no inventar datos a partir del título.
            structured_metadata={},
            extra=self._extra_fields(raw),
        )

    @staticmethod
    def _result_categories(
        raw: Mapping[str, Any], tree: Mapping[int, str]
    ) -> list[CategoryRef]:
        entries = raw.get("categories")
        if not isinstance(entries, list):
            return []
        indexer_name = as_str(raw.get("indexer"))
        indexer_id = as_int(raw.get("indexerId"))
        categories: list[CategoryRef] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            category_id = as_int(entry.get("id"))
            if category_id is None:
                continue
            categories.append(
                _build_category_ref(
                    category_id,
                    as_str(entry.get("name")),
                    tree,
                    indexer_name=indexer_name,
                    indexer_id=indexer_id,
                )
            )
        return categories

    @staticmethod
    def _external_ids(raw: Mapping[str, Any]) -> ExternalIds | None:
        imdb = as_int(raw.get("imdbId"))
        tmdb = as_int(raw.get("tmdbId"))
        tvdb = as_int(raw.get("tvdbId"))
        tvmaze = as_int(raw.get("tvMazeId"))
        ids = ExternalIds(
            imdb=str(imdb) if imdb else None,
            tmdb=str(tmdb) if tmdb else None,
            tvdb=str(tvdb) if tvdb else None,
            tvmaze=str(tvmaze) if tvmaze else None,
        )
        return None if ids.is_empty else ids

    @staticmethod
    def _extra_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
        """Datos originales útiles, sin enlaces de descarga ni proxies con clave."""
        extra: dict[str, Any] = {}
        age = as_int(raw.get("age"))
        if age is not None:
            extra["age_days"] = age
        file_name = as_str(raw.get("fileName"))
        if file_name is not None:
            extra["file_name"] = file_name
        info_hash = as_str(raw.get("infoHash"))
        if info_hash is not None:
            extra["info_hash"] = info_hash
        return extra


def flatten_categories(
    nodes: Sequence[Mapping[str, Any]], acc: dict[int, str] | None = None
) -> dict[int, str]:
    """Aplana el árbol de categorías de Prowlarr en ``{id: nombre}``."""
    result: dict[int, str] = {} if acc is None else acc
    for node in nodes:
        category_id = as_int(node.get("id"))
        name = as_str(node.get("name"))
        if category_id is not None and name is not None:
            result[category_id] = name
        children = node.get("subCategories")
        if isinstance(children, list):
            flatten_categories([child for child in children if isinstance(child, Mapping)], result)
    return result


def _relation_level(
    advertised: Iterable[CategoryRef], target: Sequence[int]
) -> CategorySupportLevel:
    """Nivel de relación entre las categorías de un indexador y el filtro.

    Solo se comparan categorías con ``namespace == newznab``: las nativas o de
    namespace desconocido no son comparables con el árbol solicitado. ``DECLARED``
    exige coincidencia exacta; ``INFERRED`` es solo jerarquía (padre/hijo).
    """
    refs = list(advertised)
    if not refs:
        return CategorySupportLevel.UNKNOWN

    newznab_ids = {
        ref.id for ref in refs if ref.namespace is CategoryNamespace.NEWZNAB
    }
    targets = set(target)
    if newznab_ids & targets:
        return CategorySupportLevel.DECLARED
    if any(
        categories_related(advertised_id, target_id)
        for advertised_id in newznab_ids
        for target_id in targets
    ):
        return CategorySupportLevel.INFERRED
    return CategorySupportLevel.NONE


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
