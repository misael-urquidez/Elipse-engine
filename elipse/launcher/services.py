"""
Service: máquina de estados de un servicio (Ollama / ELIPSE Core) —
checking → off | on ; off → starting → on ; on → stopping → off.
Card: su representación visual (Canvas con la forma "cookie" animada).
"""

import json
import math
import subprocess
import threading
import time
import tkinter as tk

from .network import probe
from .theme import C, BUSY, FBB, FDS, FS, FT, cookie, mix, rrect, tween
from .uiqueue import ui
from .widgets import Pill


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
        from .paths import LOGS
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
