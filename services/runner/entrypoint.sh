#!/bin/sh
# Runner entrypoint.
#
# When HEADLESS=false we start a virtual X display (Xvfb) and run Chromium
# *headed*. A headed browser sends a normal user-agent (not "HeadlessChrome")
# and behaves like a real one — this is a real display, NOT fingerprint spoofing
# or bot-detection evasion. When HEADLESS=true we run plain headless, which is
# lighter and fine for testing.
#
# We start Xvfb directly (rather than via `xvfb-run`) because xvfb-run hangs when
# it runs as PID 1 inside a container; here uvicorn becomes PID 1 and Xvfb is a
# managed background child.
set -e

PORT="${PORT:-8082}"

if [ "${HEADLESS}" = "false" ]; then
  echo "runner: starting headed browser under Xvfb"
  # The display must be LARGER than any viewport the viewer will request: a headed
  # browser window (and so its viewport) can't grow past the virtual screen. The
  # dynamic 1:1 viewport tracks the user's panel (height 70vh), which easily
  # exceeds 800px on normal monitors — so size the display generously (default 4K)
  # and let the runner's own clamp (2560x1600) be the real bound.
  Xvfb :99 -screen 0 "${XVFB_W:-3840}x${XVFB_H:-2160}x24" -nolisten tcp &
  export DISPLAY=:99
  # Wait (briefly) for the X socket to appear before launching the API.
  i=0
  while [ ! -e /tmp/.X11-unix/X99 ] && [ "$i" -lt 50 ]; do i=$((i + 1)); sleep 0.1; done
fi

echo "runner: launching API (HEADLESS=${HEADLESS:-true})"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT}"
