import json
import requests
import websockets
import asyncio

BASE_URL = "http://127.0.0.1:8000"


async def main():
    mensaje = input("Mensaje para ELIPSE: ")

    resp = requests.post(f"{BASE_URL}/v1/chat", json={"message": mensaje})
    data = resp.json()
    print("Respuesta inicial de /v1/chat:", data)

    task_id = data["task_id"]
    ws_url = f"ws://127.0.0.1:8000/v1/ws/{task_id}"

    async with websockets.connect(ws_url) as ws:
        while True:
            raw = await ws.recv()
            event = json.loads(raw)

            if event["type"] == "ping":
                continue
            elif event["type"] == "progreso":
                print(f"[progreso] {event['mensaje']}")
            elif event["type"] == "final":
                print("[final]", json.dumps(event["data"], indent=2, ensure_ascii=False))
                break
            elif event["type"] == "error":
                print("[error]", event["mensaje"])
                break


if __name__ == "__main__":
    asyncio.run(main())