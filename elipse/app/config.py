from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "ELIPSE"
    ollama_model: str = "qwen3:4b"

    class Config:
        env_file = ".env"

settings = Settings()