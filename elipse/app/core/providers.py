"""
Capa de proveedores de modelos.

El Core (core.py) NO habla con Ollama, Claude ni GPT directamente: habla con
un `Provider`. Todo lo específico de cada uno (cliente, formato de mensajes,
formato de herramientas, bugs raros) vive AQUÍ y no se filtra al resto.
Así se cumple el principio #1 del plan: el Core nunca depende de un proveedor.

Proveedores incluidos:
  - OllamaProvider            -> modelos locales (privado, offline, gratis)
  - AnthropicProvider         -> Claude vía API de Anthropic
  - OpenAICompatibleProvider  -> OpenAI y todo lo que hable su mismo protocolo
                                 (OpenRouter, Mistral, Groq, LM Studio, vLLM...)
                                 cambiando solo base_url / api_key / model.

Para agregar otro: una clase con `name: str` y
    chat(messages, tools=None, allow_tools=True) -> ChatResult
y registrarla en `build_provider`.

FORMATO NEUTRAL de conversación (el que usa el Core):
    {"role": "system" | "user", "content": str}
    {"role": "assistant", "content": str,
        "tool_calls": [{"id": str, "function": {"name": str, "arguments": dict}}]}   # tool_calls opcional
    {"role": "tool", "content": str, "tool_call_id": str, "name": str}
Cada proveedor traduce de/hacia su API. `tools` llega en formato JSON-Schema
estilo function-calling (el de TOOLS_SCHEMA).

`allow_tools=False` significa "responde con texto, sin pedir más herramientas":
lo usa el Core en la pasada final, cuando la conversación ya contiene
resultados de herramientas.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Protocol

import httpx
from ollama import Client

from app.config import settings

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Reintentos ante límites de velocidad (429) o saturación temporal del proveedor.
_RETRY_STATUS = {429, 502, 503, 529}
_MAX_RETRIES = 4              # reintentos extra tras el primer intento
_BASE_BACKOFF_SECONDS = 2.0   # 2s, 4s, 8s, 16s (si el proveedor no manda Retry-After)
_MAX_WAIT_SECONDS = 20.0      # tope de espera por reintento


class ProviderError(Exception):
    """Fallo al hablar con un proveedor (red, API key inválida, límite, etc.)."""


class ProviderConfigError(ProviderError):
    """El proveedor está mal configurado (falta API key, modelo, nombre desconocido...)."""


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


def _new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


@dataclass
class ToolCall:
    name: str
    arguments: dict
    # Los proveedores que dan id propio (Claude, OpenAI) lo sobreescriben; con los
    # que no (Ollama) se genera uno, porque el formato neutral siempre lo lleva.
    id: str = field(default_factory=_new_call_id)


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: Optional[int] = None  # tokens de entrada, si el proveedor lo reporta

    def as_message(self) -> dict:
        """El mensaje del asistente en formato neutral, listo para re-inyectar."""
        message: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": c.id, "function": {"name": c.name, "arguments": c.arguments}}
                for c in self.tool_calls
            ]
        return message


class Provider(Protocol):
    name: str

    def chat(
        self, messages: list[dict], tools: Optional[list[dict]] = None, allow_tools: bool = True
    ) -> ChatResult:
        ...


def _field(obj, key, default=None):
    """Lee obj[key] tanto de dicts como de los objetos de respuesta de ollama."""
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


# =============================================================================
# Ollama (local)
# =============================================================================

def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    """
    Ollama no usa ids de llamada: los quita para mandar exactamente el mismo
    formato que ya funcionaba antes de existir los ids en el formato neutral.
    """
    out = []
    for message in messages:
        message = dict(message)
        if message.get("role") == "tool":
            message.pop("tool_call_id", None)
            message.pop("name", None)
        elif message.get("tool_calls"):
            message["tool_calls"] = [{"function": c["function"]} for c in message["tool_calls"]]
        out.append(message)
    return out


class OllamaProvider:
    def __init__(
        self,
        model: Optional[str] = None,
        num_ctx: Optional[int] = None,
        timeout: Optional[int] = None,
    ):
        model = model or settings.ollama_model
        self.name = f"ollama:{model}"
        self._model = model
        self._num_ctx = num_ctx or settings.ollama_num_ctx
        # Timeout explícito: si Ollama se cuelga, falla con una excepción clara
        # en vez de dejar la tarea para siempre en "en_progreso".
        self._client = Client(timeout=timeout or settings.ollama_timeout_seconds)

    def chat(self, messages, tools=None, allow_tools=True) -> ChatResult:
        kwargs = {}
        if tools and allow_tools:
            kwargs["tools"] = tools

        response = self._client.chat(
            model=self._model,
            messages=_to_ollama_messages(messages),
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


# =============================================================================
# Base para proveedores HTTP (nube)
# =============================================================================

def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(error, dict):
            return str(error.get("message") or error)[:500]
        return str(error)[:500]
    except ValueError:
        return response.text[:500]


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    """Segundos a esperar antes de reintentar: respeta Retry-After si viene, si no, backoff exponencial."""
    raw = response.headers.get("retry-after")
    try:
        wait = float(raw) if raw else None
    except ValueError:
        wait = None
    if wait is None:
        wait = _BASE_BACKOFF_SECONDS * (2 ** attempt)
    return min(max(wait, 1.0), _MAX_WAIT_SECONDS)


class _HTTPProvider:
    label = "proveedor"

    def __init__(self, timeout: Optional[int] = None, http_client: Optional[httpx.Client] = None):
        self._http = http_client or httpx.Client(timeout=timeout or settings.cloud_timeout_seconds)

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        response = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._http.post(url, headers=headers, json=body)
            except httpx.HTTPError as e:
                raise ProviderError(f"No se pudo contactar a {self.label}: {e}") from e

            if response.status_code not in _RETRY_STATUS or attempt == _MAX_RETRIES:
                break
            time.sleep(_retry_wait(response, attempt))

        if response.status_code >= 400:
            hint = ""
            if response.status_code == 429:
                hint = (
                    f" (límite de velocidad; se reintentó {_MAX_RETRIES} veces sin éxito. "
                    "Revisa la sección Limits de tu cuenta: puede ser el tope mensual de tokens.)"
                )
            raise ProviderError(
                f"{self.label} respondió {response.status_code}: {_error_detail(response)}{hint}"
            )
        try:
            return response.json()
        except ValueError as e:
            raise ProviderError(f"{self.label} devolvió una respuesta que no es JSON válido.") from e


# =============================================================================
# Anthropic (Claude)
# =============================================================================

def _to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """
    Formato neutral -> Messages API de Anthropic. Devuelve (system, messages).

    Diferencias que hay que resolver:
      - 'system' es un parámetro aparte, no un mensaje.
      - Las llamadas a herramientas son bloques 'tool_use' dentro del mensaje del
        asistente, y los resultados son bloques 'tool_result' dentro de un mensaje
        de USUARIO que referencia el id. Varios resultados seguidos van juntos.
      - No se aceptan bloques de texto vacíos.
      - Mensajes consecutivos del mismo rol se fusionan (ej. resultados de
        herramientas + el mensaje de reintento del Core).
      - La conversación debe empezar con un mensaje de usuario (el historial
        recortado a N mensajes puede empezar con uno del asistente: se descarta).
    """
    system_parts: list[str] = []
    out: list[dict] = []

    def push(role: str, blocks: list[dict]):
        blocks = [b for b in blocks if not (b["type"] == "text" and not b["text"].strip())]
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for message in messages:
        role = message["role"]
        content = message.get("content") or ""

        if role == "system":
            if content.strip():
                system_parts.append(content)

        elif role == "user":
            push("user", [{"type": "text", "text": content}])

        elif role == "assistant":
            blocks = [{"type": "text", "text": content}]
            for call in message.get("tool_calls") or []:
                blocks.append({
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "input": call["function"]["arguments"],
                })
            push("assistant", blocks)

        elif role == "tool":
            if not message.get("tool_call_id"):
                raise ProviderError("Mensaje de herramienta sin 'tool_call_id': no se puede enviar a Claude.")
            push("user", [{
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": content,
            }])

    while out and out[0]["role"] == "assistant":
        out.pop(0)

    return "\n\n".join(system_parts), out


def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


class AnthropicProvider(_HTTPProvider):
    label = "Anthropic"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        base_url: str = "https://api.anthropic.com",
        timeout: Optional[int] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        if not api_key:
            raise ProviderConfigError("Falta ANTHROPIC_API_KEY en el .env para usar el proveedor 'anthropic'.")
        super().__init__(timeout, http_client)
        self.name = f"anthropic:{model}"
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._url = base_url.rstrip("/") + "/v1/messages"

    def chat(self, messages, tools=None, allow_tools=True) -> ChatResult:
        system, converted = _to_anthropic_messages(messages)
        if not converted:
            raise ProviderError("No hay ningún mensaje de usuario que enviar a Claude.")

        body: dict = {"model": self._model, "max_tokens": self._max_tokens, "messages": converted}
        if system:
            body["system"] = system
        if tools:
            # Con tool_use/tool_result en el historial hay que seguir declarando las
            # herramientas; para pedir solo texto se usa tool_choice "none".
            body["tools"] = _to_anthropic_tools(tools)
            if not allow_tools:
                body["tool_choice"] = {"type": "none"}

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        data = self._post(self._url, headers, body)

        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            ToolCall(name=b["name"], arguments=b.get("input") or {}, id=b["id"])
            for b in blocks
            if b.get("type") == "tool_use"
        ]

        usage = data.get("usage") or {}
        prompt_tokens = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        ) or None

        return ChatResult(content=text.strip(), tool_calls=calls, prompt_tokens=prompt_tokens)


# =============================================================================
# OpenAI y compatibles (OpenRouter, Mistral, Groq, LM Studio, vLLM, ...)
# =============================================================================

def _to_openai_messages(messages: list[dict]) -> list[dict]:
    out = []
    for message in messages:
        role = message["role"]
        content = message.get("content") or ""

        if role == "assistant" and message.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["function"]["name"],
                            "arguments": json.dumps(c["function"]["arguments"], ensure_ascii=False),
                        },
                    }
                    for c in message["tool_calls"]
                ],
            })
        elif role == "tool":
            if not message.get("tool_call_id"):
                raise ProviderError("Mensaje de herramienta sin 'tool_call_id': no se puede enviar.")
            out.append({"role": "tool", "tool_call_id": message["tool_call_id"], "content": content})
        else:
            out.append({"role": role, "content": content})
    return out


def _parse_openai_arguments(raw) -> dict:
    """Los argumentos llegan como string JSON; si vienen rotos se marcan para que la
    verificación del Core lo detecte y el modelo pueda corregirlo en el reintento."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_argumentos_invalidos": raw}
    except ValueError:
        return {"_argumentos_invalidos": raw}


class OpenAICompatibleProvider(_HTTPProvider):
    label = "OpenAI"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: Optional[int] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        if not model:
            raise ProviderConfigError("Falta OPENAI_MODEL en el .env para usar el proveedor 'openai'.")
        if not api_key and "api.openai.com" in base_url:
            raise ProviderConfigError("Falta OPENAI_API_KEY en el .env para usar el proveedor 'openai'.")
        super().__init__(timeout, http_client)
        self.name = f"openai:{model}"
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"

    def chat(self, messages, tools=None, allow_tools=True) -> ChatResult:
        body: dict = {"model": self._model, "messages": _to_openai_messages(messages)}
        if tools:
            body["tools"] = tools  # TOOLS_SCHEMA ya está en el formato de OpenAI
            if not allow_tools:
                body["tool_choice"] = "none"

        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        data = self._post(self._url, headers, body)

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.label} devolvió una respuesta sin 'choices'.")
        message = choices[0].get("message") or {}

        calls = [
            ToolCall(
                name=c["function"]["name"],
                arguments=_parse_openai_arguments(c["function"].get("arguments")),
                id=c.get("id") or _new_call_id(),
            )
            for c in (message.get("tool_calls") or [])
        ]

        return ChatResult(
            content=strip_think_blocks(message.get("content") or ""),
            tool_calls=calls,
            prompt_tokens=(data.get("usage") or {}).get("prompt_tokens"),
        )


# =============================================================================
# Fábrica: nombre (de la configuración) -> proveedor
# =============================================================================

def build_provider(name: str) -> Provider:
    key = (name or "").strip().lower()

    if key == "ollama":
        return OllamaProvider()

    if key == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            base_url=settings.anthropic_base_url,
        )

    if key == "openai":
        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
        )

    raise ProviderConfigError(
        f"Proveedor desconocido: '{name}'. Opciones válidas: ollama, anthropic, openai."
    )