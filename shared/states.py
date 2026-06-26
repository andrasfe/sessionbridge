"""Job state machine for the browser-agent lifecycle.

A job owns one isolated remote browser session. After the browser is up the job
sits in READY; each agent turn (or user navigation) flips it RUNNING -> back to a
result state, and the chat is multi-turn so result states are NOT terminal. Only
FAILED / STOPPED_BY_USER end a job. We fail closed: anything uncertain returns
REQUIRES_USER_INTERVENTION and hands control back to the human.
"""
from __future__ import annotations

from enum import Enum


class JobState(str, Enum):
    CREATED = "created"
    REMOTE_BROWSER_STARTING = "remote_browser_starting"
    READY = "ready"                  # browser up; user can chat or drive it
    RUNNING = "running"              # an agent turn is executing
    COMPLETED = "completed"          # last agent turn produced an answer
    REQUIRES_USER_INTERVENTION = "requires_user_intervention"

    # Terminal.
    FAILED = "failed"
    STOPPED_BY_USER = "stopped_by_user"


TERMINAL_STATES = {JobState.FAILED, JobState.STOPPED_BY_USER}


def can_transition(src: JobState, dst: JobState) -> bool:
    """Permissive: any move is fine except leaving a terminal state.

    The chat is multi-turn, so COMPLETED / REQUIRES_USER_INTERVENTION must be
    re-enterable (the next message runs another turn). Only FAILED and
    STOPPED_BY_USER are dead ends.
    """
    if src in TERMINAL_STATES:
        return False
    return True
