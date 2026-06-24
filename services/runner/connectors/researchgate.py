"""Deterministic ResearchGate "list my publications" connector.

After the user logs in, this navigates to their own profile and collects the
list of published papers (title + link + year when visible). It downloads
nothing. Same conservative / fail-closed posture as the T-Mobile connector: if
it cannot confidently find the profile or any publications, it raises
ConnectorUncertain and control is handed back to the user.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PWTimeout

from .tmobile import ConnectorUncertain  # reuse the shared fail-closed signal


def _host(page: Page) -> str | None:
    return urlparse(page.url).hostname if page else None


def _has_titles(anchors: list[dict]) -> bool:
    return any((a.get("title") or "").strip() and len(a["title"].strip()) >= 6
               for a in anchors)


async def _goto_own_profile(page: Page) -> None:
    """Navigate to the logged-in user's profile page."""
    # The header avatar / name links to /profile/<name>. Grab the first such
    # link and follow it.
    try:
        await page.wait_for_selector('a[href*="/profile/"]', timeout=15000)
    except PWTimeout:
        raise ConnectorUncertain("Could not find a profile link after login.")
    href = await page.eval_on_selector(
        'a[href*="/profile/"]', "el => el.getAttribute('href')"
    )
    if not href:
        raise ConnectorUncertain("Profile link had no destination.")
    if href.startswith("/"):
        href = "https://www.researchgate.net" + href
    await page.goto(href, wait_until="domcontentloaded", timeout=30000)


async def _load_more(page: Page) -> None:
    """Scroll to trigger lazy-loading of publication cards."""
    for _ in range(6):
        await page.mouse.wheel(0, 4000)
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except PWTimeout:
            pass


async def _collect(page: Page) -> list[dict]:
    """Harvest publication links from the current page (after scrolling)."""
    await _load_more(page)
    return await page.eval_on_selector_all(
        'a[href*="/publication/"]',
        """els => els.map(e => ({
              title: (e.innerText || '').trim(),
              href: e.href
           }))""",
    )


async def list_publications(page: Page, limit: int = 200) -> list[dict]:
    """Return the user's publications as [{title, url, year?}, ...].

    Human-in-the-loop: collect from whatever page the user navigated to (ideally
    their own profile / publications tab). Only if that yields nothing do we try
    to follow a profile link as a fallback — we never guess which researcher's
    profile to scrape from a feed full of other people's links.
    """
    if _host(page) and not _host(page).endswith("researchgate.net"):
        raise ConnectorUncertain("Not on researchgate.net.")

    anchors = await _collect(page)
    if not _has_titles(anchors) and "/profile/" not in (page.url or ""):
        # Not on a profile page and nothing here — try the profile link.
        try:
            await _goto_own_profile(page)
            anchors = await _collect(page)
        except ConnectorUncertain:
            pass

    seen, papers = set(), []
    for a in anchors:
        title, href = a.get("title", ""), a.get("href", "")
        # Skip empty/utility links (e.g. stats, "Read more") — keep real titles.
        if not title or len(title) < 6:
            continue
        # Normalise the publication URL (strip query/fragment).
        key = href.split("?")[0].split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        year = None
        m = re.search(r"\b(19|20)\d{2}\b", title)
        if m:
            year = m.group(0)
        papers.append({"title": title, "url": key, "year": year})
        if len(papers) >= limit:
            break

    if not papers:
        raise ConnectorUncertain(
            "No publications found on the current page. Navigate to your profile "
            "(your publications tab) in the viewer, then click Continue again."
        )
    return papers
