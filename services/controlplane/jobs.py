"""In-memory job store + state machine guard.

For the PoC jobs live in process memory. On AWS this maps to DynamoDB (the same
JobStatus shape) so multiple control-plane tasks can share state; swapping the
store is the only change required.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from shared.lease import Lease
from shared.states import JobState, can_transition


@dataclass
class Job:
    job_id: str
    state: JobState = JobState.CREATED
    message: str = ""
    current_host: Optional[str] = None
    session_id: Optional[str] = None
    lease: Optional[Lease] = None
    updated_at: float = field(default_factory=time.time)

    def set_state(self, state: JobState, message: str = "") -> None:
        if state != self.state and not can_transition(self.state, state):
            # Illegal transition: fail closed rather than silently proceed.
            raise ValueError(f"illegal transition {self.state} -> {state}")
        self.state = state
        if message:
            self.message = message
        self.updated_at = time.time()


JOBS: dict[str, Job] = {}


def get(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if not job:
        raise KeyError(job_id)
    return job
