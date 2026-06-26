"""Pluggable agent harnesses.

The agent loop that drives the remote browser is selectable at runtime via the
AGENT_HARNESS env var. Built-in harnesses:

    - builtin:   native vision loop (screenshot -> LLM action -> CDP execute)
    - openhands: OpenHands agent loop driving the same browser via custom tools

Both satisfy the AgentHarness protocol. Add your own with register_harness()
or a ``sessionbridge.harnesses`` entry point.
"""

from harness.factory import (
    create_harness,
    get_available_harnesses,
    get_harness_from_env,
    register_harness,
)
from harness.protocol import AgentHarness

__all__ = [
    "AgentHarness",
    "create_harness",
    "get_available_harnesses",
    "get_harness_from_env",
    "register_harness",
]
