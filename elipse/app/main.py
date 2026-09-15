import asyncio
import queue as queue_module
import re
import subprocess
import sys
import threading

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.config import settings
from app.core.db import get_connection, init_db
from app.core.seed_personality import seed
from app.core.safety import list_pending_actions, resolve_pending_action
from app.core.tools import TOOLS_SCHEMA, tools_schema_to_mcp
from app.core.tasks import create_task, get_task, run_agent_task

app = FastAPI(title=settings.app_name)

init_db()
seed()


class ChatRequest(BaseModel):
    message: str


class MemoryRequest(BaseModel):
    content: str


class ConfirmActionRequest(BaseModel):
    approved: bool


class InstallPackageRequest(BaseModel):
    package: str


@app.get("/v1/status")
def status():
    return {"status": "ok", "name": settings.app_name, "model": settings.ollama_model}


@app.post("/v1/chat")
def chat(request: ChatRequest):
    """
    Ya NO devuelve la respuesta directamente. Crea una tarea, la corre en un hilo
    aparte (para no bloquear el servidor), y devuelve de inmediato un task_id.
    Conéctate a ws_url para ver el progreso en tiempo real, o usa GET /v1/task/{id}
    si solo quieres el resultado final sin WebSocket.
    """
    task_id = create_task()
    threading.Thread(target=run_agent_task, args=(task_id, request.message), daemon=True).start()

    return {
        "task_id": task_id,
        "status": "en_progreso",
        "ws_url": f"/v1/ws/{task_id}",
        "poll_url": f"/v1/task/{task_id}",
    }


@app.get("/v1/task/{task_id}")
def task_status(task_id: str):
    task = get_task(task_id)
    if not task:
        return {"status": "error", "detail": "No existe una tarea con ese id."}
    return {"status": task["status"], "result": task["result"]}


@app.websocket("/v1/ws/{task_id}")
async def ws_task(websocket: WebSocket, task_id: str):
    await websocket.accept()

    task = get_task(task_id)
    if not task:
        await websocket.send_json({"type": "error", "mensaje": "No existe una tarea con ese id."})
        await websocket.close()
        return

    try:
        while True:
            try:
                # Se hace en un hilo aparte porque queue.Queue.get() es bloqueante
                # y no queremos congelar el event loop de FastAPI mientras esperamos.
                event = await asyncio.to_thread(task["queue"].get, True, 30)
            except queue_module.Empty:
                await websocket.send_json({"type": "ping"})
                continue

            await websocket.send_json(event)

            if event["type"] == "final":
                break
    except WebSocketDisconnect:
        pass

    await websocket.close()


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
    return {"actions": list_pending_actions()}


@app.post("/v1/confirm-action/{action_id}")
def confirm_action(action_id: str, request: ConfirmActionRequest):
    return resolve_pending_action(action_id, request.approved)


@app.get("/v1/tools")
def list_tools(format: str = "ollama"):
    """
    format=ollama (default): esquema nativo, el que usa el modelo local para tool-calling.
    format=mcp: mismo esquema traducido a MCP (inputSchema en vez de parameters), para que
    un cliente externo que hable MCP pueda ver qué ofrece ELIPSE sin acoplarse al formato interno.
    """
    if format == "mcp":
        return {"format": "mcp", "tools": tools_schema_to_mcp()}
    return {"format": "ollama", "tools": TOOLS_SCHEMA}


# Nombre de paquete válido: letras, números, guiones, guion bajo, punto.
PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+$")


@app.post("/v1/install-package")
def install_package(request: InstallPackageRequest):
    """
    Instala un paquete de pip. NO es una herramienta del modelo — no está en TOOLS_SCHEMA,
    así que el chat nunca puede llamarlo. Exclusivamente para uso humano directo.
    """
    package = request.package.strip()

    if not package or not PACKAGE_NAME_PATTERN.match(package):
        return {"status": "error", "detail": "Nombre de paquete inválido."}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": "La instalación tardó demasiado y se canceló."}

    output = (result.stdout or "") + (result.stderr or "")

    if result.returncode != 0:
        return {"status": "error", "package": package, "output": output[-3000:]}

    return {"status": "installed", "package": package, "output": output[-3000:]}