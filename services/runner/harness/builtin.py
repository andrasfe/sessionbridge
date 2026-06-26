"""Built-in vision-loop harness.

Wraps the original agent loop (``agent.run_agent``): screenshot -> ask the LLM
service for one action -> execute via CDP -> repeat. This is the default
harness and has no third-party dependencies.
"""

from __future__ import annotations

from typing import Any

import agent as _agent


class BuiltinHarness:
    """The native SessionBridge vision agent loop."""

    @property
    def name(self) -> str:
        return "builtin"

    async def run(
        self,
        session: Any,
        task: str,
        history: list[str],
        max_steps: int,
    ) -> dict[str, Any]:
        return await _agent.run_agent(session, task, history, max_steps)
