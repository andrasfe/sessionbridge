"""Public web app.

Serves the UI + embedded viewer and proxies everything to the control plane. It
has NO Playwright, NO session access, and never contacts the runner. This is the
only component the user's browser talks to.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from shared.config import settings

app = FastAPI(title="sessionbridge-webapp")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
CP = settings.CONTROL_PLANE_URL
CP_WS = settings.CONTROL_PLANE_WS


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/ready")
async def ready():
    return await _proxy_json("GET", "/ready")


@app.post("/api/jobs")
async def create_job():
    return await _proxy_json("POST", "/jobs", json={})


@app.post("/api/jobs/{job_id}/agent")
async def agent_turn(job_id: str, body: dict):
    return await _proxy_json("POST", f"/jobs/{job_id}/agent", json=body)


@app.post("/api/jobs/{job_id}/navigate")
async def navigate(job_id: str, body: dict):
    return await _proxy_json("POST", f"/jobs/{job_id}/navigate", json=body)


@app.post("/api/jobs/{job_id}/stop")
async def stop(job_id: str):
    return await _proxy_json("POST", f"/jobs/{job_id}/stop")


@app.get("/api/jobs/{job_id}")
async def status(job_id: str):
    return await _proxy_json("GET", f"/jobs/{job_id}")


@app.websocket("/ws/{job_id}")
async def ws_proxy(ws: WebSocket, job_id: str):
    await ws.accept()
    try:
        async with websockets.connect(f"{CP_WS}/ws/{job_id}", max_size=None) as cp:
            async def up():
                while True:
                    await cp.send(await ws.receive_text())

            async def down():
                async for msg in cp:
                    await ws.send_text(msg)

            await asyncio.gather(up(), down())
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except Exception:
        pass


async def _proxy_json(method: str, path: str, json=None):
    async with httpx.AsyncClient(timeout=200) as client:
        resp = await client.request(method, f"{CP}{path}", json=json)
    try:
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception:
        return Response(status_code=resp.status_code, content=resp.text)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
