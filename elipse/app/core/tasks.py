import queue
import time
import uuid
from typing import Optional

import ollama

from app.config import settings
from app.core.db import get_connection
from app.core.personality import build_system_prompt
from app.core.router import choose_provider
from app.core.tools import TOOLS_SCHEMA, execute_tool, verify_tool_result
from app.core.safety import is_risky, create_pending_action

HISTORY_LIMIT = 10
MAX_LOOP_ITERATIONS = 2  # 1 intento + 1 reintento si la verificación falla

# Almacén de tareas en memoria. Se pierde si reinicias el servidor — suficiente
# para esta fase; una versión futura podría guardar esto en SQLite también.
_tasks: dict[str, dict] = {}


def _save_message(role: str, content: str):
    conn = get_connection()
    conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
    conn.commit()
    conn.close()


def _get_recent_messages(limit: int = HISTORY_LIMIT):
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def _log_router_decision(message: str, provider: str, reason: str, duration: float):
    conn = get_connection()
    conn.execute(
        "INSERT INTO router_log (message, provider_chosen, reason, duration_seconds) VALUES (?, ?, ?, ?)",
        (message, provider, reason, duration)
    )
    conn.commit()
    conn.close()


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
    Corre el Agent Loop completo para 'message' y va emitiendo eventos de progreso.
    Diseñada para correr en un hilo aparte (ver main.py) — nunca bloquea el servidor
    principal mientras trabaja.

    Loop: analizar -> planificar -> ejecutar -> verificar (con reintento acotado si
    la verificación falla) -> responder.
    """
    try:
        _emit(task_id, {"type": "progreso", "mensaje": "Analizando tu mensaje..."})

        system_prompt = build_system_prompt()
        history = _get_recent_messages()

        provider_type, reason = choose_provider(message)

        # qwen2.5-coder:3b no ejecuta tool-calling de forma confiable (confirmado en pruebas reales).
        model_name = settings.ollama_model
        if provider_type == "code":
            reason = f"{reason} (forzado a modelo general: coder no soporta tool-calling confiable)"

        _save_message("user", message)

        conversation = [{"role": "system", "content": system_prompt}]
        conversation.extend(history)
        conversation.append({"role": "user", "content": message})

        start = time.time()
        final_tool_calls_used = False

        for iteration in range(1, MAX_LOOP_ITERATIONS + 1):
            response = ollama.chat(model=model_name, messages=conversation, tools=TOOLS_SCHEMA)
            tool_calls = response["message"].get("tool_calls")

            if not tool_calls:
                break  # no hizo falta ninguna herramienta

            final_tool_calls_used = True
            conversation.append(response["message"])

            # PLANIFICAR: los tool_calls que decidió el modelo SON el plan de este loop
            # mínimo. Se hace explícito emitiéndolo antes de ejecutar nada.
            plan = [{"tool": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in tool_calls]
            _emit(task_id, {"type": "plan", "intento": iteration, "pasos": plan})

            pending_confirmations = []
            verification_failures = []

            # EJECUTAR
            for call in tool_calls:
                tool_name = call["function"]["name"]
                tool_args = call["function"]["arguments"]

                _emit(task_id, {"type": "progreso", "mensaje": f"Ejecutando herramienta: {tool_name}..."})

                risky, detail = is_risky(tool_name, tool_args)
                if risky:
                    action_id = create_pending_action(tool_name, tool_args, detail)
                    pending_confirmations.append({"id": action_id, "tool": tool_name, "detail": detail})
                    conversation.append({
                        "role": "tool",
                        "content": (
                            f"Esta acción requiere confirmación humana antes de ejecutarse: {detail} "
                            f"(id: {action_id}). No se ejecutó nada todavía."
                        )
                    })
                    continue

                result = execute_tool(tool_name, tool_args)
                conversation.append({"role": "tool", "content": str(result)})

                # VERIFICAR: evidencia directa, no el texto que el modelo genere después.
                ok, detail = verify_tool_result(tool_name, tool_args, result)
                _emit(task_id, {
                    "type": "verificacion", "intento": iteration, "tool": tool_name,
                    "ok": ok, "detalle": detail,
                })
                if not ok:
                    verification_failures.append(f"- {tool_name}: {detail}")

            if pending_confirmations:
                duration = time.time() - start
                reply = (
                    "Esta acción necesita tu confirmación antes de ejecutarse. "
                    "Usa POST /v1/confirm-action/{id} con {\"approved\": true} para aprobarla, "
                    "o {\"approved\": false} para rechazarla."
                )
                _save_message("assistant", reply)
                _log_router_decision(message, model_name, reason, duration)

                final = {
                    "reply": reply,
                    "provider": model_name,
                    "reason": reason,
                    "used_tools": False,
                    "pending_confirmations": pending_confirmations,
                }
                _tasks[task_id]["status"] = "esperando_confirmacion"
                _tasks[task_id]["result"] = final
                _emit(task_id, {"type": "final", "data": final})
                return

            if verification_failures and iteration < MAX_LOOP_ITERATIONS:
                # No confiamos en que el modelo "sepa" que falló solo: se lo decimos
                # explícitamente con la evidencia recolectada y se reintenta el plan.
                _emit(task_id, {"type": "progreso", "mensaje": "La verificación encontró problemas, reintentando..."})
                conversation.append({
                    "role": "user",
                    "content": (
                        "La verificación automática (basada en evidencia directa, no en lo que reportaste) "
                        "encontró problemas con el resultado anterior:\n" + "\n".join(verification_failures) +
                        "\nCorrige el plan y vuelve a intentarlo."
                    )
                })
                continue  # re-planificar

            _emit(task_id, {"type": "progreso", "mensaje": "Generando la respuesta final..."})
            response = ollama.chat(model=model_name, messages=conversation)
            break

        duration = time.time() - start
        reply = response["message"]["content"]
        _save_message("assistant", reply)
        _log_router_decision(message, model_name, reason, duration)

        final = {"reply": reply, "provider": model_name, "reason": reason, "used_tools": final_tool_calls_used}
        _tasks[task_id]["status"] = "completado"
        _tasks[task_id]["result"] = final
        _emit(task_id, {"type": "final", "data": final})

    except Exception as e:
        final = {"reply": f"Error interno: {e}", "provider": None, "reason": None, "used_tools": False}
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["result"] = final
        _emit(task_id, {"type": "final", "data": final})