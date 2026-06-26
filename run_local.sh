#!/usr/bin/env bash
# Run all services locally without Docker (for development).
# Each service shares the same repo root on PYTHONPATH so `import shared` works.
set -euo pipefail
cd "$(dirname "$0")"

export PYTHONPATH="$PWD"
export CONTROL_PLANE_URL="http://localhost:8081"
export RUNNER_URL="http://localhost:8082"
export LLM_URL="http://localhost:8083"
export HEADLESS="${HEADLESS:-true}"
mkdir -p .data/logs

# Load optional .env (OpenRouter key, etc.)
[ -f .env ] && set -a && . ./.env && set +a

pids=()
start() {  # name dir port
  echo "starting $1 on :$3"
  ( cd "services/$2" && uvicorn app:app --host 0.0.0.0 --port "$3" \
      >"$OLDPWD/.data/logs/$1.log" 2>&1 ) &
  pids+=($!)
}

start llm         llm          8083
start runner      runner       8082
start controlplane controlplane 8081
start webapp      webapp       8080

echo "${pids[@]}" > .data/pids
echo
echo "SessionBridge up. Open http://localhost:8080"
echo "Logs in .data/logs/  ·  stop with ./stop_local.sh"
wait
