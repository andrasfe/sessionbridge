"""Agent-harness factory and registry.

Mirrors ``llm_providers.factory``: built-in harnesses are registered lazily, a
registry allows custom harnesses, and ``llm_providers``-style entry points
(group ``sessionbridge.harnesses``) let external packages plug in.

Example:
    from harness import get_harness_from_env
    harness = get_harness_from_env()          # AGENT_HARNESS env, default "builtin"
    result = await harness.run(session, task, history, max_steps)
"""

from __future__ import annotations

import logging
import os
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from harness.protocol import AgentHarness

logger = logging.getLogger(__name__)

_HARNESSES: dict[str, type] = {}
_BUILTINS_REGISTERED = False
_PLUGINS_LOADED = False


def _register_builtins() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    _BUILTINS_REGISTERED = True

    from harness.builtin import BuiltinHarness
    _HARNESSES["builtin"] = BuiltinHarness

    # OpenHands is an optional heavy dependency — only register if importable.
    try:
        from harness.openhands_harness import OpenHandsHarness
        _HARNESSES["openhands"] = OpenHandsHarness
    except Exception as e:  # noqa: BLE001
        logger.info("openhands harness unavailable: %s", e)


def _discover_plugins() -> None:
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    _PLUGINS_LOADED = True
    try:
        eps = entry_points(group="sessionbridge.harnesses")
    except TypeError:  # py<3.10
        eps = entry_points().get("sessionbridge.harnesses", [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            if ep.name.lower() in _HARNESSES:
                continue
            _HARNESSES[ep.name.lower()] = ep.load()
            logger.info("discovered harness plugin: %s -> %s", ep.name, ep.value)
        except Exception as e:  # noqa: BLE001
            logger.warning("failed to load harness plugin '%s': %s", ep.name, e)


def get_available_harnesses() -> list[str]:
    _register_builtins()
    _discover_plugins()
    return list(_HARNESSES.keys())


def register_harness(name: str, harness_class: type) -> None:
    """Register a custom harness class implementing the AgentHarness protocol."""
    _HARNESSES[name.lower()] = harness_class


def create_harness(name: str, **kwargs: Any) -> AgentHarness:
    """Create a harness by name."""
    _register_builtins()
    _discover_plugins()
    key = name.lower()
    if key not in _HARNESSES:
        available = ", ".join(_HARNESSES) or "(none)"
        raise ValueError(f"Unknown harness '{name}'. Available: {available}")
    return cast("AgentHarness", _HARNESSES[key](**kwargs))


def get_harness_from_env() -> AgentHarness:
    """Create the harness selected by the AGENT_HARNESS env var (default builtin).

    Falls back to the built-in harness if the requested one is unavailable
    (e.g. AGENT_HARNESS=openhands but the package isn't installed) — fail open
    to the working loop rather than break the agent.
    """
    name = os.getenv("AGENT_HARNESS", "builtin").lower()
    _register_builtins()
    _discover_plugins()
    if name not in _HARNESSES:
        logger.warning("AGENT_HARNESS=%s not available; using builtin", name)
        name = "builtin"
    return create_harness(name)
