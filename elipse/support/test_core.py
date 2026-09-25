"""
Pruebas del Agent Loop del Core — sin Ollama, sin internet, sin tocar tu elipse.db.

Correr desde la raíz del proyecto:
    python tests/test_core.py -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import CoreTestCase, ElipseCore, FakeProvider, call, say  # noqa: E402


class TestAgentLoop(CoreTestCase):
    def test_respuesta_directa_sin_herramientas(self):
        provider, status, final, _ = self.run_core([say("¡Hola!")])

        self.assertEqual(status, "completado")
        self.assertEqual(final["reply"], "¡Hola!")
        self.assertFalse(final["used_tools"])
        self.assertEqual(len(provider.calls), 1)

    def test_herramienta_exitosa_hace_exactamente_2_llamadas(self):
        """
        Regresión: antes, tras una herramienta exitosa el loop hacía una TERCERA
        llamada al modelo (con herramientas activas otra vez) y entregaba esa.
        """
        provider, status, final, events = self.run_core(
            [call("get_current_datetime"), say("Ya te dije la hora.")]
        )

        self.assertEqual(status, "completado")
        self.assertEqual(final["reply"], "Ya te dije la hora.")
        self.assertTrue(final["used_tools"])
        self.assertEqual(len(provider.calls), 2)
        self.assertTrue(provider.calls[0]["tools"])       # pide herramientas...
        self.assertFalse(provider.calls[1]["tools"])      # ...la respuesta final, no
        self.assertEqual(
            [e["type"] for e in events if e["type"] != "progreso"],
            ["plan", "verificacion"],
        )

    def test_reintenta_una_vez_si_falla_la_verificacion(self):
        provider, status, final, events = self.run_core([
            call("run_python", code="raise ValueError('boom')"),
            call("run_python", code="print('hola')"),
            say("Listo, corrió bien."),
        ])

        self.assertEqual(status, "completado")
        self.assertEqual(len(provider.calls), 3)
        oks = [e["ok"] for e in events if e["type"] == "verificacion"]
        self.assertEqual(oks, [False, True])
        # Al segundo intento el modelo recibió el detalle del fallo.
        retry_msgs = [m["content"] for m in provider.calls[1]["messages"] if m["role"] == "user"]
        self.assertTrue(any("verificación automática" in c for c in retry_msgs))

    def test_no_reintenta_indefinidamente(self):
        provider, status, final, _ = self.run_core([
            call("run_python", code="raise ValueError('uno')"),
            call("run_python", code="raise ValueError('dos')"),
            say("No pude lograrlo."),
        ])

        self.assertEqual(status, "completado")
        self.assertEqual(final["reply"], "No pude lograrlo.")
        self.assertEqual(len(provider.calls), 3)  # 2 intentos + respuesta final, nada más

    def test_accion_riesgosa_queda_pendiente_y_no_se_ejecuta(self):
        target = self.workspace / "nota.txt"
        target.write_text("original", encoding="utf-8")

        provider, status, final, _ = self.run_core(
            [call("write_file", path="nota.txt", content="nuevo")]
        )

        self.assertEqual(status, "esperando_confirmacion")
        self.assertEqual(len(final["pending_confirmations"]), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "original")  # NO se tocó
        self.assertEqual(len(provider.calls), 1)
        pending = self.query("SELECT status FROM pending_actions")
        self.assertEqual([p["status"] for p in pending], ["pendiente"])

    def test_error_del_proveedor_no_revienta(self):
        _, status, final, _ = self.run_core([RuntimeError("Ollama caído")])

        self.assertEqual(status, "error")
        self.assertIn("Ollama caído", final["reply"])

    def test_guarda_historial_y_loguea_decision_del_router(self):
        self.run_core([say("respuesta")], message="escribe una función en python")

        msgs = self.query("SELECT role, content FROM messages ORDER BY id")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        log = self.query("SELECT provider_chosen, reason FROM router_log")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["provider_chosen"], "fake-model")
        self.assertIn("sin proveedor de código", log[0]["reason"])

    def test_los_resultados_de_herramienta_llevan_el_id_de_su_llamada(self):
        """Claude y OpenAI exigen emparejar cada resultado con su llamada por id."""
        provider, _, _, _ = self.run_core(
            [call("get_current_datetime"), say("listo")]
        )
        final_messages = provider.calls[1]["messages"]
        assistant = next(m for m in final_messages if m.get("tool_calls"))
        tool_msg = next(m for m in final_messages if m["role"] == "tool")
        self.assertTrue(assistant["tool_calls"][0]["id"])
        self.assertEqual(tool_msg["tool_call_id"], assistant["tool_calls"][0]["id"])
        self.assertEqual(tool_msg["name"], "get_current_datetime")

    def test_consulta_de_codigo_usa_el_proveedor_de_codigo_si_existe(self):
        general = FakeProvider([say("no debería usarse")])
        general.name = "general"
        coder = FakeProvider([say("respuesta del proveedor de código")])
        coder.name = "coder"

        status, final = ElipseCore(general, code_provider=coder).run("escribe una función en python")

        self.assertEqual(status, "completado")
        self.assertEqual(final["provider"], "coder")
        self.assertEqual(len(general.calls), 0)
        self.assertNotIn("sin proveedor de código", final["reason"])

    def test_consulta_general_usa_el_proveedor_por_defecto(self):
        general = FakeProvider([say("hola")])
        general.name = "general"
        coder = FakeProvider([])
        coder.name = "coder"

        _, final = ElipseCore(general, code_provider=coder).run("hola, ¿cómo estás?")

        self.assertEqual(final["provider"], "general")
        self.assertEqual(len(coder.calls), 0)

    def test_reporta_prompt_tokens_maximo(self):
        _, _, final, _ = self.run_core([
            call("get_current_datetime"),
            say("ok", prompt_tokens=1900),
        ])
        # La 1ra llamada no reportó tokens, la 2da sí: se conserva el máximo visto.
        self.assertEqual(final["prompt_tokens"], 1900)


if __name__ == "__main__":
    unittest.main()