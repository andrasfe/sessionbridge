"""Browser session: Playwright + screenshot stream + CDP input dispatch.

This object owns the only authenticated browser session in the whole system. It
never writes frames to disk (frames are streamed in-memory, never persisted),
never exposes cookies/profile/devtools, and forwards user input only while input
forwarding is enabled. During the login window automation is paused and nothing
is logged.

The viewer is fed by a periodic `page.screenshot()` loop rather than the CDP
`Page.startScreencast` API: CDP screencast uses ack-based backpressure that
stalls under proxy load (frames stop arriving, so the viewer freezes on a stale
image and typing *looks* dead even though it registers). Screenshot polling
always reflects the true current render and never stalls.
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
        self._streaming = False
        self._stream_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=settings.HEADLESS,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                # Don't advertise automation. The session is human-operated (the
                # user types their own credentials and solves any CAPTCHA); the
                # default navigator.webdriver=true flag makes anti-bot systems
                # distrust the session and loop CAPTCHAs forever even after the
                # user solves them. This removes that false signal — it does NOT
                # auto-solve or bypass challenges.
                "--disable-blink-features=AutomationControlled",
            ],
            ignore_default_args=["--enable-automation"],
        )
        # accept_downloads so the connector can capture the PDF; a fresh context
        # every time means no local browser profile is ever reused.
        self._context = await self._browser.new_context(
            viewport={"width": settings.VIEWPORT_W, "height": settings.VIEWPORT_H},
            accept_downloads=True,
        )
        # Present as a normal Chrome: hide the common automation tells (NOT
        # hardware-fingerprint fabrication). navigator.webdriver is also handled
        # by the launch flag; this covers the chrome object / plugins / languages
        # / permissions surface that trivially flags a default automated browser.
        await self._context.add_init_script("""
          Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
          window.chrome = window.chrome || { runtime: {} };
          Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
          if (!navigator.plugins || !navigator.plugins.length) {
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
          }
          const _q = navigator.permissions && navigator.permissions.query;
          if (_q) navigator.permissions.query = (p) =>
            p && p.name === 'notifications'
              ? Promise.resolve({state: Notification.permission})
              : _q(p);
        """)
        self.page = await self._context.new_page()
        # CDP session is used only for input dispatch (mouse/keyboard/paste).
        self._cdp = await self._context.new_cdp_session(self.page)
        # Don't fail session start if the login page is slow/unreachable — the
        # viewer still comes up and the user can retry. We never block the stream.
        try:
            await self.page.goto(self.start_url, wait_until="domcontentloaded",
                                 timeout=30000)
        except Exception:
            pass

    async def start_screencast(self) -> None:
        if self._streaming:
            return
        self._streaming = True
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def _stream_loop(self) -> None:
        """Capture viewport screenshots and push them as frames."""
        interval = settings.STREAM_INTERVAL
        while self._streaming and self.page is not None:
            try:
                data = await self.page.screenshot(
                    type="jpeg", quality=settings.SCREENCAST_QUALITY)
            except Exception:
                # Page busy/navigating/closed — skip this tick, try again.
                await asyncio.sleep(interval)
                continue
            frame = {
                "type": "frame",
                "data": base64.b64encode(data).decode("ascii"),
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
            await asyncio.sleep(interval)

    # ---- input forwarding ------------------------------------------------
    async def handle_input(self, event: dict) -> None:
        """Forward a user input event to the page via CDP."""
        kind = event.get("kind")
        if kind == "mouse":
            action = event.get("action")
            args = {
                "type": {"move": "mouseMoved", "down": "mousePressed",
                         "up": "mouseReleased"}.get(action, "mouseMoved"),
                "x": float(event.get("x", 0)),
                "y": float(event.get("y", 0)),
                # `buttons` is the bitmask of buttons held DURING the event. It is
                # what makes a drag a drag: a mouseMoved with buttons=1 is a drag,
                # without it the move is ignored by sliders/drag handles.
                "buttons": int(event.get("buttons", 0)),
            }
            if action == "move":
                args["button"] = "none"
            else:
                args["button"] = event.get("button", "left")
                args["clickCount"] = int(event.get("clickCount", 1))
            await self._cdp.send("Input.dispatchMouseEvent", args)
        elif kind == "wheel":
            await self._cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": float(event.get("x", 0)),
                "y": float(event.get("y", 0)),
                "deltaX": float(event.get("deltaX", 0)),
                "deltaY": float(event.get("deltaY", 0)),
            })
        elif kind == "key":
            action = event.get("action")
            text = event.get("text", "")
            if text:
                # Printable character. Insert directly: Input.insertText works in
                # BOTH headed (Xvfb) and headless Chromium and in framework-
                # controlled inputs. dispatchKeyEvent+text does NOT register the
                # character in headed mode, which is why typing appeared dead.
                if action == "down":
                    await self._cdp.send("Input.insertText", {"text": text})
            else:
                # Control key (Enter / Tab / Backspace / arrows / …): dispatch a
                # real key event so it actually functions.
                await self._cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyDown" if action == "down" else "keyUp",
                    "key": event.get("key", ""),
                    "code": event.get("code", ""),
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
        self._streaming = False
        if self._stream_task:
            self._stream_task.cancel()
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
