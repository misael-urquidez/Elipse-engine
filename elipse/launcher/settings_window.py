"""
Ventana de Ajustes: pestaña "Modelo" (proveedor + modelo, con detección
de Ollama y chequeo real de tool-calling) y pestaña "Conexión y llaves"
(URL de red y API keys).
"""

import concurrent.futures
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk

from .env_store import env_dict, env_update, load_prefs, save_prefs
from .network import (
    check_gemini_tool_support,
    check_tool_support,
    fetch_gemini_models,
    fetch_models,
    list_ollama_models,
)
from .process_utils import lan_ip, run_keys
from .paths import PORT
from .theme import C, FB, FBB, FDS, FMONO, FS, FT
from .uiqueue import ui
from .widgets import Pill, Surface, field, lbl

OPENAI_PRESETS = {
    "Mistral": ("https://api.mistral.ai/v1", "Usa «Obtener modelos». La capa gratuita tiene límites bajos."),
    "Groq": ("https://api.groq.com/openai/v1", "Gratis con cupos diarios; usa «Obtener modelos»."),
    "OpenRouter": ("https://openrouter.ai/api/v1", "Cientos de modelos; los que terminan en :free tienen cupo diario chico."),
    "OpenAI": ("https://api.openai.com/v1", "Por ejemplo gpt-4o-mini; revisa qué modelos tiene tu cuenta."),
    "LM Studio (local)": ("http://localhost:1234/v1", "La API key puede quedar vacía."),
    "Personalizado": ("", "Cualquier servidor compatible con la API de OpenAI (vLLM y similares)."),
}
ANTHROPIC_MODELS = ["claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5-5"]
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]
PROVIDERS = ("ollama", "anthropic", "openai", "gemini")


class Settings(tk.Toplevel):
    WIDTH = 780

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
        for v, t in (
            ("ollama", "Ollama, local"),
            ("anthropic", "Claude"),
            ("openai", "Compatible con OpenAI"),
            ("gemini", "Gemini"),
        ):
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

        # Gemini (Google AI)
        p = self.pages["gemini"]
        lbl(p, "API key de Google AI (Gemini)").pack(anchor="w")
        r = tk.Frame(p, bg=C["card"])
        r.pack(fill="x", pady=(4, 12))
        self.g_key = tk.StringVar(value=env.get("GEMINI_API_KEY", ""))
        e = field(r, self.g_key, show="•")
        e.pack(side="left", fill="x", expand=True, ipady=7)
        Pill(r, "Ver", lambda e=e: e.config(show="" if e.cget("show") else "•"), kind="ghost", width=60, height=36).pack(side="left", padx=(8, 0))
        lbl(p, "Modelo").pack(anchor="w")
        r = tk.Frame(p, bg=C["card"])
        r.pack(fill="x", pady=(4, 6))
        self.g_model = tk.StringVar(value=env.get("GEMINI_MODEL", "gemini-2.5-flash"))
        self.g_combo = ttk.Combobox(r, textvariable=self.g_model, values=GEMINI_MODELS)
        self.g_combo.pack(side="left", fill="x", expand=True)
        Pill(r, "Obtener modelos", self._fetch_gemini, kind="tonal", height=36).pack(side="left", padx=(8, 0))
        self.g_status = lbl(p, "Usá «Obtener modelos» para listar lo que permite tu API key.", wrap=600)
        self.g_status.pack(anchor="w")
        self.g_checklist = tk.Text(p, height=1, bg=C["card_hi"], fg=C["text"], font=FMONO, relief="flat", bd=0,
                                   padx=10, pady=8, wrap="none", highlightthickness=0, state="disabled")
        for tag, color in (("ok", C["good"]), ("no", C["err"]), ("und", C["dim"])):
            self.g_checklist.tag_configure(tag, foreground=color)
        lbl(p, "✓ = soporta tools (necesario para ELIPSE). ✗ = no. ? = no se pudo comprobar.", wrap=600).pack(anchor="w", pady=(2, 0))

        Pill(f, "Guardar y aplicar", self._save, kind="filled", height=46).pack(anchor="e", pady=(14, 0))
        self._set_prov(self.prov.get())
        return f

    def _set_prov(self, v):
        self.prov.set(v)
        self.pages[v].tkraise()
        for k, b in self.chips.items():
            b.set(kind="filled" if k == v else "tonal")

    def _refresh_ollama(self):
        """«ollama list» / la API de Ollama tardan; se hace en un hilo para que la
        ventana nunca se congele."""
        self.o_status.config(text="Buscando modelos instalados…", fg=C["muted"])

        def run():
            models, notes = list_ollama_models()
            ui(self._got_ollama, models, notes)
        threading.Thread(target=run, daemon=True).start()

    def _got_ollama(self, models, notes):
        if not self.winfo_exists():
            return
        self.o_combo["values"] = models
        current = self.o_model.get().strip()
        if models:
            msg = f"{len(models)} modelos instalados. Elige uno de la lista o escríbelo igual que en «ollama pull»."
            if current and current not in models:
                # sugerir el más parecido (mismo prefijo) o el primero
                hint = next((m for m in models if current.split(":")[0] in m), models[0])
                msg += f"\n«{current}» no está instalado. ¿Quizá quisiste «{hint}»?"
            self.o_status.config(text=msg, fg=C["muted"])
        else:
            # Diagnóstico real en vez de un genérico "¿apagado?": esto es lo que
            # antes se perdía silenciosamente y hacía parecer roto el detector
            # en Linux cuando en realidad Ollama sí estaba corriendo.
            detail = (" — " + notes[-1]) if notes else ""
            self.o_status.config(
                text="No pude listar modelos instalados" + detail
                + ".\nEscribí el nombre a mano, p. ej. qwen2.5-coder:3b, o dale a ↻ de nuevo.",
                fg=C["err"] if notes else C["muted"],
            )

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

    def _fetch_gemini(self):
        key = self.g_key.get().strip()
        if not key:
            messagebox.showerror("Falta la API key", "Ingresa tu API key de Google AI primero.", parent=self)
            return
        self.g_status.config(text="Consultando modelos de Gemini…", fg=C["muted"])
        self.g_checklist.pack_forget()
        self.model_box.fit()
        self._autosize()

        def run():
            try:
                ui(self._got_gemini_models, fetch_gemini_models(key), None)
            except Exception as e:
                ui(self._got_gemini_models, [], str(e))
        threading.Thread(target=run, daemon=True).start()

    def _got_gemini_models(self, models, err):
        if not self.winfo_exists():
            return
        if err or not models:
            self.g_status.config(
                text=err or "La API no devolvió modelos con generateContent.",
                fg=C["err"] if err else C["muted"],
            )
            return
        self.g_combo["values"] = models
        if self.g_model.get().strip() not in models:
            self.g_model.set(models[0])
        self.g_status.config(
            text=f"{len(models)} modelos encontrados. Verificando cuáles soportan tool-calling…",
            fg=C["good"],
        )
        self._check_gemini_tool_support(models)

    def _check_gemini_tool_support(self, models):
        """Prueba cada modelo Gemini con generateContent + functionDeclarations (en paralelo)."""
        key = self.g_key.get().strip()
        icon = {True: ("✓", "ok"), False: ("✗", "no"), None: ("?", "und")}
        results = {}
        self.g_checklist.pack(fill="x", pady=(6, 8))
        self._render_gemini_checklist(models, results, icon)

        def worker():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(check_gemini_tool_support, key, m): m for m in models}
                for fut in concurrent.futures.as_completed(futs):
                    m = futs[fut]
                    try:
                        results[m] = fut.result()
                    except Exception:
                        results[m] = None
                    ui(self._render_gemini_checklist, models, dict(results), icon)
            ui(self._gemini_checklist_done, len(models))
        threading.Thread(target=worker, daemon=True).start()

    def _render_gemini_checklist(self, models, results, icon):
        if not self.winfo_exists():
            return
        self.g_checklist.config(state="normal", height=min(len(models), 12))
        self.g_checklist.delete("1.0", "end")
        for m in models:
            if m in results:
                mark, tag = icon[results[m]]
            else:
                mark, tag = "…", "und"
            self.g_checklist.insert("end", f"{mark}  ", tag)
            self.g_checklist.insert("end", f"{m}\n")
        self.g_checklist.config(state="disabled")
        self.model_box.fit()
        self._autosize()

    def _gemini_checklist_done(self, total):
        if not self.winfo_exists():
            return
        self.g_status.config(text=f"{total} modelos encontrados.", fg=C["good"])

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
        elif p == "gemini":
            if not self.g_key.get().strip():
                messagebox.showerror("Falta la API key", "Ingresa tu API key de Google AI (Gemini).", parent=self)
                return
            up.update(
                GEMINI_API_KEY=self.g_key.get().strip(),
                GEMINI_MODEL=self.g_model.get().strip() or "gemini-2.5-flash",
            )
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
