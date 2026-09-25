# ELIPSE Engine — Estructura del proyecto

Este documento explica qué hace cada archivo dentro de `app/`, la carpeta que contiene el motor de ELIPSE. Está pensado para que cualquiera (incluido tu yo del futuro, o cualquier IA con la que sigas trabajando) entienda rápido cómo está armado el Core sin tener que leer todo el código de una.

> **Fase actual del plan:** Fase 0 a 5 cerradas (multi-proveedor, tools, seguridad, Agent Loop, memoria semántica + research, MCP y autenticación multi-dispositivo). Git sigue descartado a propósito, no se necesita por ahora. Pendiente sin fase asignada todavía: una capa de **skills** (criterio reutilizable para tareas específicas, más allá de la personalidad general) — ver la sección "Notas / cosas pendientes conocidas" al final.

---

## Estructura

```
elipse/
├── .env                         # Variables de entorno (config local, no se sube a git)
├── requirements.txt             # Dependencias de Python del proyecto
├── elipse.db                    # Base de datos SQLite (se genera sola al arrancar)
├── mcp_servers.json             # Opcional: servidores MCP externos a conectar (mismo formato que Claude Desktop)
├── chroma_data/                 # Base vectorial de Chroma para la memoria semántica (se genera sola)
├── workspace/                   # Carpeta sandbox: único lugar donde las tools pueden leer/escribir
├── logs/                        # Logs de Ollama, de ELIPSE Core y del panel (los genera elipse_control_panel.py)
├── elipse_control_panel.py      # Panel de control de escritorio (Tkinter): prender/apagar servicios, ajustes, llaves
└── app/
    ├── __init__.py
    ├── main.py                  # Punto de entrada: define la API y conecta todo
    ├── config.py                 # Configuración del proyecto (Pydantic Settings)
    ├── manage_keys.py             # CLI para crear/listar/revocar API keys (fuera de la API, ver Fase 5)
    └── core/
        ├── __init__.py
        ├── db.py                  # Conexión a SQLite y creación de tablas
        ├── seed_personality.py    # Carga los datos iniciales de personalidad
        ├── personality.py         # Arma el system prompt a partir de los datos guardados
        ├── router.py              # Model Router: decide si un mensaje es "código" o "general"
        ├── providers.py           # Capa de proveedores de modelos: Ollama, Anthropic (Claude), OpenAI-compatible
        ├── core.py                # ElipseCore: el Agent Loop real (analizar → planificar → ejecutar → verificar → responder)
        ├── tools.py                # Tool System: definición + ejecución + verificación de herramientas propias
        ├── mcp_client.py           # Cliente MCP: conecta ELIPSE a servidores de herramientas externos
        ├── safety.py               # Decide qué acciones son riesgosas y gestiona confirmación humana
        ├── memory.py               # Memoria semántica de largo plazo (Chroma + embeddings)
        ├── research.py             # Pipeline de investigación: buscar en internet → resumir → guardar en memoria
        ├── tasks.py                # Registro de tareas en background y puente entre la API y el Core
        └── auth.py                 # Autenticación por API key (multi-dispositivo)
```

---

## Qué hace cada archivo

### `app/main.py`
El corazón visible del motor: acá vive la API construida con **FastAPI**.

- Al arrancar (`lifespan`), inicializa la base de datos (`init_db()`), carga la personalidad por defecto si todavía no existe (`seed()`) y conecta los servidores MCP configurados (`mcp_client.start_from_settings()`) en un hilo aparte para no bloquear el arranque. Al apagar, desconecta los servidores MCP prolijamente.
- CORS abierto a `"*"`: no hay cookies ni credenciales de por medio, toda la autenticación va por el header `Authorization`, así que abrir el origen no compromete nada — lo necesita el cliente web (Fase 5) para conectarse desde `file://` o desde otro dispositivo de la red.
- Casi todos los endpoints requieren `Authorization: Bearer <api_key>` (`Depends(require_api_key)`), salvo `/v1/status`.

**Endpoints:**
- `GET /v1/status` → chequeo de que el servidor está vivo; devuelve además qué proveedores hay activos (general y de código).
- `POST /v1/chat` → crea una tarea (`create_task`), la corre en un thread aparte (`run_agent_task`) y devuelve de inmediato `task_id` + `ws_url` + `poll_url`.
- `GET /v1/task/{task_id}` → resultado final de una tarea (alternativa a WebSocket, sin eventos intermedios).
- `WS /v1/ws/{task_id}` → progreso en vivo de una tarea: eventos `progreso`, `plan`, `verificacion` y `final` (y un `ping` cada 30s si no hay eventos, para mantener viva la conexión). Se autentica con `?token=<api_key>` porque el navegador no puede mandar headers custom al abrir un WebSocket.
- `POST /v1/memory` / `GET /v1/memory` → guardar y listar hechos permanentes (tabla `facts`, los que arma el system prompt).
- `GET /v1/router-log` → últimas 20 decisiones del Model Router.
- `GET /v1/pending-actions` → lista acciones que están esperando confirmación humana.
- `POST /v1/confirm-action/{id}` → aprueba o rechaza una acción riesgosa pendiente (ej. sobreescribir un archivo, o ejecutar una tool externa de MCP no auto-aprobada).
- `GET /v1/tools?format=ollama|mcp` → expone el catálogo de herramientas (propias + las de servidores MCP conectados), en formato nativo o traducido a MCP.
- `GET /v1/mcp/status` → estado de cada servidor MCP configurado: conectado o no, qué herramientas expone, y el error si falló (sin secretos, nunca env ni headers).
- `POST /v1/research` → dispara el pipeline de investigación (buscar → resumir → guardar en memoria semántica) como tarea en background, con el mismo mecanismo de `task_id`/WebSocket que `/v1/chat`.
- `GET /v1/research/log` → últimas 20 investigaciones hechas.
- `POST /v1/memory/semantic` → guarda algo en memoria semántica manualmente, sin pasar por el agente.
- `GET /v1/memory/semantic/search?q=...&n=4` → búsqueda semántica directa (por significado, no por palabras exactas).
- `GET /v1/memory/semantic/stats` → cuántos recuerdos hay guardados vs. el máximo configurado.
- `POST /v1/memory/semantic/cleanup` → fuerza la política de retención manualmente (normalmente corre sola tras cada guardado).
- `POST /v1/auth/keys` / `GET /v1/auth/keys` / `POST /v1/auth/keys/{id}/revoke` → gestión de API keys adicionales, una vez que ya tenés una (la primera se crea por CLI, ver `manage_keys.py`, porque crear una acá requeriría ya estar autenticado).
- `POST /v1/install-package` → instala un paquete de pip. **No** es una tool del modelo (no está en `TOOLS_SCHEMA`); solo para uso humano directo, cuando `run_python` reporta que falta una librería.

### `app/config.py`
Configuración del proyecto con `pydantic-settings`, leída desde `.env`. Grupos de configuración:

- **General:** nombre de la app, modelos de Ollama (general, código, e investigación), tamaño de contexto fijo, timeout, carpeta del workspace.
- **Proveedores** (`default_provider`, `code_provider`): elige entre `ollama` (local), `anthropic` (Claude) u `openai` (OpenAI o cualquier API compatible: OpenRouter, Mistral, Groq, LM Studio, vLLM...). `code_provider` vacío significa que el código usa también el proveedor por defecto.
- **Anthropic / OpenAI:** API keys, modelo, `base_url` (para los compatibles con OpenAI) y límites de tokens/timeout.
- **MCP:** dónde está `mcp_servers.json`, timeouts de conexión y de llamada, y límites de caracteres (descripción y salida) para no saturar el contexto del modelo con herramientas externas.
- **Memoria semántica (Chroma):** carpeta de datos, nombre de colección, máximo de recuerdos guardados, qué tipos de recuerdo se pueden podar automáticamente (`research` por defecto), cuántos resultados trae cada búsqueda, y el largo máximo de un resumen de investigación.

### `app/manage_keys.py`
**Nuevo — Fase 5.** CLI para administrar API keys, fuera de la API HTTP a propósito: crear la primera llave necesita poder correr sin estar ya autenticado.

```
python -m app.manage_keys create telefono
python -m app.manage_keys list
python -m app.manage_keys revoke 3
```

Con `--json` (siempre justo después de `app.manage_keys`, antes del subcomando) devuelve JSON en vez de texto legible — es lo que usa `elipse_control_panel.py` para su pestaña de llaves.

### `app/core/db.py`
Conexión a SQLite y creación de tablas: `identity`, `traits`, `style_rules`, `messages`, `facts`, `router_log`, `pending_actions` (acciones riesgosas que esperan aprobación humana: herramienta, argumentos, motivo y estado), `research_log` (cada investigación: tema, resumen, id de memoria y fuentes) y `api_keys` (nombre, hash sha256 de la llave, si está revocada, cuándo se creó y cuándo se usó por última vez).

### `app/core/seed_personality.py`
Puebla la base con identidad, rasgos y reglas de estilo por defecto, solo si todavía no existen. Es el archivo que se edita para ajustar cómo "es" ELIPSE desde el inicio.

### `app/core/personality.py`
`build_system_prompt()`: arma el system prompt a partir de identidad + rasgos + reglas + hechos guardados en SQLite. Sin cambios de lógica desde las primeras fases.

### `app/core/router.py`
Model Router por reglas simples (palabras clave de código → modelo de código, si no → modelo general), con el motivo devuelto como texto para loguearlo. Sin cambios de lógica — pero ahora `core.py` es quien lo consulta (antes lo hacía directamente `tasks.py`).

### `app/core/providers.py`
**Capa de proveedores de modelos.** El Core (`core.py`) nunca habla con Ollama, Claude o GPT directamente: habla con un `Provider` — una interfaz común con un solo método, `chat(messages, tools=None, allow_tools=True)`. Todo lo específico de cada proveedor (cliente HTTP, formato de mensajes, formato de herramientas, bugs raros) vive acá y no se filtra al resto del sistema.

- **`OllamaProvider`** — modelos locales, vía el cliente oficial de `ollama`. Filtra bloques `<think>...</think>` que `qwen3` a veces genera aunque se le pida `think=False` (bug conocido).
- **`AnthropicProvider`** — Claude vía la Messages API. Traduce el formato neutral de conversación (el mismo para los tres proveedores) al formato específico de Anthropic: `system` como parámetro aparte, `tool_use`/`tool_result` como bloques, mensajes consecutivos del mismo rol fusionados, etc.
- **`OpenAICompatibleProvider`** — OpenAI y cualquier servidor que hable el mismo protocolo (OpenRouter, Mistral, Groq, LM Studio, vLLM...), cambiando solo `base_url` / `api_key` / `model`.
- **`_HTTPProvider`** (base común de los dos de nube) — reintentos automáticos ante `429/502/503/529` con backoff exponencial (respetando `Retry-After` si el proveedor lo manda), y mensajes de error legibles.
- `build_provider(name)` es la fábrica: lee la configuración y devuelve la instancia correcta. Si falta una API key o el nombre no existe, falla con un mensaje claro **al construir el Core**, no a mitad de una conversación.
- `allow_tools=False` es lo que usa el Core en la pasada final del loop, cuando ya hay resultados de herramientas en la conversación y solo hace falta que el modelo responda en texto.

⚠️ **Punto importante:** ningún proveedor decide por sí mismo si el modelo soporta tool-calling — simplemente le manda el parámetro `tools` si el Core se lo pasa. Si el modelo/servidor no lo soporta y lo rechaza (por ejemplo con un 400), eso sube como excepción y el mensaje entero falla (ver la nota en `core.py` más abajo).

### `app/core/core.py`
**El Agent Loop real**, encapsulado en la clase `ElipseCore`. Esto es lo que antes vivía mezclado dentro de `tasks.py`; ahora está separado a propósito: `core.py` no sabe nada de HTTP, hilos, ni de qué proveedor hay debajo — así se puede probar sin Ollama ni internet, y cambiar de proveedor o sumar herramientas sin tocar el loop.

- `ElipseCore.run(message, emit)` nunca lanza una excepción hacia afuera: cualquier error interno (incluido un proveedor rechazando la llamada) se atrapa y se devuelve como `status="error"` con el detalle en `reply`.
- El loop por mensaje: **analizar** (system prompt + historial reciente + contexto recuperado de memoria semántica) → elegir proveedor (`router.py`, forzando el proveedor de código solo si está configurado) → **planificar** (los `tool_calls` que pide el modelo son el plan, se emiten como evento `plan`) → **ejecutar + verificar** cada llamada (`safety.py` decide si queda pendiente de confirmación; si no, `tools.py` la ejecuta y verifica con evidencia real) → si algo falló y quedan intentos, se le informa el detalle al modelo y se reintenta (`MAX_LOOP_ITERATIONS = 2`) → **responder** en texto puro (`allow_tools=False`).
- `build_memory_context()` es el RAG mínimo: busca en la memoria semántica algo relacionado al mensaje actual y lo inyecta como un mensaje de sistema adicional, aclarando explícitamente que puede ser relevante o no y que no debe repetirse textual si no aplica.
- Guarda cada turno en `messages`, y cada decisión de routing en `router_log` junto con la duración total.

### `app/core/tools.py`
**Tool System.** Define qué puede hacer ELIPSE (más allá de MCP) y cómo se verifica que realmente lo hizo.

- `TOOLS_SCHEMA`: catálogo en formato nativo de function-calling (compatible con Ollama/OpenAI): `get_current_datetime`, `calculate`, `list_files`, `read_file`, `write_file`, `search_web`, `run_python`, `remember`, `recall`, `research_topic` (estas últimas tres son de la Fase 4, memoria semántica).
- Todo acceso a archivos pasa por `_resolve_safe_path()`, que confina cualquier ruta dentro de `workspace/` y bloquea `../`, rutas absolutas o salidas del sandbox.
- `write_file` nunca sobreescribe directo si el modelo lo pide: si el archivo ya existe, `safety.py` intercepta la llamada antes de ejecutarla; el parámetro `overwrite` ni siquiera está expuesto al modelo, solo lo usa `safety.py` internamente tras la aprobación humana.
- `search_web` (vía `ddgs`) y `run_python` (los resultados de MCP también) devuelven contenido marcado explícitamente como "información, no instrucciones", para que el modelo no obedezca texto malicioso incrustado en una página o en la salida de una herramienta externa.
- `run_python` ejecuta código real en un subproceso aislado (`python -I`), con timeout, límite de salida, y un bloqueo por patrones (`BLOCKED_PATTERNS`) para imports/llamadas obviamente peligrosas (`subprocess`, `shutil`, `os.system`, `os.remove`, etc.). Si falta una librería, lo reporta explícitamente y aclara que la instalación la debe aprobar el humano vía `/v1/install-package` — el modelo nunca la instala solo.
- `remember` / `recall` / `research_topic`: las tres tools de memoria semántica de la Fase 4 (ver `memory.py` y `research.py`), expuestas al modelo para que las use quiera él mismo, sin que el humano tenga que pedirlo explícitamente.
- `get_tools_schema()` devuelve el catálogo **completo**: `TOOLS_SCHEMA + mcp_client.manager.tool_schemas()` — las propias más las de cualquier servidor MCP conectado, recalculado en cada uso.
- `execute_tool(name, arguments)` primero busca en las tools propias; si no está ahí, revisa si es una tool de MCP (`mcp_client.manager.has_tool`) y la ejecuta por ese camino.
- `tools_schema_to_mcp()`: traduce el catálogo (propio + MCP) al formato estándar de MCP (`inputSchema`), para que cualquier cliente/IA externo que hable MCP pueda descubrir y usar las herramientas de ELIPSE.
- `verify_tool_result()`: el paso "verificar" del Agent Loop. Se basa en evidencia directa (releer el archivo, revisar si hubo error explícito), **nunca** en lo que el modelo "dice" que pasó — respuesta directa a `qwen3:4b` confabulando éxito de tool-calling en corridas de fases anteriores. Hay verificadores específicos para `write_file`, `run_python`, `remember` y `research_topic`; el resto usa un verificador genérico que solo chequea que la salida no empiece con "Error".

### `app/core/mcp_client.py`
**Cliente MCP (Model Context Protocol).** Conecta ELIPSE a servidores de herramientas externos que ya existen (GitHub, filesystem, bases de datos, lo que sea que hable MCP) y las presenta al Core como una herramienta más, sin escribir un conector a mano por cada una.

- Se configura con `mcp_servers.json` en la raíz del proyecto, con el **mismo formato** que usan Claude Desktop y la mayoría de READMEs de servidores MCP (se puede copiar/pegar tal cual). Soporta transporte por `command`/`args` (stdio, procesos locales) o por `url`/`headers` (HTTP remoto), y valores `${VARIABLE}` que se resuelven contra variables de entorno.
- Extras propios de ELIPSE por servidor: `auto_approve` (`true` o una lista de nombres de tool que se ejecutan sin pedir confirmación humana — por defecto **ninguna** tool externa es de confianza), `include_tools` (exponer solo algunas, para no gastar contexto de más con modelos chicos) y `enabled: false` (dejar el servidor configurado pero apagado).
- Cada herramienta externa se expone con el nombre `<servidor>__<herramienta>` (saneado a `[a-zA-Z0-9_-]`, máximo 64 caracteres — lo que exigen Claude y OpenAI).
- Arquitectura: el SDK de MCP es asíncrono y el resto de ELIPSE usa hilos, así que el `MCPManager` corre su propio event loop en un hilo dedicado y ofrece una interfaz síncrona (`call_tool`, `tool_schemas`, `has_tool`, `is_auto_approved`, `status`). Cada servidor vive en su propia tarea de larga duración, porque los transportes MCP exigen abrirse y cerrarse desde la misma tarea.
- Limitaciones conocidas: sin reconexión automática (si un servidor muere, sus llamadas fallan hasta reiniciar ELIPSE) y no se pagina `list_tools`.

### `app/core/safety.py`
Única fuente de verdad sobre qué acción es "riesgosa" — el modelo no participa en esta decisión.

- `is_risky()`: marca como riesgosa (a) la sobreescritura de un archivo ya existente vía `write_file`, y (b) **toda** herramienta de un servidor MCP externo que no esté explícitamente marcada como `auto_approve` en `mcp_servers.json` — porque es código de terceros que ELIPSE no controla.
- `create_pending_action()` / `get_pending_action()` / `list_pending_actions()`: gestionan el ciclo de vida de una acción pendiente en la tabla `pending_actions`.
- `resolve_pending_action()`: al aprobar, ejecuta la herramienta real (forzando `overwrite=True` solo acá, para `write_file`, nunca a pedido del modelo); al rechazar, no ejecuta nada.

### `app/core/memory.py`
**Memoria semántica de largo plazo (Fase 4)**, usando **ChromaDB** con embeddings por defecto (`DefaultEmbeddingFunction`), guardada en disco (`chroma_data/`).

- Cliente y colección se inicializan de forma perezosa (singleton, con lock): recién cuando hace falta, no al importar el módulo — así un proceso que nunca usa memoria semántica no carga el modelo de embeddings.
- `add_memory(content, metadata)` guarda un texto (idealmente ya resumido, no crudo) con metadata libre (siempre incluye `created_at`).
- `search_memory(query, n_results, where)` busca por **significado**, no por palabras exactas; devuelve `{id, content, metadata, distance}` con `distance` = distancia coseno (más bajo = más parecido).
- `enforce_retention_policy()`: si hay más recuerdos que `memory_max_items`, borra los más viejos — pero **solo** entre los que tengan un `metadata['type']` marcado como podable (`memory_prunable_types`, por defecto `["research"]`). Todo lo que no tenga un tipo podable (memoria manual, identidad, etc.) queda protegido, aunque sea lo más viejo. Si podar todo lo disponible no alcanza para volver al límite, se informa `within_limit: false` y el resto queda por encima hasta liberar espacio a mano.
- `reset_memory()`: borra TODA la memoria semántica — uso manual/depuración, el agente nunca la llama.

### `app/core/research.py`
**Pipeline de investigación (Fase 4):** buscar en internet → resumir con un modelo local → guardar el resumen destilado en memoria semántica.

- Busca con `ddgs` (hasta 5 resultados), arma un bloque de texto crudo con título + fragmento + URL de cada fuente.
- Resume con `settings.ollama_research_model` (por defecto `phi3:mini`, elegido justamente porque no tiene capacidad de "thinking" y evita el bug de `think=False` de `qwen3`), con un prompt que pide parafrasear (no copiar frases textuales largas), sin opiniones, y que trata los resultados de internet explícitamente como datos a considerar, nunca instrucciones a seguir. Igual se filtran bloques `<think>` como red de seguridad.
- Guarda el resumen en memoria semántica con `metadata={"type": "research", "topic": ..., "sources": ...}` (así `enforce_retention_policy` sabe que esto sí se puede podar), y lo registra también en la tabla `research_log`.
- Nunca lanza excepción hacia afuera: siempre devuelve un dict con `status` (`"ok"` o `"error"`), pensado para usarse tanto desde la tool `research_topic` como desde `/v1/research`.

### `app/core/tasks.py`
Registro de tareas en background y **puente** entre la API y el Core — ya no contiene el Agent Loop (eso se movió a `core.py`).

- `_build_core()`: único punto donde se decide qué proveedores usa el Core según el `.env` (`DEFAULT_PROVIDER`, `CODE_PROVIDER`). Si la configuración es inválida (falta una API key, nombre desconocido), falla **al arrancar** con un mensaje claro, no a mitad de una conversación.
- `_tasks`: diccionario en memoria (`{status, result, queue}` por `task_id`). Se pierde si reinicias el servidor a mitad de una tarea — aceptable para esta fase.
- `run_agent_task(task_id, message)`: corre `ElipseCore.run()` y vuelca el resultado a la tarea, emitiendo eventos a la cola que lee el WebSocket.
- `run_research_task(task_id, topic)`: mismo mecanismo de progreso/WebSocket, pero corriendo `run_research_pipeline()` en vez del Agent Loop — así `/v1/research` reusa toda la infraestructura de `/v1/task/{id}` y `/v1/ws/{id}` sin duplicar nada.
- `describe_providers()`: qué proveedor está activo (general y de código), para `/v1/status`.

### `app/core/auth.py`
**Autenticación por API key (Fase 5 — multi-dispositivo).** Mismo patrón que plataformas como OpenRouter o Mistral: generás una llave con nombre (`"telefono"`, `"laptop"`...), la usás como `Authorization: Bearer <llave>`, y podés revocarla individualmente sin afectar las demás.

- La llave en texto plano **solo se muestra una vez**, al crearla (`create_key`). En la base de datos solo se guarda su hash SHA-256 — igual que una contraseña — así que si alguien lee `elipse.db` no puede reconstruir las llaves.
- `require_api_key` (dependency de FastAPI para rutas HTTP normales) y `require_api_key_ws` (para el WebSocket, que recibe la llave como query param `?token=...` porque el navegador no puede mandar headers custom al abrir un WebSocket) validan contra el hash y actualizan `last_used_at`.
- La primera llave se crea por CLI (`manage_keys.py`), no por HTTP, porque crear una acá ya requeriría estar autenticado.

---

## Cómo fluye una petición a `/v1/chat` ahora

```
Usuario envía mensaje (con Authorization: Bearer <api_key>)
        │
        ▼
main.py valida la llave (auth.py), crea una tarea y la corre en un thread
(run_agent_task) → responde de inmediato con task_id + ws_url + poll_url
        │
        ▼
tasks.py delega en ElipseCore.run() (core.py)
        │
        ▼
core.py arma: system_prompt (personality.py) + contexto de memoria semántica
relevante (memory.py) + historial + mensaje
        │
        ▼
router.py decide "código" o "general" → se elige el Provider correspondiente
(providers.py: Ollama, Anthropic o OpenAI-compatible)
        │
        ▼
provider.chat(...) con tools = propias (tools.py) + externas conectadas (mcp_client.py)
        │
        ▼
¿el modelo pide usar herramientas?
        │
   ┌────┴────┐
   NO         SÍ
   │           │
   │           ▼
   │      safety.py revisa cada llamada:
   │      ¿riesgosa (sobreescritura, o tool MCP no auto-aprobada)?
   │         → pendiente de confirmación humana (/v1/confirm-action)
   │      ¿segura? → tools.py (propia) o mcp_client.py (externa) la ejecuta
   │                       │
   │                       ▼
   │                tools.py la verifica con evidencia real
   │                       │
   │              ¿falló y quedan intentos? → se re-planifica
   │                       │
   │                       ▼
   └──────────────► se guarda todo en messages/router_log, se arma la respuesta final
                            │
                            ▼
        Eventos emitidos por WebSocket en cada paso (progreso, plan, verificación, final)
```

---

## `elipse_control_panel.py` (raíz del proyecto)

Panel de escritorio en Tkinter puro (sin dependencias extra) para no tener que arrancar todo a mano por consola. Vive fuera de `app/` porque no es parte del motor — es una herramienta de operación sobre él.

- Prende/apaga Ollama y ELIPSE Core como procesos, detectando si ya estaban corriendo desde afuera del panel.
- Pestaña **Modelo**: elegí el proveedor (Ollama / Claude / compatible con OpenAI) y el modelo específico, con soporte para traer la lista de modelos de cualquier servidor OpenAI-compatible (`/models`) y verificar automáticamente cuáles de esos modelos soportan tool-calling (mandando una llamada de prueba real, no adivinando por nombre).
- Pestaña **Conexión y llaves**: URL para clientes remotos, toggle de exponer en la red local, y gestión completa de API keys (crear, listar, revocar) usando `manage_keys.py --json` por debajo.
- Logs de cada servicio en `logs/*.log`, persistentes aunque cierres el panel.

---

## Notas / cosas pendientes conocidas

- **No hay capa de "skills"** (criterio reutilizable para tareas específicas — cómo armar un buen informe, cómo revisar código con cierto estilo — más allá de la personalidad general de `personality.py`). Es la pieza más grande pendiente hoy: ni el código ni el system prompt tienen ningún concepto de esto todavía.
- **Git queda fuera del Tool System por ahora** — decisión explícita, no falta ni bug, se agrega si en algún momento hace falta.
- **`run_python` no tiene sandbox de filesystem**, solo bloqueo por lista negra de patrones (`BLOCKED_PATTERNS`) y `cwd` en `workspace/`. A diferencia de `read_file`/`write_file`, el código Python que corre ahí sí podría tocar rutas absolutas fuera del workspace. Bajo riesgo mientras esto corra solo local y sin exponerse en red — pero si se expone a otros dispositivos (ya posible desde la Fase 5), conviene resolverlo con sandboxing real (contenedor separado o intérprete restringido) antes de abrir el acceso de verdad.
- **`_tasks` (estado de las tareas del Agent Loop) vive en memoria**, se pierde si reinicias el servidor a mitad de una tarea. Aceptable por ahora.
- **El `HISTORY_LIMIT` sigue fijo en 10 mensajes.** Resumir historial viejo en vez de simplemente cortarlo queda pendiente.
- **Los rasgos de personalidad y los hechos siguen siendo manuales**; la memoria semántica (Fase 4) sí guarda automáticamente lo que el modelo decide recordar vía `remember`/`research_topic`, pero la identidad/rasgos/reglas de `personality.py` no.
- **MCP sin reconexión automática**: si un servidor externo muere, hay que reiniciar ELIPSE Core para volver a conectarlo.
- **Ningún proveedor valida de antemano si el modelo soporta tool-calling.** Si elegís un modelo que no lo soporta, la llamada entera falla (no solo el uso de herramientas) porque ELIPSE siempre manda el catálogo de tools activo. Sería deseable un fallback automático que reintente sin `tools` si el proveedor rechaza específicamente ese parámetro.