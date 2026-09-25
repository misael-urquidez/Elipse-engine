"""
Rutas, constantes de plataforma y plantilla de .env.
=====================================================
Único lugar del proyecto que decide "dónde está todo". El resto de los
módulos importan de acá en vez de recalcular rutas por su cuenta, así
cualquier cambio de estructura de carpetas se hace en un solo sitio.
"""

import platform
import subprocess
from pathlib import Path

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# Flag de subprocess para no abrir consolas negras extra en Windows.
FLAGS = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

# ROOT = raíz del proyecto (carpeta que contiene app/, launcher/, .env, elipse.db...).
# Este archivo vive en <raiz>/launcher/paths.py, así que subimos dos niveles.
ROOT = Path(__file__).resolve().parent.parent

ENV_PATH = ROOT / ".env"
PREFS_PATH = ROOT / "panel_prefs.json"
LOGS = ROOT / "logs"
CLIENT_FILE = "elipse_client.html"  # cliente web que abre el botón «Abrir consola»

PORT = 8000
OLLAMA_PORT = 11434

OLLAMA_INSTALLER_WIN = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_INSTALL_SH = "https://ollama.com/install.sh"

CORE_PACKAGES = ("fastapi", "uvicorn", "httpx", "pydantic_settings")

# Plantilla mínima de .env si no existe
ENV_TEMPLATE = """# ELIPSE — generado automáticamente por el Control Panel
DEFAULT_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:3b
# Opciones de proveedor: ollama | anthropic | openai | gemini
# ANTHROPIC_API_KEY=
# ANTHROPIC_MODEL=claude-sonnet-5
# OPENAI_API_KEY=
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_MODEL=
# GEMINI_API_KEY=
# GEMINI_MODEL=gemini-2.5-flash
"""
