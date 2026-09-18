"""
Capa de proveedores de modelos.

El Core (core.py) NO habla con Ollama, Claude ni GPT directamente: habla con
un `Provider`. Todo lo específico de un proveedor (cliente, timeouts, opciones
como num_ctx, bugs raros como el <think> de qwen3) vive AQUÍ y no se filtra
al resto del sistema. Así se cumple el principio #1 del plan: el Core nunca
depende de un proveedor específico.

Para agregar un proveedor nuevo (Claude, GPT...) basta con una clase con:
    name: str
    chat(messages, tools=None) -> ChatResult

Formato neutral de conversación: lista de dicts {"role", "content"} y, para
mensajes del asistente con herramientas, "tool_calls":
    [{"function": {"name": ..., "arguments": {...}}}]
Cada proveedor traduce de/hacia su API si su formato es distinto.
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from ollama import Client

from app.config import settings

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """
    Quita bloques <think>...</think> que qwen3 a veces genera aunque se le pida
    think=False (bug conocido de Ollama). Si el bloque quedó abierto (se cortó
    la generación a medio razonamiento), descarta desde ahí: no hay nada útil.
    """
    text = _THINK_BLOCK_RE.sub("", text or "")
    if "<think>" in text:
        text = text.split("<think>")[0]
    return text.strip()


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: Optional[int] = None  # tokens de entrada, si el proveedor lo reporta

    def as_message(self) -> dict:
        """El mensaje del asistente en formato de conversación, listo para re-inyectar."""
        message: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}}
                for c in self.tool_calls
            ]
        return message


class Provider(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None) -> ChatResult:
        ...


def _field(obj, key, default=None):
    """Lee obj[key] tanto de dicts como de los objetos de respuesta de ollama."""
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


class OllamaProvider:
    def __init__(
        self,
        model: Optional[str] = None,
        num_ctx: Optional[int] = None,
        timeout: Optional[int] = None,
    ):
        self.name = model or settings.ollama_model
        self._num_ctx = num_ctx or settings.ollama_num_ctx
        # Timeout explícito: si Ollama se cuelga, falla con una excepción clara
        # en vez de dejar la tarea para siempre en "en_progreso".
        self._client = Client(timeout=timeout or settings.ollama_timeout_seconds)

    def chat(self, messages: list[dict], tools: Optional[list[dict]] = None) -> ChatResult:
        kwargs = {}
        if tools:
            kwargs["tools"] = tools

        response = self._client.chat(
            model=self.name,
            messages=messages,
            think=False,
            options={"num_ctx": self._num_ctx},
            **kwargs,
        )

        message = response["message"]
        tool_calls = [
            ToolCall(
                name=call["function"]["name"],
                arguments=dict(call["function"]["arguments"] or {}),
            )
            for call in (_field(message, "tool_calls") or [])
        ]

        return ChatResult(
            content=strip_think_blocks(_field(message, "content", "")),
            tool_calls=tool_calls,
            prompt_tokens=_field(response, "prompt_eval_count"),
        )