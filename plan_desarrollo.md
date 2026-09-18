# ELIPSE — Plan de Desarrollo

Motor de IA personal, portable e integrable, con memoria propia, personalidad persistente y capacidad de usar varios proveedores de IA como especialistas sin perder su identidad.

---

## 1. Visión

ELIPSE no es una app ni un chatbot: es un **motor** que corre en un servidor (tu PC, un servidor casero o un VPS) y expone una API. Cualquier cliente —teléfono, reloj, web, escritorio— se conecta a ese mismo cerebro. La inteligencia y la personalidad viven en el Core; los proveedores externos (Claude, ChatGPT, modelos locales) son herramientas intercambiables que el Core usa según la tarea.

---

## 2. Arquitectura general

```
                      ELIPSE
                        │
                ┌───────▼────────┐
                │   ELIPSE CORE  │
                │  Identidad     │
                │  Personalidad  │
                │  Memoria       │
                │  Planning      │
                │  Agent Loop    │
                └───────┬────────┘
                        │
      ┌─────────────────┼──────────────────┐
      ▼                 ▼                  ▼
 Model Router      Tool System         Memory Store
      │                 │                  │
      ▼                 ▼                  ▼
Claude / GPT /     Python/Git/Files/   SQLite (hechos)
Modelo local       Web/Investigación   Qdrant/Chroma (semántica)
      │
      ▼
  ┌────────────────┐
  │    FastAPI     │
  │   REST / WS    │
  └───────┬────────┘
          │
 ┌────────┼────────────┐
 ▼        ▼             ▼
Teléfono Reloj    Web / Desktop
```

**Principio rector**: si algún día quieres cambiar cómo razona ELIPSE, o de qué proveedor depende, no quieres estar limitado por decisiones de una librería externa. Por eso el Core es propio; los frameworks (LangGraph, etc.) son opcionales y se usan solo donde aporten algo concreto.

---

## 3. Componentes

### 3.1 ELIPSE Core (propio)
El "cerebro". No depende de ningún proveedor específico.
- **Identity & Memory Layer**: rasgos de personalidad, tono, valores, forma de hablar. Se inyecta en cada respuesta sin importar qué modelo la generó por debajo.
- **Agent Loop**: analizar → planificar → ejecutar → verificar → responder (o replantear si falla la verificación).
- **Planning**: descompone tareas complejas en pasos.

### 3.2 Model Router
Decide qué proveedor usar según la tarea (código → Claude, investigación web → GPT con búsqueda, privado/offline → modelo local). Empieza con reglas simples (if/else) y se sofistica después si hace falta.

### 3.3 Tool System
Acceso a Python, Git, archivos, navegación web. Diseñar el esquema de herramientas compatible con **MCP (Model Context Protocol)** para poder conectar herramientas externas ya existentes sin reinventar cada conector.

### 3.4 Memory Store
- **SQLite**: hechos concretos, historial estructurado, preferencias.
- **Qdrant/Chroma** (fase posterior): memoria semántica, para recuperar contexto relevante aunque no sea una coincidencia exacta.
- Política de "qué guardar": resúmenes destilados, no texto crudo de cada investigación (para no saturar la memoria).

### 3.5 Research/Ingestion Pipeline
Busca en la web o consulta APIs, resume con un modelo, guarda el resumen en memoria. Es lo que le da a ELIPSE la sensación de "nutrirse" con el tiempo.

### 3.6 FastAPI (capa de exposición)
REST + WebSocket. Endpoints iniciales:
```
POST /v1/chat
POST /v1/agent
POST /v1/memory
GET  /v1/status
GET  /v1/tools
```
Documentación OpenAPI automática → facilita crear un SDK después.

### 3.7 Clientes
Teléfono, reloj, web, escritorio: todos son clientes delgados del mismo servicio. Requieren autenticación (token), sincronización de sesión entre dispositivos, y opcionalmente push notifications si ELIPSE necesita "hablar primero".

---

## 4. Stack recomendado

| Componente | Elección |
|---|---|
| Lenguaje | Python |
| API | FastAPI |
| Core / Agente | Propio |
| Modelos externos | Claude API, OpenAI API (vía Model Router) |
| Modelo local | llama.cpp / Ollama (GGUF) |
| Memoria simple | SQLite |
| Memoria semántica | Qdrant / Chroma |
| Comunicación | REST + WebSocket |
| CLI | Typer |
| Configuración | Pydantic |
| Empaquetado | PyInstaller / Docker |
| SDK | Python primero (`pip install elipse-ai`) |

---

## 5. Fases de desarrollo

### Fase 0 — Fundaciones (semanas 1–2)(completado)
- Estructura del repo, entorno, configuración con Pydantic.
- Endpoint `/v1/chat` mínimo: recibe texto, llama a un solo proveedor (ej. Claude API), devuelve respuesta. Sin personalidad ni memoria todavía.
- Objetivo: tener algo que responde de punta a punta.

### Fase 1 — Identidad y memoria básica (semanas 3–5)(completada)
- Definir personalidad como datos (no como prompt hardcodeado): rasgos, tono, reglas de estilo, guardados en SQLite.
- Inyectar esa personalidad en cada llamada al modelo.
- Memoria simple: guardar historial de conversación y hechos clave.


### Fase 2 — Model Router (semanas 6–7)(completado)
- Añadir un segundo proveedor (ej. GPT o modelo local).
- Reglas simples de enrutamiento según tipo de tarea.
- Logging estructurado de cada decisión (qué proveedor, por qué, resultado) — esto es crítico para depurar y no opcional.

### Fase 3 — Agent Loop y herramientas (semanas 8–11)(completado)
- Loop analizar → planificar → ejecutar → verificar, empezando con una versión mínima (casi un wrapper de function-calling).
- Tool System: Python, archivos,web. Diseñar esquema compatible con MCP desde el inicio.
- Ejecución en background para tareas largas + WebSocket para pasos intermedios (evitar bloquear el HTTP).

**Hallazgos importantes de esta sesión (limitaciones reales del modelo, no bugs de código):**
- `qwen2.5-coder:3b` no ejecuta tool-calling de forma confiable — confabula éxito sin
  llamar la función real. Arreglado forzando `qwen3:4b` siempre que hay herramientas activas,
  independientemente de lo que decida el Model Router (el router sigue logueando su decisión
  original para no perder esa información).
- `qwen3:4b`, aunque mucho más confiable, NO es 100% consistente entre intentos idénticos:
  en una prueba, el mismo mensaje exacto una vez alucinó una llamada de tool como texto plano
  (`used_tools: false`) y la siguiente vez ejecutó la herramienta correctamente. Conclusión:
  toda verificación de comportamiento debe hacerse con evidencia directa en Python
  (`read_file`, `list_files`, etc.), nunca confiando solo en el texto de `reply`.


### Fase 4 — Memoria semántica y research pipeline (semanas 12–15)(completado)
- Migrar de SQLite puro a memoria semántica (Qdrant/Chroma) para contexto recuperable por similitud.
- Pipeline de investigación: buscar → resumir → guardar destilado.
- Política de retención/limpieza de memoria (que no crezca sin control).

### Fase 5 — Multi-dispositivo (semanas 16–18)
- Autenticación por token.
- Cliente web simple como prueba de concepto.
- Sincronización de sesión entre dispositivos.
- (Opcional) notificaciones push si ELIPSE debe iniciar contacto.

### Fase 6 — SDK y empaquetado (semanas 19+)
- `pip install elipse-ai` con interfaz simple: `from elipse import Elipse; ai = Elipse(); ai.run("...")`.
- Empaquetado con Docker para desplegar en servidor propio o VPS.
- Evaluar frameworks de agentes (LangGraph) solo si el Agent Loop propio se queda corto para flujos complejos — no antes.

---

## 6. Decisiones de diseño que hay que mantener

1. **El Core nunca depende de un proveedor específico.** Todo pasa por el Model Router.
2. **La personalidad vive en datos, no en el prompt de un proveedor externo.** Así es consistente sin importar qué modelo respondió.
3. **No construir todo sobre LangChain/LangGraph.** Se evalúan como herramientas puntuales, no como base.
4. **Observabilidad desde el día uno**, no como algo para "después".
5. **Memoria = resúmenes destilados**, no texto crudo acumulado sin filtro.

---

## 7. Riesgos a vigilar

- **Alcance del Core propio**: el loop de agente puede crecer indefinidamente en complejidad (reintentos, parsing, límites de contexto). Empezar mínimo y ampliar solo cuando de verdad se necesite.
- **Costo de usar varios proveedores pagos**: cachear respuestas y poner límites de uso desde el principio.
- **Expectativa vs. realidad del "crecimiento"**: ELIPSE no va a reentrenar sus propios pesos ni modificar su razonamiento de forma autónoma. El "crecimiento" real viene de la acumulación de memoria y del ajuste manual de reglas con el tiempo — no de una evolución autónoma tipo ciencia ficción.
