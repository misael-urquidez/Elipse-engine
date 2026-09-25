"""
Pruebas del cliente MCP — contra servidores MCP REALES de juguete (procesos
verdaderos hablando el protocolo, no simulaciones), por stdio y por HTTP.

Requieren el paquete `mcp>=2`; si no está instalado, se saltan.

Correr desde la raíz del proyecto:
    python tests/test_mcp.py -v
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import CoreTestCase, ElipseCore, call, say  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import mcp_client, safety, tools  # noqa: E402
from app.core.mcp_client import MCPManager, load_config  # noqa: E402

try:
    from mcp.server.mcpserver import MCPServer  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

requires_mcp = unittest.skipUnless(HAVE_MCP, "requiere el paquete mcp>=2")

TOY_SERVER = textwrap.dedent('''
    import asyncio, pathlib
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("toy")

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Suma dos enteros."""
        return a + b

    @mcp.tool()
    def write_marker(path: str) -> str:
        """Crea un archivo marcador (efecto secundario observable desde afuera)."""
        pathlib.Path(path).write_text("ejecutado", encoding="utf-8")
        return "marcador creado"

    @mcp.tool()
    def fail() -> str:
        """Siempre falla."""
        raise ValueError("kaboom")

    @mcp.tool()
    async def slow(seconds: float) -> str:
        """Espera un rato."""
        await asyncio.sleep(seconds)
        return "terminó"

    @mcp.tool()
    def big() -> str:
        """Devuelve un texto muy largo."""
        return "x" * 5000

    @mcp.tool(name="weird.name")
    def weird() -> str:
        """Tiene un punto en el nombre, que Claude y OpenAI no aceptan."""
        return "ok"

    @mcp.tool(description="descripcion muy larga " * 60)
    def long_desc() -> str:
        return "ok"

    if __name__ == "__main__":
        mcp.run("stdio")
''')

HTTP_SERVER = textwrap.dedent('''
    import sys
    import uvicorn
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("toyhttp")

    @mcp.tool()
    def whoami() -> str:
        """Saluda."""
        return "hola desde http"

    class Guard:
        """Rechaza con 401 toda petición sin el header de autorización correcto."""
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers = dict(scope["headers"])
                if headers.get(b"authorization") != b"Bearer secreto":
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body", "body": b"no autorizado"})
                    return
            await self.app(scope, receive, send)

    uvicorn.run(Guard(mcp.streamable_http_app()), host="127.0.0.1",
                port=int(sys.argv[1]), log_level="warning")
''')

_tmp = tempfile.TemporaryDirectory()
TOY_PATH = Path(_tmp.name) / "toy_server.py"
HTTP_PATH = Path(_tmp.name) / "http_server.py"
TOY_PATH.write_text(TOY_SERVER, encoding="utf-8")
HTTP_PATH.write_text(HTTP_SERVER, encoding="utf-8")


def toy(**extra) -> dict:
    return {"command": sys.executable, "args": [str(TOY_PATH)], **extra}


# =============================================================================
# Configuración (no necesita el SDK)
# =============================================================================

class TestLoadConfig(unittest.TestCase):
    def write(self, content) -> Path:
        path = Path(_tmp.name) / "cfg.json"
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def test_archivo_inexistente_es_mcp_apagado(self):
        self.assertEqual(load_config(Path(_tmp.name) / "no-existe.json"), {})

    def test_formato_de_claude_desktop(self):
        cfg = load_config(self.write({"mcpServers": {"fs": {"command": "npx", "args": ["-y", "x"]}}}))
        self.assertEqual(cfg["fs"]["command"], "npx")

    def test_servidores_deshabilitados_o_incompletos_se_ignoran(self):
        cfg = load_config(self.write({"mcpServers": {
            "apagado": {"command": "x", "enabled": False},
            "sin_transporte": {"args": ["nada"]},
            "bien": {"url": "http://localhost:9/mcp"},
        }}))
        self.assertEqual(list(cfg), ["bien"])

    def test_expande_variables_de_entorno(self):
        with patch.dict(os.environ, {"MI_TOKEN_TEST": "abc123"}):
            cfg = load_config(self.write({"mcpServers": {"r": {
                "url": "http://x/mcp", "headers": {"Authorization": "Bearer ${MI_TOKEN_TEST}"},
            }}}))
        self.assertEqual(cfg["r"]["headers"]["Authorization"], "Bearer abc123")

    def test_json_invalido_da_error_claro(self):
        with self.assertRaises(ValueError) as ctx:
            load_config(self.write("{ esto no es json"))
        self.assertIn("no es JSON válido", str(ctx.exception))


# =============================================================================
# Gestor por stdio
# =============================================================================

@requires_mcp
class TestManagerStdio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = MCPManager()
        cls.manager.start({
            "toy": toy(),
            "roto": {"command": "este-comando-no-existe-elipse"},
        }, connect_timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.manager.stop()

    def status_of(self, name):
        return next(s for s in self.manager.status()["servers"] if s["server"] == name)

    def test_un_servidor_roto_no_impide_que_el_otro_funcione(self):
        self.assertTrue(self.status_of("toy")["connected"])
        broken = self.status_of("roto")
        self.assertFalse(broken["connected"])
        self.assertTrue(broken["error"])

    def test_nombres_validos_para_claude_y_openai(self):
        import re
        names = [t["function"]["name"] for t in self.manager.tool_schemas()]
        self.assertIn("toy__add", names)
        for name in names:
            self.assertRegex(name, r"^[a-zA-Z0-9_-]{1,64}$")
        self.assertIn("toy__weird_name", names)  # 'weird.name' se sanea

    def test_esquema_en_formato_function_calling(self):
        schema = next(t for t in self.manager.tool_schemas() if t["function"]["name"] == "toy__add")
        self.assertEqual(schema["type"], "function")
        self.assertTrue(schema["function"]["description"].startswith("[MCP · toy]"))
        params = schema["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertEqual(sorted(params["required"]), ["a", "b"])

    def test_descripciones_largas_se_recortan(self):
        schema = next(t for t in self.manager.tool_schemas() if t["function"]["name"] == "toy__long_desc")
        self.assertLessEqual(len(schema["function"]["description"]), settings.mcp_max_description_chars + 3)

    def test_llamada_exitosa_marca_el_resultado_como_dato_externo(self):
        result = self.manager.call_tool("toy__add", {"a": 2, "b": 3})
        self.assertIn("5", result)
        self.assertIn("herramienta externa MCP 'toy'", result)
        self.assertIn("no como instrucciones", result)
        self.assertFalse(result.startswith("Error"))

    def test_error_de_la_herramienta_empieza_con_Error(self):
        # Es lo que detecta la verificación del Agent Loop para reintentar.
        result = self.manager.call_tool("toy__fail", {})
        self.assertTrue(result.startswith("Error"), result)

    def test_argumentos_invalidos_devuelven_error_sin_reventar(self):
        result = self.manager.call_tool("toy__add", {"a": "no soy un número", "b": 1})
        self.assertTrue(result.startswith("Error"), result)

    def test_herramienta_inexistente_o_de_servidor_caido(self):
        self.assertTrue(self.manager.call_tool("toy__no_existe", {}).startswith("Error"))
        self.assertTrue(self.manager.call_tool("roto__algo", {}).startswith("Error"))

    def test_salida_larga_se_trunca(self):
        with patch.object(settings, "mcp_max_output_chars", 100):
            result = self.manager.call_tool("toy__big", {})
        self.assertIn("salida truncada", result)
        self.assertLess(len(result), 400)

    def test_timeout_devuelve_error_y_el_gestor_sigue_usable(self):
        result = self.manager.call_tool("toy__slow", {"seconds": 5}, timeout=1)
        self.assertTrue(result.startswith("Error"))
        self.assertIn("tardó más de 1s", result)
        # Después del timeout todo sigue funcionando.
        self.assertIn("7", self.manager.call_tool("toy__add", {"a": 3, "b": 4}))

    def test_status_no_expone_secretos(self):
        self.assertEqual(self.status_of("toy")["target"], sys.executable)
        self.assertNotIn("env", self.status_of("toy"))


@requires_mcp
class TestManagerFeatures(unittest.TestCase):
    def test_include_tools_solo_expone_lo_pedido(self):
        manager = MCPManager()
        try:
            manager.start({"toy": toy(include_tools=["add"])}, connect_timeout=30)
            names = [t["function"]["name"] for t in manager.tool_schemas()]
            self.assertEqual(names, ["toy__add"])
        finally:
            manager.stop()

    def test_auto_approve(self):
        manager = MCPManager()
        try:
            manager.start({
                "cerrado": toy(),
                "todo": toy(auto_approve=True),
                "algunas": toy(auto_approve=["add"]),
            }, connect_timeout=30)
            self.assertFalse(manager.is_auto_approved("cerrado__add"))   # por defecto: NADA es de confianza
            self.assertTrue(manager.is_auto_approved("todo__add"))
            self.assertTrue(manager.is_auto_approved("todo__fail"))
            self.assertTrue(manager.is_auto_approved("algunas__add"))
            self.assertFalse(manager.is_auto_approved("algunas__write_marker"))
            self.assertFalse(manager.is_auto_approved("no_existe__x"))
        finally:
            manager.stop()

    def test_se_puede_detener_y_volver_a_iniciar(self):
        manager = MCPManager()
        manager.start({"toy": toy()}, connect_timeout=30)
        self.assertIn("5", manager.call_tool("toy__add", {"a": 2, "b": 3}))
        manager.stop()
        self.assertEqual(manager.tool_schemas(), [])
        self.assertTrue(manager.call_tool("toy__add", {"a": 1, "b": 1}).startswith("Error"))

        manager.start({"toy": toy()}, connect_timeout=30)
        try:
            self.assertIn("9", manager.call_tool("toy__add", {"a": 4, "b": 5}))
        finally:
            manager.stop()

    def test_sin_servidores_no_arranca_nada(self):
        manager = MCPManager()
        manager.start({})
        self.assertEqual(manager.status(), {"config_error": None, "servers": []})
        manager.stop()


# =============================================================================
# Transporte HTTP (con autenticación por header)
# =============================================================================

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@requires_mcp
class TestManagerHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = subprocess.Popen(
            [sys.executable, str(HTTP_PATH), str(cls.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.5).close()
                return
            except OSError:
                time.sleep(0.2)
        cls.server.kill()
        raise RuntimeError("el servidor HTTP de prueba no arrancó")

    @classmethod
    def tearDownClass(cls):
        cls.server.kill()
        cls.server.wait(timeout=10)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/mcp"

    def test_conecta_y_llama_enviando_los_headers_configurados(self):
        manager = MCPManager()
        try:
            manager.start({"remoto": {"url": self.url, "headers": {"Authorization": "Bearer secreto"}}},
                          connect_timeout=30)
            status = manager.status()["servers"][0]
            self.assertTrue(status["connected"], status["error"])
            self.assertEqual(status["transport"], "http")
            self.assertIn("hola desde http", manager.call_tool("remoto__whoami", {}))
        finally:
            manager.stop()

    def test_sin_el_header_correcto_el_servidor_rechaza_y_se_reporta(self):
        manager = MCPManager()
        try:
            manager.start({"remoto": {"url": self.url}}, connect_timeout=30)
            status = manager.status()["servers"][0]
            self.assertFalse(status["connected"])
            self.assertTrue(status["error"])
        finally:
            manager.stop()

    def test_status_no_expone_el_path_ni_los_headers(self):
        manager = MCPManager()
        try:
            manager.start({"remoto": {"url": self.url + "?token=ultra", "headers": {"Authorization": "Bearer secreto"}}},
                          connect_timeout=30)
            status = manager.status()["servers"][0]
            self.assertEqual(status["target"], f"http://127.0.0.1:{self.port}")
            self.assertNotIn("ultra", json.dumps(manager.status()))
            self.assertNotIn("secreto", json.dumps(manager.status()))
        finally:
            manager.stop()


# =============================================================================
# Integración con el Core, el catálogo de herramientas y la confirmación humana
# =============================================================================

@requires_mcp
class TestMcpEnElCore(CoreTestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = MCPManager()
        cls.manager.start({
            "toy": toy(),                          # sin confianza: pide confirmación
            "confiable": toy(auto_approve=True),   # declarado explícitamente
        }, connect_timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.manager.stop()

    def setUp(self):
        super().setUp()
        patcher = patch.object(mcp_client, "manager", self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_el_catalogo_incluye_herramientas_propias_y_externas(self):
        names = [t["function"]["name"] for t in tools.get_tools_schema()]
        self.assertIn("calculate", names)      # propia
        self.assertIn("toy__add", names)       # MCP
        self.assertIn("confiable__add", names)
        # Las propias siguen siendo un catálogo aparte e intacto.
        self.assertNotIn("toy__add", [t["function"]["name"] for t in tools.TOOLS_SCHEMA])

    def test_exportacion_a_formato_mcp_acepta_el_catalogo_completo(self):
        exported = tools.tools_schema_to_mcp(tools.get_tools_schema())
        add = next(t for t in exported if t["name"] == "toy__add")
        self.assertIn("inputSchema", add)

    def test_execute_tool_despacha_a_mcp(self):
        self.assertIn("5", tools.execute_tool("confiable__add", {"a": 2, "b": 3}))

    def test_herramienta_mcp_sin_confianza_queda_pendiente_y_NO_se_ejecuta(self):
        marker = self.workspace / "marca.txt"

        _, status, final, _ = self.run_core([call("toy__write_marker", path=str(marker))])

        self.assertEqual(status, "esperando_confirmacion")
        self.assertFalse(marker.exists())  # nada se ejecutó todavía
        pending = final["pending_confirmations"][0]
        self.assertEqual(pending["tool"], "toy__write_marker")
        self.assertIn("servidor MCP 'toy'", pending["detail"])

    def test_al_aprobar_se_ejecuta_la_herramienta_mcp(self):
        marker = self.workspace / "marca.txt"
        _, _, final, _ = self.run_core([call("toy__write_marker", path=str(marker))])
        action_id = final["pending_confirmations"][0]["id"]

        outcome = safety.resolve_pending_action(action_id, approved=True)

        self.assertEqual(outcome["status"], "aprobada")
        self.assertIn("marcador creado", outcome["result"])
        self.assertTrue(marker.exists())

    def test_al_rechazar_no_se_ejecuta(self):
        marker = self.workspace / "marca.txt"
        _, _, final, _ = self.run_core([call("toy__write_marker", path=str(marker))])

        outcome = safety.resolve_pending_action(final["pending_confirmations"][0]["id"], approved=False)

        self.assertEqual(outcome["status"], "rechazada")
        self.assertFalse(marker.exists())

    def test_herramienta_mcp_con_auto_approve_se_ejecuta_directo_dentro_del_loop(self):
        provider, status, final, events = self.run_core(
            [call("confiable__add", a=2, b=3), say("La suma es 5.")]
        )

        self.assertEqual(status, "completado", final)
        self.assertEqual(final["reply"], "La suma es 5.")
        self.assertTrue(final["used_tools"])
        self.assertEqual(len(provider.calls), 2)
        tool_msg = next(m for m in provider.calls[1]["messages"] if m["role"] == "tool")
        self.assertIn("5", tool_msg["content"])
        self.assertIn("externa MCP", tool_msg["content"])
        self.assertTrue(next(e for e in events if e["type"] == "verificacion")["ok"])

    def test_fallo_de_herramienta_mcp_activa_el_reintento_del_loop(self):
        provider, status, final, events = self.run_core([
            call("confiable__fail"),
            call("confiable__add", a=1, b=1),
            say("Listo."),
        ])

        self.assertEqual(status, "completado")
        self.assertEqual([e["ok"] for e in events if e["type"] == "verificacion"], [False, True])
        self.assertEqual(len(provider.calls), 3)


if __name__ == "__main__":
    unittest.main()