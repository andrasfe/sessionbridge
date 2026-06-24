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
  Xvfb :99 -screen 0 "${VIEWPORT_W:-1280}x${VIEWPORT_H:-800}x24" -nolisten tcp &
  export DISPLAY=:99
  # Wait (briefly) for the X socket to appear before launching the API.
  i=0
  while [ ! -e /tmp/.X11-unix/X99 ] && [ "$i" -lt 50 ]; do i=$((i + 1)); sleep 0.1; done
fi

echo "runner: launching API (HEADLESS=${HEADLESS:-true})"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT}"
