#!/usr/bin/env bash
set -euo pipefail

# Stop the saved RunPod image-editing pod so GPU billing stops.
# Uses runpodctl first, then the RunPod REST API as a fallback/verification path.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

RUNPOD_POD_ID="${RUNPOD_POD_ID:-0zdfnpygkahac2}"

if [ -x "${SCRIPT_DIR}/.bin/runpodctl" ]; then
  RUNPODCTL="${RUNPODCTL:-${SCRIPT_DIR}/.bin/runpodctl}"
else
  RUNPODCTL="${RUNPODCTL:-runpodctl}"
fi

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
if command -v "${RUNPODCTL}" >/dev/null 2>&1 || [ -x "${RUNPODCTL}" ]; then
  "${RUNPODCTL}" pod stop "${RUNPOD_POD_ID}" 2>&1 || true
fi

api_key="$(runpod_api_key || true)"
if [ -n "${api_key}" ]; then
  RUNPOD_API_KEY_VALUE="${api_key}" python3 - "${RUNPOD_POD_ID}" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

pod_id = sys.argv[1]
api_key = os.environ["RUNPOD_API_KEY_VALUE"]
url = f"https://rest.runpod.io/v1/pods/{pod_id}/stop"
req = urllib.request.Request(
    url,
    data=b"",
    method="POST",
    headers={"Authorization": f"Bearer {api_key}"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", "replace")
    print(f"RunPod REST stop failed: HTTP {exc.code}: {body}")
    raise SystemExit(1)

print(f"RunPod REST status: {payload.get('desiredStatus', 'unknown')}")
print(f"Last status change: {payload.get('lastStatusChange', 'unknown')}")
PY
else
  echo "No RunPod API key found for REST verification."
fi

echo
echo "Done. Check provider dashboard if you want independent billing confirmation."
