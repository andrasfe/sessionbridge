"""Deterministic T-Mobile statement connector.

Design principle: *conservative / fail closed*. The connector only ever follows
billing-related navigation, never clicks a forbidden control, and raises
`ConnectorUncertain` the moment it cannot confidently identify the next step —
at which point the runner hands control back to the user.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from playwright.async_api import Page, TimeoutError as PWTimeout

from shared import security


class ConnectorUncertain(Exception):
    """Raised when the connector is not confident; triggers fail-closed."""


@dataclass
class DownloadResult:
    path: str
    suggested_filename: str
    statement_date: Optional[str]
    source_host: str


# Candidate date formats, most specific first.
_DATE_FORMATS = ["%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"]
_DATE_RE = re.compile(
    r"([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})"
)


def _parse_date(text: str) -> Optional[datetime]:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


async def _click_billing_nav(page: Page) -> bool:
    """Follow the first safe billing-related link. Returns True if navigated."""
    for phrase in security.BILLING_NAV_PATTERNS:
        # Case-insensitive accessible-name match across links/buttons.
        loc = page.get_by_role("link", name=re.compile(re.escape(phrase), re.I))
        count = await loc.count()
        for i in range(min(count, 5)):
            item = loc.nth(i)
            try:
                text = (await item.inner_text(timeout=1000)).strip()
            except PWTimeout:
                continue
            if security.is_forbidden_control(text):
                continue
            try:
                await item.scroll_into_view_if_needed(timeout=2000)
                await item.click(timeout=4000)
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                return True
            except PWTimeout:
                continue
    return False


async def navigate_to_billing(page: Page) -> None:
    """Best-effort navigation toward the billing area. Fail closed if unsure."""
    # Two hops max: e.g. "Billing" -> "Bill details".
    moved = False
    for _ in range(2):
        if await _click_billing_nav(page):
            moved = True
        else:
            break
    if not security.host_allowed(_host(page)):
        raise ConnectorUncertain("Left allowed T-Mobile domain during navigation.")
    if not moved:
        # Maybe we already landed on a billing page after login.
        body = (await page.inner_text("body")) if page else ""
        if not re.search(r"bill|statement", body, re.I):
            raise ConnectorUncertain("Could not locate a billing section.")


async def find_and_download_latest(page: Page) -> DownloadResult:
    """Find the newest statement's PDF download and capture it."""
    candidates = []  # (date_or_None, locator, text)
    for phrase in security.DOWNLOAD_PATTERNS:
        loc = page.get_by_role("link", name=re.compile(re.escape(phrase), re.I))
        count = await loc.count()
        for i in range(min(count, 20)):
            item = loc.nth(i)
            try:
                text = (await item.inner_text(timeout=1000)).strip()
            except PWTimeout:
                continue
            if security.is_forbidden_control(text):
                continue
            # Look for a date in the link or its surrounding row.
            context_text = text
            try:
                row = item.locator("xpath=ancestor::*[self::tr or self::li or self::div][1]")
                if await row.count():
                    context_text = (await row.first.inner_text(timeout=1000)) or text
            except PWTimeout:
                pass
            candidates.append((_parse_date(context_text), item, text))
        if candidates:
            break  # Prefer the most specific download phrase that yielded hits.

    if not candidates:
        raise ConnectorUncertain("No statement download control found.")

    # Pick newest by parsed date; if none have dates and there is exactly one,
    # treat it as the latest (e.g. only the current bill offers a PDF).
    dated = [c for c in candidates if c[0] is not None]
    if dated:
        dated.sort(key=lambda c: c[0], reverse=True)
        chosen_date, chosen, _ = dated[0]
    elif len(candidates) == 1:
        chosen_date, chosen, _ = candidates[0]
    else:
        raise ConnectorUncertain(
            "Multiple statements found but none have a parseable date; refusing to guess."
        )

    try:
        async with page.expect_download(timeout=30000) as dl_info:
            await chosen.scroll_into_view_if_needed(timeout=3000)
            await chosen.click(timeout=5000)
        download = await dl_info.value
    except PWTimeout as e:
        raise ConnectorUncertain(f"Download did not start: {e}")

    tmp_path = await download.path()
    return DownloadResult(
        path=str(tmp_path),
        suggested_filename=download.suggested_filename or "tmobile-statement.pdf",
        statement_date=chosen_date.strftime("%Y-%m-%d") if chosen_date else None,
        source_host=_host(page) or "",
    )


def _host(page: Page) -> Optional[str]:
    from urllib.parse import urlparse

    return urlparse(page.url).hostname if page else None
