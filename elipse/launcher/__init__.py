"""
launcher — Panel de control de escritorio de ELIPSE (Tkinter, Material 3
Expressive). Antes vivía todo en un único archivo (`elipse_control_panel.py`
en la raíz); ahora está dividido en módulos por responsabilidad:

    paths.py            rutas y constantes de plataforma
    env_store.py         .env y panel_prefs.json
    theme.py              paleta, fuentes, helpers de dibujo
    widgets.py            Pill, Surface, lbl, field, estilo ttk
    uiqueue.py            cola para actualizar la UI desde hilos
    network.py            probe / Ollama / OpenAI-compat / Gemini
    process_utils.py      Python, venv, Ollama, procesos, manage_keys
    services.py           Service (estado) + Card (tarjeta visual)
    log_window.py          ventana de logs en vivo
    settings_window.py     ventana de Ajustes
    setup_dialog.py         aviso de "faltan paquetes" con opción de localizar un venv
    app.py                 ventana principal (App)

El punto de entrada real sigue siendo `elipse_control_panel.py`, en la
raíz del proyecto (junto a app/), que solo hace:

    from launcher.app import App
    App().mainloop()
"""

from .app import App

__all__ = ["App"]
