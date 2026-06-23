"""Validates the core viewer mechanism: CDP screencast frames flow out of a real
BrowserSession and input dispatch doesn't error. Uses a local data: page so no
network/T-Mobile access is involved."""
import asyncio
import base64
import sys

sys.path.insert(0, "services/runner")
from browser import BrowserSession  # noqa: E402

PAGE = "data:text/html,<html><body style='background:%23e20074'><h1>hi</h1>" \
       "<input id=x /></body></html>"


async def main():
    sess = BrowserSession("job_test", PAGE)
    await sess.start()
    await sess.start_screencast()

    # Wait for at least one screencast frame.
    frame = await asyncio.wait_for(sess.frame_queue.get(), timeout=15)
    ok_frame = frame["type"] == "frame" and len(base64.b64decode(frame["data"])) > 100
    print("PASS frame received" if ok_frame else "FAIL frame received",
          f"({len(base64.b64decode(frame['data']))} bytes)")

    # Exercise input dispatch (must not raise).
    try:
        await sess.handle_input({"kind": "mouse", "action": "move", "x": 10, "y": 10})
        await sess.handle_input({"kind": "mouse", "action": "down", "x": 10, "y": 10,
                                 "button": "left", "clickCount": 1})
        await sess.handle_input({"kind": "mouse", "action": "up", "x": 10, "y": 10,
                                 "button": "left", "clickCount": 1})
        await sess.handle_input({"kind": "key", "action": "down", "key": "a", "code": "KeyA", "text": "a"})
        await sess.handle_input({"kind": "key", "action": "up", "key": "a", "code": "KeyA"})
        await sess.handle_input({"kind": "text", "text": "hello"})
        await sess.handle_input({"kind": "wheel", "x": 10, "y": 10, "deltaX": 0, "deltaY": 100})
        print("PASS input dispatch")
        ok_input = True
    except Exception as e:  # noqa: BLE001
        print("FAIL input dispatch:", e)
        ok_input = False

    host = sess.current_host
    print("PASS current_host" if host is None or isinstance(host, str) else "FAIL host")

    await sess.destroy()
    print("PASS session destroyed")
    return ok_frame and ok_input


ok = asyncio.run(main())
print("\nRESULT:", "ALL TESTS PASSED" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
