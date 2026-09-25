"""
Tema visual: paleta Material 3 Expressive, fuentes con fallback
multiplataforma, y helpers de dibujo (transiciones, esquinas
redondeadas, forma "cookie" festoneada).
"""

import math
import tkinter as tk
import tkinter.font as tkfont

C = dict(
    bg="#141218", card="#211F26", card_hi="#2B2930", outline="#49454F",
    text="#E6E0E9", muted="#CAC4D0", dim="#938F99",
    primary="#D0BCFF", on_primary="#381E72", primary_c="#4F378B", on_primary_c="#EADDFF",
    sec_c="#4A4458", on_sec_c="#E8DEF8",
    good="#B5E8B9", on_good="#0C3B1A", err="#F2B8B5", on_err="#601410", warm="#FFB783",
)

BOLT = [(4, -15), (-9, 3), (-1, 3), (-4, 15), (9, -4), (1, -4)]
BUSY = ("checking", "starting", "stopping")

# Valores por defecto hasta que se llame a init_fonts() (necesita un root de Tk vivo).
FD = ("Segoe UI Black", 28, "bold")
FDS = ("Segoe UI Black", 20, "bold")
FT = ("Segoe UI", 15, "bold")
FB = ("Segoe UI", 10)
FBB = ("Segoe UI", 10, "bold")
FS = ("Segoe UI", 9)
FBTN = ("Segoe UI", 10, "bold")
FMONO = ("Consolas", 9)


def _pick_font(candidates, size, weight="normal"):
    """Elige la primera fuente instalada de la lista (Win/Linux/macOS)."""
    available = set()
    try:
        root = tk._default_root
        if root is not None:
            available = set(tkfont.families(root))
    except Exception:
        available = set()
    for name in candidates:
        if not available or name in available:
            return (name, size, weight) if weight != "normal" else (name, size)
    name = candidates[-1]
    return (name, size, weight) if weight != "normal" else (name, size)


def init_fonts():
    """Fuentes con fallback multiplataforma. Llamar solo cuando ya exista un root de Tk
    (Windows: Segoe UI; Linux: Ubuntu/Noto/DejaVu; siempre cae a algo genérico)."""
    global FD, FDS, FT, FB, FBB, FS, FBTN, FMONO
    ui = ["Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial", "sans-serif"]
    ui_black = ["Segoe UI Black", "Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans", "sans-serif"]
    mono = ["Consolas", "Cascadia Mono", "Ubuntu Mono", "DejaVu Sans Mono", "Liberation Mono", "Courier New", "monospace"]
    FD = _pick_font(ui_black, 28, "bold")
    FDS = _pick_font(ui_black, 20, "bold")
    FT = _pick_font(ui, 15, "bold")
    FB = _pick_font(ui, 10)
    FBB = _pick_font(ui, 10, "bold")
    FS = _pick_font(ui, 9)
    FBTN = _pick_font(ui, 10, "bold")
    FMONO = _pick_font(mono, 9)


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
