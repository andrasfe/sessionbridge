"""Vision agent loop.

Drives the browser to accomplish a free-text task: screenshot -> ask the LLM
service for ONE next action -> validate + execute it here -> repeat. The LLM
only proposes actions; this module is the only thing that touches the page, and
it enforces the allowed-domain policy on navigation (fail-closed). The live
viewer streams the whole thing so the user can watch and take over.
"""
from __future__ import annotations

import asyncio
import base64

import httpx

from shared.config import settings
from shared.logging import log
from shared import security
from shared.states import JobState

from browser import BrowserSession


async def _screenshot_b64(sess: BrowserSession) -> str:
    try:
        data = await sess.page.screenshot(type="jpeg", quality=settings.SCREENCAST_QUALITY)
        return base64.b64encode(data).decode("ascii")
    except Exception:
        return ""


async def run_agent(sess: BrowserSession, task: str, history: list[str],
                    max_steps: int) -> dict:
    """Run the agent loop. Returns {state, answer, steps, message}."""
    steps: list[dict] = []

    for i in range(max_steps):
        shot = await _screenshot_b64(sess)
        url = sess.page.url if sess.page else ""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{settings.LLM_URL}/agent/act",
                    json={"task": task, "url": url, "history": history,
                          "screenshot_b64": shot,
                          # The viewport is dynamic (1:1 with the viewer), so the
                          # model must be told the actual pixel space it clicks in.
                          "width": sess.view_w, "height": sess.view_h},
                )
            resp.raise_for_status()
            act = resp.json()
        except Exception as e:  # noqa: BLE001
            log("runner", "agent_llm_error", job_id=sess.job_id, reason=str(e))
            return {"state": JobState.FAILED, "answer": "",
                    "steps": steps, "message": "Agent brain unavailable."}

        a = act.get("action", "wait")
        thought = act.get("thought", "")
        step = {"action": a, "thought": thought}

        # ---- execute ----
        try:
            if a == "navigate":
                target = act.get("url") or ""
                if not security.url_allowed(target):
                    step["result"] = "blocked: domain not allowed"
                    steps.append(step)
                    history.append(f"navigate {target} -> BLOCKED (domain not allowed)")
                    return {"state": JobState.REQUIRES_USER_INTERVENTION, "answer": "",
                            "steps": steps,
                            "message": f"Agent tried to leave allowed domains ({target})."}
                await sess.page.goto(target, wait_until="domcontentloaded", timeout=30000)
                step["detail"] = target

            elif a == "click":
                x, y = float(act.get("x") or 0), float(act.get("y") or 0)
                await sess.handle_input({"kind": "mouse", "action": "move", "x": x, "y": y})
                await sess.handle_input({"kind": "mouse", "action": "down", "x": x, "y": y,
                                         "button": "left", "buttons": 1, "clickCount": 1})
                await sess.handle_input({"kind": "mouse", "action": "up", "x": x, "y": y,
                                         "button": "left", "buttons": 0, "clickCount": 1})
                step["detail"] = f"({int(x)},{int(y)})"

            elif a == "type":
                await sess.handle_input({"kind": "text", "text": act.get("text") or ""})
                step["detail"] = "typed text"  # never log the text itself

            elif a == "key":
                await sess.handle_input({"kind": "key", "action": "down",
                                         "key": act.get("key") or "", "code": "", "text": ""})
                await sess.handle_input({"kind": "key", "action": "up",
                                         "key": act.get("key") or "", "code": "", "text": ""})
                step["detail"] = act.get("key")

            elif a == "scroll":
                await sess.page.mouse.wheel(0, float(act.get("dy") or 600))

            elif a == "wait":
                await asyncio.sleep(1.5)

            elif a == "ask":
                steps.append(step)
                return {"state": JobState.REQUIRES_USER_INTERVENTION, "answer": "",
                        "steps": steps,
                        "message": act.get("message") or "Agent needs you to take over."}

            elif a == "done":
                step["detail"] = "done"
                steps.append(step)
                return {"state": JobState.COMPLETED, "answer": act.get("answer") or "",
                        "steps": steps, "message": "Task complete."}

            else:
                step["result"] = f"unknown action '{a}'"
        except Exception as e:  # noqa: BLE001 - never crash the loop on one bad step
            step["result"] = f"error: {type(e).__name__}"

        steps.append(step)
        history.append(
            f"{a} {step.get('detail','')}".strip() + f" (now: {sess.current_host or ''})")
        # Let the page react before the next observation.
        await asyncio.sleep(1.0)

    return {"state": JobState.REQUIRES_USER_INTERVENTION, "answer": "",
            "steps": steps,
            "message": f"Reached the {max_steps}-step limit without finishing."}
