"""Job state machine.

The happy path is a strict linear progression. A handful of terminal/branch
states can be entered from (almost) anywhere because the system is required to
"fail closed": if anything is uncertain we stop and hand control back.
"""
from __future__ import annotations

from enum import Enum


class JobState(str, Enum):
    CREATED = "created"
    REMOTE_BROWSER_STARTING = "remote_browser_starting"
    AWAITING_USER_LOGIN = "awaiting_user_login"
    LOGIN_IN_PROGRESS = "login_in_progress"
    LOGIN_CONFIRMED_BY_USER = "login_confirmed_by_user"
    CHECKING_DOMAIN = "checking_domain"
    NAVIGATING_TO_BILLING = "navigating_to_billing"
    FINDING_LATEST_STATEMENT = "finding_latest_statement"
    DOWNLOADING_PDF = "downloading_pdf"
    VALIDATING_PDF = "validating_pdf"
    COMPLETED = "completed"

    # Branch / terminal states reachable from many points.
    FAILED = "failed"
    STOPPED_BY_USER = "stopped_by_user"
    REQUIRES_USER_INTERVENTION = "requires_user_intervention"


# Linear happy-path order used to validate forward transitions.
HAPPY_PATH = [
    JobState.CREATED,
    JobState.REMOTE_BROWSER_STARTING,
    JobState.AWAITING_USER_LOGIN,
    JobState.LOGIN_IN_PROGRESS,
    JobState.LOGIN_CONFIRMED_BY_USER,
    JobState.CHECKING_DOMAIN,
    JobState.NAVIGATING_TO_BILLING,
    JobState.FINDING_LATEST_STATEMENT,
    JobState.DOWNLOADING_PDF,
    JobState.VALIDATING_PDF,
    JobState.COMPLETED,
]

TERMINAL_STATES = {
    JobState.COMPLETED,
    JobState.FAILED,
    JobState.STOPPED_BY_USER,
}

# States during which the user is interacting with the login form. While in any
# of these the system MUST: pause automation, disable the LLM, disable input
# logging, and disable screenshot persistence.
LOGIN_STATES = {
    JobState.AWAITING_USER_LOGIN,
    JobState.LOGIN_IN_PROGRESS,
}

# Branch states can always be entered (we fail closed).
_BRANCH = {
    JobState.FAILED,
    JobState.STOPPED_BY_USER,
    JobState.REQUIRES_USER_INTERVENTION,
}


def can_transition(src: JobState, dst: JobState) -> bool:
    """Allow forward moves along the happy path plus fail-closed branches."""
    if dst in _BRANCH:
        return src not in TERMINAL_STATES
    if src in TERMINAL_STATES:
        return False
    if src in HAPPY_PATH and dst in HAPPY_PATH:
        return HAPPY_PATH.index(dst) >= HAPPY_PATH.index(src)
    # Allow resuming from an intervention back onto the happy path.
    if src == JobState.REQUIRES_USER_INTERVENTION and dst in HAPPY_PATH:
        return True
    return False


def automation_allowed(state: JobState) -> bool:
    """Automation/LLM/logging are only permitted outside the login window."""
    return state not in LOGIN_STATES and state not in {JobState.CREATED}
