#!/usr/bin/env bash
set -euo pipefail

# Start/restart the saved RunPod FireRed image-editing pod and deploy the UI.
# Uses runpodctl when available, otherwise falls back to RunPod REST API.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

RUNPOD_POD_ID="${RUNPOD_POD_ID:-rek8hrqadhx00k}"
MAX_WAIT_MINUTES="${MAX_WAIT_MINUTES:-20}"
POLL_SECONDS="${POLL_SECONDS:-10}"
UI_SCRIPT="${UI_SCRIPT:-${SCRIPT_DIR}/simple_firered_ui.py}"
VIDEO_UI_SCRIPT="${VIDEO_UI_SCRIPT:-${SCRIPT_DIR}/simple_wan_video_ui.py}"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/id_ed25519}"

if [ -x "${SCRIPT_DIR}/.bin/runpodctl" ]; then
  RUNPODCTL="${RUNPODCTL:-${SCRIPT_DIR}/.bin/runpodctl}"
else
  RUNPODCTL="${RUNPODCTL:-runpodctl}"
fi

log() {
  printf '%s\n' "$*"
}

json_get() {
  local expr="$1"
  python3 -c "import json,sys; d=json.load(sys.stdin); v=${expr}; print('' if v is None else v)"
}

has_runpodctl() {
  command -v "${RUNPODCTL}" >/dev/null 2>&1 || [ -x "${RUNPODCTL}" ]
}

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

runpod_rest() {
  local method="$1"
  local path="$2"
  local api_key
  api_key="$(runpod_api_key)"
  RUNPOD_API_KEY_VALUE="${api_key}" python3 - "${method}" "${path}" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

method, path = sys.argv[1], sys.argv[2]
api_key = os.environ["RUNPOD_API_KEY_VALUE"]
url = f"https://rest.runpod.io/v1/{path.lstrip('/')}"
data = b"" if method != "GET" else None
req = urllib.request.Request(
    url,
    data=data,
    method=method,
    headers={"Authorization": f"Bearer {api_key}"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "replace")
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", "replace")
    print(json.dumps({"error": body, "status": exc.code}))
    raise SystemExit(1)
print(body)
PY
}

require_file() {
  if [ ! -f "$1" ]; then
    log "Missing required file: $1"
    exit 1
  fi
}

require_file "${UI_SCRIPT}"
python3 -m py_compile "${UI_SCRIPT}"
if [ -f "${VIDEO_UI_SCRIPT}" ]; then
  python3 -m py_compile "${VIDEO_UI_SCRIPT}"
fi

log "Starting RunPod pod ${RUNPOD_POD_ID}..."
if has_runpodctl; then
  start_output="$("${RUNPODCTL}" pod start "${RUNPOD_POD_ID}" 2>&1 || true)"
  if echo "${start_output}" | grep -qi "not enough free GPUs"; then
    log "RunPod unavailable: no free GPU on the saved pod host."
    exit 1
  fi
  if echo "${start_output}" | grep -qi '"error"'; then
    log "RunPod start failed:"
    log "${start_output}"
    exit 1
  fi
  if [ -n "${start_output}" ]; then
    log "${start_output}"
  fi
else
  log "runpodctl not found; using RunPod REST API."
  if ! start_output="$(runpod_rest POST "pods/${RUNPOD_POD_ID}/start")"; then
    log "RunPod REST start failed:"
    log "${start_output}"
    exit 1
  fi
  log "${start_output}"
fi

max_attempts=$((MAX_WAIT_MINUTES * 60 / POLL_SECONDS))
[ "${max_attempts}" -ge 1 ] || max_attempts=1

ip=""
port=""
pod_cost=""
gpu_type=""
for attempt in $(seq 1 "${max_attempts}"); do
  if has_runpodctl; then
    info_json="$("${RUNPODCTL}" ssh info "${RUNPOD_POD_ID}" 2>/dev/null || true)"
    ip="$(json_get 'd.get("ip","")' <<<"${info_json}" 2>/dev/null || true)"
    port="$(json_get 'd.get("port","")' <<<"${info_json}" 2>/dev/null || true)"
  else
    info_json="$(runpod_rest GET "pods/${RUNPOD_POD_ID}" 2>/dev/null || true)"
    ip="$(json_get 'd.get("publicIp","")' <<<"${info_json}" 2>/dev/null || true)"
    port="$(json_get '((d.get("portMappings") or {}).get("22") or "")' <<<"${info_json}" 2>/dev/null || true)"
    pod_cost="$(json_get 'd.get("costPerHr","")' <<<"${info_json}" 2>/dev/null || true)"
    gpu_type="$(json_get '((d.get("machine") or {}).get("gpuTypeId") or "")' <<<"${info_json}" 2>/dev/null || true)"
  fi
  if [ -n "${ip}" ] && [ -n "${port}" ]; then
    break
  fi
  log "RunPod still waiting for SSH... (${attempt}/${max_attempts})"
  sleep "${POLL_SECONDS}"
done

if [ -z "${ip}" ] || [ -z "${port}" ]; then
  log "RunPod did not become SSH-ready within ${MAX_WAIT_MINUTES} minute(s)."
  exit 1
fi

log "RunPod SSH ready: root@${ip}:${port}"
log "Using SSH key: ${SSH_KEY}"

scp -i "${SSH_KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -P "${port}" \
  "${UI_SCRIPT}" "root@${ip}:/workspace/simple_firered_ui.py"
if [ -f "${VIDEO_UI_SCRIPT}" ]; then
  scp -i "${SSH_KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -P "${port}" \
    "${VIDEO_UI_SCRIPT}" "root@${ip}:/workspace/simple_wan_video_ui.py"
fi

ssh -i "${SSH_KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -p "${port}" "root@${ip}" '
  set -e
  mkdir -p /workspace/logs
  python3 -m pip install -U gradio pillow >/workspace/logs/simple_firered_ui_pip.log 2>&1 || {
    tail -80 /workspace/logs/simple_firered_ui_pip.log
    exit 1
  }

  if [ -f /workspace/logs/comfyui.pid ]; then
    old="$(cat /workspace/logs/comfyui.pid || true)"
    [ -n "$old" ] && kill "$old" 2>/dev/null || true
  fi
  if [ -f /workspace/logs/simple_firered_ui.pid ]; then
    old="$(cat /workspace/logs/simple_firered_ui.pid || true)"
    [ -n "$old" ] && kill "$old" 2>/dev/null || true
  fi
  sleep 2

  comfy_dir=""
  for dir in /workspace/runpod-slim/ComfyUI /workspace/ComfyUI /ComfyUI; do
    if [ -d "$dir" ]; then
      comfy_dir="$dir"
      break
    fi
  done
  if [ -z "$comfy_dir" ]; then
    echo "Could not find ComfyUI directory on pod."
    exit 1
  fi

  cd "$comfy_dir"
  nohup python3 main.py --listen 0.0.0.0 --port 8188 --enable-cors-header \
    > /workspace/logs/comfyui.log 2>&1 < /dev/null &
  echo $! > /workspace/logs/comfyui.pid
  sleep 12

  nohup python3 /workspace/simple_firered_ui.py \
    > /workspace/logs/simple_firered_ui.log 2>&1 < /dev/null &
  echo $! > /workspace/logs/simple_firered_ui.pid
  sleep 5

  ps -p "$(cat /workspace/logs/simple_firered_ui.pid)" -o pid,cmd
'

ui_url="https://${RUNPOD_POD_ID}-7860.proxy.runpod.net"
comfy_url="https://${RUNPOD_POD_ID}-8188.proxy.runpod.net"

log ""
log "READY"
log "Provider: RunPod"
log "Simple UI: ${ui_url}"
log "ComfyUI:    ${comfy_url}"
if [ -n "${gpu_type}" ]; then
  log "GPU:        ${gpu_type}"
fi
if [ -n "${pod_cost}" ]; then
  log "Cost:      about \$${pod_cost}/hr while running"
else
  log "Cost:      check RunPod pod details for current hourly rate"
fi
