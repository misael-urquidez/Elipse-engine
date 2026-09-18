"""
ELIPSE Control Panel
---------------------
Panel tipo XAMPP para arrancar/detener Ollama y el backend de ELIPSE juntos,
con estado en vivo y logs por servicio. Pensado principalmente para Windows
(instalador .exe de Ollama, subprocesos sin consola extra), pero degrada
razonablemente en Linux/Mac (ahí el botón de instalar solo abre el navegador).

CÓMO USARLO
-----------
1. Copia este archivo en la RAÍZ del proyecto ELIPSE (al mismo nivel que la
   carpeta `app/`, `elipse.db`, `.env`, `workspace/` — no dentro de `app/`).
2. Corre:  python elipse_control_panel.py
3. Si Ollama no está instalado, aparece un botón para descargarlo e instalarlo.
4. Dale "Iniciar" a Ollama primero, y luego a ELIPSE Core (o al revés, ELIPSE
   igual falla con un mensaje claro si Ollama todavía no respondió).

Para convertir esto en un .exe de doble clic más adelante (consistente con tu
Fase 6): `pip install pyinstaller` y luego
`pyinstaller --onefile --windowed elipse_control_panel.py`
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox
import urllib.request
import webbrowser

IS_WINDOWS = platform.system() == "Windows"

OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
OLLAMA_HEALTH_URL = "http://127.0.0.1:11434"
ELIPSE_HEALTH_URL = "http://127.0.0.1:8000/v1/status"

# Carpeta donde vive este script == raíz del proyecto (padre de app/).
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_python_exe():
    """
    Usa el Python del venv del proyecto si existe (carpeta 'venv/' junto a
    este script) — que es donde probablemente están instalados fastapi,
    uvicorn, ollama, etc. Si no hay venv, cae de vuelta a sys.executable
    (el Python con el que se corrió este control panel).
    """
    if IS_WINDOWS:
        candidate = os.path.join(PROJECT_DIR, "venv", "Scripts", "python.exe")
    else:
        candidate = os.path.join(PROJECT_DIR, "venv", "bin", "python")
    if os.path.exists(candidate):
        return candidate
    return sys.executable

BG = "#16130f"
PANEL = "#1d1811"
BORDER = "#332a1c"
TEXT = "#edeae2"
MUTED = "#93897a"
ACCENT = "#e8a33d"
GOOD = "#74b788"
BAD = "#d9694f"


def find_ollama_exe():
    """Busca el ejecutable de ollama en PATH o en la ruta típica de instalación en Windows."""
    path = shutil.which("ollama")
    if path:
        return path
    if IS_WINDOWS:
        candidate = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if os.path.exists(candidate):
            return candidate
    return None


def url_is_up(url, timeout=1.5):
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def kill_process_on_port(port):
    """Mata el proceso que esté escuchando en `port`, sin importar quién lo lanzó."""
    if IS_WINDOWS:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    else:
        subprocess.run(f"lsof -ti:{port} | xargs -r kill -9", shell=True, capture_output=True)


class ServiceRow:
    """Una fila del panel: nombre, lucecita de estado, botones y su propio log."""

    def __init__(self, root, container, row_index, name, health_url, start_cmd_fn, external_kill_fn=None):
        self.root = root
        self.name = name
        self.health_url = health_url
        self.start_cmd_fn = start_cmd_fn  # función que devuelve el Popen ya lanzado
        self.external_kill_fn = external_kill_fn  # cómo apagar el servicio si NO lo lanzamos nosotros
        self.process = None
        self.adopted = False  # True si detectamos que ya estaba corriendo, sin lanzarlo nosotros
        self.log_lines = []
        self._log_window = None
        self._log_text_widget = None

        self.dot = tk.Canvas(container, width=14, height=14, highlightthickness=0, bg=PANEL)
        self.dot_id = self.dot.create_oval(2, 2, 12, 12, fill=MUTED, outline="")
        self.dot.grid(row=row_index, column=0, padx=(12, 8), pady=8)

        tk.Label(container, text=name, bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold"),
                 width=14, anchor="w").grid(row=row_index, column=1, sticky="w")

        self.status_label = tk.Label(container, text="detenido", bg=PANEL, fg=MUTED,
                                      width=13, anchor="w", font=("Segoe UI", 9))
        self.status_label.grid(row=row_index, column=2, sticky="w")

        self.start_btn = tk.Button(container, text="Iniciar", width=9, command=self.start)
        self.start_btn.grid(row=row_index, column=3, padx=3)

        self.stop_btn = tk.Button(container, text="Detener", width=9, command=self.stop, state="disabled")
        self.stop_btn.grid(row=row_index, column=4, padx=3)

        self.logs_btn = tk.Button(container, text="Logs", width=7, command=self.show_logs)
        self.logs_btn.grid(row=row_index, column=5, padx=(3, 12))

    def set_dot(self, color):
        self.dot.itemconfig(self.dot_id, fill=color)

    # ---- ciclo de vida ----

    def start(self):
        if self.process is not None or self.adopted:
            return
        self.status_label.config(text="verificando...", fg=ACCENT)
        self.set_dot(ACCENT)
        self.start_btn.config(state="disabled")
        threading.Thread(target=self._start_thread, daemon=True).start()

    def _start_thread(self):
        if url_is_up(self.health_url):
            # Ya hay algo respondiendo en esa dirección (ej. Ollama corriendo como
            # app de fondo) — lo adoptamos en vez de lanzar un segundo proceso
            # que solo va a chocar por el puerto ya ocupado.
            self.adopted = True
            self.log_lines.append(
                f"[info] {self.name} ya estaba corriendo en {self.health_url} — "
                "no se lanzó un proceso nuevo, solo se detectó."
            )
            self.root.after(0, lambda: self._mark_running(adopted=True))
            return
        try:
            self.process = self.start_cmd_fn()
        except Exception as e:
            self.log_lines.append(f"[error al lanzar el proceso] {e}")
            self.root.after(0, self._mark_stopped)
            return
        threading.Thread(target=self._read_output, daemon=True).start()
        threading.Thread(target=self._watch_health, daemon=True).start()

    def _read_output(self):
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            self.log_lines.append(line.rstrip())
            if len(self.log_lines) > 500:
                self.log_lines = self.log_lines[-500:]
        # el proceso terminó (o crasheó) y ya no hay más salida que leer
        self.root.after(0, self._check_alive)

    def _check_alive(self):
        if self.process and self.process.poll() is not None:
            self._mark_stopped()

    def _watch_health(self):
        # Espera hasta ~30s a que el health check responda antes de marcar "corriendo".
        for _ in range(60):
            if url_is_up(self.health_url):
                self.root.after(0, self._mark_running)
                return
            if self.process and self.process.poll() is not None:
                self.root.after(0, self._mark_stopped)
                return
            time.sleep(0.5)
        # No confirmó salud en 30s, pero el proceso sigue vivo: no lo damos por
        # muerto, solo avisamos que tarda (puede ser una PC lenta cargando el modelo).
        if self.process and self.process.poll() is None:
            self.root.after(0, lambda: self._mark_running(unconfirmed=True))

    def _mark_running(self, unconfirmed=False, adopted=False):
        if adopted:
            text = "corriendo (externo)"
        elif unconfirmed:
            text = "corriendo (?)"
        else:
            text = "corriendo"
        self.status_label.config(text=text, fg=GOOD)
        self.set_dot(GOOD)
        self.stop_btn.config(state="normal")

    def _mark_stopped(self):
        self.status_label.config(text="detenido", fg=MUTED)
        self.set_dot(MUTED)
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.process = None
        self.adopted = False

    def stop(self):
        self.stop_btn.config(state="disabled")
        threading.Thread(target=self._stop_thread, daemon=True).start()

    def _stop_thread(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=8)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        elif self.adopted:
            if self.external_kill_fn:
                try:
                    self.external_kill_fn()
                except Exception as e:
                    self.log_lines.append(f"[error al detener externamente] {e}")
            else:
                self.log_lines.append(
                    f"[aviso] {self.name} no fue iniciado por este panel, no se puede detener desde aquí."
                )
                self.root.after(0, lambda: self._mark_running(adopted=True))
                return
        self.root.after(0, self._mark_stopped)

    # ---- logs ----

    def show_logs(self):
        if self._log_window and self._log_window.winfo_exists():
            self._log_window.lift()
            return
        win = tk.Toplevel(self.root)
        self._log_window = win
        win.title(f"Logs — {self.name}")
        win.geometry("680x420")
        win.configure(bg=BG)
        text = tk.Text(win, bg=BG, fg=TEXT, insertbackground=TEXT, font=("Consolas", 9))
        text.pack(fill="both", expand=True, padx=6, pady=6)
        self._log_text_widget = text
        self._refresh_logs()

    def _refresh_logs(self):
        win = self._log_window
        if not win or not win.winfo_exists():
            return
        text = self._log_text_widget
        text.delete("1.0", "end")
        text.insert("end", "\n".join(self.log_lines[-500:]) or "(sin salida todavía)")
        text.see("end")
        win.after(1000, self._refresh_logs)


class ControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ELIPSE Control Panel")
        self.geometry("640x300")
        self.configure(bg=BG)
        self.resizable(False, False)

        tk.Label(self, text="ELIPSE Control Panel", bg=BG, fg=ACCENT,
                 font=("Georgia", 16)).pack(pady=(16, 2))
        tk.Label(self, text="Arranca Ollama y el motor de ELIPSE juntos, con un click.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(pady=(0, 12))

        grid = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        grid.pack(padx=16, fill="x")

        self.ollama_row = ServiceRow(self, grid, 0, "Ollama", OLLAMA_HEALTH_URL, self.launch_ollama,
                                      external_kill_fn=self.kill_ollama_external)
        tk.Frame(grid, bg=BORDER, height=1).grid(row=1, column=0, columnspan=6, sticky="ew")
        self.elipse_row = ServiceRow(self, grid, 2, "ELIPSE Core", ELIPSE_HEALTH_URL, self.launch_elipse,
                                      external_kill_fn=lambda: kill_process_on_port(8000))

        for i in range(3):
            grid.grid_rowconfigure(i, minsize=8)

        # ---- instalación de Ollama si falta ----
        self.install_frame = tk.Frame(self, bg=BG)
        self.install_frame.pack(pady=14)
        self.install_btn = tk.Button(self.install_frame, text="⬇ Descargar e instalar Ollama",
                                      command=self.install_ollama)
        self.install_label = tk.Label(self.install_frame, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.check_ollama_installed()

        tk.Label(
            self,
            text=(
                "Al cerrar esta ventana, los servicios que iniciaste aquí siguen corriendo en "
                "segundo plano (igual que XAMPP) — usa 'Detener' antes si quieres apagarlos."
            ),
            bg=BG, fg="#555", wraplength=600, justify="left", font=("Segoe UI", 8),
        ).pack(padx=16, pady=(4, 10))

    # ---- instalación ----

    def check_ollama_installed(self):
        if find_ollama_exe():
            self.install_btn.pack_forget()
            self.install_label.config(text="Ollama ya está instalado ✓", fg=GOOD)
            self.install_label.pack()
        else:
            self.install_label.pack_forget()
            self.install_btn.pack()

    def install_ollama(self):
        if not IS_WINDOWS:
            webbrowser.open("https://ollama.com/download")
            return
        self.install_btn.config(state="disabled", text="Descargando...")
        threading.Thread(target=self._install_ollama_thread, daemon=True).start()

    def _install_ollama_thread(self):
        try:
            installer_path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
            urllib.request.urlretrieve(OLLAMA_INSTALLER_URL, installer_path)
            subprocess.Popen([installer_path])
            self.after(0, lambda: self.install_label.config(
                text="Instalador abierto — sigue los pasos en esa ventana.", fg=ACCENT))
            self.after(0, self.install_label.pack)
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: messagebox.showerror(
                "No se pudo descargar Ollama",
                f"{msg}\n\nDescárgalo manual desde https://ollama.com/download"))
        finally:
            self.after(0, lambda: self.install_btn.config(state="normal", text="⬇ Descargar e instalar Ollama"))
            self.after(3000, self.check_ollama_installed)

    # ---- lanzadores de proceso ----

    def kill_ollama_external(self):
        """
        Apaga Ollama cuando corre como app de fondo (no lo lanzamos nosotros,
        así que no tenemos el objeto Popen — hay que matarlo por nombre).
        En Windows, Ollama corre como 'ollama.exe' y a veces también hay una
        'ollama app.exe' de bandeja del sistema que lo vuelve a levantar.
        """
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
            subprocess.run(["taskkill", "/F", "/IM", "ollama app.exe"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)

    def launch_ollama(self):
        exe = find_ollama_exe() or "ollama"
        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        return subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=creationflags,
        )

    def launch_elipse(self):
        if not os.path.isdir(os.path.join(PROJECT_DIR, "app")):
            messagebox.showerror(
                "No encuentro el proyecto",
                f"No hay una carpeta 'app/' junto a este script en:\n{PROJECT_DIR}\n\n"
                "Copia elipse_control_panel.py a la raíz de tu proyecto ELIPSE "
                "(al mismo nivel que la carpeta app/).",
            )
            raise RuntimeError("carpeta app/ no encontrada junto al control panel")
        creationflags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
        python_exe = find_python_exe()
        return subprocess.Popen(
            [python_exe, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
            cwd=PROJECT_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=creationflags,
        )


if __name__ == "__main__":
    ControlPanel().mainloop()