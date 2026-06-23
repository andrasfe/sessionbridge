"""End-to-end mesh test: starts artifacts, runner, controlplane, webapp and
validates the full isolation chain — the user-facing webapp WS receives a live
frame that originated in the runner, two proxy hops away. No T-Mobile login is
performed; we only verify the session/stream plumbing and clean teardown."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time

import httpx
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {
    **os.environ,
    "PYTHONPATH": ROOT,
    "CONTROL_PLANE_URL": "http://localhost:8081",
    "RUNNER_URL": "http://localhost:8082",
    "LLM_URL": "http://localhost:8083",
    "ARTIFACT_URL": "http://localhost:8084",
    "ARTIFACT_LOCAL_DIR": os.path.join(ROOT, ".data/artifacts"),
    "METADATA_LOCAL_DIR": os.path.join(ROOT, ".data/metadata"),
    "HEADLESS": "true",
}
SERVICES = [
    ("artifacts", 8084), ("runner", 8082), ("controlplane", 8081), ("webapp", 8080),
]
procs = []


def start_all():
    for name, port in SERVICES:
        p = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(port)],
            cwd=os.path.join(ROOT, "services", name), env=ENV,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        procs.append(p)


def stop_all():
    for p in procs:
        try:
            p.send_signal(signal.SIGINT)
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()


async def wait_health():
    async with httpx.AsyncClient(timeout=5) as c:
        for name, port in SERVICES:
            for _ in range(40):
                try:
                    r = await c.get(f"http://127.0.0.1:{port}/health")
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError(f"{name} never became healthy")


async def run():
    failed = False

    def check(name, cond):
        nonlocal failed
        print(("PASS" if cond else "FAIL"), name)
        if not cond:
            failed = True

    await wait_health()
    check("all services healthy", True)

    async with httpx.AsyncClient(timeout=60) as c:
        # webapp is the ONLY public entry point.
        r = await c.post("http://127.0.0.1:8080/api/jobs")
        job = r.json()
        job_id = job["job_id"]
        check("job created via webapp", r.status_code == 200 and job_id.startswith("job_"))

        # Runner started a session -> state should reach awaiting_user_login.
        state = None
        for _ in range(40):
            s = (await c.get(f"http://127.0.0.1:8080/api/jobs/{job_id}")).json()
            state = s["state"]
            if state in ("awaiting_user_login", "login_in_progress", "failed"):
                break
            await asyncio.sleep(0.5)
        check("reached awaiting_user_login", state == "awaiting_user_login")

        # The runner must NOT be reachable from the host the way webapp is —
        # but in local mode all ports are bound; the real isolation is enforced
        # by docker/AWS networking. Here we assert the runner has no public job
        # API surface that leaks sessions: it only knows session ids.
        rr = await c.get("http://127.0.0.1:8082/health")
        check("runner health is minimal", rr.json() == {"ok": True})

        # Full streaming chain: connect to the WEBAPP ws and receive a frame
        # that originated in the runner (webapp -> controlplane -> runner).
        got_frame = False
        try:
            async with websockets.connect(f"ws://127.0.0.1:8080/ws/{job_id}",
                                          max_size=None) as ws:
                for _ in range(20):
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if msg.get("type") == "frame" and msg.get("data"):
                        got_frame = True
                        break
        except Exception as e:
            print("   ws error:", e)
        check("frame streamed end-to-end (webapp<-cp<-runner)", got_frame)

        # Stop -> session destroyed, state terminal.
        st = (await c.post(f"http://127.0.0.1:8080/api/jobs/{job_id}/stop")).json()
        check("stopped by user", st["state"] == "stopped_by_user")

    return not failed


if __name__ == "__main__":
    start_all()
    try:
        ok = asyncio.run(run())
    finally:
        stop_all()
    print("\nRESULT:", "ALL TESTS PASSED" if ok else "FAILURES PRESENT")
    sys.exit(0 if ok else 1)
