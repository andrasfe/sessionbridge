"""Remote browser runner.

The ONLY component with Playwright. It has no public inbound access (enforced by
network/infra); only the control plane reaches it. It streams frames, forwards
input, enforces allowed domains, runs the agent harness, and destroys the
session at job end.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from shared.config import settings
from shared.logging import log
from shared import security
from shared.schemas import (
    AgentRunRequest, AgentRunResult, StartSessionRequest, StartSessionResponse,
)

from browser import BrowserSession
from harness import get_harness_from_env

app = FastAPI(title="sessionbridge-runner")

# session_id -> dict(session, job_id, lease_token, lease_exp, input_enabled)
SESSIONS: dict[str, dict] = {}


def _lease_ok(rec: dict, token: str) -> bool:
    return token == rec["lease_token"] and time.time() < rec["lease_exp"]


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/sessions", response_model=StartSessionResponse)
async def start_session(req: StartSessionRequest):
    if not security.url_allowed(req.start_url):
        raise HTTPException(400, "start_url not on an allowed domain")
    session_id = f"sess_{req.job_id}"
    sess = BrowserSession(req.job_id, req.start_url)
    await sess.start()
    await sess.start_screencast()
    SESSIONS[session_id] = {
        "session": sess,
        "job_id": req.job_id,
        "lease_token": req.lease_token,
        "lease_exp": time.time() + settings.LEASE_TTL,
        # Input forwarding is enabled during the login window.
        "input_enabled": True,
        # Accumulated agent action history across chat turns on this session.
        "history": [],
    }
    log("runner", "session_started", job_id=req.job_id, session_id=session_id)
    return StartSessionResponse(session_id=session_id)


@app.websocket("/ws/{session_id}")
async def ws_stream(ws: WebSocket, session_id: str):
    rec = SESSIONS.get(session_id)
    if not rec:
        await ws.close(code=4404)
        return
    await ws.accept()
    sess: BrowserSession = rec["session"]

    async def pump_frames():
        while True:
            frame = await sess.frame_queue.get()
            await ws.send_json(frame)

    async def pump_input():
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            # Dynamic 1:1 sizing: the viewer reports its on-screen pixel size and
            # the remote viewport tracks it. Always honoured (not input-gated).
            if mtype == "resize":
                try:
                    await sess.set_view_size(int(msg.get("w", 0)), int(msg.get("h", 0)))
                except Exception as e:  # noqa: BLE001
                    log("runner", "resize_error", reason=str(e))
                continue
            if mtype != "input":
                continue
            # Input is forwarded but NEVER logged (no input logging, per spec).
            if rec["input_enabled"]:
                try:
                    await sess.handle_input(msg["event"])
                except Exception as e:  # log the failure mode (kind only, never content)
                    log("runner", "input_error",
                        kind=(msg.get("event") or {}).get("kind"), reason=str(e))

    sender = asyncio.create_task(pump_frames())
    receiver = asyncio.create_task(pump_input())
    try:
        await asyncio.gather(sender, receiver)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        receiver.cancel()


@app.post("/sessions/{session_id}/agent", response_model=AgentRunResult)
async def agent_run(session_id: str, req: AgentRunRequest):
    rec = SESSIONS.get(session_id)
    if not rec:
        raise HTTPException(404, "no session")
    if not _lease_ok(rec, req.lease_token):
        raise HTTPException(403, "invalid or expired lease")
    sess: BrowserSession = rec["session"]
    # The agent drives; user input stays enabled so the user can take over.
    rec["input_enabled"] = True
    harness = get_harness_from_env()
    log("runner", "agent_start", job_id=rec["job_id"], harness=harness.name)
    result = await harness.run(sess, req.task, rec["history"], req.max_steps)
    return AgentRunResult(**result)


@app.post("/sessions/{session_id}/navigate")
async def navigate(session_id: str, url: str, lease_token: str):
    """User-driven navigation: point the remote browser at a URL."""
    rec = SESSIONS.get(session_id)
    if not rec:
        raise HTTPException(404, "no session")
    if not _lease_ok(rec, lease_token):
        raise HTTPException(403, "invalid or expired lease")
    if not security.url_allowed(url):
        raise HTTPException(400, "url not on an allowed domain")
    sess: BrowserSession = rec["session"]
    try:
        await sess.page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:  # noqa: BLE001 - navigation may time out; report, don't crash
        log("runner", "navigate_error", job_id=rec["job_id"], reason=str(e))
        return {"ok": False, "host": sess.current_host, "message": "navigation did not complete"}
    # Record it so the agent has context on the next turn.
    rec["history"].append(f"user navigated to {url} (now: {sess.current_host or ''})")
    log("runner", "navigated", job_id=rec["job_id"], host=sess.current_host)
    return {"ok": True, "host": sess.current_host}


@app.post("/sessions/{session_id}/destroy")
async def destroy(session_id: str):
    rec = SESSIONS.pop(session_id, None)
    if rec:
        await rec["session"].destroy()
        log("runner", "session_destroyed", session_id=session_id)
    return {"ok": True}
