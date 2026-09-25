"""
ELIPSE Control Panel — punto de entrada
========================================
El código real vive dividido en módulos dentro de `launcher/` (junto a
`app/`, en la raíz del proyecto). Este archivo solo arranca la app para
mantener el mismo comando de siempre:

    python elipse_control_panel.py

Requiere que la carpeta `launcher/` esté en la raíz, al lado de este
archivo y de `app/`.
"""

from launcher.app import App

if __name__ == "__main__":
    App().mainloop()
