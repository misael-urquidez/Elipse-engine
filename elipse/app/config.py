from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "ELIPSE"
    ollama_model: str = "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"
    ollama_code_model: str = "qwen2.5-coder:3b"
    ollama_research_model: str = "phi3:mini"  # sin capacidad de thinking; evita el bug de think=False en qwen3:4b
    ollama_num_ctx: int = 2048  # contexto fijo y chico a propósito: libera VRAM para meter más capas en GPU
    ollama_timeout_seconds: int = 120  # si Ollama se cae o se cuelga, fallar rápido en vez de esperar para siempre
    workspace_dir: str = "workspace"

    # --- Fase 4: memoria semántica (Chroma) ---
    chroma_dir: str = "chroma_data"
    chroma_collection: str = "elipse_memory"
    memory_max_items: int = 500
    memory_prunable_types: list[str] = ["research"]  # tipos que SÍ se borran automáticamente al llenarse
    memory_retrieval_k: int = 4
    research_summary_max_chars: int = 800

    class Config:
        env_file = ".env"

settings = Settings()