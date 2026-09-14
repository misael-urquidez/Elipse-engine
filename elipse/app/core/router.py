CODE_KEYWORDS = [
    "código", "codigo", "función", "funcion", "bug", "error",
    "script", "python", "javascript", "clase", "debug",
    "variable", "compilar", "sintaxis", "api", "endpoint",
    "sql", "html", "css", "algoritmo"
]


def choose_provider(message: str):
    """
    Devuelve (model_name, reason) según reglas simples.
    Empieza con if/else — se sofistica después si hace falta.
    """
    lowered = message.lower()

    for keyword in CODE_KEYWORDS:
        if keyword in lowered:
            return "code", f"palabra clave de código detectada: '{keyword}'"

    return "general", "sin palabras clave de código, usando modelo general"