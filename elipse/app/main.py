from fastapi import FastAPI
from pydantic import BaseModel
import ollama
from app.config import settings

app = FastAPI(title=settings.app_name)

class ChatRequest(BaseModel):
    message: str

@app.get("/v1/status")
def status():
    return {"status": "ok", "name": settings.app_name, "model": settings.ollama_model}

@app.post("/v1/chat")
def chat(request: ChatRequest):
    response = ollama.chat(
        model=settings.ollama_model,
        messages=[{"role": "user", "content": request.message}]
    )
    return {"reply": response["message"]["content"]}