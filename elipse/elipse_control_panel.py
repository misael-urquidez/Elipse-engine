"""
ELIPSE Control Panel — Material 3 Expressive
============================================
Tkinter puro, sin dependencias extra.

USO: copia este archivo a la RAÍZ del proyecto (junto a app/, .env, elipse.db)
y corre:  python elipse_control_panel.py

- Al abrir, detecta solo si Ollama y ELIPSE Core ya están corriendo.
- Un solo botón por servicio: Iniciar se convierte en Detener.
- La salida de cada servicio se guarda en logs/*.log (sobrevive si cierras el panel).
- Si algo falla dentro del panel, se muestra el error y se guarda en logs/panel_error.log.
"""

import concurrent.futures
import json
import math
import os
import platform
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

IS_WIN = platform.system() == "Windows"
FLAGS = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
ROOT = Path(__file__).resolve().parent
ENV_PATH, PREFS_PATH, LOGS = ROOT / ".env", ROOT / "panel_prefs.json", ROOT / "logs"
CLIENT_FILE = "elipse_client.html"  # cliente web que abre el botón «Abrir consola»
PORT, OLLAMA_PORT = 8000, 11434
OLLAMA_INSTALLER = "https://ollama.com/download/OllamaSetup.exe"

# ─────────────────────────── Diseño: paleta y tipografía ───────────────────────────
C = dict(
    bg="#141218", card="#211F26", card_hi="#2B2930", outline="#49454F",
    text="#E6E0E9", muted="#CAC4D0", dim="#938F99",
    primary="#D0BCFF", on_primary="#381E72", primary_c="#4F378B", on_primary_c="#EADDFF",
    sec_c="#4A4458", on_sec_c="#E8DEF8",
    good="#B5E8B9", on_good="#0C3B1A", err="#F2B8B5", on_err="#601410", warm="#FFB783",
)
FD = ("Segoe UI Black", 28)
FDS = ("Segoe UI Black", 20)
FT = ("Segoe UI", 15, "bold")
FB = ("Segoe UI", 10)
FBB = ("Segoe UI", 10, "bold")
FS = ("Segoe UI", 9)
FBTN = ("Segoe UI", 10, "bold")
FMONO = ("Consolas", 9)
BOLT = [(4, -15), (-9, 3), (-1, 3), (-4, 15), (9, -4), (1, -4)]
BUSY = ("checking", "starting", "stopping")


def mix(a, b, t):
    ra, rb = (tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for h in (a, b))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def tween(w, frm, to, apply, steps=9, ms=16):
    """Transición suave de colores (smoothstep); cancela la anterior del mismo widget."""
    if getattr(w, "_tw", None):
        try:
            w.after_cancel(w._tw)
        except Exception:
            pass

    def step(i=1):
        try:
            t = i / steps
            t = t * t * (3 - 2 * t)
            apply(tuple(mix(a, b, t) for a, b in zip(frm, to)))
            w._tw = w.after(ms, step, i + 1) if i < steps else None
        except tk.TclError:
            pass
    step()


def rrect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def cookie(cx, cy, r, phase=0.0, amp=0.1, lobes=8, n=64):
    """Forma festoneada (la firma visual de Material 3 Expressive)."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        rad = r * (1 + amp * math.cos(lobes * a + phase))
        pts += [cx + rad * math.cos(a), cy + rad * math.sin(a)]
    return pts


# ─────────────────────────── Cola para actualizar la UI desde hilos ───────────────────────────
UIQ = queue.Queue()


def ui(fn, *args):
    UIQ.put((fn, args))


# ─────────────────────────── .env y preferencias del panel ───────────────────────────
def env_lines():
    return ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []


def env_dict():
    d = {}
    for line in env_lines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.strip()  # si hay duplicadas, gana la última (igual que dotenv)
    return d


def env_update(updates):
    """Actualiza o agrega variables, elimina duplicadas y respeta comentarios y orden."""
    left, out = dict(updates), []
    for line in env_lines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                if k in left:
                    out.append(f"{k}={left.pop(k)}")
                continue
        out.append(line)
    if left:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- añadido desde el Control Panel ---")
        out += [f"{k}={v}" for k, v in left.items()]
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def load_prefs():
    try:
        return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_prefs(p):
    PREFS_PATH.write_text(json.dumps(p), encoding="utf-8")


# ─────────────────────────── Red y procesos ───────────────────────────
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # ignora proxies para localhost


def probe(port, path="", timeout=1.2):
    """Cuerpo de la respuesta si el puerto local responde; None si está caído."""
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return ""  # el servidor contestó (aunque sea con error): está vivo
    except Exception:
        return None


def find_python():
    p = ROOT / "venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
    return str(p) if p.exists() else sys.executable


def find_ollama():
    exe = shutil.which("ollama")
    if exe:
        return exe
    if IS_WIN:
        p = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        return p if os.path.exists(p) else None
    return None


def list_ollama_models():
    """Bloquea hasta 10 s: llamar SIEMPRE desde un hilo, nunca desde la interfaz."""
    exe = find_ollama()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "list"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=10, creationflags=FLAGS).stdout or ""
    except Exception:
        return []
    return [ln.split()[0] for ln in out.splitlines()[1:] if ln.strip()]


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


def kill_port(port):
    if IS_WIN:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, creationflags=FLAGS).stdout
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                subprocess.run(["taskkill", "/F", "/T", "/PID", line.split()[-1]], capture_output=True, creationflags=FLAGS)
    else:
        subprocess.run(f"lsof -ti:{port} | xargs -r kill -9", shell=True, capture_output=True)


def kill_ollama():
    if IS_WIN:  # primero la app de bandeja, si no vuelve a levantar el servidor
        for exe in ("ollama app.exe", "ollama.exe"):
            subprocess.run(["taskkill", "/F", "/T", "/IM", exe], capture_output=True, creationflags=FLAGS)
    else:
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)


def launch_ollama(logf):
    return subprocess.Popen([find_ollama() or "ollama", "serve"], stdout=logf, stderr=subprocess.STDOUT, creationflags=FLAGS)


def launch_elipse(logf):
    if not (ROOT / "app").is_dir():
        raise RuntimeError("no hay carpeta app/ junto al panel; copia este archivo a la raíz del proyecto")
    host = "0.0.0.0" if load_prefs().get("lan") else "127.0.0.1"
    return subprocess.Popen([find_python(), "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(PORT)],
                            cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, creationflags=FLAGS)


def run_keys(args):
    """python -m app.manage_keys --json <args>. (revoke devuelve código 1 si no existe: es válido)."""
    r = subprocess.run([find_python(), "-m", "app.manage_keys", "--json"] + args, cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=30, creationflags=FLAGS)
    if r.returncode not in (0, 1):
        raise RuntimeError((r.stderr.strip().splitlines() or [f"código de salida {r.returncode}"])[-1])
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        tail = (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["Sin salida del comando."])[-1]
        raise RuntimeError(tail)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


# ─────────────────────────── Widgets M3 ───────────────────────────
class Pill(tk.Canvas):
    """Botón en forma de píldora con transición de color."""
    KINDS = {"filled": (C["primary"], C["on_primary"]), "tonal": (C["sec_c"], C["on_sec_c"]),
             "danger": (C["err"], C["on_err"]), "busy": (C["card_hi"], C["dim"]),
             "ghost": (C["card_hi"], C["muted"])}

    def __init__(self, parent, text, cmd=None, kind="tonal", width=0, height=40, padx=22, font=FBTN, bg=None):
        w = max(width, tkfont.Font(font=font).measure(text) + 2 * padx)
        super().__init__(parent, width=w, height=height, bg=bg or parent.cget("bg"), highlightthickness=0, cursor="hand2")
        self.cmd, self.kind, self.enabled = cmd, kind, True
        self.fill, self.fg = self.KINDS[kind]
        self.shape = rrect(self, 1, 1, w - 1, height - 1, (height - 2) / 2, fill=self.fill, outline="")
        self.label = self.create_text(w / 2, height / 2, text=text, fill=self.fg, font=font)
        self.bind("<Button-1>", lambda e: self.enabled and self.cmd and self.cmd())
        self.bind("<Enter>", lambda e: self.enabled and self.itemconfig(self.shape, fill=mix(self.fill, "#ffffff", .12)))
        self.bind("<Leave>", lambda e: self.itemconfig(self.shape, fill=self.fill))

    def _apply(self, cols):
        self.fill, self.fg = cols
        self.itemconfig(self.shape, fill=self.fill)
        self.itemconfig(self.label, fill=self.fg)

    def set(self, text=None, kind=None, enabled=True):
        if text is not None:
            self.itemconfig(self.label, text=text)
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        if kind and kind != self.kind:
            self.kind = kind
            tween(self, (self.fill, self.fg), self.KINDS[kind], self._apply)


class Surface(tk.Frame):
    """Contenedor de esquinas redondeadas; el contenido va en .inner.
    Sin `height`, se ajusta solo al contenido (así no se corta con otras fuentes o escalas de pantalla)."""

    def __init__(self, parent, height=None, fill=None, r=26, pad=20):
        pbg = parent.cget("bg")
        super().__init__(parent, bg=pbg)
        self.fill, self.r, self.pad, self.min_h = fill or C["card"], r, pad, height or 0
        self.cv = tk.Canvas(self, bg=pbg, highlightthickness=0, height=max(self.min_h, 2 * pad))
        self.cv.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.cv, bg=self.fill)
        self.win = self.cv.create_window(pad, pad, window=self.inner, anchor="nw")
        self.cv.bind("<Configure>", self._cfg)
        self.after_idle(self.fit)

    def fit(self):
        try:
            self.update_idletasks()
            self.cv.configure(height=max(self.min_h, self.inner.winfo_reqheight() + 2 * self.pad))
        except tk.TclError:
            pass

    def _cfg(self, e):
        self.cv.delete("bg")
        rrect(self.cv, 1, 1, e.width - 1, e.height - 1, self.r, fill=self.fill, outline="", tags="bg")
        self.cv.tag_lower("bg")
        self.cv.itemconfig(self.win, width=e.width - 2 * self.pad, height=e.height - 2 * self.pad)


def lbl(parent, text, fg=None, font=FS, wrap=0):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg or C["muted"], font=font,
                    anchor="w", justify="left", wraplength=wrap)


def field(parent, var, show=None):
    return tk.Entry(parent, textvariable=var, show=show or "", bg=C["card_hi"], fg=C["text"], insertbackground=C["text"],
                    relief="flat", font=FB, highlightthickness=2, highlightbackground=C["outline"], highlightcolor=C["primary"])


def style_ttk(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure("TCombobox", fieldbackground=C["card_hi"], background=C["card_hi"], foreground=C["text"],
                arrowcolor=C["text"], bordercolor=C["outline"], lightcolor=C["outline"], darkcolor=C["outline"], padding=7)
    s.map("TCombobox", fieldbackground=[("readonly", C["card_hi"])], foreground=[("readonly", C["text"])],
          selectbackground=[("readonly", C["card_hi"])], selectforeground=[("readonly", C["text"])],
          bordercolor=[("focus", C["primary"])])
    s.configure("Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["text"],
                rowheight=30, borderwidth=0, font=FB)
    s.configure("Treeview.Heading", background=C["card_hi"], foreground=C["muted"], relief="flat", font=FBB)
    s.map("Treeview", background=[("selected", C["primary_c"])], foreground=[("selected", C["on_primary_c"])])
    s.map("Treeview.Heading", background=[("active", C["card_hi"])])
    for k, v in (("background", C["card_hi"]), ("foreground", C["text"]),
                 ("selectBackground", C["primary_c"]), ("selectForeground", C["on_primary_c"]), ("font", FB)):
        root.option_add(f"*TCombobox*Listbox.{k}", v)


# ─────────────────────────── Servicios ───────────────────────────
class Service:
    """Estados: checking → off | on ; off → starting → on ; on → stopping → off."""

    def __init__(self, app, key, name, port, path, launch, kill, installed=lambda: True):
        self.app, self.key, self.name, self.port, self.path = app, key, name, port, path
        self.launch, self.kill, self.installed = launch, kill, installed
        self.state, self.proc, self.external, self.detail = "checking", None, False, ""
        self._inst, self.card = installed(), None

    def set_state(self, s):
        if s != self.state:
            self.state = s
            self.app.on_change(self)

    def toggle(self):
        if self.state == "on":
            self.stop()
        elif self.state == "off":
            self.start() if self.installed() else self.app.install_ollama()

    # -- detección continua (también al abrir el panel) --
    def observe(self, body):
        up = body is not None
        if self.proc and self.proc.poll() is not None:
            self.proc = None
        if self.installed() != self._inst:
            self._inst = self.installed()
            self.app.on_change(self)
        det = ""
        if up and self.key == "elipse":
            try:
                det = "modelo " + str(json.loads(body).get("model", ""))
            except Exception:
                pass
        if det != self.detail and self.state in ("checking", "off", "on"):
            self.detail = det
            self.app.on_change(self)
        st = self.state
        if st == "checking" or (st == "off" and up):
            self.external = up and self.proc is None
            self.set_state("on" if up else "off")
        elif st == "on" and not up and self.proc is None:
            self.external, self.detail = False, ""
            self.set_state("off")

    # -- iniciar --
    def start(self):
        if self.state != "off":
            return
        self.set_state("starting")
        threading.Thread(target=self._start_thread, daemon=True).start()

    def _start_thread(self):
        log = LOGS / f"{self.key}.log"
        try:
            LOGS.mkdir(exist_ok=True)
            if log.exists() and log.stat().st_size > 2_000_000:
                log.write_bytes(b"")
            with open(log, "ab") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} iniciando {self.name} =====\n".encode())
                f.flush()
                p = self.proc = self.launch(f)
        except Exception as e:
            ui(self.app.toast, f"No se pudo iniciar {self.name}: {e}")
            ui(self.set_state, "off")
            return
        for _ in range(90):
            if probe(self.port, self.path) is not None:
                ui(self.set_state, "on")
                return
            if p.poll() is not None:
                ui(self.app.toast, f"{self.name} se cerró al iniciar. Revisa sus logs.")
                ui(self.set_state, "off")
                return
            time.sleep(0.5)
        ui(self.set_state, "on")  # el proceso sigue vivo pero aún no responde

    # -- detener --
    def _stop_sync(self):
        p = self.proc
        try:
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
            else:
                self.kill()
        except Exception as e:
            ui(self.app.toast, f"Error al detener {self.name}: {e}")
        for _ in range(25):
            if probe(self.port, self.path) is None:
                return True
            time.sleep(0.4)
        return False

    def _stopped(self):
        self.proc, self.external, self.detail = None, False, ""
        self.set_state("off")

    def stop(self, then_start=False):
        if self.state != "on":
            return
        self.set_state("stopping")

        def run():
            if self._stop_sync():
                ui(self._stopped)
                if then_start:
                    ui(self.start)
            else:
                ui(self.app.toast, f"{self.name} sigue respondiendo después de detenerlo")
                ui(self.set_state, "on")
        threading.Thread(target=run, daemon=True).start()

    def restart(self):
        self.stop(then_start=True)


class Card(tk.Canvas):
    W, H = 600, 108
    LOOK = {"off": (C["card"], C["card_hi"], C["dim"]),
            "on": (mix(C["card"], C["good"], .45), C["good"], C["on_good"]),
            "busy": (mix(C["card"], C["primary"], .45), C["primary_c"], C["on_primary_c"])}

    def __init__(self, parent, svc):
        super().__init__(parent, width=self.W, height=self.H, bg=C["bg"], highlightthickness=0)
        self.svc, self.phase, self._dirty = svc, 0.0, True
        self._cols = self.LOOK["off"]
        self.bg = rrect(self, 2, 2, self.W - 2, self.H - 2, 34, fill=C["card"], outline=self._cols[0], width=2)
        self.cookie = self.create_polygon(cookie(58, 54, 30), fill=self._cols[1], outline="")
        self.letter = self.create_text(58, 54, text=svc.name[0], font=FDS, fill=self._cols[2])
        self.create_text(106, 34, anchor="w", text=svc.name, font=FT, fill=C["text"])
        self.state_t = self.create_text(106, 60, anchor="w", text="", font=FBB, fill=C["muted"])
        self.detail_t = self.create_text(106, 82, anchor="w", text="", font=FS, fill=C["dim"])
        self.btn = Pill(self, "…", svc.toggle, kind="busy", width=140, height=44, bg=C["card"])
        self.logs = Pill(self, "Logs", lambda: svc.app.open_logs(svc), kind="ghost", width=72, height=44, bg=C["card"])
        self.create_window(self.W - 24, 54, window=self.btn, anchor="e")
        self.create_window(self.W - 24 - 140 - 10, 54, window=self.logs, anchor="e")

    def _apply(self, cols):
        self._cols = cols
        self.itemconfig(self.bg, outline=cols[0])
        self.itemconfig(self.cookie, fill=cols[1])
        self.itemconfig(self.letter, fill=cols[2])

    def refresh(self):
        s = self.svc
        st = s.state
        tween(self, self._cols, self.LOOK["busy" if st in BUSY else st], self._apply)
        self._dirty = True
        ok = s.installed()
        state_txt, detail, btn, kind, en, color = {
            "checking": ("Buscando…", "Comprobando si ya está corriendo", "…", "busy", False, C["primary"]),
            "starting": ("Iniciando…", "Esperando respuesta del servicio", "Iniciando…", "busy", False, C["primary"]),
            "stopping": ("Deteniendo…", "Cerrando el proceso", "Deteniendo…", "busy", False, C["primary"]),
            "on": ("Corriendo" + (" (externo)" if s.external else ""),
                   f"Puerto {s.port}" + (f", {s.detail}" if s.detail else ""), "Detener", "danger", True, C["good"]),
            "off": ("Detenido", "Listo para iniciar" if ok else "No está instalado en este equipo",
                    "Iniciar" if ok else "Instalar", "filled" if ok else "tonal", True, C["muted"]),
        }[st]
        if len(detail) > 46:
            detail = detail[:45] + "…"
        self.itemconfig(self.state_t, text=state_txt, fill=color)
        self.itemconfig(self.detail_t, text=detail)
        self.btn.set(btn, kind, en)

    def spin(self):
        """La forma solo se mueve mientras el servicio está ocupado."""
        busy = self.svc.state in BUSY
        if not busy and not self._dirty:
            return
        self._dirty = False
        if busy:
            self.phase += 0.22
        amp = 0.10 + (0.05 * math.sin(self.phase * 1.5) if busy else 0)
        self.coords(self.cookie, *cookie(58, 54, 30, self.phase, amp))


class LogWindow(tk.Toplevel):
    def __init__(self, app, svc):
        super().__init__(app)
        self.path, self._sz = LOGS / f"{svc.key}.log", -1
        self.title(f"Logs de {svc.name}")
        self.geometry("780x460")
        self.configure(bg=C["bg"])
        tk.Label(self, text=svc.name, font=FT, fg=C["primary"], bg=C["bg"]).pack(anchor="w", padx=22, pady=(16, 8))
        self.txt = tk.Text(self, bg=C["card"], fg=C["text"], font=FMONO, relief="flat", bd=0, padx=16, pady=12,
                           wrap="none", highlightthickness=0, state="disabled")
        self.txt.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self._refresh()

    def _refresh(self):
        if not self.winfo_exists():
            return
        try:
            sz = self.path.stat().st_size
        except OSError:
            sz = -2
        if sz != self._sz:
            self._sz = sz
            if sz >= 0:
                with open(self.path, "rb") as f:
                    f.seek(max(0, sz - 60000))
                    text = f.read().decode("utf-8", "replace")
            else:
                text = "Sin logs todavía.\n\nSi el servicio se inició fuera del panel, su salida no se captura aquí."
            bottom = self.txt.yview()[1] > .98
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")
            self.txt.insert("end", text)
            self.txt.config(state="disabled")
            if bottom:
                self.txt.see("end")
        self.after(800, self._refresh)


# ─────────────────────────── Ajustes ───────────────────────────
OPENAI_PRESETS = {
    "Mistral": ("https://api.mistral.ai/v1", "Usa «Obtener modelos». La capa gratuita tiene límites bajos."),
    "Groq": ("https://api.groq.com/openai/v1", "Gratis con cupos diarios; usa «Obtener modelos»."),
    "OpenRouter": ("https://openrouter.ai/api/v1", "Cientos de modelos; los que terminan en :free tienen cupo diario chico."),
    "OpenAI": ("https://api.openai.com/v1", "Por ejemplo gpt-4o-mini; revisa qué modelos tiene tu cuenta."),
    "LM Studio (local)": ("http://localhost:1234/v1", "La API key puede quedar vacía."),
    "Personalizado": ("", "Cualquier servidor compatible con la API de OpenAI (vLLM y similares)."),
}
ANTHROPIC_MODELS = ["claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5-5"]
PROVIDERS = ("ollama", "anthropic", "openai")


class Settings(tk.Toplevel):
    WIDTH = 700

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Ajustes de ELIPSE")
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        top = tk.Frame(self, bg=C["bg"])
        top.pack(fill="x", padx=28, pady=(22, 10))
        tk.Label(top, text="Ajustes", font=FDS, fg=C["primary"], bg=C["bg"]).pack(side="left")
        tabs = tk.Frame(top, bg=C["bg"])
        tabs.pack(side="right")
        self.tab_btns = {}
        for k, t in (("model", "Modelo"), ("keys", "Conexión y llaves")):
            b = Pill(tabs, t, lambda k=k: self.show(k), height=36)
            b.pack(side="left", padx=(6, 0))
            self.tab_btns[k] = b
        self.body = tk.Frame(self, bg=C["bg"])
        self.body.pack(fill="both", expand=True, padx=28, pady=(0, 20))
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)
        self.tabs = {"model": self._build_model(), "keys": self._build_keys()}
        self.show("model")
        self._refresh_ollama()
        self._load_keys()
        self.after_idle(self._autosize)
        self.after(150, self._autosize)

    def _autosize(self):
        """Alto según el contenido (y nunca más alto que la pantalla)."""
        try:
            self.update_idletasks()
            h = min(self.winfo_reqheight(), self.winfo_screenheight() - 100)
            self.geometry(f"{self.WIDTH}x{h}")
        except tk.TclError:
            pass

    def show(self, k):
        self.tabs[k].tkraise()
        for name, b in self.tab_btns.items():
            b.set(kind="filled" if name == k else "tonal")

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.app.toast("Copiado al portapapeles")

    # ---- pestaña Modelo ----
    def _build_model(self):
        env = env_dict()
        f = tk.Frame(self.body, bg=C["bg"])
        f.grid(row=0, column=0, sticky="nsew")
        tk.Label(f, text="¿Qué usa ELIPSE para pensar?", font=FT, fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(4, 10))
        chips = tk.Frame(f, bg=C["bg"])
        chips.pack(anchor="w", pady=(0, 12))
        prov = env.get("DEFAULT_PROVIDER", "ollama").strip().lower()
        self.prov = tk.StringVar(value=prov if prov in PROVIDERS else "ollama")
        self.chips = {}
        for v, t in (("ollama", "Ollama, local"), ("anthropic", "Claude"), ("openai", "Compatible con OpenAI")):
            b = Pill(chips, t, lambda v=v: self._set_prov(v), height=38)
            b.pack(side="left", padx=(0, 8))
            self.chips[v] = b
        box = self.model_box = Surface(f)  # se ajusta solo al contenido más alto de sus páginas
        box.pack(fill="x")
        box.inner.grid_columnconfigure(0, weight=1)
        self.pages = {k: tk.Frame(box.inner, bg=C["card"]) for k in PROVIDERS}
        for p in self.pages.values():
            p.grid(row=0, column=0, sticky="nsew")

        # Ollama
        p = self.pages["ollama"]
        lbl(p, "Modelo de Ollama").pack(anchor="w")
        r = tk.Frame(p, bg=C["card"])
        r.pack(fill="x", pady=(4, 8))
        self.o_model = tk.StringVar(value=env.get("OLLAMA_MODEL", ""))
        self.o_combo = ttk.Combobox(r, textvariable=self.o_model, values=[])
        self.o_combo.pack(side="left", fill="x", expand=True)
        Pill(r, "↻", self._refresh_ollama, kind="ghost", width=44, height=36).pack(side="left", padx=(8, 0))
        self.o_status = lbl(p, "Buscando modelos instalados…", wrap=600)
        self.o_status.pack(anchor="w")

        # Anthropic
        p = self.pages["anthropic"]
        lbl(p, "API key de Anthropic").pack(anchor="w")
        r = tk.Frame(p, bg=C["card"])
        r.pack(fill="x", pady=(4, 12))
        self.a_key = tk.StringVar(value=env.get("ANTHROPIC_API_KEY", ""))
        e = field(r, self.a_key, show="•")
        e.pack(side="left", fill="x", expand=True, ipady=7)
        Pill(r, "Ver", lambda e=e: e.config(show="" if e.cget("show") else "•"), kind="ghost", width=60, height=36).pack(side="left", padx=(8, 0))
        lbl(p, "Modelo").pack(anchor="w")
        self.a_model = tk.StringVar(value=env.get("ANTHROPIC_MODEL", "claude-sonnet-5"))
        ttk.Combobox(p, textvariable=self.a_model, values=ANTHROPIC_MODELS).pack(fill="x", pady=(4, 6))
        lbl(p, "claude-haiku-4-5-20251001 es el más barato de la lista.").pack(anchor="w")

        # Compatible con OpenAI
        p = self.pages["openai"]
        base = env.get("OPENAI_BASE_URL", "")
        guess = next((n for n, (u, _) in OPENAI_PRESETS.items() if u and u == base), "Personalizado" if base else "Mistral")
        lbl(p, "Servicio").pack(anchor="w")
        self.x_preset = tk.StringVar(value=guess)
        pc = ttk.Combobox(p, textvariable=self.x_preset, values=list(OPENAI_PRESETS), state="readonly")
        pc.pack(fill="x", pady=(4, 8))
        pc.bind("<<ComboboxSelected>>", lambda e: self._apply_preset())
        lbl(p, "URL base").pack(anchor="w")
        self.x_base = tk.StringVar(value=base or OPENAI_PRESETS[guess][0])
        field(p, self.x_base).pack(fill="x", pady=(4, 8), ipady=7)
        lbl(p, "API key (vacía si el servidor es local)").pack(anchor="w")
        r = tk.Frame(p, bg=C["card"])
        r.pack(fill="x", pady=(4, 8))
        self.x_key = tk.StringVar(value=env.get("OPENAI_API_KEY", ""))
        e = field(r, self.x_key, show="•")
        e.pack(side="left", fill="x", expand=True, ipady=7)
        Pill(r, "Ver", lambda e=e: e.config(show="" if e.cget("show") else "•"), kind="ghost", width=60, height=36).pack(side="left", padx=(8, 0))
        lbl(p, "Modelo (obligatorio)").pack(anchor="w")
        r = tk.Frame(p, bg=C["card"])
        r.pack(fill="x", pady=(4, 6))
        self.x_model = tk.StringVar(value=env.get("OPENAI_MODEL", ""))
        self.x_combo = ttk.Combobox(r, textvariable=self.x_model)
        self.x_combo.pack(side="left", fill="x", expand=True)
        Pill(r, "Obtener modelos", self._fetch, kind="tonal", height=36).pack(side="left", padx=(8, 0))
        self.x_status = lbl(p, "")
        self.x_status.pack(anchor="w")
        self.x_checklist = tk.Text(p, height=1, bg=C["card_hi"], fg=C["text"], font=FMONO, relief="flat", bd=0,
                                   padx=10, pady=8, wrap="none", highlightthickness=0, state="disabled")
        for tag, color in (("ok", C["good"]), ("no", C["err"]), ("und", C["dim"])):
            self.x_checklist.tag_configure(tag, foreground=color)
        self.x_hint = lbl(p, OPENAI_PRESETS[guess][1], wrap=600)
        self.x_hint.pack(anchor="w", pady=(2, 0))

        Pill(f, "Guardar y aplicar", self._save, kind="filled", height=46).pack(anchor="e", pady=(14, 0))
        self._set_prov(self.prov.get())
        return f

    def _set_prov(self, v):
        self.prov.set(v)
        self.pages[v].tkraise()
        for k, b in self.chips.items():
            b.set(kind="filled" if k == v else "tonal")

    def _refresh_ollama(self):
        """«ollama list» tarda; se hace en un hilo para que la ventana nunca se congele."""
        self.o_status.config(text="Buscando modelos instalados…")

        def run():
            ui(self._got_ollama, list_ollama_models())
        threading.Thread(target=run, daemon=True).start()

    def _got_ollama(self, models):
        if not self.winfo_exists():
            return
        self.o_combo["values"] = models
        self.o_status.config(
            text=(f"{len(models)} modelos instalados. Elige uno de la lista o escríbelo igual que en «ollama pull»."
                  if models else
                  "No pude leer «ollama list» (¿Ollama está apagado o sin modelos?). Puedes escribir el nombre a mano."))

    def _apply_preset(self):
        url, hint = OPENAI_PRESETS[self.x_preset.get()]
        if url:
            self.x_base.set(url)
        self.x_hint.config(text=hint)

    def _fetch(self):
        base, key = self.x_base.get().strip(), self.x_key.get().strip()
        if not base:
            messagebox.showerror("Falta la URL base", "Escribe la URL base del servicio primero.", parent=self)
            return
        self.x_status.config(text="Consultando…", fg=C["muted"])
        self.x_checklist.pack_forget()
        self.model_box.fit()
        self._autosize()

        def run():
            try:
                ui(self._got_models, fetch_models(base, key), None)
            except Exception as e:
                ui(self._got_models, [], str(e))
        threading.Thread(target=run, daemon=True).start()

    def _got_models(self, models, err):
        if not self.winfo_exists():
            return
        if err or not models:
            self.x_status.config(text=err or "El servidor no devolvió modelos.", fg=C["err"] if err else C["muted"])
            return
        self.x_combo["values"] = models
        if self.x_model.get().strip() not in models:
            self.x_model.set(models[0])
        self.x_status.config(text=f"{len(models)} modelos encontrados. Verificando cuáles soportan tool-calling…", fg=C["good"])
        self._check_tool_support(models)

    def _check_tool_support(self, models):
        """Prueba cada modelo con una llamada real y mínima (max_tokens=1) para ver si el
        servidor acepta el parámetro "tools". Se corre en paralelo (4 a la vez) para no tardar
        una eternidad con listas largas."""
        base, key = self.x_base.get().strip(), self.x_key.get().strip()
        icon = {True: ("✓", "ok"), False: ("✗", "no"), None: ("?", "und")}
        results = {}
        self.x_checklist.pack(fill="x", pady=(6, 8))
        self._render_checklist(models, results, icon)

        def worker():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(check_tool_support, base, key, m): m for m in models}
                for fut in concurrent.futures.as_completed(futs):
                    m = futs[fut]
                    try:
                        results[m] = fut.result()
                    except Exception:
                        results[m] = None
                    ui(self._render_checklist, models, dict(results), icon)
            ui(self._checklist_done, len(models))
        threading.Thread(target=worker, daemon=True).start()

    def _render_checklist(self, models, results, icon):
        if not self.winfo_exists():
            return
        self.x_checklist.config(state="normal", height=min(len(models), 10))
        self.x_checklist.delete("1.0", "end")
        for m in models:
            if m in results:
                mark, tag = icon[results[m]]
            else:
                mark, tag = "…", "und"
            self.x_checklist.insert("end", f"{mark}  ", tag)
            self.x_checklist.insert("end", f"{m}\n")
        self.x_checklist.config(state="disabled")
        self.model_box.fit()
        self._autosize()

    def _checklist_done(self, total):
        if not self.winfo_exists():
            return
        self.x_status.config(text=f"{total} modelos encontrados.", fg=C["good"])

    def _save(self):
        p, up = self.prov.get(), {"DEFAULT_PROVIDER": self.prov.get()}
        if p == "ollama":
            if self.o_model.get().strip():
                up["OLLAMA_MODEL"] = self.o_model.get().strip()
        elif p == "anthropic":
            if not self.a_key.get().strip():
                messagebox.showerror("Falta la API key", "Ingresa tu API key de Anthropic.", parent=self)
                return
            up.update(ANTHROPIC_API_KEY=self.a_key.get().strip(), ANTHROPIC_MODEL=self.a_model.get().strip() or "claude-sonnet-5")
        else:
            if not self.x_model.get().strip():
                messagebox.showerror("Falta el modelo", "OPENAI_MODEL es obligatorio.", parent=self)
                return
            up.update(OPENAI_BASE_URL=self.x_base.get().strip() or "https://api.openai.com/v1",
                      OPENAI_API_KEY=self.x_key.get().strip(), OPENAI_MODEL=self.x_model.get().strip())
        try:
            env_update(up)
        except Exception as e:
            messagebox.showerror("No se pudo guardar", str(e), parent=self)
            return
        self.app.toast("Guardado en .env")
        core = self.app.svc["elipse"]
        if core.state == "on" and messagebox.askyesno("Reiniciar ELIPSE Core",
                                                       "ELIPSE Core está corriendo. ¿Reiniciarlo para aplicar los cambios?", parent=self):
            core.restart()

    # ---- pestaña Conexión y llaves ----
    def _build_keys(self):
        f = tk.Frame(self.body, bg=C["bg"])
        f.grid(row=0, column=0, sticky="nsew")
        tk.Label(f, text="Conexión", font=FT, fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(4, 8))
        s = Surface(f)
        s.pack(fill="x")
        i = s.inner
        lbl(i, "URL de la API para clientes (web, teléfono)").pack(anchor="w")
        r = tk.Frame(i, bg=C["card"])
        r.pack(fill="x", pady=(4, 8))
        ip, lan = lan_ip(), bool(load_prefs().get("lan"))
        opts = [f"http://127.0.0.1:{PORT}"] + ([f"http://{ip}:{PORT}"] if ip else [])
        self.url = tk.StringVar(value=opts[-1] if lan else opts[0])
        ttk.Combobox(r, textvariable=self.url, values=opts).pack(side="left", fill="x", expand=True)
        Pill(r, "Copiar", lambda: self._copy(self.url.get()), height=36).pack(side="left", padx=(8, 0))
        self.lan = tk.BooleanVar(value=lan)
        tk.Checkbutton(i, text="Permitir otros dispositivos de mi red", variable=self.lan, command=self._lan_changed,
                       bg=C["card"], fg=C["text"], selectcolor=C["card_hi"], activebackground=C["card"],
                       activeforeground=C["text"], font=FB).pack(anchor="w")
        lbl(i, "Apagado, ELIPSE solo escucha en esta PC. Encendido, cualquiera de tu wifi con una llave válida puede usarlo.",
            wrap=600).pack(anchor="w")

        tk.Label(f, text="Llaves de ELIPSE", font=FT, fg=C["text"], bg=C["bg"]).pack(anchor="w", pady=(16, 6))
        bar = tk.Frame(f, bg=C["bg"])
        bar.pack(anchor="w", pady=(0, 8))
        for t, cmd, k in (("Actualizar", self._load_keys, "tonal"), ("Nueva llave", self._new_key, "filled"),
                          ("Revocar", self._revoke, "danger")):
            Pill(bar, t, cmd, kind=k, height=36).pack(side="left", padx=(0, 8))
        cols = (("id", "ID", 44), ("nombre", "Nombre", 130), ("estado", "Estado", 90), ("creada", "Creada", 160), ("uso", "Último uso", 160))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=6)
        for c, t, w in cols:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x")
        self.kstatus = lbl(f, "Una llave ya creada no se puede volver a ver, solo se guarda su hash. Para rotarla, crea otra y revoca la vieja.", wrap=640)
        self.kstatus.pack(anchor="w", pady=(8, 0))
        return f

    def _lan_changed(self):
        p = load_prefs()
        p["lan"] = self.lan.get()
        save_prefs(p)
        self.app.toast("Reinicia ELIPSE Core para aplicar el cambio de red" if self.app.svc["elipse"].state == "on" else "Preferencia guardada")

    def _load_keys(self):
        def run():
            try:
                ui(self._populate, run_keys(["list"]).get("keys", []), None)
            except Exception as e:
                ui(self._populate, [], str(e))
        threading.Thread(target=run, daemon=True).start()

    def _populate(self, keys, err):
        if not self.winfo_exists():
            return
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for k in keys:
            self.tree.insert("", "end", values=(k.get("id"), k.get("name"), "revocada" if k.get("revoked") else "activa",
                                                k.get("created_at") or "", k.get("last_used_at") or "nunca"))
        if err:
            self.kstatus.config(text=f"No se pudieron leer las llaves: {err}", fg=C["err"])
        else:
            self.kstatus.config(text="Una llave ya creada no se puede volver a ver, solo se guarda su hash. Para rotarla, crea otra y revoca la vieja.",
                                fg=C["muted"])

    def _new_key(self):
        name = simpledialog.askstring("Nueva llave", "Nombre del dispositivo o cliente (por ejemplo, telefono):", parent=self)
        if not name:
            return

        def run():
            try:
                ui(self._show_key, run_keys(["create", name]).get("api_key", ""))
            except Exception as e:
                ui(messagebox.showerror, "No se pudo crear la llave", str(e))
            ui(self._load_keys)
        threading.Thread(target=run, daemon=True).start()

    def _show_key(self, raw):
        w = tk.Toplevel(self)
        w.title("Nueva llave")
        w.configure(bg=C["bg"])
        w.resizable(False, False)
        tk.Label(w, text="Guárdala ahora, no se vuelve a mostrar", font=FT, fg=C["primary"], bg=C["bg"]).pack(padx=24, pady=(20, 10))
        e = tk.Entry(w, width=54, font=FMONO, bg=C["card_hi"], fg=C["text"], relief="flat", readonlybackground=C["card_hi"])
        e.insert(0, raw)
        e.config(state="readonly")
        e.pack(padx=24, ipady=8)
        Pill(w, "Copiar", lambda: self._copy(raw), kind="filled").pack(pady=18)

    def _revoke(self):
        sel = self.tree.selection()
        if not sel:
            self.app.toast("Selecciona una llave de la lista primero")
            return
        kid, name = self.tree.item(sel[0], "values")[:2]
        if not messagebox.askyesno("Revocar llave", f"¿Revocar la llave «{name}» (id {kid})? No se puede deshacer.", parent=self):
            return

        def run():
            try:
                if not run_keys(["revoke", str(kid)]).get("ok"):
                    ui(self.app.toast, "No existe una llave con ese id")
            except Exception as e:
                ui(messagebox.showerror, "No se pudo revocar", str(e))
            ui(self._load_keys)
        threading.Thread(target=run, daemon=True).start()


# ─────────────────────────── Ventana principal ───────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ELIPSE")
        self.geometry("640x560")
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        style_ttk(self)
        self._closing, self._toast_id, self._win, self._errs = False, None, {}, 0
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.services = [
            Service(self, "ollama", "Ollama", OLLAMA_PORT, "", launch_ollama, kill_ollama, installed=lambda: bool(find_ollama())),
            Service(self, "elipse", "ELIPSE Core", PORT, "/v1/status", launch_elipse, lambda: kill_port(PORT)),
        ]
        self.svc = {s.key: s for s in self.services}

        hd = tk.Canvas(self, width=640, height=112, bg=C["bg"], highlightthickness=0)
        hd.pack()
        hd.create_polygon(cookie(62, 58, 32, amp=0.12), fill=C["primary_c"], outline="")
        hd.create_polygon([c for x, y in BOLT for c in (62 + x * 1.1, 58 + y * 1.1)], fill=C["warm"], outline="")
        hd.create_text(112, 48, anchor="w", text="ELIPSE", font=FD, fill=C["primary"])
        hd.create_text(114, 84, anchor="w", text="Panel de control", font=FB, fill=C["muted"])
        hd.create_window(620, 58, window=Pill(hd, "Ajustes", self.open_settings, height=42), anchor="e")

        for s in self.services:
            s.card = Card(self, s)
            s.card.pack(pady=7)
            s.card.refresh()

        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x", padx=20, pady=(12, 0))
        self.all_btn = Pill(bar, "Iniciar todo", self.toggle_all, kind="filled", width=170, height=48)
        self.all_btn.pack(side="right")
        Pill(bar, "Abrir consola", self.open_console, kind="tonal", height=48).pack(side="right", padx=10)
        lbl(self, "Al cerrar el panel, lo que iniciaste aquí sigue corriendo. Usa Detener para apagarlo.",
            fg=C["dim"], wrap=600).pack(anchor="w", padx=24, pady=(16, 0))

        self.toast_lbl = tk.Label(self, bg="#322F35", fg=C["text"], font=FB, padx=20, pady=11)
        self._pump()
        self._tick()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # -- errores: nunca mueren en silencio --
    def report_callback_exception(self, exc, val, tb):
        msg = "".join(traceback.format_exception(exc, val, tb))
        traceback.print_exception(exc, val, tb)
        try:
            LOGS.mkdir(exist_ok=True)
            with open(LOGS / "panel_error.log", "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{msg}")
        except Exception:
            pass
        self._errs += 1
        if self._errs <= 3:  # evita una lluvia de ventanas si el error se repite
            try:
                messagebox.showerror("Error en el panel",
                                     f"{val}\n\nDetalle guardado en logs/panel_error.log\n\n{msg[-900:]}")
            except Exception:
                pass

    # -- motores internos --
    def _pump(self):
        try:
            while True:
                fn, args = UIQ.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    self.report_callback_exception(*sys.exc_info())
        except queue.Empty:
            pass
        self.after(60, self._pump)

    def _tick(self):
        for s in self.services:
            s.card.spin()
        self.after(40, self._tick)

    def _poll_loop(self):
        while not self._closing:
            for s in self.services:
                ui(s.observe, probe(s.port, s.path))
            time.sleep(2.5)

    def on_change(self, svc):
        svc.card.refresh()
        busy = any(s.state in BUSY for s in self.services)
        every = all(s.state == "on" for s in self.services)
        self.all_btn.set("Detener todo" if every else "Iniciar todo", "danger" if every else "filled", not busy)

    # -- acciones --
    def toggle_all(self):
        if any(s.state in BUSY for s in self.services):
            return
        if all(s.state == "on" for s in self.services):
            for s in self.services:
                s.stop()
        else:
            for s in self.services:
                if s.state == "off" and s.installed():
                    s.start()

    def open_settings(self):
        self._single("settings", lambda: Settings(self))

    def open_logs(self, svc):
        self._single("log_" + svc.key, lambda: LogWindow(self, svc))

    def _single(self, key, make):
        w = self._win.get(key)
        if w and w.winfo_exists():
            w.lift()
        else:
            self._win[key] = make()

    def open_console(self):
        p = ROOT / CLIENT_FILE
        if p.exists():
            webbrowser.open(p.as_uri())
        else:
            self.toast(f"No encontré {CLIENT_FILE} junto al panel")

    def install_ollama(self):
        if not IS_WIN:
            webbrowser.open("https://ollama.com/download")
            return
        self.toast("Descargando el instalador de Ollama…")

        def run():
            try:
                path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
                urllib.request.urlretrieve(OLLAMA_INSTALLER, path)
                subprocess.Popen([path])
                ui(self.toast, "Instalador abierto. Sigue los pasos en esa ventana.")
            except Exception as e:
                ui(messagebox.showerror, "No se pudo descargar Ollama", f"{e}\n\nDescárgalo desde https://ollama.com/download")
        threading.Thread(target=run, daemon=True).start()

    def toast(self, msg):
        self.toast_lbl.config(text=msg)
        self.toast_lbl.place(relx=.5, rely=1.0, y=-22, anchor="s")
        self.toast_lbl.lift()
        if self._toast_id:
            self.after_cancel(self._toast_id)
        self._toast_id = self.after(3600, self.toast_lbl.place_forget)

    def close(self):
        self._closing = True
        self.destroy()


if __name__ == "__main__":
    App().mainloop()