"""
Ventana de logs en vivo (tail -f casero) para un servicio.
"""

import tkinter as tk

from .paths import LOGS
from .theme import C, FMONO, FT, rrect
from .widgets import Pill


class LogWindow(tk.Toplevel):
    def __init__(self, app, svc):
        super().__init__(app)
        self.path, self._sz = LOGS / f"{svc.key}.log", -1
        self.title(f"Logs de {svc.name}")
        self.geometry("780x460")
        self.configure(bg=C["bg"])

        top = tk.Frame(self, bg=C["bg"])
        top.pack(fill="x", padx=22, pady=(16, 8))
        tk.Label(top, text=svc.name, font=FT, fg=C["primary"], bg=C["bg"]).pack(side="left")

        # Botón: ir al final de los logs
        self.btn_bottom = Pill(
            top, "↓ Final", self._go_bottom,
            kind="ghost", width=90, height=36, bg=C["bg"]
        )
        self.btn_bottom.pack(side="right")

        # Contenedor del texto (para poder poner el botón flotante encima)
        self.body = tk.Frame(self, bg=C["bg"])
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.txt = tk.Text(
            self.body, bg=C["card"], fg=C["text"], font=FMONO, relief="flat",
            bd=0, padx=16, pady=12, wrap="none", highlightthickness=0, state="disabled"
        )
        self.txt.pack(fill="both", expand=True)

        # Botón flotante ↓ (aparece si no estás al final)
        self.float_btn = tk.Canvas(
            self.body, width=40, height=40, bg=C["card"],
            highlightthickness=0, cursor="hand2"
        )
        rrect(self.float_btn, 2, 2, 38, 38, 12, fill=C["primary_c"], outline="")
        self.float_btn.create_text(20, 20, text="↓", fill=C["on_primary_c"], font=("Segoe UI", 14, "bold"))
        self.float_btn.bind("<Button-1>", lambda e: self._go_bottom())
        self.float_btn.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
        self.float_btn.place_forget()  # oculto al inicio

        self.txt.bind("<MouseWheel>", lambda e: self.after(50, self._check_scroll))
        self.txt.bind("<Button-4>", lambda e: self.after(50, self._check_scroll))  # Linux scroll up
        self.txt.bind("<Button-5>", lambda e: self.after(50, self._check_scroll))  # Linux scroll down

        self._refresh()

    def _go_bottom(self):
        """Lleva el scroll hasta la última línea."""
        try:
            self.txt.see("end")
            self.txt.yview_moveto(1.0)
            self._hide_float()
        except tk.TclError:
            pass

    def _at_bottom(self):
        try:
            return self.txt.yview()[1] >= 0.98
        except tk.TclError:
            return True

    def _check_scroll(self):
        if self._at_bottom():
            self._hide_float()
        else:
            self._show_float()

    def _show_float(self):
        try:
            self.float_btn.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
            self.float_btn.lift()
        except tk.TclError:
            pass

    def _hide_float(self):
        try:
            self.float_btn.place_forget()
        except tk.TclError:
            pass

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
            bottom = self._at_bottom()
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")
            self.txt.insert("end", text)
            self.txt.config(state="disabled")
            if bottom:
                self.txt.see("end")
                self._hide_float()
            else:
                self._show_float()
        self.after(800, self._refresh)
