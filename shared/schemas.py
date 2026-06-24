"""Pydantic models shared across service boundaries."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .states import JobState


# ---------------------------------------------------------------------------
# Job / control-plane API
# ---------------------------------------------------------------------------
class CreateJobRequest(BaseModel):
    connector: str = "tmobile"


class JobStatus(BaseModel):
    job_id: str
    state: JobState
    message: str = ""
    # Non-secret host the runner is currently on (for the domain banner).
    current_host: Optional[str] = None
    artifact_id: Optional[str] = None
    # Connector-specific structured result (e.g. {"papers": [...]}).
    data: Optional[dict] = None
    updated_at: float


# ---------------------------------------------------------------------------
# Runner API
# ---------------------------------------------------------------------------
class StartSessionRequest(BaseModel):
    job_id: str
    connector: str = "tmobile"
    # Short-lived lease token minted by the control plane.
    lease_token: str
    start_url: str


class StartSessionResponse(BaseModel):
    session_id: str


class AutomateResponse(BaseModel):
    """Result of the deterministic connector run."""
    state: JobState
    message: str = ""
    artifact_id: Optional[str] = None
    statement_date: Optional[str] = None
    source_host: Optional[str] = None
    # Connector-specific structured result (e.g. {"papers": [...]}).
    data: Optional[dict] = None


# ---------------------------------------------------------------------------
# LLM service API (operates only on redacted, post-login visible text)
# ---------------------------------------------------------------------------
class ClassifyRequest(BaseModel):
    job_id: str
    # Already redacted by the caller; the LLM service redacts again defensively.
    redacted_text: str = Field(max_length=8000)
    candidate_labels: list[str]


class ClassifyResponse(BaseModel):
    label: Optional[str] = None
    confidence: float = 0.0
    enabled: bool = True
    note: str = ""


# ---------------------------------------------------------------------------
# Artifact service API
# ---------------------------------------------------------------------------
class ArtifactMetadata(BaseModel):
    artifact_id: str
    job_id: str
    filename: str
    size_bytes: int
    sha256: str
    content_type: str
    source_host: str
    statement_date: Optional[str] = None
    validation_status: str
    created_at: float


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
