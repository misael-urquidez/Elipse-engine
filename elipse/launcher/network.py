"""
Todo lo que habla HTTP con servicios locales o remotos:
- probe(): saber si un puerto local está vivo.
- list_ollama_models(): listar modelos instalados en Ollama (API + CLI,
  con diagnóstico detallado — ver nota abajo sobre el bug de Linux).
- fetch_models() / fetch_gemini_models(): catálogo de modelos de un
  proveedor compatible con OpenAI o de Gemini.
- check_tool_support() / check_gemini_tool_support(): prueba real de si
  un modelo soporta tool-calling.

──────────────────────────────────────────────────────────────────────
Nota sobre el bug "en Linux no detecta los modelos de Ollama":
──────────────────────────────────────────────────────────────────────
El código original solo probaba 127.0.0.1/localhost:11434 y, si algo
fallaba, se tragaba la excepción (`except Exception: continue`) sin
dejar rastro — así que cuando fallaba, el panel decía genéricamente
"¿Ollama apagado?" incluso con Ollama corriendo (visible en la tarjeta
principal, que usa un chequeo distinto). Dos causas típicas en Linux
que ese código no cubría:

1. **PATH incompleto en apps de escritorio.** Cuando el panel se abre
   con doble clic (o desde una IDE) en vez de una terminal, el proceso
   hereda un PATH mínimo que muchas veces no incluye `~/.local/bin`,
   `/snap/bin` ni otras rutas típicas donde queda instalado `ollama`.
   `shutil.which("ollama")` entonces no lo encuentra y el fallback por
   CLI (`ollama list`) nunca corre. Solución: además de la lista de
   rutas fijas, se intenta también invocar `ollama` a través del shell
   de login del usuario (`bash -lc "ollama list"`), que sí carga su
   `.bashrc`/`.profile` con el PATH real.
2. **`OLLAMA_HOST` no está en 127.0.0.1:11434.** Instalaciones systemd
   con `Environment="OLLAMA_HOST=0.0.0.0:11434"` u otro puerto hacen
   que la llamada a la API fije de antemano falle en silencio.
   Solución: leer `OLLAMA_HOST` del entorno y probarlo también.

Además, ahora se guarda un registro de qué se intentó y por qué falló
cada intento, para poder mostrarlo en la UI en vez de un mensaje
genérico.
"""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .paths import FLAGS, IS_WIN, OLLAMA_PORT

# Ignora variables de entorno de proxy (http_proxy, etc.) para tráfico local:
# un proxy corporativo mal configurado no debería poder romper el acceso a
# localhost.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(port, path="", timeout=1.2):
    """Cuerpo de la respuesta si el puerto local responde; None si está caído."""
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return ""  # el servidor contestó (aunque sea con error): está vivo
    except Exception:
        return None


def _ollama_host_candidates():
    """(host, port) a probar: 127.0.0.1/localhost en el puerto por defecto, más lo
    que diga OLLAMA_HOST si está seteado distinto (systemd, Docker, etc.)."""
    candidates = [("127.0.0.1", OLLAMA_PORT), ("localhost", OLLAMA_PORT)]
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    if raw:
        # Formatos válidos: "host:port", "http://host:port", o solo "host"
        parsed = urllib.parse.urlparse(raw if "//" in raw else f"//{raw}")
        host = parsed.hostname or raw
        port = parsed.port or OLLAMA_PORT
        if host and (host, port) not in candidates:
            candidates.insert(0, (host, port))
    return candidates


def _ollama_api_attempt(host, port, timeout=5):
    """Un intento de leer /api/tags. Devuelve (names, error_str)."""
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/api/tags",
            headers={"User-Agent": "ELIPSE-Panel/1.0"},
        )
        with _LOCAL.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        data = json.loads(body)
        names = []
        for m in data.get("models") or []:
            n = (m.get("name") or m.get("model") or "").strip()
            if n:
                names.append(n)
        return names, None
    except urllib.error.URLError as e:
        return [], f"{host}:{port} → {getattr(e, 'reason', e)}"
    except json.JSONDecodeError:
        return [], f"{host}:{port} → respuesta no es JSON válido"
    except Exception as e:
        return [], f"{host}:{port} → {e}"


def _ollama_cli_candidates():
    """Rutas de binario a probar, en orden. Incluye ubicaciones típicas de
    instalación por usuario (no solo las de sistema) para no depender de que
    el PATH del proceso esté completo."""
    exe = shutil.which("ollama")
    cands = [exe] if exe else []
    extra = [
        "ollama",
        "/usr/local/bin/ollama", "/usr/bin/ollama",
        "/snap/bin/ollama",
        os.path.expanduser("~/.local/bin/ollama"),
        os.path.expanduser("~/bin/ollama"),
    ]
    if IS_WIN:
        extra += [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Ollama\ollama.exe"),
        ]
    for p in extra:
        if p and p not in cands:
            cands.append(p)
    return cands


def _ollama_cli_attempt():
    """Fallback por CLI: prueba binarios directos y, en Linux/macOS, también
    a través del shell de login del usuario (para heredar su PATH real,
    aunque el panel se haya abierto sin terminal)."""
    commands = [[exe, "list"] for exe in _ollama_cli_candidates()]
    if not IS_WIN and shutil.which("bash"):
        commands.append(["bash", "-lc", "ollama list"])

    for cmd in commands:
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=12, creationflags=FLAGS,
            )
            out = r.stdout or ""
            if not out.strip() and r.returncode != 0:
                continue
            names = []
            for ln in out.splitlines()[1:]:
                ln = ln.strip()
                if ln:
                    names.append(ln.split()[0])
            if names:
                return names, None
        except FileNotFoundError:
            continue
        except Exception as e:
            continue
    return [], "no encontré el binario «ollama» en el PATH ni en las rutas habituales"


def list_ollama_models():
    """Lista modelos de Ollama. Preferencia: API HTTP (más fiable) → CLI.
    Bloquea hasta ~15 s: llamar SIEMPRE desde un hilo, nunca desde la interfaz.

    Devuelve (names, notes): `names` es la lista ordenada sin duplicados;
    `notes` es una lista de strings con lo que se intentó, para poder
    mostrar un diagnóstico real en vez de "¿Ollama apagado?" a ciegas."""
    notes = []

    # 1) API local (varios hosts/puertos posibles)
    for host, port in _ollama_host_candidates():
        names, err = _ollama_api_attempt(host, port)
        if names:
            return sorted(set(names)), notes
        if err:
            notes.append(f"API {err}")

    # 2) CLI
    names, err = _ollama_cli_attempt()
    if names:
        return sorted(set(names)), notes
    if err:
        notes.append(f"CLI: {err}")

    return [], notes


def fetch_models(base_url, api_key):
    """GET {base_url}/models en un servidor compatible con OpenAI. Lanza RuntimeError legible."""
    headers = {"User-Agent": "ELIPSE-Panel/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url.rstrip("/") + "/models", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("API key inválida o sin permiso (401)." if e.code == 401
                           else f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"No se pudo conectar a {base_url}: {e.reason}")
    except ValueError:
        raise RuntimeError("El servidor respondió algo que no es JSON. ¿La URL base termina en /v1?")
    return sorted(i["id"] for i in body.get("data", []) if "id" in i)


def fetch_gemini_models(api_key, base_url="https://generativelanguage.googleapis.com/v1beta"):
    """GET {base_url}/models con x-goog-api-key. Solo modelos que soportan generateContent."""
    if not api_key:
        raise RuntimeError("Falta la API key de Gemini.")
    headers = {"User-Agent": "ELIPSE-Panel/1.0", "x-goog-api-key": api_key}
    url = base_url.rstrip("/") + "/models?pageSize=100"
    names = []
    try:
        while url:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode("utf-8"))
            for m in body.get("models") or []:
                # name viene como "models/gemini-2.5-flash"
                raw = (m.get("name") or "").strip()
                short = raw.split("/")[-1] if raw else ""
                if not short:
                    continue
                methods = m.get("supportedGenerationMethods") or m.get("supported_generation_methods") or []
                # Si no viene la lista, igual lo incluimos; si viene, filtramos por generateContent.
                if methods and "generateContent" not in methods:
                    continue
                names.append(short)
            token = body.get("nextPageToken") or body.get("next_page_token")
            if token:
                url = base_url.rstrip("/") + "/models?pageSize=100&pageToken=" + urllib.parse.quote(token)
            else:
                url = None
    except urllib.error.HTTPError as e:
        raise RuntimeError("API key inválida o sin permiso (401/403)." if e.code in (401, 403)
                           else f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"No se pudo conectar a la API de Gemini: {e.reason}")
    except ValueError:
        raise RuntimeError("La API de Gemini devolvió algo que no es JSON válido.")
    return sorted(set(names))


_DUMMY_TOOL = {
    "type": "function",
    "function": {
        "name": "ping_de_prueba",
        "description": "Función de prueba del panel de ELIPSE. No la uses de verdad.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def check_tool_support(base_url, api_key, model, timeout=20):
    """Prueba real (no listada): manda un chat completion mínimo con una tool de prueba
    y ve si el servidor la acepta. Devuelve True/False si se pudo determinar, o None
    si el error no fue concluyente (auth, rate limit, modelo caído, etc.)."""
    headers = {"User-Agent": "ELIPSE-Panel/1.0", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hola"}],
        "tools": [_DUMMY_TOOL],
        "tool_choice": "auto",
        "max_tokens": 1,
    }).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True  # el servidor aceptó el parámetro "tools" y respondió 200: lo soporta
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", "replace").lower()
        except Exception:
            msg = ""
        if e.code == 400 and any(s in msg for s in ("tool", "function_call", "function calling", "does not support")):
            return False  # rechazo explícito por usar "tools"
        return None  # otro error (401, 404 de modelo, límite de cuota...): no concluyente
    except Exception:
        return None


def check_gemini_tool_support(api_key, model, base_url="https://generativelanguage.googleapis.com/v1beta", timeout=20):
    """Prueba real: generateContent mínimo con functionDeclarations.
    True = aceptó tools, False = rechazo explícito, None = no concluyente."""
    headers = {
        "User-Agent": "ELIPSE-Panel/1.0",
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": "hola"}]}],
        "tools": [{
            "functionDeclarations": [{
                "name": "ping_de_prueba",
                "description": "Función de prueba del panel de ELIPSE. No la uses de verdad.",
                "parameters": {"type": "object", "properties": {}},
            }]
        }],
        "generationConfig": {"maxOutputTokens": 1},
    }).encode("utf-8")
    url = base_url.rstrip("/") + f"/models/{model}:generateContent"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        return True
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", "replace").lower()
        except Exception:
            msg = ""
        if e.code == 400 and any(s in msg for s in (
            "tool", "function", "functioncall", "function_call",
            "functiondeclaration", "does not support", "not supported",
        )):
            return False
        return None
    except Exception:
        return None
