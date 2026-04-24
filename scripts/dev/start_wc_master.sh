#!/bin/bash
# Start web_console_ng from host Python against current checkout.
#
# Intended fallback for environments where docker web_console image rebuild is
# temporarily blocked. This script:
# 1) Stops docker web_console_dev to free port 8080
# 2) Loads env vars from .env (or provided env file)
# 3) Rewrites docker service hostnames to localhost host-mapped ports
# 4) Starts apps.web_console_ng.main in the background and waits for readiness

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${1:-$ROOT_DIR/.env}"
VENV_PY="$ROOT_DIR/.venv/bin/python"
LOG_DIR="${WC_MASTER_LOG_DIR:-/tmp/wc_master_logs}"
PID_FILE="${WC_MASTER_PID_FILE:-/tmp/wc_master.pid}"
HOST_PORT="${WC_MASTER_PORT:-8080}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing python executable: $VENV_PY"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE"
  exit 1
fi

echo "Stopping docker web console (if running) to free port $HOST_PORT..."
docker stop trading_platform_web_console_dev >/dev/null 2>&1 || true

TMP_ENV_SH="$(mktemp /tmp/wc_master_env.XXXXXX.sh)"

"$VENV_PY" - <<'PY' "$ENV_FILE" "$TMP_ENV_SH"
import pathlib
import re
import shlex
import sys

env_path = pathlib.Path(sys.argv[1])
out_path = pathlib.Path(sys.argv[2])

mapping = {
    "@redis:6379": "@localhost:6379",
    "//redis:6379": "//localhost:6379",
    "REDIS_HOST=redis": "REDIS_HOST=localhost",
    "@postgres:5432": "@localhost:5433",
    "//postgres:5432": "//localhost:5433",
    "POSTGRES_HOST=postgres": "POSTGRES_HOST=localhost",
    "@execution_gateway:8002": "@localhost:8002",
    "//execution_gateway:8002": "//localhost:8002",
    "@orchestrator:8003": "@localhost:8003",
    "//orchestrator:8003": "//localhost:8003",
    "@market_data_service:8004": "@localhost:8004",
    "//market_data_service:8004": "//localhost:8004",
    "@signal_service:8001": "@localhost:8001",
    "//signal_service:8001": "//localhost:8001",
    "@model_registry:8005": "@localhost:8005",
    "//model_registry:8005": "//localhost:8005",
    "@auth_service:8006": "@localhost:8006",
    "//auth_service:8006": "//localhost:8006",
    "//loki:3100": "//localhost:3100",
    "//prometheus:9090": "//localhost:9090",
}

def parse_line(raw: str) -> tuple[str, str] | None:
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", stripped)
    if not match:
        return None
    key = match.group(1)
    value = match.group(2).strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return key, value

lines = ["#!/bin/bash", "set -a"]
for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    parsed = parse_line(raw_line)
    if parsed is None:
        continue
    key, value = parsed
    merged = f"{key}={value}"
    for source, replacement in mapping.items():
        merged = merged.replace(source, replacement)
    final_key, _, final_value = merged.partition("=")
    lines.append(f"export {final_key}={shlex.quote(final_value)}")
lines.append("set +a")

out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

chmod +x "$TMP_ENV_SH"
# shellcheck disable=SC1090
source "$TMP_ENV_SH"

export HOST=0.0.0.0
export PORT="$HOST_PORT"
export PYTHONPATH="$ROOT_DIR"

mkdir -p "$LOG_DIR"
echo "Starting web console from host checkout..."
"$VENV_PY" -m apps.web_console_ng.main >"$LOG_DIR/out.log" 2>&1 &
PID=$!
echo "$PID" >"$PID_FILE"
echo "started pid=$PID (logs: $LOG_DIR/out.log)"

for i in {1..60}; do
  if curl -s -o /dev/null "http://localhost:$HOST_PORT/login" 2>/dev/null; then
    echo "ready after ${i}s"
    rm -f "$TMP_ENV_SH"
    exit 0
  fi
  sleep 1
done

echo "Web console did not become ready in 60s; tailing log:"
tail -30 "$LOG_DIR/out.log" || true
rm -f "$TMP_ENV_SH"
exit 1
