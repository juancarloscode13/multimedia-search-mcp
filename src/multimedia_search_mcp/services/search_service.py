"""Servicio de búsqueda multimedia.

Orquesta proveedor + normalización + paginación. Es la única capa que conoce el
registro de proveedores; las herramientas MCP solo traducen entrada/salida.
"""

from __future__ import annotations

from itertools import combinations

from ..dedup import find_exact_duplicates
from ..errors import ConfigurationError
from ..models import (
    CategoryMatch,
    CategoryMatchSummary,
    DuplicateGroup,
    EmptyResultAssessment,
    IndexerInfo,
    MediaResult,
    PairRelation,
    ProviderCapabilities,
    ProviderInfo,
    ProviderSearchRequest,
    ProviderSearchResult,
    ProviderStatus,
    SearchRequest,
    SearchResponse,
)
from ..normalization import normalize_records
from ..relations import compare_pair
from ..providers.base import SearchProvider
from ..providers.registry import ProviderRegistry

__all__ = ["MultimediaSearchService"]


class MultimediaSearchService:
    """Fachada de búsqueda independiente del proveedor concreto."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    # -- proveedores -----------------------------------------------------

    def list_providers(self) -> list[ProviderInfo]:
        return self._registry.describe()

    def security_secrets(self) -> tuple[str, ...]:
        """Secretos declarados por los proveedores, para redactar la salida MCP.

        La capa MCP no necesita saber qué proveedores existen ni cómo se
        configuran: pregunta y obtiene una lista plana de valores a ocultar.
        """
        secrets: list[str] = []
        for provider in self._registry:
            secrets.extend(provider.secret_values())
        return tuple(dict.fromkeys(secret for secret in secrets if secret))

    def _select(self, provider_name: str | None) -> SearchProvider:
        if provider_name is None:
            return self._registry.default()
        provider = self._registry.get(provider_name)
        if not provider.configured:
            missing = ", ".join(provider.missing_configuration) or "desconocidos"
            raise ConfigurationError(
                f"El proveedor '{provider.name}' no está configurado. Faltan: {missing}"
            )
        return provider

    async def list_indexers(
        self, provider_name: str | None = None
    ) -> tuple[SearchProvider, list[IndexerInfo]]:
        provider = self._select(provider_name)
        return provider, await provider.list_indexers()

    async def get_provider_status(self, provider_name: str | None = None) -> ProviderStatus:
        if provider_name is None:
            # El estado debe poder consultarse aunque no haya nada configurado.
            try:
                provider = self._registry.default()
            except ConfigurationError:
                first = next(iter(self._registry), None)
                if first is None:
                    raise
                return await first.get_status()
            return await provider.get_status()
        return await self._registry.get(provider_name).get_status()

    async def get_capabilities(
        self, provider_name: str | None = None
    ) -> ProviderCapabilities:
        provider = self._select(provider_name)
        return await provider.get_capabilities()

    # -- búsqueda --------------------------------------------------------

    async def search(self, request: SearchRequest) -> SearchResponse:
        provider = self._select(request.provider)
        provider_result = await provider.search(
            ProviderSearchRequest(
                query=request.query,
                media_type=request.media_type,
                year=request.year,
                categories=tuple(request.categories),
            )
        )

        normalized = self._dedupe(
            normalize_records(
                provider_result.results,
                media_type=request.media_type,
                provider=provider.name,
            )
        )
        received = len(normalized)
        page = normalized[request.offset : request.offset + request.limit]
        end = request.offset + len(page)

        # Duplicados y heurísticas se calculan solo sobre la página devuelta: la
        # ventana recibida es una muestra y afirmar más sería inventar cobertura.
        duplicate_groups = sorted(
            find_exact_duplicates(page), key=lambda group: group.group_id
        )
        heuristic_relations = self._heuristic_relations(page, duplicate_groups)

        # `has_more` solo se afirma cuando quedan elementos en la ventana
        # recibida. Al final de la ventana no se puede saber si el proveedor
        # tiene más: se devuelve None, nunca False.
        has_more = True if end < received else None
        next_offset = end if has_more else None

        category_summary = self._category_summary(normalized, provider_result)
        warnings = list(provider_result.warnings)
        warnings.extend(self._category_warnings(category_summary))
        warnings.extend(
            self._window_warnings(
                provider_result, received=received, has_more=has_more
            )
        )
        if not normalized:
            warnings.extend(self._empty_warnings(provider_result.empty_result))
        elif request.offset >= received:
            warnings.append(
                f"El offset {request.offset} no devuelve resultados: la ventana recibida "
                f"tiene {received} resultados; no implica que no existan más en el proveedor."
            )

        return SearchResponse(
            provider=provider.name,
            query=request.query,
            media_type=request.media_type,
            year=request.year,
            applied_categories=provider_result.applied_categories,
            used_general_search=provider_result.used_general_search,
            received_count=received,
            returned_count=len(page),
            limit=request.limit,
            offset=request.offset,
            has_more=has_more,
            next_offset=next_offset,
            total_available=provider_result.total_available,
            truncated=provider_result.truncated,
            per_indexer=provider_result.per_indexer,
            category_match_summary=category_summary,
            empty_result=provider_result.empty_result,
            warnings=list(dict.fromkeys(warnings)),
            duplicate_groups=duplicate_groups,
            heuristic_relations=heuristic_relations,
            results=page,
        )

    @staticmethod
    def _heuristic_relations(
        page: list[MediaResult], groups: list[DuplicateGroup]
    ) -> list[PairRelation]:
        """Relaciones ``possible`` entre pares de la página, sin las ya confirmadas.

        Una pareja que aparece junta en un grupo confirmado no genera además una
        heurística: sería evidencia redundante y de menor fuerza. La supresión es
        por pareja directa; no se propaga entre grupos ni se crea transitividad.
        """
        covered: set[tuple[str, str]] = {
            pair
            for group in groups
            for pair in combinations(sorted(group.members), 2)
        }
        relations: list[PairRelation] = []
        for left, right in combinations(page, 2):
            relation = compare_pair(left, right)
            if relation is None:
                continue
            if (relation.left_id, relation.right_id) in covered:
                continue
            relations.append(relation)
        return sorted(relations, key=lambda relation: relation.relation_id)

    # -- clasificación y advertencias ------------------------------------

    @staticmethod
    def _category_summary(
        results: list[MediaResult], provider_result: ProviderSearchResult
    ) -> CategoryMatchSummary | None:
        """Recuenta los estados de categoría; ``None`` si no había filtro."""
        if not provider_result.applied_categories:
            return None

        summary = CategoryMatchSummary(applicable=True)
        for result in results:
            if result.category_match is CategoryMatch.IN_SUBTREE:
                summary.in_subtree += 1
            elif result.category_match is CategoryMatch.OFF_CATEGORY:
                summary.off_category += 1
            elif result.category_match is CategoryMatch.UNCATEGORIZED:
                summary.uncategorized += 1
            else:
                summary.unclassified += 1
        return summary

    @staticmethod
    def _category_warnings(summary: CategoryMatchSummary | None) -> list[str]:
        if summary is None:
            return []
        total = (
            summary.in_subtree
            + summary.off_category
            + summary.uncategorized
            + summary.unclassified
        )
        warnings: list[str] = []
        if summary.uncategorized:
            warnings.append(
                f"{summary.uncategorized} de {total} resultados no tienen categorías "
                "verificables: el proveedor puede conservarlos aunque no sea posible "
                "confirmar su correspondencia con el filtro solicitado. No se presentan "
                "como coincidencia de categoría confirmada."
            )
        if summary.off_category:
            warnings.append(
                f"{summary.off_category} de {total} resultados traen categorías que no "
                "pertenecen al filtro solicitado ni a su subárbol: no se clasifican como "
                "coincidencia de categoría."
            )
        if summary.unclassified:
            warnings.append(
                f"{summary.unclassified} de {total} resultados no pudieron clasificarse "
                "respecto al filtro (el proveedor no aportó el estado)."
            )
        return warnings

    @staticmethod
    def _window_warnings(
        provider_result: ProviderSearchResult, *, received: int, has_more: bool | None
    ) -> list[str]:
        if provider_result.truncated is True:
            return [
                "El proveedor ha indicado que la ventana recibida puede estar "
                "limitada: no se puede garantizar que no existan más resultados."
            ]
        if received and has_more is None:
            return [
                "No hay más resultados en la ventana recibida, pero el proveedor no "
                "informa del total disponible: no se puede garantizar que no existan más "
                "resultados en el índice."
            ]
        return []

    @staticmethod
    def _empty_warnings(assessment: EmptyResultAssessment | None) -> list[str]:
        warnings = [
            "Respuesta vacía: no se puede garantizar que no existan coincidencias. El "
            "proveedor puede devolver una lista vacía aunque un indexador falle "
            "internamente (timeout, error del parser) o no soporte la categoría pedida."
        ]
        if assessment is None:
            return warnings
        if assessment.indexers_available is None:
            warnings.append(
                "No se puede determinar cuántos indexadores están habilitados: el "
                "proveedor no informa de su estado o del listado en este momento."
            )
        if assessment.no_compatible_indexers is True:
            warnings.append(
                "Ningún indexador habilitado anuncia categorías compatibles con el filtro "
                "aplicado: la respuesta vacía es esperable por esa causa."
            )
        elif assessment.indexers_compatible_with_filter is not None:
            warnings.append(
                f"{assessment.indexers_compatible_with_filter} indexador(es) anuncian "
                "categorías compatibles con el filtro; aun así una lista vacía no demuestra "
                "que no haya coincidencias."
            )
        if assessment.indexer_failure_reported is True:
            warnings.append(
                f"El proveedor registra {assessment.reported_failed_queries} consultas "
                "fallidas en sus indexadores: parte de la búsqueda pudo fallar."
            )
        elif assessment.reported_failed_queries == 0:
            warnings.append(
                "El proveedor reporta cero consultas fallidas, pero ese contador no "
                "demuestra que los indexadores estén sanos."
            )
        else:
            warnings.append(
                "No hay datos de errores por indexador: no se puede verificar que todos "
                "hayan respondido correctamente."
            )
        return warnings

    @staticmethod
    def _dedupe(results: list[MediaResult]) -> list[MediaResult]:
        """Elimina duplicados exactos que algunos indexadores repiten."""
        seen: set[str] = set()
        unique: list[MediaResult] = []
        for result in results:
            if result.result_id in seen:
                continue
            seen.add(result.result_id)
            unique.append(result)
        return unique

    async def aclose(self) -> None:
        for provider in self._registry:
            await provider.aclose()
