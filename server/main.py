import argparse
import asyncio
import json
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.models import Message, PositionReport
from server.state import message_store, store

_mesh_send = None  # set to mesh_listener.send_text when running with real hardware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Blue Force Tracker")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

update_queue: asyncio.Queue[PositionReport] = asyncio.Queue(maxsize=256)
message_queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=256)
connected_clients: set[WebSocket] = set()


@app.on_event("startup")
async def startup():
    store.set_queue(update_queue)
    message_store.set_queue(message_queue)
    asyncio.create_task(_broadcaster())
    asyncio.create_task(_message_broadcaster())


async def _broadcaster():
    while True:
        report = await update_queue.get()
        data = report.model_dump_json()
        stale_clients = []
        for ws in connected_clients:
            try:
                await ws.send_text(data)
            except Exception:
                stale_clients.append(ws)
        for ws in stale_clients:
            connected_clients.discard(ws)


async def _message_broadcaster():
    while True:
        msg = await message_queue.get()
        data = msg.model_dump_json()
        stale_clients = []
        for ws in connected_clients:
            try:
                await ws.send_text(data)
            except Exception:
                stale_clients.append(ws)
        for ws in stale_clients:
            connected_clients.discard(ws)


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/positions")
async def get_positions():
    return store.get_all()


@app.get("/api/messages")
async def get_messages(channel: str | None = None):
    messages = message_store.get_all()
    if channel:
        messages = [m for m in messages if m.channel == channel]
    return messages


class SendMessageRequest(BaseModel):
    channel: str
    body: str


@app.post("/api/messages")
async def post_message(req: SendMessageRequest):
    msg = Message(sender="HQ", channel=req.channel, body=req.body)
    message_store.add(msg)
    if _mesh_send:
        _mesh_send(req.channel, req.body)
    return msg


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(connected_clients))
    try:
        for pos in store.get_all():
            await ws.send_text(pos.model_dump_json())
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "message":
                channel = data.get("channel", "BROADCAST")
                body = data.get("body", "")
                msg = Message(sender="HQ", channel=channel, body=body)
                message_store.add(msg)
                if _mesh_send:
                    _mesh_send(channel, body)
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)
        logger.info(
            "WebSocket client disconnected (%d remaining)", len(connected_clients)
        )


def main():
    parser = argparse.ArgumentParser(description="Blue Force Tracker Server")
    parser.add_argument(
        "--simulate", action="store_true", help="Run with simulated nodes"
    )
    parser.add_argument(
        "--port",
        type=str,
        default="/dev/ttyUSB0",
        help="Serial port for Meshtastic node",
    )
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    parser.add_argument("--web-port", type=int, default=8000, help="HTTP port")
    args = parser.parse_args()

    if args.simulate:
        from server.simulator import run_simulator

        logger.info("Starting in simulation mode")
        t = threading.Thread(target=run_simulator, daemon=True)
        t.start()
    else:
        from server.mesh_listener import send_text, start_listener

        global _mesh_send
        _mesh_send = send_text
        start_listener(args.port)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.web_port, log_level="info")


if __name__ == "__main__":
    main()
