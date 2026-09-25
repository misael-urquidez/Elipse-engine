"""
Piezas compartidas por las pruebas: stubs de dependencias pesadas, un modelo
FALSO, y un CoreTestCase que aísla cada prueba con su propia DB y workspace
temporales (nunca toca tu elipse.db ni tu workspace real).
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Si alguna dependencia pesada no está instalada (ej. en CI), se reemplaza por un
# stub: el Core no la necesita para estas pruebas.
for _name in ("chromadb", "ddgs", "ollama"):
    try:
        __import__(_name)
    except ImportError:
        sys.modules[_name] = MagicMock()
        if _name == "chromadb":
            sys.modules["chromadb.utils"] = MagicMock()

from app.core import db, memory, tools  # noqa: E402
from app.core.core import ElipseCore  # noqa: E402
from app.core.providers import ChatResult, ToolCall  # noqa: E402
from app.core.seed_personality import seed  # noqa: E402


def say(text: str, prompt_tokens=None) -> ChatResult:
    return ChatResult(content=text, prompt_tokens=prompt_tokens)


def call(name: str, **arguments) -> ChatResult:
    return ChatResult(content="", tool_calls=[ToolCall(name=name, arguments=arguments)])


class FakeProvider:
    """Responde en orden lo que se le programó. Si el Core pide de más, falla fuerte."""

    name = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None, allow_tools=True):
        # "tools" = ¿el modelo pudo pedir herramientas en esta llamada?
        self.calls.append({"tools": bool(tools) and allow_tools, "messages": list(messages)})
        if not self.script:
            raise AssertionError("El Core llamó al modelo más veces de las esperadas.")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)

        (tmp / "workspace").mkdir()
        self.workspace = (tmp / "workspace").resolve()

        patches = [
            patch.object(db, "DB_PATH", tmp / "test.db"),
            patch.object(tools, "WORKSPACE_ROOT", self.workspace),
            patch.object(memory, "search_memory", lambda *a, **k: []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

        seed()  # crea tablas + personalidad por defecto en la DB temporal

    def run_core(self, script, message="hola"):
        provider = FakeProvider(script)
        events = []
        status, final = ElipseCore(provider).run(message, emit=events.append)
        return provider, status, final, events

    def query(self, sql):
        conn = sqlite3.connect(db.DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
        conn.close()
        return rows