"""Control plane.

Orchestrates the job lifecycle and is the ONLY bridge between the public web app
and the private runner. It never holds session state — it mints a short-lived
lease, tells the runner what to do, and proxies the frame / input stream. The web
app and the runner never speak to each other directly.
"""
from __future__ import annotations

import asyncio
import secrets

import httpx
import websockets
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from shared.config import settings
from shared.lease import mint_lease
from shared.logging import log
from shared import security
from shared.schemas import CreateJobRequest, JobStatus
from shared.states import JobState, TERMINAL_STATES

import jobs as jobstore

app = FastAPI(title="sessionbridge-controlplane")


@app.get("/health")
async def health():
    return {"ok": True}


async def _healthy(base_url: str) -> bool:
    """Cheap liveness probe of a downstream service."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{base_url}/health")
        return r.status_code == 200 and r.json().get("ok") is True
    except Exception:
        return False


@app.get("/ready")
async def ready():
    """Is the remote browser runner up? The web app gates on this so the user
    gets an explicit "remote browser not ready yet" instead of a failed job."""
    runner_ok = await _healthy(settings.RUNNER_URL)
    return {"ready": runner_ok, "services": {"runner": runner_ok}}


@app.post("/jobs", response_model=JobStatus)
async def create_job(req: CreateJobRequest):
    # Pre-flight: don't create a job we can't run. Surface a clear, retryable
    # message rather than a generic failure.
    if not await _healthy(settings.RUNNER_URL):
        raise HTTPException(503, "Remote browser runner is not ready yet. Please try again in a moment.")

    job_id = "job_" + secrets.token_hex(8)
    job = jobstore.Job(job_id=job_id)
    jobstore.JOBS[job_id] = job
    job.set_state(JobState.REMOTE_BROWSER_STARTING, "Starting isolated remote browser.")

    job.lease = mint_lease(job_id, settings.LEASE_TTL)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{settings.RUNNER_URL}/sessions", json={
                "job_id": job_id,
                "lease_token": job.lease.token,
                "start_url": security.START_URL,
            })
        resp.raise_for_status()
        job.session_id = resp.json()["session_id"]
    except Exception as e:  # noqa: BLE001
        job.set_state(JobState.FAILED, "Could not start remote browser.")
        log("controlplane", "runner_start_failed", job_id=job_id, reason=str(e))
        raise HTTPException(502, "runner unavailable")

    job.set_state(JobState.READY, "Browser ready. Type a URL or a task.")
    log("controlplane", "job_created", job_id=job_id)
    return _status(job)


@app.post("/jobs/{job_id}/navigate")
async def navigate(job_id: str, body: dict):
    """User-driven navigation to a URL they typed."""
    try:
        job = jobstore.get(job_id)
    except KeyError:
        raise HTTPException(404, "no job")
    if not job.session_id:
        raise HTTPException(409, "session not running")
    url = (body or {}).get("url", "").strip()
    if not url:
        raise HTTPException(422, "empty url")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url  # be forgiving: "example.com" -> "https://example.com"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.RUNNER_URL}/sessions/{job.session_id}/navigate",
                params={"url": url, "lease_token": job.lease.token},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log("controlplane", "navigate_failed", job_id=job_id, reason=str(e))
        raise HTTPException(502, "navigation failed")
    job.current_host = data.get("host")
    return data


@app.post("/jobs/{job_id}/agent")
async def agent_turn(job_id: str, body: dict):
    """Run one agent turn (a chat message) on the job's browser session."""
    try:
        job = jobstore.get(job_id)
    except KeyError:
        raise HTTPException(404, "no job")
    if not job.session_id:
        raise HTTPException(409, "session not running")
    task = (body or {}).get("task", "").strip()
    if not task:
        raise HTTPException(422, "empty task")
    try:
        job.set_state(JobState.RUNNING, "Agent working.")
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                f"{settings.RUNNER_URL}/sessions/{job.session_id}/agent",
                json={"task": task, "lease_token": job.lease.token},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log("controlplane", "agent_failed", job_id=job_id, reason=str(e))
        raise HTTPException(502, "agent run failed")
    job.current_host = None
    try:
        job.set_state(JobState(data["state"]), data.get("message", ""))
    except Exception:
        pass
    return data


@app.post("/jobs/{job_id}/stop", response_model=JobStatus)
async def stop_job(job_id: str):
    try:
        job = jobstore.get(job_id)
    except KeyError:
        raise HTTPException(404, "no job")
    # Stop is idempotent: a job that already ended (failed/stopped) just gets its
    # session torn down again, never a transition error.
    if job.state not in TERMINAL_STATES:
        job.set_state(JobState.STOPPED_BY_USER, "Stopped by user.")
    await _destroy_session(job)
    return _status(job)


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    try:
        return _status(jobstore.get(job_id))
    except KeyError:
        raise HTTPException(404, "no job")


@app.websocket("/ws/{job_id}")
async def ws_proxy(ws: WebSocket, job_id: str):
    """Proxy the frame/input stream between the web app and the runner.

    Neither end ever learns the other's address; the control plane is the only
    party that knows the runner's (private) location.
    """
    try:
        job = jobstore.get(job_id)
    except KeyError:
        await ws.close(code=4404)
        return
    await ws.accept()

    runner_ws_url = f"{settings.RUNNER_WS}/ws/{job.session_id}"
    try:
        async with websockets.connect(runner_ws_url, max_size=None) as runner:
            async def up():  # user -> runner
                while True:
                    msg = await ws.receive_text()
                    await runner.send(msg)

            async def down():  # runner -> user
                async for msg in runner:
                    await ws.send_text(msg)

            await asyncio.gather(up(), down())
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    except Exception as e:  # noqa: BLE001
        log("controlplane", "ws_proxy_error", job_id=job_id, reason=str(e))


async def _destroy_session(job: jobstore.Job) -> None:
    if not job.session_id:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(f"{settings.RUNNER_URL}/sessions/{job.session_id}/destroy")
    except Exception:
        pass
    job.session_id = None


def _status(job: jobstore.Job) -> JobStatus:
    return JobStatus(
        job_id=job.job_id, state=job.state, message=job.message,
        current_host=job.current_host, updated_at=job.updated_at,
    )
