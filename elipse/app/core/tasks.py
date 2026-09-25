"""
Registro de tareas en background y puente entre la API y el Core.

Aquí NO vive el Agent Loop (eso es core.py). Este módulo solo:
  - lleva el registro de tareas (estado, resultado y cola de eventos),
  - conecta el `emit` del Core con la cola que lee el WebSocket,
  - corre el pipeline de investigación con el mismo mecanismo.
"""

import queue
import uuid
from typing import Optional

from app.config import settings
from app.core.core import ElipseCore
from app.core.providers import build_provider
from app.core.research import run_research_pipeline


def _build_core() -> ElipseCore:
    """
    Único punto donde se decide QUÉ proveedores usa el Core, según el .env:
        DEFAULT_PROVIDER=ollama | anthropic | openai
        CODE_PROVIDER=            (vacío = el mismo; o p. ej. anthropic solo para código)
    Si la configuración es inválida (falta una API key, nombre desconocido) falla
    AL ARRANCAR con un mensaje claro, no a mitad de una conversación.
    """
    default = build_provider(settings.default_provider)
    code = build_provider(settings.code_provider) if settings.code_provider.strip() else None
    return ElipseCore(provider=default, code_provider=code)


_core = _build_core()


def describe_providers() -> dict:
    """Qué proveedores hay activos (para /v1/status)."""
    return {
        "default": _core.provider.name,
        "code": _core.code_provider.name if _core.code_provider else None,
    }

# Almacén de tareas en memoria. Se pierde si reinicias el servidor — suficiente
# para esta fase; una versión futura podría guardar esto en SQLite también.
_tasks: dict[str, dict] = {}


def create_task() -> str:
    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {
        "status": "en_progreso",
        "result": None,
        "queue": queue.Queue(),
    }
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)


def _emit(task_id: str, event: dict):
    """Manda un evento a la cola de la tarea. El WebSocket la va leyendo en vivo."""
    task = _tasks.get(task_id)
    if task:
        task["queue"].put(event)


def run_agent_task(task_id: str, message: str):
    """
    Corre el Core para 'message' dentro de una tarea. Diseñada para correr en un
    hilo aparte (ver main.py) — nunca bloquea el servidor principal.
    """
    status, final = _core.run(message, emit=lambda event: _emit(task_id, event))

    _tasks[task_id]["status"] = status
    _tasks[task_id]["result"] = final
    _emit(task_id, {"type": "final", "data": final})


def run_research_task(task_id: str, topic: str):
    """
    Corre el pipeline de investigación (buscar -> resumir -> guardar) dentro de
    una tarea con el mismo mecanismo de progreso/WebSocket que run_agent_task,
    para reusar /v1/task/{id} y /v1/ws/{id} sin duplicar infraestructura.
    """
    def emit_cb(event):
        _emit(task_id, event)

    try:
        result = run_research_pipeline(topic, emit=emit_cb)

        if result["status"] != "ok":
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["result"] = result
            _emit(task_id, {"type": "final", "data": result})
            return

        _tasks[task_id]["status"] = "completado"
        _tasks[task_id]["result"] = result
        _emit(task_id, {"type": "final", "data": result})

    except Exception as e:
        final = {"status": "error", "detail": f"Error interno: {e}"}
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["result"] = final
        _emit(task_id, {"type": "final", "data": final})