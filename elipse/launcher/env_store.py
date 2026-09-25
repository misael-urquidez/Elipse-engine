"""
.env y preferencias del panel (panel_prefs.json).
"""

import json

from .paths import ENV_PATH, PREFS_PATH


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
