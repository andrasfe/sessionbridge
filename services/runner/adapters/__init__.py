"""API-render adapters: the "API plane".

For sites whose browser DOM is bot-walled but which expose an official API, an
adapter fetches the API and returns a clean, self-contained HTML page. The runner
serves that page into the live browser via request interception, so the user (and
the agent) see a normal page in the canvas — re-entrant on link clicks — while the
data flows through the sanctioned API instead of the blocked DOM.

The fetch runs in the runner (the sole egress boundary), driven by the control
plane's existing navigate — nothing bypasses the chain.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable, Optional

from . import hackernews, reddit

# Each adapter exposes matches(url) -> bool and async render(url) -> str | None.
_ADAPTERS = [hackernews, reddit]

# Regex over all adapter hosts, used to scope Playwright route interception so
# only these domains are touched; everything else browses normally.
ROUTE_PATTERN = re.compile(
    r"^https?://([a-z0-9-]+\.)*(news\.ycombinator\.com|reddit\.com)(/|$)",
    re.IGNORECASE,
)


def pick(url: str) -> Optional[Callable[[str], Awaitable[Optional[str]]]]:
    """Return the render() for the first adapter that claims this URL, else None."""
    for a in _ADAPTERS:
        try:
            if a.matches(url):
                return a.render
        except Exception:
            continue
    return None
