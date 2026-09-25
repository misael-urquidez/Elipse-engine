"""
Pruebas de la capa de proveedores — sin internet y sin API keys.

Los proveedores de nube se prueban con httpx.MockTransport: se captura la
petición HTTP EXACTA que se enviaría (URL, headers, cuerpo JSON) y se responde
con lo que devolvería la API real. Así se verifica la traducción de formatos.

Correr desde la raíz del proyecto:
    python tests/test_providers.py -v
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from support import CoreTestCase, ElipseCore  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import providers  # noqa: E402
from app.core.providers import (  # noqa: E402
    AnthropicProvider,
    ChatResult,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderConfigError,
    ProviderError,
    ToolCall,
    build_provider,
)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Calcula una expresión.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
}]

# Conversación en formato NEUTRAL, con todos los casos incómodos a la vez:
# dos system, un mensaje huérfano del asistente al inicio (el historial recortado
# puede empezar así), asistente con dos llamadas y texto vacío, dos resultados
# seguidos y un mensaje de reintento del Core al final.
CONVERSATION = [
    {"role": "system", "content": "Eres ELIPSE."},
    {"role": "system", "content": "Contexto de memoria."},
    {"role": "assistant", "content": "mensaje huérfano al inicio del historial"},
    {"role": "user", "content": "hola"},
    {"role": "assistant", "content": "¡Hola!"},
    {"role": "user", "content": "suma 2+2 y dime la hora"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_a", "function": {"name": "calculate", "arguments": {"expression": "2+2"}}},
        {"id": "call_b", "function": {"name": "get_current_datetime", "arguments": {}}},
    ]},
    {"role": "tool", "tool_call_id": "call_a", "name": "calculate", "content": "4"},
    {"role": "tool", "tool_call_id": "call_b", "name": "get_current_datetime", "content": "2026-09-21"},
    {"role": "user", "content": "Corrige el plan y reintenta."},
]


class Recorder:
    """Handler para MockTransport: guarda cada petición y responde con lo programado."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item if isinstance(item, tuple) else (200, item)
        return httpx.Response(status, json=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def body(self, index=-1) -> dict:
        return json.loads(self.requests[index].content)


# =============================================================================
# Formato neutral
# =============================================================================

class TestNeutralFormat(unittest.TestCase):
    def test_as_message_incluye_ids_de_las_llamadas(self):
        result = ChatResult(content="", tool_calls=[ToolCall("calculate", {"expression": "1+1"}, id="x1")])
        self.assertEqual(
            result.as_message(),
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "x1", "function": {"name": "calculate", "arguments": {"expression": "1+1"}}}]},
        )

    def test_toolcall_genera_id_unico_si_el_proveedor_no_da_uno(self):
        a, b = ToolCall("t", {}), ToolCall("t", {})
        self.assertTrue(a.id.startswith("call_"))
        self.assertNotEqual(a.id, b.id)


# =============================================================================
# Ollama
# =============================================================================

class TestOllama(unittest.TestCase):
    def test_traduccion_quita_ids_y_deja_el_formato_que_ya_funcionaba(self):
        out = providers._to_ollama_messages(CONVERSATION)
        tool_msgs = [m for m in out if m["role"] == "tool"]
        self.assertEqual(tool_msgs[0], {"role": "tool", "content": "4"})
        assistant = next(m for m in out if m.get("tool_calls"))
        self.assertEqual(assistant["tool_calls"][0], {"function": {"name": "calculate", "arguments": {"expression": "2+2"}}})
        # no muta la conversación original del Core
        self.assertIn("tool_call_id", CONVERSATION[7])

    def _provider_with(self, response, seen):
        provider = OllamaProvider(model="qwen3:4b")

        def fake_chat(**kwargs):
            seen.update(kwargs)
            return response

        provider._client.chat = fake_chat
        return provider

    def test_chat_parsea_respuesta_y_limpia_think(self):
        seen = {}
        provider = self._provider_with({
            "message": {
                "content": "<think>razonando</think>Hola",
                "tool_calls": [{"function": {"name": "calculate", "arguments": {"expression": "1+1"}}}],
            },
            "prompt_eval_count": 321,
        }, seen)

        result = provider.chat([{"role": "user", "content": "x"}], tools=TOOLS)

        self.assertEqual(provider.name, "ollama:qwen3:4b")
        self.assertEqual(result.content, "Hola")
        self.assertEqual(result.tool_calls[0].name, "calculate")
        self.assertEqual(result.tool_calls[0].arguments, {"expression": "1+1"})
        self.assertEqual(result.prompt_tokens, 321)
        self.assertIs(seen["think"], False)
        self.assertEqual(seen["tools"], TOOLS)

    def test_allow_tools_false_no_manda_herramientas(self):
        seen = {}
        provider = self._provider_with({"message": {"content": "ok"}}, seen)
        provider.chat([{"role": "user", "content": "x"}], tools=TOOLS, allow_tools=False)
        self.assertNotIn("tools", seen)


# =============================================================================
# Anthropic (Claude)
# =============================================================================

class TestAnthropicConversion(unittest.TestCase):
    def test_conversion_completa(self):
        system, messages = providers._to_anthropic_messages(CONVERSATION)

        self.assertEqual(system, "Eres ELIPSE.\n\nContexto de memoria.")
        # El mensaje huérfano del asistente del inicio se descartó: empieza en 'user'.
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(messages[0]["content"], [{"type": "text", "text": "hola"}])

        # Asistente con dos llamadas: el texto vacío no genera bloque de texto.
        assistant = messages[3]
        self.assertEqual([b["type"] for b in assistant["content"]], ["tool_use", "tool_use"])
        self.assertEqual(assistant["content"][0], {
            "type": "tool_use", "id": "call_a", "name": "calculate", "input": {"expression": "2+2"},
        })

        # Los dos resultados van juntos en UN mensaje de usuario, con su id, y el
        # texto del reintento va DESPUÉS de los tool_result (requisito de la API).
        last = messages[4]
        self.assertEqual([b["type"] for b in last["content"]], ["tool_result", "tool_result", "text"])
        self.assertEqual(last["content"][0]["tool_use_id"], "call_a")
        self.assertEqual(last["content"][1]["tool_use_id"], "call_b")
        self.assertEqual(last["content"][2]["text"], "Corrige el plan y reintenta.")

    def test_usuarios_consecutivos_se_fusionan(self):
        _, messages = providers._to_anthropic_messages([
            {"role": "user", "content": "uno"},
            {"role": "user", "content": "dos"},
        ])
        self.assertEqual(len(messages), 1)
        self.assertEqual(len(messages[0]["content"]), 2)

    def test_contenido_vacio_se_descarta(self):
        _, messages = providers._to_anthropic_messages([
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "   "},
            {"role": "user", "content": "¿sigues ahí?"},
        ])
        self.assertEqual([m["role"] for m in messages], ["user"])

    def test_resultado_sin_tool_call_id_falla_claro(self):
        with self.assertRaises(ProviderError):
            providers._to_anthropic_messages([{"role": "tool", "content": "x"}])

    def test_herramientas_al_formato_de_anthropic(self):
        self.assertEqual(providers._to_anthropic_tools(TOOLS), [{
            "name": "calculate",
            "description": "Calcula una expresión.",
            "input_schema": TOOLS[0]["function"]["parameters"],
        }])


class TestAnthropicProvider(unittest.TestCase):
    RESPONSE = {
        "id": "msg_1",
        "content": [
            {"type": "text", "text": "Voy a calcular. "},
            {"type": "tool_use", "id": "toolu_1", "name": "calculate", "input": {"expression": "2+2"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 5},
    }

    def make(self, recorder, **kwargs):
        return AnthropicProvider(api_key="sk-ant-secreta", model="claude-sonnet-5",
                                 http_client=recorder.client(), **kwargs)

    def test_peticion_bien_formada(self):
        rec = Recorder(self.RESPONSE)
        self.make(rec).chat(CONVERSATION, tools=TOOLS)

        request = rec.requests[0]
        self.assertEqual(str(request.url), "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.headers["x-api-key"], "sk-ant-secreta")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")

        body = rec.body()
        self.assertEqual(body["model"], "claude-sonnet-5")
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(body["system"], "Eres ELIPSE.\n\nContexto de memoria.")
        self.assertEqual(body["tools"][0]["name"], "calculate")
        self.assertNotIn("tool_choice", body)  # permitido pedir herramientas

    def test_allow_tools_false_declara_herramientas_con_tool_choice_none(self):
        rec = Recorder({"content": [{"type": "text", "text": "listo"}]})
        self.make(rec).chat(CONVERSATION, tools=TOOLS, allow_tools=False)

        body = rec.body()
        # El historial trae tool_use/tool_result, así que la API exige seguir
        # declarando las herramientas; solo se les prohíbe usarlas.
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tool_choice"], {"type": "none"})

    def test_parseo_de_respuesta_con_herramienta(self):
        rec = Recorder(self.RESPONSE)
        result = self.make(rec).chat(CONVERSATION, tools=TOOLS)

        self.assertEqual(result.content, "Voy a calcular.")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "calculate")
        self.assertEqual(result.tool_calls[0].arguments, {"expression": "2+2"})
        self.assertEqual(result.tool_calls[0].id, "toolu_1")  # conserva el id de Claude
        self.assertEqual(result.prompt_tokens, 120)           # input + cache

    def test_nombre_y_url_personalizados(self):
        rec = Recorder({"content": [{"type": "text", "text": "ok"}]})
        provider = AnthropicProvider(api_key="k", model="claude-haiku-4-5-20251001",
                                     base_url="https://proxy.local/", http_client=rec.client())
        provider.chat([{"role": "user", "content": "hola"}])
        self.assertEqual(provider.name, "anthropic:claude-haiku-4-5-20251001")
        self.assertEqual(str(rec.requests[0].url), "https://proxy.local/v1/messages")

    def test_error_de_la_api_es_claro_y_no_filtra_la_key(self):
        rec = Recorder((401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}))
        with self.assertRaises(ProviderError) as ctx:
            self.make(rec).chat([{"role": "user", "content": "hola"}])
        self.assertIn("401", str(ctx.exception))
        self.assertIn("invalid x-api-key", str(ctx.exception))
        self.assertNotIn("sk-ant-secreta", str(ctx.exception))

    def test_error_de_red_se_convierte_en_provider_error(self):
        rec = Recorder(httpx.ConnectError("sin internet"))
        with self.assertRaises(ProviderError) as ctx:
            self.make(rec).chat([{"role": "user", "content": "hola"}])
        self.assertIn("No se pudo contactar", str(ctx.exception))

    def test_sin_api_key_falla_al_construir(self):
        with self.assertRaises(ProviderConfigError) as ctx:
            AnthropicProvider(api_key="", model="claude-sonnet-5")
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))


# =============================================================================
# OpenAI y compatibles
# =============================================================================

class TestOpenAIConversion(unittest.TestCase):
    def test_conversion_completa(self):
        out = providers._to_openai_messages(CONVERSATION)

        assistant = next(m for m in out if m.get("tool_calls"))
        self.assertIsNone(assistant["content"])  # texto vacío -> null, como espera OpenAI
        call = assistant["tool_calls"][0]
        self.assertEqual(call["id"], "call_a")
        self.assertEqual(call["type"], "function")
        # Los argumentos viajan como STRING JSON, no como objeto.
        self.assertEqual(call["function"]["arguments"], '{"expression": "2+2"}')

        tools = [m for m in out if m["role"] == "tool"]
        self.assertEqual(tools[0], {"role": "tool", "tool_call_id": "call_a", "content": "4"})
        self.assertEqual(tools[1]["tool_call_id"], "call_b")

    def test_argumentos_con_acentos_no_se_escapan(self):
        out = providers._to_openai_messages([{
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c", "function": {"name": "t", "arguments": {"texto": "canción"}}}],
        }])
        self.assertIn("canción", out[0]["tool_calls"][0]["function"]["arguments"])


class TestOpenAIProvider(unittest.TestCase):
    RESPONSE = {
        "choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "call_zzz", "type": "function",
                            "function": {"name": "calculate", "arguments": '{"expression": "2+2"}'}}],
        }}],
        "usage": {"prompt_tokens": 210, "completion_tokens": 9},
    }

    def make(self, recorder, **kwargs):
        defaults = dict(api_key="sk-secreta", model="modelo-x", http_client=recorder.client())
        defaults.update(kwargs)
        return OpenAICompatibleProvider(**defaults)

    def test_peticion_bien_formada(self):
        rec = Recorder(self.RESPONSE)
        self.make(rec).chat(CONVERSATION, tools=TOOLS)

        request = rec.requests[0]
        self.assertEqual(str(request.url), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer sk-secreta")
        body = rec.body()
        self.assertEqual(body["model"], "modelo-x")
        self.assertEqual(body["tools"], TOOLS)  # TOOLS_SCHEMA ya está en formato OpenAI
        self.assertNotIn("tool_choice", body)

    def test_allow_tools_false_usa_tool_choice_none(self):
        rec = Recorder({"choices": [{"message": {"content": "listo"}}]})
        self.make(rec).chat(CONVERSATION, tools=TOOLS, allow_tools=False)
        self.assertEqual(rec.body()["tool_choice"], "none")

    def test_parseo_de_respuesta_con_herramienta(self):
        rec = Recorder(self.RESPONSE)
        result = self.make(rec).chat(CONVERSATION, tools=TOOLS)

        self.assertEqual(result.content, "")
        self.assertEqual(result.tool_calls[0].arguments, {"expression": "2+2"})  # string JSON -> dict
        self.assertEqual(result.tool_calls[0].id, "call_zzz")
        self.assertEqual(result.prompt_tokens, 210)

    def test_argumentos_rotos_se_marcan_en_vez_de_reventar(self):
        rec = Recorder({"choices": [{"message": {"tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "calculate", "arguments": '{"expression": '}}
        ]}}]})
        result = self.make(rec).chat(CONVERSATION, tools=TOOLS)
        self.assertIn("_argumentos_invalidos", result.tool_calls[0].arguments)

    def test_limpia_think_de_modelos_locales_compatibles(self):
        rec = Recorder({"choices": [{"message": {"content": "<think>hmm</think>Respuesta"}}]})
        self.assertEqual(self.make(rec).chat([{"role": "user", "content": "x"}]).content, "Respuesta")

    def test_servidor_local_compatible_no_necesita_api_key(self):
        rec = Recorder({"choices": [{"message": {"content": "ok"}}]})
        provider = self.make(rec, api_key="", base_url="http://localhost:1234/v1")
        provider.chat([{"role": "user", "content": "x"}])
        self.assertEqual(str(rec.requests[0].url), "http://localhost:1234/v1/chat/completions")
        self.assertNotIn("authorization", rec.requests[0].headers)

    def test_configuracion_invalida(self):
        with self.assertRaises(ProviderConfigError) as ctx:
            OpenAICompatibleProvider(api_key="k", model="")
        self.assertIn("OPENAI_MODEL", str(ctx.exception))
        with self.assertRaises(ProviderConfigError) as ctx:
            OpenAICompatibleProvider(api_key="", model="m")  # api.openai.com sin key
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_respuesta_sin_choices(self):
        rec = Recorder({"choices": []})
        with self.assertRaises(ProviderError):
            self.make(rec).chat([{"role": "user", "content": "x"}])

    def test_error_http_incluye_el_mensaje_de_la_api(self):
        rec = Recorder((429, {"error": {"message": "Rate limit reached"}}))
        with self.assertRaises(ProviderError) as ctx:
            self.make(rec).chat([{"role": "user", "content": "x"}])
        self.assertIn("429", str(ctx.exception))
        self.assertIn("Rate limit reached", str(ctx.exception))


# =============================================================================
# Fábrica
# =============================================================================

class TestBuildProvider(unittest.TestCase):
    def test_ollama_ignora_mayusculas_y_espacios(self):
        self.assertIsInstance(build_provider("  Ollama "), OllamaProvider)

    def test_nombre_desconocido(self):
        with self.assertRaises(ProviderConfigError) as ctx:
            build_provider("gemini")
        self.assertIn("ollama, anthropic, openai", str(ctx.exception))

    def test_anthropic_sin_key_falla_claro(self):
        with patch.object(settings, "anthropic_api_key", ""):
            with self.assertRaises(ProviderConfigError):
                build_provider("anthropic")

    def test_anthropic_y_openai_con_configuracion_valida(self):
        with patch.object(settings, "anthropic_api_key", "k"), \
             patch.object(settings, "openai_api_key", "k"), \
             patch.object(settings, "openai_model", "modelo-x"):
            self.assertEqual(build_provider("anthropic").name, f"anthropic:{settings.anthropic_model}")
            self.assertEqual(build_provider("openai").name, "openai:modelo-x")


# =============================================================================
# Extremo a extremo: Core + proveedor de nube con un loop de herramientas real
# =============================================================================

class TestCoreConProveedoresDeNube(CoreTestCase):
    def test_loop_completo_con_claude(self):
        rec = Recorder(
            {"content": [{"type": "tool_use", "id": "toolu_HORA", "name": "get_current_datetime", "input": {}}],
             "usage": {"input_tokens": 500}},
            {"content": [{"type": "text", "text": "Son las tantas."}], "usage": {"input_tokens": 620}},
        )
        provider = AnthropicProvider(api_key="k", model="claude-sonnet-5", http_client=rec.client())

        status, final = ElipseCore(provider).run("¿qué hora es?")

        self.assertEqual(status, "completado", final)
        self.assertEqual(final["reply"], "Son las tantas.")
        self.assertEqual(final["provider"], "anthropic:claude-sonnet-5")
        self.assertEqual(final["prompt_tokens"], 620)
        self.assertEqual(len(rec.requests), 2)  # exactamente 2 llamadas

        first, second = rec.body(0), rec.body(1)
        self.assertNotIn("tool_choice", first)
        self.assertEqual(second["tool_choice"], {"type": "none"})

        # La segunda petición devuelve el resultado emparejado con el id que dio Claude.
        assistant = next(m for m in second["messages"] if m["role"] == "assistant")
        self.assertEqual(assistant["content"][0]["id"], "toolu_HORA")
        result_msg = second["messages"][-1]
        self.assertEqual(result_msg["role"], "user")
        self.assertEqual(result_msg["content"][0]["type"], "tool_result")
        self.assertEqual(result_msg["content"][0]["tool_use_id"], "toolu_HORA")

        # El nombre del proveedor quedó en el log del router.
        log = self.query("SELECT provider_chosen FROM router_log")
        self.assertEqual(log[0]["provider_chosen"], "anthropic:claude-sonnet-5")

    def test_loop_completo_con_openai(self):
        rec = Recorder(
            {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "call_HORA", "type": "function",
                 "function": {"name": "get_current_datetime", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"content": "Son las tantas."}}]},
        )
        provider = OpenAICompatibleProvider(api_key="k", model="modelo-x", http_client=rec.client())

        status, final = ElipseCore(provider).run("¿qué hora es?")

        self.assertEqual(status, "completado", final)
        self.assertEqual(final["reply"], "Son las tantas.")
        self.assertEqual(len(rec.requests), 2)

        second = rec.body(1)
        self.assertEqual(second["tool_choice"], "none")
        assistant = next(m for m in second["messages"] if m.get("tool_calls"))
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_HORA")
        tool_msg = second["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(tool_msg["tool_call_id"], "call_HORA")

    def test_error_de_api_llega_al_usuario_como_error_no_como_excepcion(self):
        rec = Recorder((401, {"error": {"message": "invalid x-api-key"}}))
        provider = AnthropicProvider(api_key="k", model="claude-sonnet-5", http_client=rec.client())

        status, final = ElipseCore(provider).run("hola")

        self.assertEqual(status, "error")
        self.assertIn("invalid x-api-key", final["reply"])


if __name__ == "__main__":
    unittest.main()