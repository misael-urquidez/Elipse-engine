# ELIPSE Engine — Estructura del proyecto

Este documento explica qué hace cada archivo dentro de `app/`, la carpeta que contiene el motor de ELIPSE. Está pensado para que cualquiera (incluido tu yo del futuro, o cualquier IA con la que sigas trabajando) entienda rápido cómo está armado el Core sin tener que leer todo el código de una.

> **Fase actual del plan:** Fase 0, 1 y 2 cerradas. Próxima: Fase 3 — Agent Loop y herramientas.

---

## Estructura

```
elipse/
├── .env                        # Variables de entorno (config local, no se sube a git)
├── requirements.txt            # Dependencias de Python del proyecto
├── elipse.db                   # Base de datos SQLite (se genera sola al arrancar)
└── app/
    ├── __init__.py
    ├── main.py                 # Punto de entrada: define la API y conecta todo
    ├── config.py                # Configuración del proyecto (Pydantic Settings)
    └── core/
        ├── __init__.py
        ├── db.py                 # Conexión a SQLite y creación de tablas
        ├── seed_personality.py   # Carga los datos iniciales de personalidad
        ├── personality.py        # Arma el system prompt a partir de los datos guardados
        └── router.py              # Model Router: decide qué modelo de Ollama usar según el mensaje
```

---

## Qué hace cada archivo

### `app/main.py`
Es el corazón visible del motor: acá vive la API construida con **FastAPI**.

- Al arrancar, inicializa la base de datos (`init_db()`) y carga la personalidad por defecto si todavía no existe (`seed()`).
- Define los endpoints:
  - `GET /v1/status` → chequeo simple de que el servidor está vivo, devuelve el nombre de la app y el modelo activo.
  - `POST /v1/chat` → endpoint principal. Recibe un mensaje del usuario, arma el `system_prompt` con la personalidad de ELIPSE, junta los últimos mensajes del historial para dar contexto de la conversación en curso, usa el **Model Router** para decidir qué modelo de Ollama usar, le manda todo al modelo elegido, guarda tanto el mensaje del usuario como la respuesta en la base de datos, registra la decisión del router en el log, y devuelve la respuesta (junto con qué proveedor se usó y por qué).
  - `POST /v1/memory` → guarda un "hecho" permanente sobre el usuario o su contexto (ej: en qué proyecto está trabajando), para que ELIPSE lo tenga presente en cada conversación futura, no solo cuando se menciona.
  - `GET /v1/memory` → lista los hechos guardados hasta ahora.
  - `GET /v1/router-log` → muestra las últimas 20 decisiones que tomó el Model Router: qué modelo eligió, por qué, y cuánto tardó en responder.
- Es el archivo que "conecta" todas las piezas: configuración, base de datos, personalidad, historial, router y el proveedor del modelo (Ollama).

### `app/config.py`
Define la configuración del proyecto usando `pydantic-settings`.

- Lee variables desde el archivo `.env`.
- Define qué modelo de Ollama se usa para conversación general (`ollama_model`) y cuál se usa para tareas de código (`ollama_code_model`) — estos son los dos proveedores que usa el Model Router.
- Centraliza cualquier valor que pueda cambiar según el entorno, sin tener que tocar el código en `main.py`.

### `app/core/db.py`
Maneja todo lo relacionado a la conexión con SQLite.

- `get_connection()`: abre una conexión a `elipse.db` (el archivo de base de datos, que vive en la raíz del proyecto).
- `init_db()`: crea las tablas si todavía no existen. Define la estructura de la memoria, identidad y decisiones de ELIPSE:
  - **`identity`**: datos fijos — nombre, creador, propósito, valores centrales. No cambia con cada conversación.
  - **`traits`**: rasgos de personalidad como números entre 0.0 y 1.0 (curiosidad, calidez, formalidad, etc). Permite que ELIPSE tenga varios rasgos a la vez, en vez de "un solo tono".
  - **`style_rules`**: reglas de estilo en texto plano (cómo debe hablar, qué evitar, incluyendo reglas de identidad como no negar quién es su creador).
  - **`messages`**: historial de conversación — cada mensaje (de usuario o de ELIPSE), con su rol y fecha. Se usa tanto para guardar registro como para dar contexto de la conversación en curso.
  - **`facts`**: hechos permanentes guardados manualmente vía `/v1/memory`, que se inyectan siempre en el `system_prompt` (no solo cuando se los menciona en la conversación).
  - **`router_log`**: registro estructurado de cada decisión del Model Router — mensaje recibido, modelo elegido, motivo de la elección, y tiempo de respuesta.

### `app/core/seed_personality.py`
Se encarga de poblar la base de datos con los valores iniciales de personalidad, **solo si todavía no existen** (para no pisar cambios que hagas a mano más adelante).

- Inserta la identidad base de ELIPSE (nombre, creador, propósito, valores).
- Inserta los rasgos por defecto (curiosidad, analítica, calidez, formalidad, humor, proactividad, prudencia), cada uno con un valor inicial.
- Inserta las reglas de estilo por defecto (tono informal salvo que el tema amerite formalidad, priorizar utilidad, explicar el razonamiento, no negar quién es su creador, etc).

Este archivo es el que se edita cuando querés ajustar cómo "es" ELIPSE desde el inicio — es la definición de su personalidad base en forma de datos, no de prompt escrito a mano en el código de chat.

### `app/core/personality.py`
Convierte los datos guardados en SQLite en el texto que efectivamente se le manda al modelo como `system prompt`.

- `build_system_prompt()`: lee la identidad, los rasgos, las reglas de estilo y los hechos guardados (`facts`) desde la base de datos, y arma un bloque de texto único con todo eso.
- Este texto es el que hace que el modelo de turno (el que haya elegido el router) responda "siendo" ELIPSE, con su propósito, personalidad y memoria de hechos clave, en vez de responder genérico.

### `app/core/router.py`
El **Model Router**: decide qué modelo de Ollama usar para cada mensaje, según reglas simples (if/else), tal como pide la Fase 2 del plan.

- `choose_provider(message)`: revisa si el mensaje contiene palabras clave relacionadas a código (python, función, bug, error, sql, etc). Si encuentra alguna, indica que se use el modelo de código (`qwen2.5-coder:3b`). Si no, indica que se use el modelo general (`qwen3:4b`).
- Devuelve también el motivo de la decisión (qué palabra clave disparó la regla, o que no se encontró ninguna), que se guarda en `router_log` para poder auditar después cómo está decidiendo el router.

---

## Cómo fluye una petición a `/v1/chat`

```
Usuario envía mensaje
        │
        ▼
main.py recibe el POST
        │
        ▼
personality.py arma el system_prompt (identidad + rasgos + reglas + hechos, vía db.py)
        │
        ▼
main.py trae los últimos mensajes del historial (contexto de la conversación en curso)
        │
        ▼
router.py decide qué modelo usar (general o código) y por qué
        │
        ▼
db.py guarda el mensaje del usuario en la tabla `messages`
        │
        ▼
main.py llama a Ollama con [system_prompt + historial + mensaje del usuario], usando el modelo elegido
        │
        ▼
Ollama devuelve la respuesta
        │
        ▼
db.py guarda la respuesta en `messages`
        │
        ▼
db.py guarda la decisión del router en `router_log`
        │
        ▼
main.py devuelve la respuesta al usuario (+ qué proveedor se usó y por qué)
```

---

## Notas / cosas pendientes conocidas

- **El `HISTORY_LIMIT` está fijo en 10 mensajes.** Si una conversación crece mucho, en algún momento va a convenir resumir el historial viejo en vez de mandarlo siempre crudo — eso queda para una fase de memoria más avanzada, no es urgente ahora.
- **Los rasgos de personalidad se editan a mano por ahora** (vía `seed_personality.py` o directo en la base). La idea de una personalidad que evoluciona sola con el tiempo, con un mecanismo de evaluación controlado, queda para una fase posterior — no se construye todavía para evitar complejidad prematura.
- **Los hechos (`facts`) se guardan manualmente**, no hay todavía un sistema que detecte solo qué vale la pena recordar de una conversación. Eso corresponde a la Fase 4 (memoria semántica / research pipeline).
- **El Model Router usa reglas simples por palabras clave.** Es exactamente lo que pide esta fase del plan ("empieza con reglas simples, se sofistica después si hace falta") — no está pensado para ser perfecto, sino para cumplir el mínimo necesario y dejar espacio a mejorarlo cuando haga falta.
- **Sin GPU dedicada, las respuestas pueden tardar bastante** (se vieron casos de +40 segundos con el modelo de código). El campo `duration_seconds` en `router_log` sirve justamente para poder medir esto con datos reales, no a ojo.
- **Todavía no hay Agent Loop ni Tool System.** ELIPSE puede conversar con personalidad, memoria y elegir entre dos modelos, pero todavía no puede ejecutar acciones (Python, archivos, Git, web). Eso es la Fase 3, la próxima en el plan.