import time
from fastapi import FastAPI
from pydantic import BaseModel
import ollama
from app.config import settings
from app.core.db import get_connection, init_db
from app.core.seed_personality import seed
from app.core.personality import build_system_prompt
from app.core.router import choose_provider
from app.core.tools import TOOLS_SCHEMA, execute_tool
from app.core.safety import is_risky, create_pending_action, list_pending_actions, resolve_pending_action

app = FastAPI(title=settings.app_name)

init_db()
seed()

HISTORY_LIMIT = 10


class ChatRequest(BaseModel):
    message: str


class MemoryRequest(BaseModel):
    content: str


class ConfirmActionRequest(BaseModel):
    approved: bool


def save_message(role: str, content: str):
    conn = get_connection()
    conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
    conn.commit()
    conn.close()


def get_recent_messages(limit: int = HISTORY_LIMIT):
    conn = get_connection()
    rows = conn.execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def log_router_decision(message: str, provider: str, reason: str, duration: float):
    conn = get_connection()
    conn.execute(
        "INSERT INTO router_log (message, provider_chosen, reason, duration_seconds) VALUES (?, ?, ?, ?)",
        (message, provider, reason, duration)
    )
    conn.commit()
    conn.close()


@app.get("/v1/status")
def status():
    return {"status": "ok", "name": settings.app_name, "model": settings.ollama_model}


@app.post("/v1/chat")
def chat(request: ChatRequest):
    system_prompt = build_system_prompt()
    history = get_recent_messages()

    provider_type, reason = choose_provider(request.message)

    # qwen2.5-coder:3b no ejecuta tool-calling de forma confiable (confirmado en pruebas reales).
    # Mientras haya herramientas activas, forzamos siempre el modelo general.
    model_name = settings.ollama_model
    if provider_type == "code":
        reason = f"{reason} (forzado a modelo general: coder no soporta tool-calling confiable)"

    save_message("user", request.message)

    conversation = [{"role": "system", "content": system_prompt}]
    conversation.extend(history)
    conversation.append({"role": "user", "content": request.message})

    start = time.time()

    response = ollama.chat(model=model_name, messages=conversation, tools=TOOLS_SCHEMA)
    tool_calls = response["message"].get("tool_calls")

    if tool_calls:
        conversation.append(response["message"])
        pending_confirmations = []

        for call in tool_calls:
            tool_name = call["function"]["name"]
            tool_args = call["function"]["arguments"]

            risky, detail = is_risky(tool_name, tool_args)

            if risky:
                # No se ejecuta nada. Se guarda como pendiente y se corta el loop aquí.
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

        if pending_confirmations:
            duration = time.time() - start
            reply = (
                "Esta acción necesita tu confirmación antes de ejecutarse. "
                "Usa POST /v1/confirm-action/{id} con {\"approved\": true} para aprobarla, "
                "o {\"approved\": false} para rechazarla."
            )
            save_message("assistant", reply)
            log_router_decision(request.message, model_name, reason, duration)
            return {
                "reply": reply,
                "provider": model_name,
                "reason": reason,
                "used_tools": False,
                "pending_confirmations": pending_confirmations
            }

        response = ollama.chat(model=model_name, messages=conversation)

    duration = time.time() - start

    reply = response["message"]["content"]
    save_message("assistant", reply)
    log_router_decision(request.message, model_name, reason, duration)

    return {"reply": reply, "provider": model_name, "reason": reason, "used_tools": bool(tool_calls)}


@app.post("/v1/memory")
def add_memory(request: MemoryRequest):
    conn = get_connection()
    conn.execute("INSERT INTO facts (content) VALUES (?)", (request.content,))
    conn.commit()
    conn.close()
    return {"status": "saved", "content": request.content}


@app.get("/v1/memory")
def list_memory():
    conn = get_connection()
    facts = conn.execute("SELECT id, content, created_at FROM facts ORDER BY id DESC").fetchall()
    conn.close()
    return {"facts": [dict(f) for f in facts]}


@app.get("/v1/router-log")
def router_log():
    conn = get_connection()
    logs = conn.execute(
        "SELECT id, message, provider_chosen, reason, duration_seconds, created_at FROM router_log ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return {"logs": [dict(l) for l in logs]}


@app.get("/v1/pending-actions")
def pending_actions():
    """Lista todas las acciones que han pasado por el flujo de confirmación (pendientes, aprobadas o rechazadas)."""
    return {"actions": list_pending_actions()}


@app.post("/v1/confirm-action/{action_id}")
def confirm_action(action_id: str, request: ConfirmActionRequest):
    """
    Aprueba o rechaza una acción pendiente. Este endpoint es exclusivamente para uso humano
    (por Swagger UI, curl, o un futuro front con un botón) — el modelo no tiene ninguna forma
    de llamarlo, no está en TOOLS_SCHEMA.
    """
    return resolve_pending_action(action_id, request.approved)