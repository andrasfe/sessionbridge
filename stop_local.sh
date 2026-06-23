#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .data/pids ]; then
  kill $(cat .data/pids) 2>/dev/null || true
  rm -f .data/pids
fi
# Sweep any stragglers bound to our ports.
pkill -f "uvicorn app:app --host 0.0.0.0 --port 808" 2>/dev/null || true
echo "stopped."
