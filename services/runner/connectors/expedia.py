"""Deterministic Expedia flight-results connector.

After the page shows flight results (the user runs/confirms the search in the
viewer, solving any "slide to verify" human-check themselves), this scrapes the
visible flight offers — price plus a short summary (times / airline / stops). It
books nothing and submits nothing. Fail-closed: if it can't find offers it hands
control back so the user can adjust the search.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PWTimeout

from .tmobile import ConnectorUncertain

# Selectors Expedia has used for a flight offer card, most specific first.
_OFFER_SELECTORS = [
    '[data-test-id="offer-listing"]',
    '[data-test-id="journey-listing"]',
    'ul[data-test-id="listings"] > li',
    '[data-stid="property-listing"]',
]
_PRICE_RE = re.compile(r"(?:US\$|\$|€|£|RON|lei)\s?\d[\d,.\s]*", re.I)
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\s?(?:[ap]\.?m\.?)?\b", re.I)


def _host(page: Page) -> str | None:
    return urlparse(page.url).hostname if page else None


async def _scroll(page: Page) -> None:
    for _ in range(5):
        await page.mouse.wheel(0, 3500)
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except PWTimeout:
            pass


async def list_flights(page: Page, limit: int = 25) -> dict:
    if _host(page) and not _host(page).endswith("expedia.com"):
        raise ConnectorUncertain("Not on expedia.com.")

    await _scroll(page)

    cards = []
    for sel in _OFFER_SELECTORS:
        try:
            cards = await page.eval_on_selector_all(
                sel, "els => els.map(e => (e.innerText || '').trim())")
        except Exception:
            cards = []
        cards = [c for c in cards if c]
        if cards:
            break

    flights = []
    for text in cards[:limit]:
        compact = re.sub(r"\s+", " ", text).strip()
        price_m = _PRICE_RE.search(compact)
        times = _TIME_RE.findall(compact)
        flights.append({
            "price": price_m.group(0).strip() if price_m else None,
            "times": " – ".join(times[:2]) if times else None,
            "summary": compact[:180],
        })

    # Keep only entries that look like real offers (have a price or times).
    flights = [f for f in flights if f["price"] or f["times"]]

    if not flights:
        raise ConnectorUncertain(
            "No flight offers found on the current page. Run/adjust the search in "
            "the viewer (and clear any human-verification slider), then click "
            "Continue again."
        )
    return {"flights": flights, "source_host": _host(page)}
