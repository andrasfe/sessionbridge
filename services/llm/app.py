"""LLM service (optional, provider-agnostic).

Powers the builtin vision harness, routed through the pluggable `llm_providers`
package so the same code works with OpenRouter, OpenAI, Anthropic, or any
registered provider (select via LLM_PROVIDER + the matching API key):

  /agent/act — the browser agent's brain: given task + history + a screenshot,
  return ONE next action. The LLM only proposes actions; the runner executes
  them. It never controls the browser directly.

Disabled (returns an "ask" action) when no provider key is set.
"""
from __future__ import annotations

import json
import re

from fastapi import FastAPI

from shared.logging import log
from shared.schemas import AgentAction, AgentDecideRequest

from llm_providers import LLMProvider, Message, get_provider_from_env

app = FastAPI(title="sessionbridge-llm")

# Build the provider once from the environment (LLM_PROVIDER + provider keys).
# None when no key is configured → the service reports itself disabled.
try:
    _provider: LLMProvider | None = get_provider_from_env()
    log("llm", "provider_ready", model=_provider.default_model)
except Exception as e:  # noqa: BLE001 - missing key / unknown provider
    _provider = None
    log("llm", "provider_disabled", reason=str(e))


AGENT_SYSTEM = """You are a web-browsing agent controlling a Chromium browser at \
1280x800 pixels. You are given the user's task, the recent action history, and a \
screenshot of the current page. Decide the SINGLE next action.

Respond with ONLY a JSON object (no prose, no markdown), with a short "thought" \
and one "action". Valid actions:
- {"thought":"...","action":"navigate","url":"https://..."}
- {"thought":"...","action":"click","x":<0-1280>,"y":<0-800>}   // pixel coords on the screenshot
- {"thought":"...","action":"type","text":"..."}                // types into the currently focused field
- {"thought":"...","action":"key","key":"Enter"}                // Enter, Tab, Backspace, ArrowDown, ...
- {"thought":"...","action":"scroll","dy":<pixels: + down, - up>}
- {"thought":"...","action":"wait"}                              // let the page settle, then look again
- {"thought":"...","action":"ask","message":"..."}              // ask the human to take over (e.g. solve a CAPTCHA / log in)
- {"thought":"...","action":"done","answer":"..."}              // task complete; give the final answer

Rules: click a field before typing into it. Submit search forms with key Enter. \
If a CAPTCHA / human-verification / login wall blocks progress, use "ask". When \
you have the information the task requested, use "done" with a clear answer."""


@app.get("/health")
async def health():
    return {"ok": True, "enabled": _provider is not None}


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of the model's reply."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {"action": "wait", "thought": "could not parse model output"}


@app.post("/agent/act", response_model=AgentAction)
async def agent_act(req: AgentDecideRequest):
    if _provider is None:
        # No key → tell the runner to hand control to the human.
        return AgentAction(action="ask",
                           message="No LLM provider configured (set a provider key).")

    history = "\n".join(req.history[-12:]) or "(none yet)"
    user_text = (
        f"TASK:\n{req.task}\n\nCURRENT URL: {req.url or 'about:blank'}\n\n"
        f"RECENT ACTIONS:\n{history}\n\nDecide the next action."
    )
    messages = [
        Message(role="system", content=AGENT_SYSTEM),
        Message(
            role="user",
            content=user_text,
            images=(req.screenshot_b64,) if req.screenshot_b64 else (),
        ),
    ]

    try:
        resp = await _provider.complete(messages, temperature=0.0, max_tokens=500)
        raw = resp.content
    except Exception as e:  # noqa: BLE001
        log("llm", "agent_error", reason=str(e))
        return AgentAction(action="ask",
                           message=f"LLM call failed ({type(e).__name__}); take over or retry.")

    data = _extract_json(raw)
    # Log only the action type (never the screenshot or typed text).
    log("llm", "agent_decided", act=data.get("action"))
    try:
        return AgentAction(**data)
    except Exception:
        return AgentAction(action="wait", thought="invalid action shape")
