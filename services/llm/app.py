"""LLM service (optional).

Classifies REDACTED, post-login visible page text into one of a few candidate
labels to help the control plane reason about page type. It never controls the
browser and never sees credentials, cookies, tokens, login pages or PDF content.
Disabled entirely unless an OpenRouter key is configured.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI

import json
import re

from shared.config import settings
from shared.logging import log
from shared.security import redact
from shared.schemas import (
    AgentAction, AgentDecideRequest, ClassifyRequest, ClassifyResponse,
)

app = FastAPI(title="sessionbridge-llm")

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
    return {"ok": True, "enabled": settings.LLM_ENABLED}


@app.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest):
    if not settings.LLM_ENABLED:
        return ClassifyResponse(enabled=False, note="LLM disabled (no OpenRouter key).")

    # Defence in depth: redact again even though the caller already redacted.
    text = redact(req.redacted_text, limit=6000)
    labels = ", ".join(req.candidate_labels)
    prompt = (
        "You label web page text. Respond with EXACTLY one of these labels and "
        f"nothing else: {labels}.\n\nPAGE TEXT:\n{text}"
    )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
                json={
                    "model": settings.OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 10, "temperature": 0,
                },
            )
        resp.raise_for_status()
        out = resp.json()["choices"][0]["message"]["content"].strip().lower()
    except Exception as e:  # noqa: BLE001
        log("llm", "classify_error", reason=str(e))
        return ClassifyResponse(enabled=True, note="LLM call failed; ignored.")

    match = next((l for l in req.candidate_labels if l.lower() in out), None)
    log("llm", "classified", label=match or "")
    return ClassifyResponse(label=match, confidence=1.0 if match else 0.0, enabled=True)


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
    if not settings.LLM_ENABLED:
        # No key → tell the runner to hand control to the human.
        return AgentAction(action="ask",
                           message="No LLM key configured (set OPENROUTER_API_KEY).")

    history = "\n".join(req.history[-12:]) or "(none yet)"
    user_text = (
        f"TASK:\n{req.task}\n\nCURRENT URL: {req.url or 'about:blank'}\n\n"
        f"RECENT ACTIONS:\n{history}\n\nDecide the next action."
    )
    content = [{"type": "text", "text": user_text}]
    if req.screenshot_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{req.screenshot_b64}"},
        })

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
                json={
                    "model": settings.OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": AGENT_SYSTEM},
                        {"role": "user", "content": content},
                    ],
                    "max_tokens": 500, "temperature": 0,
                },
            )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        log("llm", "agent_error", reason=str(e))
        return AgentAction(action="ask",
                           message=f"LLM call failed ({type(e).__name__}); take over or retry.")

    data = _extract_json(raw)
    # Log only the action type + thought (never the screenshot or typed text).
    log("llm", "agent_decided", act=data.get("action"))
    try:
        return AgentAction(**data)
    except Exception:
        return AgentAction(action="wait", thought="invalid action shape")
