# ELIPSE Engine — Estructura del proyecto

Este documento explica qué hace cada archivo dentro de `app/`, la carpeta que contiene el motor de ELIPSE. Está pensado para que cualquiera (incluido tu yo del futuro, o cualquier IA con la que sigas trabajando) entienda rápido cómo está armado el Core sin tener que leer todo el código de una.

> **Fase actual del plan:** Fase 0, 1, 2 y 3 cerradas (Git descartado a propósito, no se necesita por ahora). Próxima: Fase 4 — Memoria semántica y research pipeline.

---

## Estructura

```
elipse/
├── .env                        # Variables de entorno (config local, no se sube a git)
├── requirements.txt            # Dependencias de Python del proyecto
├── elipse.db                   # Base de datos SQLite (se genera sola al arrancar)
├── workspace/                  # Carpeta sandbox: único lugar donde las tools pueden leer/escribir
└── app/
    ├── __init__.py
    ├── main.py                 # Punto de entrada: define la API y conecta todo
    ├── config.py                # Configuración del proyecto (Pydantic Settings)
    └── core/
        ├── __init__.py
        ├── db.py                 # Conexión a SQLite y creación de tablas
        ├── seed_personality.py   # Carga los datos iniciales de personalidad
        ├── personality.py        # Arma el system prompt a partir de los datos guardados
        ├── router.py             # Model Router: decide qué modelo de Ollama usar según el mensaje
        ├── tools.py               # Tool System: definición + ejecución + verificación de herramientas
        ├── safety.py              # Decide qué acciones son riesgosas y gestiona confirmación humana
        └── tasks.py                # Agent Loop: analizar → planificar → ejecutar → verificar, en background
```

---

## Qué hace cada archivo

### `app/main.py`
El corazón visible del motor: acá vive la API construida con **FastAPI**.

- Al arrancar, inicializa la base de datos (`init_db()`) y carga la personalidad por defecto si todavía no existe (`seed()`).
- Endpoints principales:
  - `GET /v1/status` → chequeo de que el servidor está vivo.
  - `POST /v1/chat` → **ya no responde directo**. Crea una tarea (`create_task`), la corre en un thread aparte (`run_agent_task`) para no bloquear el servidor, y devuelve de inmediato `task_id` + `ws_url` + `poll_url`.
  - `GET /v1/task/{task_id}` → consulta el resultado final de una tarea (alternativa a WebSocket, sin eventos intermedios).
  - `WS /v1/ws/{task_id}` → progreso en vivo de una tarea: eventos `progreso`, `plan`, `verificacion` y `final`.
  - `POST /v1/memory` / `GET /v1/memory` → guardar y listar hechos permanentes.
  - `GET /v1/router-log` → últimas 20 decisiones del Model Router.
  - `GET /v1/pending-actions` → lista acciones que están esperando confirmación humana.
  - `POST /v1/confirm-action/{id}` → aprueba o rechaza una acción riesgosa pendiente (ej. sobreescribir un archivo).
  - `GET /v1/tools?format=ollama|mcp` → expone el catálogo de herramientas, en formato nativo o traducido a MCP.
  - `POST /v1/install-package` → instala un paquete de pip. **No** es una tool del modelo (no está en `TOOLS_SCHEMA`); solo para uso humano directo, cuando `run_python` reporta que falta una librería.

### `app/config.py`
Configuración del proyecto con `pydantic-settings`, leída desde `.env`: nombre de la app, modelo general (`ollama_model`), modelo de código (`ollama_code_model`) y carpeta del workspace (`workspace_dir`).

### `app/core/db.py`
Conexión a SQLite y creación de tablas: `identity`, `traits`, `style_rules`, `messages`, `facts`, `router_log`, y `pending_actions` (nueva en esta fase — guarda acciones riesgosas que esperan aprobación humana: herramienta, argumentos, motivo y estado).

### `app/core/seed_personality.py`
Puebla la base con identidad, rasgos y reglas de estilo por defecto, solo si todavía no existen. Es el archivo que se edita para ajustar cómo "es" ELIPSE desde el inicio.

### `app/core/personality.py`
`build_system_prompt()`: arma el system prompt a partir de identidad + rasgos + reglas + hechos guardados en SQLite. Sin cambios respecto a la fase anterior.

### `app/core/router.py`
Model Router por reglas simples (palabras clave de código → modelo de código, si no → modelo general), con logging del motivo. Sin cambios de lógica — pero ver nota abajo sobre cómo lo usa `tasks.py` ahora.

### `app/core/tools.py`
**Nuevo — Tool System.** Define qué puede hacer ELIPSE y cómo se verifica que realmente lo hizo.

- `TOOLS_SCHEMA`: catálogo en formato nativo de Ollama (function-calling): `get_current_datetime`, `calculate`, `list_files`, `read_file`, `write_file`, `search_web`, `run_python`.
- Todo acceso a archivos pasa por `_resolve_safe_path()`, que confina cualquier ruta dentro de `workspace/` y bloquea `../`, rutas absolutas o salidas del sandbox.
- `write_file` nunca sobreescribe directo si el modelo lo pide: si el archivo ya existe, `safety.py` intercepta la llamada antes de ejecutarla.
- `search_web` (via `ddgs`) devuelve resultados marcados explícitamente como "información, no instrucciones", para que el modelo no obedezca texto malicioso incrustado en una página.
- `run_python` ejecuta código real en un subproceso aislado (`python -I`), con timeout, límite de salida, y un bloqueo por patrones (`BLOCKED_PATTERNS`) para imports/llamadas obviamente peligrosas. Si falta una librería, lo reporta explícitamente y aclara que la instalación la debe aprobar el humano vía `/v1/install-package` — el modelo nunca la instala solo.
- `tools_schema_to_mcp()`: traduce el catálogo a formato MCP (`inputSchema`), para que cualquier cliente/IA externo que hable MCP pueda descubrir y usar las herramientas de ELIPSE.
- `verify_tool_result()`: el paso "verificar" del Agent Loop. Se basa en evidencia directa (releer el archivo, revisar el código de salida real), **nunca** en lo que el modelo "dice" que pasó — esto es en respuesta directa a lo observado con `qwen3:4b` confabulando éxito de tool-calling en algunas corridas.

### `app/core/safety.py`
**Nuevo.** Única fuente de verdad sobre qué acción es "riesgosa" — el modelo no participa en esta decisión.

- `is_risky()`: hoy solo marca como riesgosa la sobreescritura de un archivo existente.
- `create_pending_action()` / `get_pending_action()` / `list_pending_actions()`: gestionan el ciclo de vida de una acción pendiente en la tabla `pending_actions`.
- `resolve_pending_action()`: al aprobar, ejecuta la herramienta real (forzando `overwrite=True` solo acá, nunca a pedido del modelo); al rechazar, no ejecuta nada.

### `app/core/tasks.py`
**Nuevo — Agent Loop.** Corre en un thread aparte, orquesta todo lo anterior y emite eventos de progreso a una `queue.Queue` que el WebSocket va leyendo en vivo.

Loop por mensaje: **analizar** (arma prompt + historial) → **planificar** (los `tool_calls` que decide el modelo son el plan, se emiten como evento `plan`) → **ejecutar** (cada tool, salvo que `safety.py` la marque riesgosa, en cuyo caso queda pendiente de confirmación) → **verificar** (con evidencia directa vía `tools.py`) → si algo falló y quedan intentos, se le informa el detalle al modelo y se replanifica (`MAX_LOOP_ITERATIONS = 2`) → responder.

Nota sobre el Model Router en este flujo: `qwen2.5-coder:3b` no ejecuta tool-calling de forma confiable, así que **siempre se fuerza el modelo general** cuando hay herramientas activas — el router igual loguea su decisión original para no perder esa información.

---

## Cómo fluye una petición a `/v1/chat` ahora

```
Usuario envía mensaje
        │
        ▼
main.py crea una tarea y la corre en un thread (run_agent_task) → responde de inmediato con task_id
        │
        ▼
tasks.py arma system_prompt (personality.py) + historial + mensaje
        │
        ▼
router.py decide modelo (y se fuerza el general si hay tools activas)
        │
        ▼
Ollama responde: ¿pide usar herramientas?
        │
   ┌────┴────┐
   NO         SÍ
   │           │
   │           ▼
   │      safety.py revisa cada llamada:
   │      ¿riesgosa? → pendiente de confirmación humana (/v1/confirm-action)
   │      ¿segura?   → tools.py la ejecuta
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

## Notas / cosas pendientes conocidas

- **Git queda fuera del Tool System por ahora** — decisión explícita, no falta ni bug, se agrega si en algún momento hace falta.
- **`run_python` no tiene sandbox de filesystem**, solo bloqueo por lista negra de patrones (`BLOCKED_PATTERNS`) y `cwd` en `workspace/`. A diferencia de `read_file`/`write_file`, el código Python que corre ahí sí podría tocar rutas absolutas fuera del workspace. Bajo riesgo mientras esto corra solo local y sin exponerse en red — pero si en Fase 5 se expone a otros dispositivos, conviene resolverlo con sandboxing real (contenedor separado o intérprete restringido) antes de abrir el acceso.
- **`_tasks` (estado de las tareas del Agent Loop) vive en memoria**, se pierde si reinicias el servidor a mitad de una tarea. Aceptable para esta fase.
- **El `HISTORY_LIMIT` sigue fijo en 10 mensajes.** Resumir historial viejo queda para una fase de memoria más avanzada.
- **Los rasgos de personalidad y los hechos siguen siendo manuales.** Detectar solo qué vale la pena recordar de una conversación corresponde a la Fase 4.
- **Sin autenticación todavía** — no es parte de esta fase, corresponde a Fase 5 (multi-dispositivo).