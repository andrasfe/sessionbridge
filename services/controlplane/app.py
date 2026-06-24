"""Control plane.

Orchestrates the job state machine and is the ONLY bridge between the public web
app and the private runner. It never holds raw credentials or session state — it
mints a short-lived lease, tells the runner what to do, and proxies the frame /
input stream. The web app and the runner never speak to each other directly.
"""
from __future__ import annotations

import asyncio
import secrets

import httpx
import websockets
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from shared.config import settings
from shared.lease import mint_lease
from shared.logging import log
from shared import security
from shared.schemas import CreateJobRequest, JobStatus
from shared.states import JobState

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
    """Are the downstream services the job depends on actually up?

    The web app uses this to gate the "Fetch" button so the user gets an
    explicit "remote browser not ready yet" instead of a failed job.
    """
    runner_ok, artifacts_ok = await _healthy(settings.RUNNER_URL), await _healthy(settings.ARTIFACT_URL)
    return {
        "ready": runner_ok and artifacts_ok,
        "services": {"runner": runner_ok, "artifacts": artifacts_ok},
    }


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
                "connector": settings.CONNECTOR,
                "lease_token": job.lease.token,
                "start_url": security.START_URL,
            })
        resp.raise_for_status()
        job.session_id = resp.json()["session_id"]
    except Exception as e:  # noqa: BLE001
        job.set_state(JobState.FAILED, "Could not start remote browser.")
        log("controlplane", "runner_start_failed", job_id=job_id, reason=str(e))
        raise HTTPException(502, "runner unavailable")

    job.set_state(JobState.AWAITING_USER_LOGIN,
                  "Log into T-Mobile in the viewer, then click Continue.")
    log("controlplane", "job_created", job_id=job_id)
    return _status(job)


@app.post("/jobs/{job_id}/confirm-login", response_model=JobStatus)
async def confirm_login(job_id: str):
    try:
        job = jobstore.get(job_id)
    except KeyError:
        raise HTTPException(404, "no job")
    # Also allow retrying after the system handed control back to the user.
    if job.state not in (JobState.AWAITING_USER_LOGIN, JobState.LOGIN_IN_PROGRESS,
                         JobState.REQUIRES_USER_INTERVENTION):
        raise HTTPException(409, f"cannot confirm login from state {job.state}")

    job.set_state(JobState.LOGIN_CONFIRMED_BY_USER, "Login confirmed by user.")
    # Coarse-grained progression: the runner performs domain-check ->
    # navigate -> find -> download -> validate in one deterministic call and
    # returns the final state. We mark CHECKING_DOMAIN so the UI reflects that
    # automation has resumed.
    job.set_state(JobState.CHECKING_DOMAIN, "Verifying allowed domain and locating billing.")

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{settings.RUNNER_URL}/sessions/{job.session_id}/automate",
                params={"lease_token": job.lease.token},
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        job.set_state(JobState.FAILED, "Automation could not run.")
        log("controlplane", "automate_failed", job_id=job_id, reason=str(e))
        await _destroy_session(job)
        return _status(job)

    job.current_host = data.get("source_host")
    job.statement_date = data.get("statement_date")
    job.artifact_id = data.get("artifact_id")
    job.data = data.get("data")
    job.set_state(JobState(data["state"]), data.get("message", ""))

    # Session is always destroyed once automation has finished, whatever the
    # outcome (completed / failed / requires intervention handled separately).
    if job.state in (JobState.COMPLETED, JobState.FAILED):
        await _destroy_session(job)
    return _status(job)


@app.post("/jobs/{job_id}/stop", response_model=JobStatus)
async def stop_job(job_id: str):
    try:
        job = jobstore.get(job_id)
    except KeyError:
        raise HTTPException(404, "no job")
    job.set_state(JobState.STOPPED_BY_USER, "Stopped by user.")
    await _destroy_session(job)
    return _status(job)


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    try:
        return _status(jobstore.get(job_id))
    except KeyError:
        raise HTTPException(404, "no job")


@app.get("/jobs/{job_id}/artifact")
async def get_artifact(job_id: str):
    try:
        job = jobstore.get(job_id)
    except KeyError:
        raise HTTPException(404, "no job")
    if not job.artifact_id:
        raise HTTPException(404, "no artifact yet")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{settings.ARTIFACT_URL}/artifacts/{job.artifact_id}/download")
    if resp.status_code != 200:
        raise HTTPException(502, "artifact unavailable")
    return Response(
        content=resp.content,
        media_type="application/pdf",
        headers={"Content-Disposition": resp.headers.get(
            "content-disposition", "attachment; filename=statement.pdf")},
    )


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

    # First user interaction means login is in progress.
    if job.state == JobState.AWAITING_USER_LOGIN:
        try:
            job.set_state(JobState.LOGIN_IN_PROGRESS, "Login in progress.")
        except ValueError:
            pass

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
        current_host=job.current_host, artifact_id=job.artifact_id,
        data=job.data, updated_at=job.updated_at,
    )
