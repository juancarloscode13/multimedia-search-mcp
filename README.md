# multimedia-search-mcp

[![CI](https://github.com/juancarloscode13/multimedia-search-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/juancarloscode13/multimedia-search-mcp/actions/workflows/ci.yml)

Servidor [MCP](https://modelcontextprotocol.io) en Python para **buscar** contenido multimedia
(películas, series, anime, libros y manga) a través de proveedores intercambiables. La primera
implementación es [Prowlarr](https://prowlarr.com).

> **Solo lectura.** El servidor únicamente ejecuta operaciones `GET` contra la API del proveedor.
> No añade torrents, no descarga ficheros, no pausa descargas, no modifica indexadores y no abre
> enlaces automáticamente. Está pensado para fuentes y contenido cuya distribución sea autorizada.

- **Estado:** `0.1.0` (alfa), primera versión pública.
- **Requisitos:** Python ≥ 3.12, [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
- **Licencia:** MIT.

## Características

- Búsqueda por título en seis tipos de contenido: `movie`, `series`, `anime`, `book`, `manga` y
  `other`.
- Resultados **normalizados**: todos los proveedores producen el mismo modelo, con categorías
  separadas por namespace (Newznab global o nativa de un indexador).
- **Ventana honesta**: se distingue lo recibido, lo devuelto y lo que se sabe. Nunca se presenta el
  tamaño de la ventana como total del índice.
- **Duplicados confirmados** entre resultados (mismo `info_hash` o misma entrada de un mismo
  indexador) y **relaciones heurísticas posibles** entre pares (`same_release` / `variant`), sin
  fusionar ni reordenar resultados.
- **Capacidades por proveedor e indexador**: qué categorías aplica cada tipo, qué indexadores las
  declaran y cuáles se consultarán con certeza.
- **Solo lectura**: cinco herramientas de consulta, sin efectos secundarios sobre el proveedor.

## Tipos de contenido

Correspondencias verificadas contra el árbol de categorías Newznab que expone Prowlarr 3.0.x:

| `media_type` | `type` enviado a Prowlarr | Categoría del filtro | Modo |
|---|---|---|---|
| `movie` | `movie` | 2000 Movies | filtrado |
| `series` | `tvsearch` | 5000 TV | filtrado |
| `anime` | `tvsearch` | 5070 TV/Anime | filtrado |
| `book` | `book` | 7000 Books | filtrado |
| `manga` | — | sin categoría verificada | general, con advertencia |
| `other` | — | sin filtro | general, por diseño |

`media_type` decide dos cosas: el tipo de búsqueda que se envía al proveedor y, **si no se pasan
`categories`**, la categoría del filtro. Si se pasan `categories`, **sustituyen** ese mapeo por
completo (no se suman ni se cruzan) y la búsqueda deja de ser general aunque el tipo sea `manga`
u `other`. La respuesta indica siempre lo aplicado en `applied_categories` y
`used_general_search`.

Las categorías describen el **filtrado del proveedor**, no el contenido: `TV (5000)` incluye
`TV/Anime (5070)`, así que un mismo resultado puede aparecer en series y en anime, y algunos
indexadores mapean a `Movies (2000)` contenido que no es una película. El tipo nunca se deduce del
título ni del nombre del fichero.

## Herramientas MCP

| Herramienta | Parámetros | Devuelve |
|---|---|---|
| `search_media` | `query`, `media_type`, `year?`, `categories?`, `limit?` (1–100), `offset?`, `provider?` | Resultados normalizados, ventana, clasificación de categorías, advertencias, duplicados y relaciones |
| `list_providers` | — | Proveedores registrados y si están configurados |
| `list_indexers` | `provider?` | Indexadores, estado y estadísticas (si existen) |
| `get_provider_status` | `provider?` | Conectividad, autenticación, versión y latencia |
| `get_search_capabilities` | `provider?` | Tipos admitidos, categorías aplicadas y soporte por indexador |

Todas devuelven un objeto JSON con `ok` (`true`/`false`). En caso de error:

```json
{"ok": false, "error": {"type": "ProviderAuthError", "provider": "prowlarr",
                        "message": "[prowlarr] autenticación rechazada (401). Revisa PROWLARR_API_KEY."}}
```

Ejemplos de uso (sin credenciales reales):

```
search_media(query="debian", media_type="other", limit=10, offset=0)
search_media(query="Prowlarr", media_type="book", categories=["Books/EBook"])
list_indexers()
get_search_capabilities()
```

## Respuesta de `search_media`

La respuesta distingue entre **lo que se recibió**, **lo que se devuelve** y **lo que se sabe**. Un
`null` significa «no se puede determinar», nunca cero ni «completo».

```json
{
  "ok": true,
  "provider": "prowlarr",
  "query": "debian",
  "media_type": "other",
  "year": null,
  "applied_categories": [],
  "used_general_search": true,
  "received_count": 26,
  "returned_count": 2,
  "limit": 2,
  "offset": 0,
  "has_more": true,
  "next_offset": 2,
  "total_available": null,
  "truncated": null,
  "per_indexer": [
    {"indexer": "LinuxTracker", "indexer_id": 2, "received_count": 25,
     "window": "unknown", "supports_pagination": false},
    {"indexer": "Nyaa.si", "indexer_id": 1, "received_count": 1,
     "window": "unknown", "supports_pagination": false}
  ],
  "category_match_summary": null,
  "empty_result": null,
  "warnings": ["Búsqueda general sin filtro de categoría: ..."],
  "duplicate_groups": [
    {"group_id": "dup:8c1f...", "relation": "confirmed", "kind": "same_publication",
     "basis": "info_hash", "anchor_id": "prowlarr:4f19ce21aa4c54dc",
     "members": ["prowlarr:4f19ce21aa4c54dc", "prowlarr:b2a1c0d9e8f7a6b5"]}
  ],
  "heuristic_relations": [
    {"relation_id": "rel:1a2b...", "left_id": "prowlarr:4f19ce21aa4c54dc",
     "right_id": "prowlarr:77de01aa33cc44dd", "relation": "possible",
     "kind": "variant", "evidence": ["base_title", "structure_match"],
     "differences": ["resolution"], "note": null}
  ],
  "results": [
    {"result_id": "prowlarr:4f19ce21aa4c54dc", "title": "...", "media_type": "other",
     "provider": "prowlarr", "indexer": "Nyaa.si",
     "categories": [
       {"id": 5070, "name": "TV/Anime", "namespace": "newznab"},
       {"id": 130420, "namespace": "native", "indexer": "Nyaa.si", "indexer_id": 1}
     ],
     "size_bytes": 17394617548, "seeders": 6, "publish_date": "2022-02-03T14:26:00Z",
     "info_hash": "fa9a682f7a7bf27a9b17e238adb7369d36bd0934",
     "info_url": "https://nyaa.si/view/1486155"}
  ]
}
```

| Campo | Significado |
|---|---|
| `received_count` | Resultados que el proveedor entregó y se pudieron normalizar y deduplicar. **Es una ventana, no el total del índice.** |
| `returned_count` | Resultados de esta página tras aplicar `limit`/`offset` local. |
| `has_more` | `true` = quedan resultados en la ventana recibida; `null` = no puede determinarse si el proveedor tiene más. **Nunca `false`.** |
| `next_offset` | `offset` de la siguiente página de la ventana recibida, o `null`. |
| `total_available` | Total real solo si el proveedor lo garantiza; con Prowlarr siempre `null`. |
| `truncated` | `true` solo con evidencia explícita de corte; `null` = no verificable (con Prowlarr siempre `null`). **Nunca `false` por ausencia de evidencia.** |
| `per_indexer[]` | Recuento por indexador, `window` (`unknown` salvo evidencia) y `supports_pagination` (capacidad declarada, no prueba de corte). La suma puede superar `received_count` porque este se cuenta tras deduplicar filas repetidas. |
| `category_match_summary` | Recuento `in_subtree` / `off_category` / `uncategorized` / `unclassified`; `null` si no había filtro. |
| `empty_result` | Solo si la lista viene vacía: indexadores disponibles, compatibles con el filtro y fallos reportados. |
| `duplicate_groups` | Grupos **confirmados** entre resultados de esta página; vacío si no hay. No elimina ni fusiona resultados. |
| `heuristic_relations` | Relaciones `possible` entre pares de esta página, sin las ya cubiertas por un grupo confirmado; vacío si no hay. Nunca forma grupos. |

### Identificador de publicación (`info_hash`)

Cada resultado incluye `info_hash` cuando el proveedor entrega un hash de BitTorrent válido:

- **Formatos aceptados:** 40 caracteres hexadecimales (BTIH v1) o 64 (BTIH v2, BEP 52).
- **Canonicalización:** solo se recortan espacios exteriores y se pasa a minúsculas.
- **Validación estricta:** un valor truncado, de otra longitud, no hexadecimal, en base32 o con
  prefijo (`0x…`) produce `info_hash: null`. Nunca se repara un valor inválido para que parezca un
  hash.
- **`null` significa «no disponible o no válido»**, no «sin hash» deducido del título.
- El valor original sin transformar sigue en `provider_metadata["info_hash"]`.

### Duplicados confirmados y relaciones posibles

Son dos conceptos distintos y se publican por separado; **una relación posible nunca se presenta
como duplicado confirmado**.

| `kind` | Base | Qué afirma |
|---|---|---|
| `same_publication` | `info_hash` | Mismo torrent. Única prueba válida **entre indexadores**. |
| `same_source_entry` | GUID + indexador | Misma entrada del mismo indexador. **No** demuestra contenido idéntico. |
| `same_release` (posible) | Título normalizado + tamaño | Título idéntico, tamaño conocido e idéntico, sin contradicciones ni diferencias técnicas. |
| `variant` (posible) | Título base + diferencia técnica | Título base idéntico (sin los fragmentos técnicos reconocidos) y al menos una diferencia técnica explícita. |

Reglas comunes:

- Los grupos confirmados exigen compatibilidad **por pares** dentro del grupo: no se encadena
  transitivamente, y un nombre de indexador ambiguo queda fuera en vez de atribuirse.
- Las relaciones heurísticas se calculan **solo sobre la página devuelta**; dos resultados en
  páginas distintas no se comparan. No hay grupos heurísticos ni transitividad: si A–B y B–C son
  variantes, A–C solo aparece si el par A–C cumple las reglas por sí mismo.
- La ausencia de un dato nunca cuenta como diferencia; un indicador ambiguo bloquea la relación.
- El tamaño solo interviene en `same_release` (debe ser conocido e igual).
- Las parejas ya cubiertas por un grupo confirmado no generan relación heurística.
- Ni los grupos ni las relaciones eliminan, fusionan o reordenan resultados: solo los referencian
  por `result_id`.

### Categorías y namespaces

| `category_match` | Significado |
|---|---|
| `in_subtree` | Al menos una categoría pertenece al filtro o a su subárbol Newznab. |
| `off_category` | Trae categorías, pero ninguna pertenece al filtro (incluye ids nativos no comparables). |
| `uncategorized` | No trae categorías verificables. |
| ausente | La búsqueda no llevaba filtro. |

Cada categoría lleva su namespace:

```json
{"id": 5070, "name": "TV/Anime", "namespace": "newznab"}
{"id": 130420, "namespace": "native", "indexer": "Nyaa.si", "indexer_id": 1}
```

- `newznab`: el id está en el árbol global de categorías.
- `native`: no está en el árbol y alcanza el rango de ids propios de Prowlarr (≥ 100000); solo tiene
  sentido en su indexador.
- `unknown`: no se puede asegurar; no se adivina.

Un id nativo **nunca** cuenta como Newznab ni como descendiente, aunque el número coincida, y no se
puede usar como filtro: Prowlarr aplica el filtro de forma global y esa asociación no se puede
expresar de forma segura.

Los resultados sin categoría que atraviesan el filtro se devuelven marcados `uncategorized` (no se
descartan) y la respuesta añade una única advertencia con el recuento.

### Validación del filtro `categories`

| Entrada | Resultado |
|---|---|
| Id Newznab existente (`2040`) | Se aplica. |
| Nombre exacto del árbol (`Movies/HD`) | Se resuelve al id y se aplica. |
| Id inexistente (`9999`) o número arbitrario | `InvalidRequestError`; **no se llama** a `/search`. |
| Id nativo (`130420`) | `InvalidRequestError`, incluso si el árbol no está disponible. |
| Árbol no disponible | Los ids numéricos se envían sin validar y se avisa; los nombres se rechazan. |

### Capacidades y participación

`get_search_capabilities` separa el soporte declarado del inferido y de la participación:

| Campo | Significado |
|---|---|
| `indexers_supporting` | Indexadores que **declaran exactamente** la categoría del filtro. |
| `indexers_inferred` | No la declaran, pero anuncian un padre o un descendiente: compatibilidad por jerarquía, sin confirmar. |
| `indexers_participating` | A quiénes consultará Prowlarr **con certeza**: en modo general, todos los habilitados; con filtro, solo los de soporte declarado. Participar no implica devolver resultados. |
| `indexers_unknown_state` | Indexadores sin campo `enable`: habilitación desconocida; no cuentan como participantes. |
| `participation_known` | `true` solo si se conoce el estado de **todos** los indexadores relevantes. No significa que exista participación, sino que `indexers_participating` es completo. |
| `indexer_support[]` | Nivel por indexador (`declared` / `inferred` / `none` / `unknown`) con las categorías que lo motivan. |

Prowlarr descarta indexadores **antes de buscar** cuando hay filtro de categorías, según su propia
correspondencia interna, que no es deducible desde la API: una coincidencia exacta garantiza la
consulta, una relación padre/hijo no. Por eso solo los declarados cuentan como participantes.

## Límites conocidos

Estos comportamientos están verificados contra una instancia real de Prowlarr 3.0.x y forman parte
del contrato:

1. **La ventana no es el total.** Prowlarr agrega lo que devuelve cada indexador y no informa del
   total del índice; cada indexador recorta por su cuenta. `total_available` es siempre `null` y no
   se codifica ningún límite numérico (25, 75 o 100 no son universales).
2. **La paginación es local.** `limit`/`offset` no se envían a la API: se aplican sobre la ventana
   recibida y `next_offset` indica cómo continuar.
3. **El truncamiento no es verificable.** `supportsPagination` describe la capacidad del indexador,
   no lo que ha devuelto esta búsqueda. `window` es `unknown` y `truncated` es `null`.
4. **`year` no filtra.** La API de Prowlarr no acepta el año: se emite una advertencia y el valor
   solo sirve como criterio de análisis.
5. **Lista vacía ≠ sin resultados.** Prowlarr puede responder `200 []` por falta de coincidencias,
   categoría no soportada, timeout o error interno del indexador. `empty_result` aporta la evidencia
   disponible y nunca se afirma que el contenido no exista; `failed_queries == 0` tampoco demuestra
   salud.
6. **Sin metadatos técnicos estructurados.** Prowlarr no entrega resolución, códec, idioma,
   subtítulos, temporada, episodio, autor, ISBN, volumen ni capítulo. No se deducen del título ni
   del fichero: esos campos quedan vacíos.
7. **Manga no es libros.** No hay categoría de manga verificada: se usa búsqueda general con
   advertencia.
8. **Un indexador inexistente responde `400`**, que se traduce a un error de petición con el detalle
   saneado.

## Instalación

Requisitos previos:

- **Python ≥ 3.12** (el mínimo declarado en `pyproject.toml`; desarrollado y probado con CPython
  3.14).
- **[`uv`](https://docs.astral.sh/uv/getting-started/installation/)** para crear el entorno virtual
  e instalar las dependencias de forma reproducible desde `uv.lock`.

```bash
cd /ruta/al/repo        # p. ej. ~/proyectos/multimedia-search-mcp
uv sync --locked --extra dev
```

## Configuración

Copia el ejemplo y rellena la API Key real de Prowlarr (**no** la escribas en el código):

```bash
cp .env.example .env
```

```dotenv
PROWLARR_BASE_URL=http://127.0.0.1:9696
PROWLARR_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# PROWLARR_TIMEOUT=10        # opcional, segundos (1-120)
```

- `.env` está en `.gitignore` y **nunca** se imprime.
- Las variables ya presentes en el entorno del proceso tienen prioridad sobre el fichero.
- Si lanzas el servidor desde otro directorio de trabajo, indica la ruta del fichero:

```bash
PROWLARR_ENV_FILE=/ruta/al/repo/.env .venv/bin/python -m multimedia_search_mcp
```

La búsqueda del `.env` sigue este orden: `PROWLARR_ENV_FILE` → `./.env` → `.env` en la raíz del
repositorio.

## Ejecución

```bash
.venv/bin/python -m multimedia_search_mcp     # transporte stdio (uso normal con un cliente MCP)
# o, tras instalar el paquete:
.venv/bin/multimedia-search-mcp
```

stdout está reservado para el protocolo MCP; los diagnósticos van a stderr.

## Registrar el MCP en Hermes

Hermes lanza los servidores MCP por stdio con un **entorno filtrado** (no hereda las variables del
shell), por eso conviene indicarle dónde está el `.env` con `PROWLARR_ENV_FILE`:

```bash
# ejecutado desde la raíz del repositorio
hermes mcp add multimedia-search \
  --command "$PWD/.venv/bin/python" \
  --env PROWLARR_ENV_FILE="$PWD/.env" \
  --args -m multimedia_search_mcp
```

`--args` debe ir al final. Equivalente manual en `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  multimedia-search:
    command: /ruta/al/repo/.venv/bin/python
    args: ["-m", "multimedia_search_mcp"]
    env:
      PROWLARR_ENV_FILE: /ruta/al/repo/.env
    timeout: 60
    connect_timeout: 30
```

Comprobación:

```bash
hermes mcp test multimedia-search     # conexión + descubrimiento de herramientas
hermes mcp list                       # debe aparecer "multimedia-search"
```

Si no se configura `PROWLARR_ENV_FILE`, el servidor también busca `./.env` y, como último recurso,
el `.env` de la raíz del repositorio (funciona con instalación editable).

## Pruebas y CI

```bash
# Unitarias: sin red y sin Prowlarr (604 pruebas)
.venv/bin/python -m pytest

# Integración: requieren un Prowlarr accesible y un .env válido (22 pruebas)
.venv/bin/python -m pytest -m integration -v

# Comprobación de tipos
.venv/bin/mypy
```

Las pruebas unitarias usan `httpx.MockTransport` y un proveedor falso: no tocan la red. Incluyen una
prueba de protocolo completa (cliente MCP oficial contra el servidor en memoria: `list_tools` +
`call_tool`). Las de integración se marcan con `@pytest.mark.integration` y se omiten si no hay
configuración; solo buscan software libre con términos Linux, nunca simulan resultados.

El workflow de [`.github/workflows/ci.yml`](.github/workflows/ci.yml) se ejecuta en cada `push` y
`pull_request`: instala con `uv sync --locked --extra dev` sobre Python 3.12, comprueba que
`uv.lock` está sincronizado, ejecuta las pruebas unitarias (excluyendo las de integración) y
`mypy`. No necesita secretos ni credenciales de Prowlarr.

## Seguridad

- La API Key solo se lee de `.env`/entorno y se envía en la cabecera `X-Api-Key`.
- Nunca se registra en logs, excepciones, respuestas MCP ni resultados de pruebas.
- Prowlarr devuelve enlaces de proxy con `?apikey=...`: se descartan y, como red de seguridad,
  **toda** respuesta pasa por `security.redact()` antes de salir del proceso.
- Las herramientas son de solo lectura y no abren enlaces.

## Arquitectura

```
                 ┌──────────────────────────────┐
  Cliente MCP ──►│ server.py  (herramientas MCP) │
                 └──────────────┬───────────────┘
                                │
                 ┌──────────────▼───────────────┐
                 │ services/search_service.py   │  proveedor + normalización + paginación
                 └───────┬──────────────┬───────┘
                         │              │
        ┌────────────────▼───┐   ┌──────▼──────────────────┐
        │ providers/base.py   │   │ normalization.py        │
        │ (SearchProvider)    │   │ ReleaseRecord→MediaResult│
        └────────┬────────────┘   └──────────────────────────┘
                 │ implementa
        ┌────────▼────────────────┐        ┌──────────────────┐
        │ providers/prowlarr.py   │──────► │ categories.py    │
        │ ProwlarrClient + adapt. │        │ tipos de contenido│
        └─────────────────────────┘        └──────────────────┘
```

| Capa | Fichero | Responsabilidad |
|---|---|---|
| Servidor MCP | `server.py`, `__main__.py` | Traducir herramientas MCP ↔ casos de uso. Nada de HTTP. |
| Servicio | `services/search_service.py` | Selección de proveedor, normalización, paginación, duplicados y relaciones. |
| Proveedores | `providers/base.py`, `providers/registry.py` | Interfaz abstracta y registro. |
| Adaptador | `providers/prowlarr.py` | Cliente HTTP de Prowlarr + correspondencia de categorías. |
| Normalización | `normalization.py` | Modelo común de resultados, independiente del proveedor. |
| Duplicados | `dedup.py` | Agrupación por identidad exacta (`info_hash` y GUID + indexador); alimenta `duplicate_groups`. |
| Texto e indicadores | `textnorm.py`, `indicators.py` | Normalización de títulos y extracción conservadora de indicadores; internos, no se exponen. |
| Relaciones | `relations.py` | Comparación por pares (`same_release` / `variant`); alimenta `heuristic_relations` sin crear grupos. |
| Dominio | `models.py`, `categories.py` | Modelos pydantic y catálogo de tipos de contenido. |
| Configuración | `config.py` | Lectura de `.env`, validación, secreto protegido. |
| Seguridad | `security.py`, `errors.py` | Saneado de enlaces/claves y errores controlados. |

El servidor MCP **no depende de Prowlarr**: depende de `SearchProvider`. Añadir un proveedor nuevo
no toca `server.py` ni `services/`.

### Implementar un proveedor adicional

1. Crea `providers/<nombre>.py` con una clase que herede de `SearchProvider`.
2. Implementa `configured`, `missing_configuration`, `search`, `list_indexers`, `get_status`,
   `get_capabilities` y, si hace falta, `aclose`.
3. Devuelve `ReleaseRecord` neutros, rellenando `structured_metadata` solo con campos que la API
   entregue de forma estructurada.
4. Rellena `category_match`, `per_indexer`, `truncated` y `empty_result` solo si tu proveedor puede
   demostrarlo; si no, déjalos en `None`.
5. Anota cada `CategoryRef` con su `namespace` y, si es nativa, con su indexador.
6. Valida los filtros de categoría antes de consultar y degrada con advertencia cuando no puedas
   verificarlos.
7. Traduce errores a `ProviderAuthError`, `ProviderTimeoutError`, `ProviderUnavailableError`,
   `ProviderRequestError` o `ProviderResponseError`.
8. Declara tus credenciales en `secret_values()`: la capa MCP las redacta en todas las respuestas.
9. Registra el proveedor en `app.py::build_registry`.

### Añadir una nueva categoría multimedia

1. Añade el valor al enum `MediaType` (`models.py`).
2. Añade su `MediaTypeSpec` en `categories.py` (etiqueta, `type` del proveedor, ids de categoría
   verificados, notas de limitaciones y, si aplica, nota de solapamiento).
3. Mapea los campos específicos en `normalization.py` si el proveedor los aporta.
4. Añade pruebas de normalización y de correspondencia de categorías.

Si el proveedor no tiene categoría propia para ese tipo, deja `category_ids=()`: la búsqueda será
general y la respuesta incluirá la advertencia automática.

## Evolución del proyecto

El proyecto se desarrolló de forma iterativa, por fases auditadas, y cada capacidad se acompaña de
pruebas unitarias y, cuando aplica, de comprobaciones contra una instancia real de Prowlarr:

- **Integración con Prowlarr**: cliente HTTP asíncrono, autenticación por cabecera, traducción de
  errores, estado del proveedor y registro de indexadores.
- **Normalización y ventana honesta**: modelo común de resultados, paginación local y distinción
  explícita entre ventana recibida, página devuelta y datos desconocidos (`null`).
- **Categorías y capacidades**: árbol Newznab frente a ids nativos, validación del filtro, soporte
  declarado frente a inferido, participación conocida y advertencias de solapamiento semántico.
- **Duplicados y relaciones**: identidad exacta por hash y por entrada de indexador, normalización
  de títulos, extracción de indicadores y comparación heurística por pares sin transitividad.
- **Calidad**: suite de pruebas unitarias con transporte simulado, pruebas de integración
  opcionales, tipado estricto con `mypy` y CI en GitHub Actions con instalación reproducible.

## Desarrollo asistido por IA

Este proyecto se ha elaborado con asistencia de herramientas y modelos de inteligencia artificial,
de forma iterativa y bajo dirección y revisión del autor:

- **ChatGPT** — apoyo en planificación, diseño, revisión y toma de decisiones.
- **Hermes Agent** — agente utilizado para ejecutar tareas de desarrollo local y trabajar sobre el
  repositorio.
- **DeepSeek Flash** — modelo utilizado a través de Hermes para las tareas de desarrollo.

El autor ha definido el alcance, revisado los resultados y validado cada fase; no todas las líneas
de código proceden de IA ni se atribuyen a estos sistemas tareas concretas que no puedan
confirmarse.

## Licencia

Este proyecto se distribuye bajo la licencia **MIT**. El texto completo y la atribución de copyright
están en [`LICENSE`](LICENSE).
