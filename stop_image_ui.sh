#!/usr/bin/env bash
set -euo pipefail

# Stop the saved RunPod image-editing pod so GPU billing stops.
# Uses Runpod REST API v2 and waits for the actual EXITED state.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

RUNPOD_POD_ID="${RUNPOD_POD_ID:-tcqjvnm1tfnu5u}"

runpod_api_key() {
  if [ -n "${RUNPOD_API_KEY:-}" ]; then
    printf '%s\n' "${RUNPOD_API_KEY}"
    return 0
  fi
  python3 - <<'PY'
import re
from pathlib import Path
p = Path.home() / ".runpod" / "config.toml"
if not p.exists():
    raise SystemExit(1)
m = re.search(r"apikey\s*=\s*'([^']+)'", p.read_text())
if not m:
    raise SystemExit(1)
print(m.group(1))
PY
}

echo "Stopping RunPod image pod ${RUNPOD_POD_ID} if running..."
api_key="$(runpod_api_key || true)"
if [ -n "${api_key}" ]; then
  RUNPOD_API_KEY_VALUE="${api_key}" python3 - "${RUNPOD_POD_ID}" <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

pod_id = sys.argv[1]
api_key = os.environ["RUNPOD_API_KEY_VALUE"]
url = f"https://api.runpod.io/v2/pods/{pod_id}"

def request(method="GET", action=None):
    req = urllib.request.Request(
        url + ("/action" if action else ""),
        data=json.dumps({"action": action}).encode() if action else None,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "pod-image-ui-launcher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"RunPod REST request failed: HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Runpod API connection failed: {exc.reason}")

payload = request()
before = payload
if payload.get("status") == "EXITED":
    print("RunPod REST status: EXITED (already stopped)")
    print("Session cost estimate: unavailable for an already stopped pod; no stop timestamp returned by API v2.")
    raise SystemExit(0)
payload = request("POST", "stop")
deadline = time.monotonic() + 180
while payload.get("status") != "EXITED":
    if time.monotonic() >= deadline:
        raise SystemExit(f"Stop not confirmed; RunPod status: {payload.get('status')}")
    time.sleep(2)
    payload = request()

print(f"RunPod REST status: {payload.get('status', 'unknown')}")


def parse_started_at(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z UTC", "%Y-%m-%d %H:%M:%S %z UTC"):
        try:
            return datetime.strptime(value, fmt).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def format_duration(hours):
    total_seconds = max(0, int(round(hours * 3600)))
    minutes, seconds = divmod(total_seconds, 60)
    hours_part, minutes = divmod(minutes, 60)
    return f"{hours_part}h {minutes}m {seconds}s"


started_at = parse_started_at(payload.get("startedAt") or before.get("startedAt"))
stopped_at = datetime.now(timezone.utc)
cost_per_hr = payload.get("cost", before.get("cost"))

if started_at and cost_per_hr is not None:
    hours = max(0.0, (stopped_at - started_at).total_seconds() / 3600)
    estimated_cost = hours * float(cost_per_hr)
    print(f"Session duration: {format_duration(hours)}")
    print(f"Session cost estimate: ${estimated_cost:.2f} at ${float(cost_per_hr):.2f}/hr")
else:
    print("Session cost estimate: unavailable; RunPod did not return start time or hourly rate.")
PY
else
  echo "No RunPod API key found for REST verification."
  exit 1
fi

echo
echo "Done. Check provider dashboard if you want independent billing confirmation."
