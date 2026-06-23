"""LLM service (optional).

Classifies REDACTED, post-login visible page text into one of a few candidate
labels to help the control plane reason about page type. It never controls the
browser and never sees credentials, cookies, tokens, login pages or PDF content.
Disabled entirely unless an OpenRouter key is configured.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI

from shared.config import settings
from shared.logging import log
from shared.security import redact
from shared.schemas import ClassifyRequest, ClassifyResponse

app = FastAPI(title="sessionbridge-llm")


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
