"""OpenHands-backed agent harness.

Runs an OpenHands agent loop (LLM = an OpenRouter model, default
``openrouter/z-ai/glm-5.2``) that drives SessionBridge's *own* remote browser
through a custom ``browser`` tool. OpenHands is the brain; SessionBridge stays
the isolated browser. Selected with ``AGENT_HARNESS=openhands``.

Design notes:
- Perception is **text**, not screenshots: the browser tool returns the page
  title/URL, a list of interactive elements with click coordinates, and the
  visible text. (Images in tool-result messages are dropped by the OpenAI/
  OpenRouter transport, so text is the provider-agnostic choice. The builtin
  harness is the vision one.)
- OpenHands' loop is synchronous; it runs in a worker thread. The tool executor
  bridges back to the Playwright event loop via ``run_coroutine_threadsafe``.
- A context var carries (session, loop) into the executor so concurrent
  sessions stay isolated.

Importing this module requires ``openhands-ai`` to be installed; the harness
factory guards the import and falls back to the builtin harness otherwise.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from shared import security
from shared.logging import log
from shared.states import JobState

# OpenHands SDK (heavy, optional dependency).
from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)

DEFAULT_MODEL = os.getenv("OPENHANDS_MODEL", "openrouter/z-ai/glm-5.2")
# Cap output tokens per call. Without this litellm requests the model maximum
# (65536 for glm-5.2), which OpenRouter rejects with a 402 unless the account
# can afford that ceiling. A few-thousand-token cap is ample for tool-calling.
MAX_OUTPUT_TOKENS = int(os.getenv("OPENHANDS_MAX_OUTPUT_TOKENS", "4096"))

# Carries (BrowserSession, event_loop) into the (threaded, sync) tool executor.
# asyncio.to_thread copies the calling task's context, so a value set in run()
# before to_thread is visible to the executor and isolated per request.
_CTX: contextvars.ContextVar[tuple] = contextvars.ContextVar("sb_browser_ctx")

_OBSERVE_JS = r"""
() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const sel = 'a,button,input,textarea,select,[role=button],[onclick]';
  const items = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || r.bottom < 0 || r.top > vh) continue;
    const label = (el.innerText || el.value || el.placeholder ||
                   el.getAttribute('aria-label') || el.name || '').trim().replace(/\s+/g,' ').slice(0, 70);
    items.push({tag: el.tagName.toLowerCase(), type: el.type || '', label,
                x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)});
    if (items.length >= 40) break;
  }
  const text = (document.body ? document.body.innerText : '').replace(/\s+/g,' ').trim().slice(0, 2500);
  return {title: document.title, url: location.href, elements: items, text};
}
"""


def _format_page(p: dict) -> str:
    lines = [f"URL: {p.get('url','')}", f"TITLE: {p.get('title','')}", "",
             "INTERACTIVE ELEMENTS (click with command=click, x, y):"]
    for e in p.get("elements", []):
        t = e["tag"] + (f"[{e['type']}]" if e.get("type") else "")
        lines.append(f'- {t} "{e.get("label","")}" @ ({e["x"]},{e["y"]})')
    lines += ["", "PAGE TEXT:", p.get("text", "")]
    return "\n".join(lines)


class BrowserAction(Action):
    """One browser operation."""

    command: Literal["observe", "navigate", "click", "type", "key", "scroll"] = Field(
        description="observe=re-read page; navigate(url); click(x,y); "
                    "type(text into focused field); key(Enter/Tab/Backspace/...); scroll(dy)."
    )
    url: str = Field(default="", description="URL for navigate")
    x: float = Field(default=0, description="x pixel for click (0-1280)")
    y: float = Field(default=0, description="y pixel for click (0-800)")
    text: str = Field(default="", description="text to type")
    key: str = Field(default="", description="key name for the key command")
    dy: float = Field(default=600, description="scroll delta (+down/-up)")


class BrowserObservation(Observation):
    pass


class BrowserExecutor(ToolExecutor):
    """Executes a BrowserAction on the live BrowserSession (text perception)."""

    def __call__(self, action: BrowserAction, conversation=None) -> BrowserObservation:  # noqa: ARG002
        session, loop = _CTX.get()

        def run(coro):
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=60)

        try:
            cmd = action.command
            if cmd == "navigate":
                if not security.url_allowed(action.url):
                    return BrowserObservation.from_text(
                        text=f"Blocked: {action.url} is not on an allowed domain.",
                        is_error=True)
                run(session.page.goto(action.url, wait_until="domcontentloaded", timeout=30000))
            elif cmd == "click":
                x, y = float(action.x), float(action.y)
                run(session.handle_input({"kind": "mouse", "action": "move", "x": x, "y": y}))
                run(session.handle_input({"kind": "mouse", "action": "down", "x": x, "y": y,
                                          "button": "left", "buttons": 1, "clickCount": 1}))
                run(session.handle_input({"kind": "mouse", "action": "up", "x": x, "y": y,
                                          "button": "left", "buttons": 0, "clickCount": 1}))
            elif cmd == "type":
                run(session.handle_input({"kind": "text", "text": action.text}))
            elif cmd == "key":
                run(session.handle_input({"kind": "key", "action": "down", "key": action.key, "code": "", "text": ""}))
                run(session.handle_input({"kind": "key", "action": "up", "key": action.key, "code": "", "text": ""}))
            elif cmd == "scroll":
                run(session.page.mouse.wheel(0, float(action.dy)))
            # else: observe — just re-read below.

            if cmd != "observe":
                run(asyncio.sleep(1.0))  # let the page react
            page = run(session.page.evaluate(_OBSERVE_JS))
            return BrowserObservation.from_text(text=_format_page(page))
        except Exception as e:  # noqa: BLE001 - never crash the agent loop
            return BrowserObservation.from_text(
                text=f"Browser error on {action.command}: {type(e).__name__}: {e}",
                is_error=True)


class BrowserTool(ToolDefinition[BrowserAction, BrowserObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["BrowserTool"]:  # noqa: ARG003
        return [cls(
            action_type=BrowserAction,
            observation_type=BrowserObservation,
            description=(
                "Control a web browser viewport (1280x800). Use `observe` first to "
                "read the page, then `navigate`/`click`/`type`/`key`/`scroll`. Click "
                "uses pixel coordinates from the element list. After login forms, "
                "click the field, `type` the value, then click the submit button or "
                "press Enter. When done, call `finish` with the answer."
            ),
            annotations=ToolAnnotations(title="browser", readOnlyHint=False, openWorldHint=True),
            executor=BrowserExecutor(),
        )]


_REGISTERED = False


def _ensure_registered() -> None:
    global _REGISTERED
    if not _REGISTERED:
        register_tool("browser", BrowserTool)
        _REGISTERED = True


SYSTEM_PROMPT = (
    "You are a web-browsing agent. You control a real browser through the "
    "`browser` tool (text perception: page title, URL, a list of interactive "
    "elements with pixel coordinates, and the visible text). Accomplish the "
    "user's task by issuing browser commands one at a time, calling `observe` "
    "whenever you need to see the current page. If a login wall, CAPTCHA, or "
    "human-verification blocks you, call `finish` explaining that the human must "
    "take over. When you have the requested information or have completed the "
    "task, call `finish` with a clear answer."
)


class OpenHandsHarness:
    """Drives the remote browser with an OpenHands agent loop (glm-5.2 default)."""

    @property
    def name(self) -> str:
        return "openhands"

    async def run(self, session: Any, task: str, history: list[str], max_steps: int) -> dict:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            return {"state": JobState.REQUIRES_USER_INTERVENTION, "answer": "",
                    "steps": [], "message": "No OPENROUTER_API_KEY for the OpenHands harness."}

        _ensure_registered()
        loop = asyncio.get_running_loop()
        _CTX.set((session, loop))

        steps: list[dict] = []
        answer_holder: list[str] = []

        import tempfile
        conv_box: list = []  # lets cb reach conv for step-cap interruption

        def cb(event) -> None:
            action = getattr(event, "action", None)
            if action is None:
                return
            kind = type(action).__name__
            if kind == "BrowserAction":
                detail = action.url or action.text or action.key or (
                    f"({int(action.x)},{int(action.y)})" if action.command == "click" else "")
                steps.append({"action": action.command, "detail": detail})
                if len(steps) >= max_steps and conv_box:
                    try:
                        conv_box[0].pause()
                    except Exception:
                        pass
            elif kind == "FinishAction":
                answer_holder.append(getattr(action, "message", "") or "")

        llm = LLM(model=DEFAULT_MODEL, api_key=api_key, service_id="sessionbridge-agent",
                  max_output_tokens=MAX_OUTPUT_TOKENS)
        agent = Agent(llm=llm, tools=[Tool(name="browser")], system_prompt=SYSTEM_PROMPT)
        conv = Conversation(agent, workspace=tempfile.mkdtemp(prefix="oh-"), callbacks=[cb])
        conv_box.append(conv)

        log("runner", "openhands_start", job_id=getattr(session, "job_id", "?"), model=DEFAULT_MODEL)
        try:
            conv.send_message(task)
            await asyncio.to_thread(conv.run)
        except Exception as e:  # noqa: BLE001
            log("runner", "openhands_error", reason=str(e))
            return {"state": JobState.FAILED, "answer": "", "steps": steps,
                    "message": f"OpenHands run failed: {type(e).__name__}"}
        finally:
            history.extend(f"{s['action']} {s.get('detail','')}".strip() for s in steps)

        # Final answer: from the finish callback, else scan conversation state.
        answer = answer_holder[-1] if answer_holder else ""
        if not answer:
            for ev in getattr(getattr(conv, "state", None), "events", []) or []:
                a = getattr(ev, "action", None)
                if a is not None and type(a).__name__ == "FinishAction":
                    answer = getattr(a, "message", "") or ""
        if answer:
            return {"state": JobState.COMPLETED, "answer": answer, "steps": steps,
                    "message": "Task complete."}
        return {"state": JobState.REQUIRES_USER_INTERVENTION, "answer": "", "steps": steps,
                "message": f"Stopped after {len(steps)} steps without a final answer."}
