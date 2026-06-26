"""Pydantic models shared across service boundaries."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .states import JobState


# ---------------------------------------------------------------------------
# Job / control-plane API
# ---------------------------------------------------------------------------
class CreateJobRequest(BaseModel):
    # Reserved for future options; a job is just an isolated browser session.
    pass


class JobStatus(BaseModel):
    job_id: str
    state: JobState
    message: str = ""
    # Non-secret host the runner is currently on (for the domain banner).
    current_host: Optional[str] = None
    updated_at: float


# ---------------------------------------------------------------------------
# Runner API
# ---------------------------------------------------------------------------
class StartSessionRequest(BaseModel):
    job_id: str
    # Short-lived lease token minted by the control plane.
    lease_token: str
    start_url: str


class StartSessionResponse(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# Browser agent (chatbot). The builtin vision harness asks the LLM service for
# ONE action from a screenshot + task + history; the runner validates and
# executes it. The LLM never touches the browser directly.
# ---------------------------------------------------------------------------
class AgentDecideRequest(BaseModel):
    task: str
    url: str = ""
    history: list[str] = Field(default_factory=list)
    screenshot_b64: str = ""  # JPEG, base64


class AgentAction(BaseModel):
    thought: str = ""
    # navigate | click | type | key | scroll | wait | ask | done
    action: str
    url: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    text: Optional[str] = None
    key: Optional[str] = None
    dy: Optional[float] = None
    message: Optional[str] = None
    answer: Optional[str] = None


class AgentRunRequest(BaseModel):
    task: str
    lease_token: str
    max_steps: int = 18


class AgentRunResult(BaseModel):
    state: JobState
    answer: str = ""
    steps: list[dict] = Field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Streaming WS protocol (one socket per hop, JSON frames)
# ---------------------------------------------------------------------------
# Down (runner -> user):
#   {"type": "frame", "data": "<base64 jpeg>", "w": int, "h": int}
#   {"type": "status", "state": "<JobState>", "message": str, "host": str|None}
# Up (user -> runner):
#   {"type": "input", "event": {...}}  # see security/runner for event shapes
WS_FRAME = "frame"
WS_STATUS = "status"
WS_INPUT = "input"
