from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "ELIPSE"
    ollama_model: str = "qwen3:4b"
    ollama_code_model: str = "qwen2.5-coder:3b"
    workspace_dir: str = "workspace"

    class Config:
        env_file = ".env"

settings = Settings()