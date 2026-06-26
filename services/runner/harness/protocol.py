"""Agent-harness protocol.

A provider-agnostic interface for the *agent loop* that drives the remote
browser. The built-in vision loop and an OpenHands-backed loop both satisfy this
Protocol, so the runner can switch harnesses by env (AGENT_HARNESS) without code
changes — the same plugin pattern as ``llm_providers``.

A harness receives the live ``BrowserSession`` (it drives the browser through
``session.handle_input`` / ``session.page``), the user's task, the accumulated
action history, and a step cap. It returns a result dict matching
``shared.schemas.AgentRunResult``:

    {"state": JobState, "answer": str, "steps": list[dict], "message": str}
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentHarness(Protocol):
    """Protocol for an agent loop that drives the remote browser."""

    @property
    def name(self) -> str:
        """Short identifier for this harness (e.g. "builtin", "openhands")."""
        ...

    async def run(
        self,
        session: Any,
        task: str,
        history: list[str],
        max_steps: int,
    ) -> dict[str, Any]:
        """Drive ``session`` to accomplish ``task``.

        Args:
            session: The live BrowserSession (duck-typed to avoid import
                coupling). The harness acts on it via handle_input/page.
            task: Natural-language task for this turn.
            history: Accumulated action history across turns (mutated in place
                so later turns/harnesses share context).
            max_steps: Maximum agent steps before giving up.

        Returns:
            A dict with keys: state, answer, steps, message.
        """
        ...
