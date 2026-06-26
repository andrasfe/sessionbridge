"""Remote browser runner.

The ONLY component with Playwright and an authenticated T-Mobile session. It has
no public inbound access (enforced by network/infra); only the control plane
reaches it. It streams frames, forwards input, enforces allowed domains, runs the
deterministic connector, and destroys the session at job end.
"""
from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from shared.config import settings
from shared.logging import log
from shared import security
from shared.schemas import (
    AutomateResponse, StartSessionRequest, StartSessionResponse,
)
from shared.states import JobState

from browser import BrowserSession
from connectors import tmobile, researchgate, overleaf, expedia
from connectors.tmobile import ConnectorUncertain
import agent as agentlib
from shared.schemas import AgentRunRequest, AgentRunResult

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
            if msg.get("type") != "input":
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


@app.post("/sessions/{session_id}/automate", response_model=AutomateResponse)
async def automate(session_id: str, lease_token: str):
    rec = SESSIONS.get(session_id)
    if not rec:
        raise HTTPException(404, "no session")
    if not _lease_ok(rec, lease_token):
        raise HTTPException(403, "invalid or expired lease")

    sess: BrowserSession = rec["session"]
    # Login is complete; further user input is no longer accepted by automation.
    rec["input_enabled"] = False
    page = sess.page

    # checking_domain
    if not security.host_allowed(sess.current_host):
        rec["input_enabled"] = True
        return AutomateResponse(
            state=JobState.REQUIRES_USER_INTERVENTION,
            message="Current page is not on an allowed domain.",
            source_host=sess.current_host,
        )

    connector = rec.get("connector", "tmobile")

    # ---- ResearchGate: return the publication list (downloads nothing) ----
    if connector == "researchgate":
        try:
            papers = await researchgate.list_publications(page)
        except ConnectorUncertain as e:
            log("runner", "connector_uncertain", job_id=rec["job_id"], reason=str(e))
            rec["input_enabled"] = True
            return AutomateResponse(
                state=JobState.REQUIRES_USER_INTERVENTION, message=str(e),
                source_host=sess.current_host,
            )
        except Exception as e:  # noqa: BLE001 - fail closed
            log("runner", "connector_failed", job_id=rec["job_id"], reason=str(e))
            return AutomateResponse(
                state=JobState.FAILED, message="Automation error; stopped.",
                source_host=sess.current_host,
            )
        log("runner", "publications_listed", job_id=rec["job_id"], count=len(papers))
        return AutomateResponse(
            state=JobState.COMPLETED,
            message=f"Found {len(papers)} publications.",
            source_host=sess.current_host,
            data={"papers": papers},
        )

    # ---- Overleaf: return the topmost project's title (opens nothing) ----
    if connector == "overleaf":
        try:
            result = await overleaf.top_project(page)
        except ConnectorUncertain as e:
            log("runner", "connector_uncertain", job_id=rec["job_id"], reason=str(e))
            rec["input_enabled"] = True
            return AutomateResponse(
                state=JobState.REQUIRES_USER_INTERVENTION, message=str(e),
                source_host=sess.current_host,
            )
        except Exception as e:  # noqa: BLE001 - fail closed
            log("runner", "connector_failed", job_id=rec["job_id"], reason=str(e))
            return AutomateResponse(
                state=JobState.FAILED, message="Automation error; stopped.",
                source_host=sess.current_host,
            )
        log("runner", "top_project", job_id=rec["job_id"])
        return AutomateResponse(
            state=JobState.COMPLETED,
            message=f"Topmost project: {result['top_project']}",
            source_host=sess.current_host,
            data=result,
        )

    # ---- Expedia: scrape the visible flight offers (books nothing) ----
    if connector == "expedia":
        try:
            result = await expedia.list_flights(page)
        except ConnectorUncertain as e:
            log("runner", "connector_uncertain", job_id=rec["job_id"], reason=str(e))
            rec["input_enabled"] = True
            return AutomateResponse(
                state=JobState.REQUIRES_USER_INTERVENTION, message=str(e),
                source_host=sess.current_host,
            )
        except Exception as e:  # noqa: BLE001 - fail closed
            log("runner", "connector_failed", job_id=rec["job_id"], reason=str(e))
            return AutomateResponse(
                state=JobState.FAILED, message="Automation error; stopped.",
                source_host=sess.current_host,
            )
        n = len(result["flights"])
        log("runner", "flights_listed", job_id=rec["job_id"], count=n)
        return AutomateResponse(
            state=JobState.COMPLETED, message=f"Found {n} flight offers.",
            source_host=sess.current_host, data=result,
        )

    # ---- T-Mobile: download + validate the latest statement PDF ----
    try:
        await tmobile.navigate_to_billing(page)
        result = await tmobile.find_and_download_latest(page)
    except ConnectorUncertain as e:
        log("runner", "connector_uncertain", job_id=rec["job_id"], reason=str(e))
        # Hand control back: re-enable input so the user can drive, then retry.
        rec["input_enabled"] = True
        return AutomateResponse(
            state=JobState.REQUIRES_USER_INTERVENTION, message=str(e),
            source_host=sess.current_host,
        )
    except Exception as e:  # noqa: BLE001 - fail closed on anything unexpected
        log("runner", "connector_failed", job_id=rec["job_id"], reason=str(e))
        return AutomateResponse(
            state=JobState.FAILED, message="Automation error; stopped.",
            source_host=sess.current_host,
        )

    # Hand the bytes to the artifact service (internal). The PDF never goes to
    # the LLM and the runner does not keep it.
    with open(result.path, "rb") as f:
        pdf_bytes = f.read()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{settings.ARTIFACT_URL}/artifacts",
            files={"file": (result.suggested_filename, pdf_bytes, "application/pdf")},
            data={
                "job_id": rec["job_id"],
                "source_host": result.source_host,
                "statement_date": result.statement_date or "",
            },
        )
    if resp.status_code != 200:
        return AutomateResponse(
            state=JobState.FAILED,
            message=f"Artifact validation failed: {resp.text[:200]}",
            source_host=result.source_host,
        )
    meta = resp.json()
    return AutomateResponse(
        state=JobState.COMPLETED,
        message="Statement downloaded and validated.",
        artifact_id=meta["artifact_id"],
        statement_date=result.statement_date,
        source_host=result.source_host,
    )


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
    log("runner", "agent_start", job_id=rec["job_id"])
    result = await agentlib.run_agent(sess, req.task, rec["history"], req.max_steps)
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
