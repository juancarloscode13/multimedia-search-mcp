# multimedia-search-mcp

Servidor [MCP](https://modelcontextprotocol.io) en Python para **buscar** contenido
multimedia (películas, series, anime, libros y manga) a través de proveedores
intercambiables. La primera implementación es [Prowlarr](https://prowlarr.com).

> **Solo lectura.** El servidor únicamente ejecuta operaciones `GET` contra la API del
> proveedor. No añade torrents, no descarga ficheros, no pausa descargas, no modifica
> indexadores y no abre enlaces automáticamente. Está pensado para fuentes y contenido
> cuya distribución sea autorizada.

---

## 1. Arquitectura

```
                 ┌──────────────────────────────┐
   Hermes  ───►  │ server.py  (herramientas MCP) │
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
        │ providers/prowlarr.py   │──────► │ categorías.py    │
        │ ProwlarrClient + adapt. │        │ tipos de contenido│
        └─────────────────────────┘        └──────────────────┘
```

Capas y responsabilidades:

| Capa | Fichero | Responsabilidad |
|---|---|---|
| Servidor MCP | `server.py`, `__main__.py` | Traducir herramientas MCP ↔ casos de uso. Nada de HTTP. |
| Servicio | `services/search_service.py` | Selección de proveedor, normalización, paginación, deduplicado. |
| Proveedores | `providers/base.py`, `providers/registry.py` | Interfaz abstracta y registro. |
| Adaptador | `providers/prowlarr.py` | Cliente HTTP de Prowlarr + correspondencia de categorías. |
| Normalización | `normalization.py` | Modelo común de resultados, independiente del proveedor. |
| Duplicados exactos | `dedup.py` | Motor puro de agrupación por identidad exacta (`info_hash` y GUID+indexador). Alimenta `duplicate_groups` en la respuesta de `search_media` (P9, Etapas B y C3). |
| Texto de títulos | `textnorm.py` | Normalización determinista e idempotente de títulos (P9, Etapa C1). No se expone. |
| Indicadores | `indicators.py` | Extracción conservadora de indicadores estructurales/técnicos con estados `present`/`absent`/`ambiguous` (P9, Etapa C1). No se expone. |
| Relaciones heurísticas | `relations.py` | Comparación por pares (`same_release` / `variant`) con evidencia y diferencias explícitas. Alimenta `heuristic_relations` en `search_media`; no crea grupos ni aplica transitividad (P9, Etapas C2 y C3). |
| Dominio | `models.py`, `categories.py` | Modelos pydantic y catálogo de tipos de contenido. |
| Configuración | `config.py` | Lectura de `.env`, validación, secreto protegido. |
| Seguridad | `security.py`, `errors.py` | Saneado de enlaces/claves (cada proveedor declara sus secretos en `secret_values()`) y errores controlados. |

`duplicate_groups` contiene únicamente coincidencias **confirmadas** (`same_publication` por
`info_hash`; `same_source_entry` por GUID e indexador). `heuristic_relations` contiene
únicamente relaciones `possible` (`same_release` o `variant`) entre pares de resultados,
sin transitividad ni agrupación automática de variantes. Son conceptos distintos: una
relación posible nunca se presenta como un duplicado confirmado.

El servidor MCP **no depende de Prowlarr**: depende de `SearchProvider`. Añadir un
proveedor nuevo no toca `server.py` ni `services/`.

## 2. Dependencias

- Python ≥ 3.12 (desarrollado y probado con CPython 3.14).
- [`mcp`](https://pypi.org/project/mcp/) **2.x** (línea estable; API verificada con 2.3.0).
- `httpx` (cliente HTTP asíncrono).
- `python-dotenv` (carga de `.env`).
- `pydantic` 2.x (modelos y validación).
- Desarrollo: `pytest`, `pytest-asyncio`, `mypy`.

> **Aviso SDK v2:** en `mcp` 2.x `FastMCP` se ha renombrado a **`MCPServer`** y se importa
> de `mcp.server.mcpserver`. `mcp.server.fastmcp` lanza `ModuleNotFoundError` a propósito.
> Este proyecto usa la API v2.

## 3. Instalación

Requisitos previos:

- **Python ≥ 3.12** (el mínimo declarado en `pyproject.toml`; desarrollado y probado con
  CPython 3.14).
- **[`uv`](https://docs.astral.sh/uv/getting-started/installation/)** para crear el entorno
  virtual e instalar las dependencias.

```bash
cd /ruta/al/repo        # p. ej. ~/proyectos/multimedia-search-mcp
uv venv --python 3.14 .venv          # cualquier versión ≥ 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
```

## 4. Configuración

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
PROWLARR_ENV_FILE=/ruta/al/repo/.env \
  .venv/bin/python -m multimedia_search_mcp
```

La búsqueda del `.env` sigue este orden: `PROWLARR_ENV_FILE` → `./.env` → `.env` en la raíz
del repositorio.

## 5. Ejecución del servidor

```bash
.venv/bin/python -m multimedia_search_mcp     # transporte stdio (uso normal con Hermes)
# o, tras instalar el paquete:
.venv/bin/multimedia-search-mcp
```

stdout está reservado para el protocolo MCP; los diagnósticos van a stderr.

## 6. Herramientas MCP

| Herramienta | Parámetros | Devuelve |
|---|---|---|
| `search_media` | `query`, `media_type`, `year?`, `categories?`, `limit?`, `offset?`, `provider?` | Resultados normalizados + ventana + clasificación de categorías + advertencias (§7) |
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

## 7. Esquema de respuesta de `search_media`

La respuesta distingue siempre entre **lo que se recibió**, **lo que se devuelve** y **lo que
se sabe**. Un `null` significa "no se puede determinar", nunca cero ni "completo".

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
| `truncated` | `true` = el proveedor ha aportado evidencia explícita de corte; `null` = no se puede verificar (con Prowlarr **siempre** `null`: no hay evidencia). **Nunca `false` por ausencia de evidencia.** |
| `per_indexer[]` | `received_count` por indexador, `window` (`unknown` salvo evidencia explícita del proveedor) y `supports_pagination` (capacidad declarada, `null` si no la informa; **no** demuestra corte). Se agrupa por `indexer_id` + nombre; la suma puede superar `received_count` porque este se cuenta tras deduplicar filas repetidas. |
| `category_match_summary` | Recuento `in_subtree` / `off_category` / `uncategorized` / `unclassified`; `null` si no había filtro. |
| `empty_result` | Solo cuando la lista viene vacía: indexadores disponibles (`null` si algún indexador no informa de su estado), compatibles con el filtro y fallos reportados (§9.3). |
| `duplicate_groups` | Grupos **confirmados** (B) entre resultados de esta página; lista vacía si no hay. No elimina ni fusiona resultados. |
| `heuristic_relations` | Relaciones `possible` (C2) entre pares de esta página, sin las parejas ya cubiertas por un grupo confirmado; lista vacía si no hay. Nunca forma grupos. |

### Identificador de publicación (`info_hash`)

Cada resultado incluye `info_hash` cuando el proveedor entrega un hash de BitTorrent
válido:

- **Formatos aceptados**: 40 caracteres hexadecimales (BTIH v1) o 64 (BTIH v2, BEP 52).
  En esta instancia Prowlarr entrega siempre 40 hexadecimales; Prowlarr normaliza el valor
  a minúsculas aunque el magnet del `guid` conserve el original en mayúsculas.
- **Canonicalización**: se eliminan espacios exteriores y se pasa a minúsculas. Nada más.
- **Validación estricta**: un valor truncado, de otra longitud, con caracteres no
  hexadecimales, en base32 o con prefijo (`0x…`) produce `info_hash: null`. **Nunca** se
  repara un valor inválido para que parezca un hash.
- **`null` significa «no disponible o no válido»**, no «sin hash» deducido del título: el
  MCP no inventa identificadores.
- **Compatibilidad**: el valor original sigue en `provider_metadata["info_hash"]`, sin
  transformar, para no romper consumidores que ya lo leían de ahí.
- **Alcance actual**: el campo solo se expone. No se agrupan, fusionan ni eliminan
  resultados por hash; `result_id` tampoco cambia (identifica la publicación *en su
  indexador*). Los duplicados y relaciones se calculan sobre la página devuelta (§9.4).

### Detección interna de duplicados exactos (P9, Etapa B)

`dedup.py` es un módulo **puro** (sin red, sin proveedores, sin estado) que agrupa
resultados ya normalizados por identidades exactas, sin borrar, fusionar ni reordenar nada:

| `kind` | `basis` | Qué afirma |
|---|---|---|
| `same_publication` | `info_hash` | Mismo torrent (`info_hash` canónico). Única prueba válida **entre indexadores**. |
| `same_source_entry` | `indexer_guid` | Misma entrada del mismo indexador (GUID literal + proveedor + indexador inequívoco). **No** demuestra que el contenido sea idéntico. |

Cada grupo referencia `result_id`, tiene un `group_id` determinista y se ordena de forma
estable. **Todos los miembros de un grupo son compatibles entre sí** (no se agrupa por
cadena: la compatibilidad de indexador no es transitiva, y un nombre que apunta a varios
`indexer_id` queda fuera en lugar de atribuirse). Los hashes ausentes o inválidos no generan
coincidencias, y un grupo por GUID
cuyos miembros ya comparten hash no se emite (no aporta relación nueva). El GUID necesario
para la regla de entrada de origen vive en `MediaResult.guid` con `exclude=True`: **no se
serializa** y el esquema público de `search_media` no cambia. Los grupos todavía no se
exponen en la respuesta como `duplicate_groups` (etapa C3).

### Normalización de títulos e indicadores (P9, Etapa C1)

Dos módulos **internos** (no forman parte del contrato de MCP, no se añaden a `MediaResult`
ni a `SearchResponse`):

- `textnorm.normalize_title(title)`: NFKC + `casefold` + puntuación como separador + colapso
  de espacios. Conserva letras, números y marcas Unicode (japonés, chino, coreano,
  devanagari) y **no elimina** años, temporadas, episodios, volúmenes, capítulos,
  resoluciones, códecs, idiomas, fuentes, ediciones ni subtítulos. `None`, `""` y solo
  espacios devuelven `""`. Es idempotente.
- `indicators.extract_indicators(title)`: parte del título normalizado y reconoce
  año, temporada, episodio, volumen, capítulo, resolución, fuente, códec, audio, idioma,
  subtítulos y modalidad de audio, con límites de palabra y **sin interpretar números
  sueltos**.

Cada atributo es un `Indicator` con estado `present` / `absent` / `ambiguous`. `values` y
`matches` son **tuplas**: `frozen=True` impide reasignar campos, pero una lista seguiría
siendo mutable por dentro. Si un
atributo de valor único aparece con valores excluyentes (`720p.1080p`, `S01E02.S02E03`,
`BluRay.WEB-DL`) el estado es `ambiguous` y se conservan todos los valores: no se elige
ninguno. Los atributos de conjunto (idiomas, audio, subtítulos) admiten varios valores sin
ser contradictorios. Cada valor guarda su procedencia (`pattern`, `text`).

Valores canónicos: `4K` → `2160p`; `x264`/`AVC` → `h264`; `x265`/`HEVC` → `hevc`;
`Web-DL`/`WEB-DL` → `web-dl`; `Blu-Ray` → `bluray`; `DVDRip` → `dvd`; `SUB`/`SUBS` → `sub`.

Año: se reconocen `19xx`/`20xx` con límites de palabra, **con contexto**:

- Se neutralizan para el año los corchetes que contienen letras (`[Scan SP-1920]`, `[Group1998]`):
  son etiquetas de release, no años. Un corchete solo numérico (`[2024]`) sí se conserva.
- Un número precedido de un marcador técnico (`SP`, `DPI`, `PX`, `SCAN`, `RES`, `RESOLUTION`)
  no es un año (`SP-1920` = DPI de escaneo).
- `1920x1080` no es año (ni temporada/episodio) por los límites de palabra.
- Un `1920` suelto sí es un año (`Movie (1920)`, `Movie.1920.1080p`): no hay exclusión global,
  así que una película de 1920 conserva su año.
- Los rangos (`(2003-2015)`) quedan `ambiguous`, no se elige uno.

### Comparación heurística por pares (P9, Etapa C2)

`relations.compare_pair(left, right)` compara **exactamente dos** `MediaResult` y devuelve
un `PairRelation` (`relation="possible"`) o `None`. No hay grupos, transitividad,
puntuaciones ni umbrales; A–B y B–C no dicen nada de A–C.

| `kind` | Cuándo se emite |
|---|---|
| `same_release` | Títulos normalizados idénticos y no vacíos, tamaño conocido e idéntico, sin contradicciones estructurales, sin diferencias técnicas y sin ambigüedad. Evidencia: `title_normalized`, `size_exact` (+ `structure_match` si hay dimensiones presentes en ambos). |
| `variant` | Título **base** idéntico (normalizado menos los fragmentos técnicos que C1 reconoció), unidad estructural compatible y al menos una diferencia técnica explícita. Evidencia: `base_title` (+ `structure_match`). |

Reglas comunes:

- `result_id` distintos. Si ambos comparten `info_hash`, no se emite heurística: B ya lo
  confirma y sería evidencia redundante.
- Ambigüedad en un indicador estructural, o en cualquier indicador técnico, bloquea la
  relación; una dimensión ambigua nunca cuenta como diferencia.
- Contradicción estructural (dos valores presentes y distintos en año, temporada, episodio,
  volumen o capítulo) bloquea la relación.
- La ausencia nunca es una diferencia: `1080p` frente a «sin resolución» no es variante.
- El tamaño solo se usa en `same_release` (obligatorio igual y conocido). Un tamaño distinto
  o desconocido no justifica ni bloquea `variant`.
- Compatibilidad de **unidad** (aplica a `same_release` y a `variant`): temporada, episodio,
  volumen y capítulo deben estar presentes en ambos con el mismo valor o ausentes en los dos;
  el año, si está en ambos, debe coincidir, y present/absent solo se admite con otra dimensión
  de unidad coincidente. Es imprescindible porque C1 neutraliza el año dentro de corchetes:
  `[Group 2024] Movie` y `Group 2024 Movie` comparten título normalizado pero extraen años
  distintos, y no deben relacionarse.

El `base_title` retira únicamente coincidencias de `IndicatorMatch` de C1 (resolución, fuente,
códec, audio, idioma, subtítulos y modalidad), en tokens completos y por orden fijo: el primer
fragmento reclamado se queda con los tokens solapados. Nada más se elimina, así que cualquier
diferencia en un token no reconocido rompe la igualdad del título base.

`PairRelation` guarda `relation_id` determinista, ids en orden canónico, `kind`, `evidence`,
`differences` y una `note` opcional (solo cuando los tamaños difieren en una variante).

### Integración de duplicados y relaciones (P9, Etapa C3)

`search_media` devuelve ahora dos campos adicionales, calculados **solo sobre la página
devuelta** (tras `offset`/`limit`):

1. `duplicate_groups`: grupos confirmados de `dedup.find_exact_duplicates(page)`.
2. `heuristic_relations`: pares `possible` de `relations.compare_pair`, **sin** las parejas
   que ya aparecen juntas en un mismo grupo confirmado.

Flujo: normalizar → deduplicar por `result_id` → `received_count` → recortar la página →
B sobre la página → C2 sobre todos los pares distintos de la página → suprimir parejas
cubiertas por B → ordenar → respuesta.

| Regla | Detalle |
|---|---|
| Supresión | Para cada grupo de B se cubren todas sus parejas internas (un grupo de A, B y C cubre A-B, A-C y B-C). Se descarta la relación C2 de esa pareja; el resto se conserva. La supresión es por pareja directa: no se propaga a terceros ni entre grupos. |
| Sin transitividad | C2 compara cada par de forma independiente. Si A-B y B-C son variantes, A-C solo aparece si `compare_pair(A, C)` lo produce. Nunca se crean grupos heurísticos. |
| Orden | `duplicate_groups` por `group_id`; `heuristic_relations` por `relation_id`. `results` mantiene su orden original. |
| No destructivo | Los resultados no se eliminan, fusionan ni reordenan; grupos y relaciones solo los referencian por `result_id`. |
| Alcance | Solo la página. Dos resultados en páginas distintas no se comparan, y no se afirma haber encontrado todos los duplicados del índice. |
| Coste | C2 compara todos los pares de la página: con `limit` máximo (100) son 4.950 comparaciones (≈0,5 s de CPU en esta máquina). |

Ejemplo de relación real (dos codificaciones del mismo anime):

```json
{"relation_id": "rel:20a4faa1f55e5c59", "relation": "possible", "kind": "variant",
 "left_id": "prowlarr:...", "right_id": "prowlarr:...",
 "evidence": ["base_title", "structure_match"], "differences": ["codec"], "note": null}
```

### Estados de categoría (solo con filtro)

| `category_match` | Significado |
|---|---|
| `in_subtree` | Al menos una categoría del resultado pertenece al filtro o a su subárbol Newznab. |
| `off_category` | El resultado trae categorías, pero ninguna pertenece al filtro ni a su subárbol (incluye ids nativos/custom que no son comparables). |
| `uncategorized` | El resultado no trae categorías verificables (lista vacía o nula). |
| ausente / `unclassified` | No aplica: la búsqueda no llevaba filtro (campo omitido), o el proveedor no clasificó. |

**Por qué se conservan los `uncategorized`:** Prowlarr no los filtra a propósito cuando el
parser del indexador no pudo mapear la categoría. El servidor los devuelve porque pueden ser
relevantes, pero **no** los presenta como coincidencia de categoría confirmada y añade una
advertencia con el recuento (una sola, no una por resultado).

### Modelo de categorías y namespaces

Cada categoría de un resultado o de una capacidad lleva su namespace:

```json
{"id": 5070, "name": "TV/Anime", "namespace": "newznab"}
{"id": 130420, "namespace": "native", "indexer": "Nyaa.si", "indexer_id": 1}
```

| Campo | Significado |
|---|---|
| `id` | Identificador tal cual lo devuelve Prowlarr (nunca se reescribe). |
| `name` | Nombre solo si el proveedor o el árbol Newznab lo aportan; nunca inventado. |
| `namespace` | `newznab` (global), `native` (propia de un indexador) o `unknown` (no se puede asegurar). |
| `indexer` / `indexer_id` | Indexador al que pertenece una categoría `native`. |

Reglas aplicadas:

- `newznab` ⇔ el id está en el árbol de `/api/v1/indexer/categories`.
- `native` ⇔ no está en el árbol y alcanza el rango de ids propios de Prowlarr
  (≥ 100000; observados `100017`, `130420`, `140679`).
- Cualquier otro id → `unknown` (no se adivina el namespace).
- Una categoría `native` **nunca** cuenta como Newznab ni como descendiente suyo, aunque
  el número coincida: la clasificación P2 la marca siempre como `off_category` (jamás
  `in_subtree`). Los ids nativos tampoco son portables entre indexadores.
- Sin árbol de categorías no se puede afirmar el namespace de nada: los resultados se
  devuelven **sin clasificar** (`category_match` ausente, contados como `unclassified`) y se
  avisa; no se inventa una clasificación.

### Validación del filtro `categories`

Antes de buscar se valida el filtro contra el árbol Newznab (con la caché existente):

| Entrada | Resultado |
|---|---|
| Id Newznab existente (`2040`) | Se aplica. |
| Nombre exacto del árbol (`Movies/HD`) | Se resuelve al id y se aplica. |
| Id inexistente (`9999`) | `InvalidRequestError`; **no se llama** a `/search`. |
| Id nativo (`130420`) | `InvalidRequestError`: el filtro de Prowlarr es global y un id nativo solo tiene sentido en su indexador; no se finge una asociación. Se rechaza incluso si el árbol no está disponible. |
| Número arbitrario (`12345`) | `InvalidRequestError`. |
| Árbol no disponible | Degradación: los ids numéricos se envían **sin validar** y se avisa; los nombres se rechazan porque no se pueden resolver. |

### Soporte por indexador y participación

`get_search_capabilities` distingue tres cosas que antes se mezclaban:

| Campo | Significado |
|---|---|
| `indexers_supporting` | Indexadores que **declaran exactamente** la categoría del filtro (soporte confirmado). |
| `indexers_inferred` | No la declaran, pero anuncian un padre o un descendiente: compatibilidad **inferida** por jerarquía. Prowlarr **puede o no** consultarlos. |
| `indexers_participating` | A quiénes consultará Prowlarr **con certeza**: en modo `general`, todos los habilitados; en `filtered`, solo los de soporte declarado. Participar no implica devolver resultados. |
| `indexers_unknown_state` | Indexadores sin campo `enable`: su habilitación es desconocida y no se cuentan como participantes. |
| `participation_known` | `true` **solo** si se conoce el estado de participación de todos los indexadores relevantes: el listado se leyó y ninguno quedó indeterminado. Es `false` si el listado no se pudo leer o si alguno aparece en `indexers_unknown_state`. No significa que exista participación; significa que `indexers_participating` es completo. Un listado vacío (sin indexadores) cuenta como estado conocido. |
| `indexer_support[]` | Nivel por indexador (`declared` / `inferred` / `none` / `unknown`) con las categorías que lo motivan. |

#### Cómo se determina la participación (evidencia)

Prowlarr **no** consulta todos los indexadores cuando hay filtro de categorías. En
`ReleaseSearchService.Dispatch` (3.0.x) filtra la lista antes de buscar:

```csharp
if (criteriaBase.Categories is { Length: > 0 })
    indexers = indexers.Where(i => i.GetCapabilities().Categories
        .SupportedCategories(criteriaBase.Categories).Any()).ToList();
```

Comprobado contra esta instancia (3.0.2.5780) leyendo `numberOfQueries` de
`/api/v1/indexerstats` antes y después de cada búsqueda:

| Búsqueda | Nyaa.si (anuncia 5000, 7000) | LinuxTracker (anuncia 4000) |
|---|---|---|
| sin categorías | consultado | consultado |
| `categories=2000` (Nyaa exacto) | consultado | no consultado |
| `categories=5070` (Nyaa solo anuncia el padre 5000) | consultado | no consultado |
| `categories=7020` (Nyaa solo anuncia el padre 7000) | **no consultado** | no consultado |
| `categories=6000` (nadie lo anuncia) | no consultado | no consultado |

Conclusión: el conjunto consultado es la correspondencia interna de cada indexador, que **no
es deducible desde la API**. Una coincidencia exacta garantiza la consulta; una relación
padre/hijo no (5070 sí, 7020 no). Por eso solo los declarados cuentan como participantes y
los inferidos se informan como compatibilidad sin confirmar. El post-filtro de Prowlarr, en
cambio, **conserva** resultados sin categorías (`Categories?.Any() != true`), de ahí que P2
siga marcándolos `uncategorized`.

Ejemplo real actual (anime `5070`, Nyaa solo anuncia el padre `5000`):

```json
{
  "media_type": "anime", "mode": "filtered",
  "category_ids": [5070], "category_names": ["TV/Anime"],
  "indexers_supporting": [],
  "indexers_inferred": ["Nyaa.si"],
  "indexers_participating": [],
  "indexers_unknown_state": [],
  "participation_known": true,
  "indexer_support": [
    {"indexer": "Nyaa.si", "indexer_id": 1, "support": "inferred",
     "matched_category_ids": [5000], "matched_category_names": ["TV"]}
  ],
  "warnings": ["... 1 indexador(es) solo se relacionan por jerarquía (compatibilidad inferida) y Prowlarr podría consultarlos o no: no se puede confirmar desde la API."]
}
```

En una búsqueda general (p. ej. `manga`) los participantes son los habilitados confirmados,
p. ej. `["LinuxTracker", "Nyaa.si"]`, y no aparecen ni los deshabilitados ni los de estado
desconocido.

### Solapamiento semántico (movie / series / anime)

Las categorías son el **filtrado disponible** del proveedor, no una clasificación del
contenido. La jerarquía Newznab hace que `TV (5000)` incluya `TV/Anime (5070)`, así que un
resultado puede aparecer en series y en anime; algunos indexadores mapean a `Movies (2000)`
contenido que no es una película. `get_search_capabilities` incluye una advertencia global y
cada tipo su nota; la búsqueda repite la nota específica una sola vez. **No** se deduce el
tipo de contenido desde el título ni desde el nombre del fichero.

### Compatibilidad y cambios de esquema

Esta fase cambia la semántica de tres campos y añade información:

| Antes | Ahora | Motivo |
|---|---|---|
| `returned` (int) | `returned_count` (int) | Nombre explícito frente a `received_count`. |
| `total_available` (int, era la ventana) | `total_available` (`null`) + `received_count` | La ventana no es el total del índice. |
| `has_more` (bool, `false` al final) | `has_more` (`true`/`null`) + `next_offset` | `false` afirmaba completitud que no se puede demostrar. |
| `categories[].name` | `categories[].name` + `namespace` (+ `indexer`/`indexer_id` si es nativa) | Separar Newznab de ids nativos. |
| `indexers_supporting` (declarado + inferido) | `indexers_supporting` (solo declarado) + `indexers_inferred` + `indexers_participating` (solo consultados con certeza) + `indexers_unknown_state` + `participation_known` + `indexer_support[]` | No presentar inferencias como soporte ni como participación confirmada, y no devolver `[]` en búsquedas generales. |
| `categories` aceptaba cualquier número | Se valida contra el árbol; nativos e inexistentes se rechazan | Evitar búsquedas silenciosamente vacías por error de entrada. |

Se añaden también `per_indexer`, `truncated`, `category_match_summary`, `empty_result` y, por
resultado, `category_match`.

## 8. Tipos de contenido soportados

Correspondencias **verificadas** contra el árbol de categorías Newznab que expone
Prowlarr (3.0.2.5780):

| `media_type` | `type` Prowlarr | Categorías | Estado |
|---|---|---|---|
| `movie` | `movie` | 2000 Movies | filtrado verificado |
| `series` | `tvsearch` | 5000 TV | filtrado verificado |
| `anime` | `tvsearch` | 5070 TV/Anime | filtrado verificado |
| `book` | `book` | 7000 Books | filtrado verificado |
| `manga` | — | sin categoría propia | **búsqueda general + advertencia** |
| `other` | — | sin filtro | búsqueda general (por diseño) |

"Filtrado verificado" significa que la categoría existe en el árbol Newznab del proveedor,
no que todos los indexadores la declaren: el soporte real por indexador (declarado o inferido)
se consulta en `get_search_capabilities` (§7).

## 9. Limitaciones actuales de las búsquedas

Verificadas empíricamente contra la instancia real (Prowlarr 3.0.2.5780).

### 9.1 Ventana, totales y truncamiento

1. **La ventana no es el total.** Prowlarr agrega lo que devuelve cada indexador y no informa
   del total del índice; además cada indexador recorta por su cuenta (en la instalación
   auditada se observaron ventanas de 25 y 75 resultados). Por eso `total_available` es
   `null`: presentar `len(recibidos)` como total sería falso. **No se codifica ningún límite
   numérico**: 25, 75 o 100 no se tratan como universales.
2. **Paginación local.** `limit`/`offset` no se envían a la API, porque Prowlarr no recorta el
   resultado agregado (con `limit=2` devolvió 87 elementos). Se aplican sobre la ventana
   recibida y `next_offset` indica cómo continuar.
3. **Truncamiento no verificable.** La API no informa del total del índice, del tamaño de
   página ni de si hay más resultados. El campo `supportsPagination` de `/api/v1/indexer`
   describe la **capacidad del indexador**, no lo que ha devuelto esta búsqueda: no demuestra
   que se haya servido una sola página, ni que falten resultados, ni dice nada cuando el
   indexador devolvió cero. Por eso `window` es `unknown` y `truncated` es `null`: recibir 25,
   75 o 100 resultados tampoco cambia esa conclusión y **no se codifica ningún límite
   numérico**. `POSSIBLY_INCOMPLETE` queda reservado a un proveedor que aporte evidencia
   explícita (p. ej. un cursor con más páginas).
4. **`year` no se aplica.** `/api/v1/search` no acepta `year`; se devuelve una advertencia y
   el filtro no se aplica. Rellenar el año reescribiendo la consulta se consideró y se
   descartó: alteraría el significado de la búsqueda.

### 9.2 Categorías

5. **Hay resultados sin categoría que atraviesan el filtro.** Prowlarr conserva a propósito
   los releases sin categorías cuando aplica un filtro (su parser no pudo mapearlas). El
   servidor los devuelve marcados `uncategorized`, no los descarta y **no** los presenta como
   coincidencia confirmada; añade una advertencia con el recuento. Cada resultado expone
   `category_match` (`in_subtree` / `off_category` / `uncategorized`) y la respuesta incluye
   `category_match_summary`.
6. **Los ids nativos no son Newznab.** Un id custom (`130420`, `102153`, `140679`…) pertenece
   al indexador, no al árbol Newznab: nunca se interpreta como `4000` ni como `5070`, y se
   publica con `namespace: "native"` más el indexador. Si un resultado solo trae ids custom,
   se clasifica `off_category` (no se puede demostrar pertenencia). Los ids originales se
   conservan tal cual. Un id nativo **no** se puede usar como filtro: el filtro de Prowlarr es
   global y esa asociación no se puede expresar de forma segura (§7).
7. **El filtro `categories` se valida.** Un id inexistente produce error antes de consultar
   (Prowlarr respondería `200 []`, indistinguible de "sin coincidencias"). Si el árbol no está
   disponible, los ids se envían sin validar y se avisa; nunca se afirma haber validado.
8. **Anime se solapa con series.** `TV (5000)` incluye `5070 TV/Anime`: los mismos resultados
   pueden aparecer en ambas búsquedas y algunos indexadores mapean a `Movies (2000)` contenido
   que no es una película. Las categorías describen el filtrado del proveedor, no el tipo real
   del contenido; no se deduce del título.
9. **Soporte por indexador: declarado o inferido.** Que un indexador anuncie el padre o un
   descendiente de la categoría pedida no prueba que ejecute bien ese filtro: se publica como
   `inferred`, separado de `indexers_supporting` (declarado).
10. **Manga no es libros.** No existe categoría de manga verificada; se usa búsqueda general
    con advertencia explícita. Tampoco se asume que `Books/Comics (7030)` sea manga.
11. **Sin metadatos técnicos estructurados.** Prowlarr no devuelve resolución, códec, idioma,
    subtítulos, temporada, episodio, autor, ISBN, volumen ni capítulo. **No se deducen del
    título ni del nombre del fichero**: esos campos quedan vacíos. El modelo común los admite
    para proveedores que sí los aporten.

### 9.3 Respuestas vacías y fallos silenciosos

12. **Lista vacía ≠ sin resultados.** Prowlarr captura errores internos de los indexadores
    (timeouts, fallos del parser) y puede responder `200` con `[]`. Una búsqueda vacía puede
    significar: sin coincidencias, categoría no soportada, timeout o error interno. El
    servidor devuelve `empty_result` con la evidencia disponible y **nunca** afirma que no
    existan coincidencias.
13. **`failed_queries == 0` no demuestra salud.** Un indexador caído puede no incrementar el
    contador. Por eso `indexer_failure_reported` es `true` solo si hay fallos reportados, y
    `null` (nunca `false`) cuando el contador está a cero o no hay datos.
14. **Categoría desconocida = lista vacía.** La API responde `200` con `[]` tanto si no hay
    coincidencias como si ningún indexador soporta la categoría; `empty_result` indica cuántos
    indexadores habilitados anuncian compatibilidad con el filtro.
15. **Indexador inexistente = `400`.** Se traduce a `ProviderRequestError` con el detalle
    saneado del proveedor.
16. **Colecciones no normalizadas.** Sin puntuación de calidad ni agrupación por
    temporada/volumen: esa lógica pertenece a la futura skill `multimedia-discovery`.

## 10. Ejecución de pruebas

```bash
# Unitarias: sin red, sin Prowlarr (604 pruebas)
.venv/bin/python -m pytest

# Integración: requieren Prowlarr accesible y .env válido (22 pruebas)
.venv/bin/python -m pytest -m integration -v
```

Las pruebas unitarias usan `httpx.MockTransport` y un proveedor falso: no tocan la red.
Incluyen un test de protocolo completo (cliente MCP oficial contra el servidor en memoria:
`list_tools` + `call_tool`). Las de integración se marcan con `@pytest.mark.integration` y se
omiten si no hay configuración (nunca se simulan resultados de películas, libros o manga
inexistentes: solo se busca software libre con términos Linux).

Comprobación de tipos:

```bash
.venv/bin/mypy
```

## 11. Cómo implementar un proveedor adicional

1. Crea `providers/<nombre>.py` con una clase que herede de `SearchProvider`.
2. Implementa `configured`, `missing_configuration`, `search`, `list_indexers`,
   `get_status`, `get_capabilities` y, si hace falta, `aclose`.
3. En `search`, devuelve `ProviderSearchResult` con objetos `ReleaseRecord` **neutros**:
   rellena `structured_metadata` solo con campos que la API entregue de forma estructurada.
4. Rellena también lo que la fase de fiabilidad exige, y solo si tu proveedor lo sabe:
   `category_match` en cada `ReleaseRecord` (solo con filtro aplicado), `per_indexer`,
   `truncated` (solo `True` con evidencia explícita de corte; si no, `None`) y
   `empty_result`. Si no puedes demostrar un dato, déjalo en `None`: el servicio lo tratará
   como desconocido.
5. Anota cada `CategoryRef` con su `namespace` (`newznab` / `native` / `unknown`) y, si es
   nativa, con el indexador que la aporta. En `get_capabilities`, separa el soporte
   declarado (`indexers_supporting`) del inferido por jerarquía (`indexers_inferred`), indica
   quién participa y si esa participación se conoce (`participation_known`).
6. Valida los filtros de categoría de entrada antes de consultar (`InvalidRequestError`) y
   degrada con advertencia cuando no puedas verificarlos.
7. Traduce errores a `ProviderAuthError`, `ProviderTimeoutError`,
   `ProviderUnavailableError`, `ProviderRequestError` o `ProviderResponseError`
   (mensajes ya saneados, sin credenciales).
8. Declara tus credenciales en `secret_values()`: la capa MCP las redacta en todas
   las respuestas sin necesitar saber qué proveedor las aporta.
9. Registra el proveedor en `app.py::build_registry`.

No hay que modificar `server.py`, `services/` ni `normalization.py`.

## 12. Cómo añadir una nueva categoría multimedia

1. Añade el valor al enum `MediaType` (`models.py`).
2. Añade su `MediaTypeSpec` en `categories.py` (etiqueta, `type` del proveedor,
   ids de categoría **verificados**, notas de limitaciones y, si aplica, su nota de
   solapamiento en `OVERLAP_NOTES`).
3. Mapea los campos específicos en `normalization.py` si el proveedor los aporta
   (nuevo grupo opcional en `models.py`, sin tocar los existentes).
4. Añade pruebas de normalización y de correspondencia de categorías.

Si el proveedor no tiene categoría propia para ese tipo, deja `category_ids=()`: la
búsqueda será general y la respuesta incluirá la advertencia automática.

## 13. Seguridad

- La API Key solo se lee de `.env`/entorno y se envía en la cabecera `X-Api-Key`.
- Nunca se registra en logs, excepciones, respuestas MCP ni resultados de pruebas.
- Prowlarr devuelve enlaces de proxy con `?apikey=...`: se descartan y, como red de
  seguridad, **toda** respuesta pasa por `security.redact()` antes de salir del proceso.
- Las herramientas son de solo lectura y no abren enlaces.

## 14. Registrar el MCP en Hermes

Hermes lanza los servidores MCP por stdio con un **entorno filtrado** (no hereda tus
variables de shell), por eso conviene indicarle dónde está el `.env` con
`PROWLARR_ENV_FILE`. Verificado con un handshake real por stdio desde un `cwd` ajeno al
proyecto.

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

Comprobación y reinicio:

```bash
hermes mcp test multimedia-search     # conexión + descubrimiento de herramientas
hermes mcp list                       # debe aparecer "multimedia-search"
```

Las herramientas quedan disponibles como `mcp_multimedia_search_search_media`,
`mcp_multimedia_search_list_providers`, `mcp_multimedia_search_list_indexers`,
`mcp_multimedia_search_get_provider_status` y
`mcp_multimedia_search_get_search_capabilities`. Hermes necesita reiniciarse para
descubrirlas.

Si prefieres no configurar `PROWLARR_ENV_FILE`, el servidor también busca `./.env` y, como
último recurso, el `.env` de la raíz del repositorio (funciona con instalación editable).

## 15. Licencia

Este proyecto se distribuye bajo la licencia **MIT**. El texto completo y la atribución de
copyright están en [`LICENSE`](LICENSE).
