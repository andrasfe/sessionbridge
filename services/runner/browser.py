"""Browser session: Playwright + CDP screencast + input dispatch.

This object owns the only authenticated browser session in the whole system. It
never writes frames to disk (screenshot persistence is disabled), never exposes
cookies/profile/devtools, and forwards user input only while input forwarding is
enabled. During the login window automation is paused and nothing is logged.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Optional

from playwright.async_api import async_playwright

from shared.config import settings


class BrowserSession:
    def __init__(self, job_id: str, start_url: str):
        self.job_id = job_id
        self.start_url = start_url
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self._cdp = None
        # Bounded queue: drop stale frames rather than build latency.
        self.frame_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._screencasting = False

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=settings.HEADLESS,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        # accept_downloads so the connector can capture the PDF; a fresh context
        # every time means no local browser profile is ever reused.
        self._context = await self._browser.new_context(
            viewport={"width": settings.VIEWPORT_W, "height": settings.VIEWPORT_H},
            accept_downloads=True,
        )
        self.page = await self._context.new_page()
        self._cdp = await self._context.new_cdp_session(self.page)
        self._cdp.on("Page.screencastFrame", self._on_frame)
        # Don't fail session start if the login page is slow/unreachable — the
        # viewer still comes up and the user can retry. We never block the stream.
        try:
            await self.page.goto(self.start_url, wait_until="domcontentloaded",
                                 timeout=30000)
        except Exception:
            pass

    async def start_screencast(self) -> None:
        if self._screencasting:
            return
        self._screencasting = True
        # Enable the Page domain first so the screencast attaches even when the
        # initial navigation was slow or failed (we still bring the viewer up).
        await self._cdp.send("Page.enable")
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": settings.SCREENCAST_QUALITY,
                "maxWidth": settings.VIEWPORT_W,
                "maxHeight": settings.VIEWPORT_H,
                "everyNthFrame": 1,
            },
        )

    def _on_frame(self, params: dict) -> None:
        # Ack immediately so CDP keeps streaming; never persist the frame.
        session_id = params.get("sessionId")
        asyncio.create_task(self._ack(session_id))
        frame = {
            "type": "frame",
            "data": params["data"],
            "w": settings.VIEWPORT_W,
            "h": settings.VIEWPORT_H,
        }
        # Keep only the freshest frame.
        if self.frame_queue.full():
            try:
                self.frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.frame_queue.put_nowait(frame)
        except asyncio.QueueFull:
            pass

    async def _ack(self, session_id) -> None:
        try:
            await self._cdp.send("Page.screencastAck", {"sessionId": session_id})
        except Exception:
            pass

    # ---- input forwarding ------------------------------------------------
    async def handle_input(self, event: dict) -> None:
        """Forward a user input event to the page via CDP."""
        kind = event.get("kind")
        if kind == "mouse":
            await self._cdp.send("Input.dispatchMouseEvent", {
                "type": {"move": "mouseMoved", "down": "mousePressed",
                         "up": "mouseReleased"}.get(event.get("action"), "mouseMoved"),
                "x": float(event.get("x", 0)),
                "y": float(event.get("y", 0)),
                "button": event.get("button", "left") if event.get("action") != "move" else "none",
                "clickCount": int(event.get("clickCount", 1)) if event.get("action") in ("down", "up") else 0,
            })
        elif kind == "wheel":
            await self._cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": float(event.get("x", 0)),
                "y": float(event.get("y", 0)),
                "deltaX": float(event.get("deltaX", 0)),
                "deltaY": float(event.get("deltaY", 0)),
            })
        elif kind == "key":
            await self._cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown" if event.get("action") == "down" else "keyUp",
                "key": event.get("key", ""),
                "code": event.get("code", ""),
                "text": event.get("text", "") if event.get("action") == "down" else "",
            })
        elif kind == "text":
            # Used for paste; inserts text directly.
            await self._cdp.send("Input.insertText", {"text": event.get("text", "")})

    @property
    def current_host(self) -> Optional[str]:
        if not self.page:
            return None
        from urllib.parse import urlparse

        return urlparse(self.page.url).hostname

    async def destroy(self) -> None:
        """Tear everything down; the session never outlives the job."""
        try:
            if self._screencasting and self._cdp:
                await self._cdp.send("Page.stopScreencast")
        except Exception:
            pass
        for closer in (self._context, self._browser):
            try:
                if closer:
                    await closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self.page = None
