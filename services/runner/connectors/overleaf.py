"""Deterministic Overleaf "topmost project" connector.

After the user logs in, this reads the project dashboard and returns the title
of the topmost project (the first row — by default Overleaf's most-recently-
modified project). It opens/downloads nothing. Same conservative / fail-closed
posture: if it can't confidently find the project list it raises
ConnectorUncertain and hands control back to the user.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PWTimeout

from .tmobile import ConnectorUncertain  # shared fail-closed signal

# Overleaf project URLs look like /project/<24-hex-id>.
_PROJECT_RE = re.compile(r"/project/[0-9a-f]{24}", re.I)
_DASHBOARD = "https://www.overleaf.com/project"


def _host(page: Page) -> str | None:
    return urlparse(page.url).hostname if page else None


async def top_project(page: Page) -> dict:
    """Return {"top_project": <title>, "projects": [<title>, ...]}."""
    if _host(page) and not _host(page).endswith("overleaf.com"):
        raise ConnectorUncertain("Not on overleaf.com.")

    # Make sure we're on the project dashboard (login usually lands here).
    if "/project" not in (page.url or ""):
        try:
            await page.goto(_DASHBOARD, wait_until="domcontentloaded", timeout=30000)
        except PWTimeout:
            raise ConnectorUncertain("Could not open the Overleaf project dashboard.")

    try:
        await page.wait_for_selector('a[href*="/project/"]', timeout=15000)
    except PWTimeout:
        raise ConnectorUncertain(
            "No projects visible. Open your Project dashboard in the viewer, "
            "then click Continue again."
        )

    rows = await page.eval_on_selector_all(
        'a[href*="/project/"]',
        """els => els.map(e => ({
              title: (e.innerText || e.textContent || '').trim(),
              href: e.href
           }))""",
    )

    seen, projects = set(), []
    for r in rows:
        m = _PROJECT_RE.search(r.get("href", ""))
        title = (r.get("title") or "").strip()
        if not m or not title:
            continue
        pid = m.group(0)
        if pid in seen:
            continue
        seen.add(pid)
        projects.append(title)

    if not projects:
        raise ConnectorUncertain(
            "Logged in, but found no projects on the dashboard."
        )
    # DOM order matches the visible list, so the first entry is the topmost.
    return {"top_project": projects[0], "projects": projects}
