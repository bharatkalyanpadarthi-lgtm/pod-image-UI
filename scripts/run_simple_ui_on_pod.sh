#!/usr/bin/env bash
set -euo pipefail

POD_ID="${POD_ID:-t27hlszguhrqcb}"

cd "$(dirname "$0")"

echo "Starting pod ${POD_ID} if needed..."
START_OUTPUT="$(./.bin/runpodctl pod start "${POD_ID}" 2>&1 || true)"
if echo "${START_OUTPUT}" | grep -qi "not enough free GPUs"; then
  echo "${START_OUTPUT}"
  echo
  echo "RunPod does not currently have a free GPU on the old host for this stopped pod."
  echo "Try again later, or create a new pod in the same datacenter attached to the existing Network Volume."
  exit 1
fi
if echo "${START_OUTPUT}" | grep -qi '"error"'; then
  echo "${START_OUTPUT}"
  exit 1
fi

echo "Waiting for SSH info..."
IP=""
PORT=""
KEY=""
for attempt in $(seq 1 60); do
  INFO_JSON="$(./.bin/runpodctl ssh info "${POD_ID}" 2>/dev/null || true)"
  IP="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("ip",""))' <<<"${INFO_JSON}" 2>/dev/null || true)"
  PORT="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("port",""))' <<<"${INFO_JSON}" 2>/dev/null || true)"
  KEY="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("ssh_key",{}).get("path",""))' <<<"${INFO_JSON}" 2>/dev/null || true)"

  if [ -n "${IP}" ] && [ -n "${PORT}" ] && [ -n "${KEY}" ]; then
    echo "SSH ready: root@${IP}:${PORT}"
    break
  fi

  echo "Still waiting for SSH... (${attempt}/60)"
  sleep 10
done

if [ -z "${IP}" ] || [ -z "${PORT}" ] || [ -z "${KEY}" ]; then
  echo "Could not get SSH details for pod ${POD_ID}."
  echo "Try again in a minute, or run: ./.bin/runpodctl ssh info ${POD_ID}"
  exit 1
fi

echo "Uploading simple UI to pod..."
scp -i "${KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -P "${PORT}" \
  simple_firered_ui.py root@"${IP}":/workspace/simple_firered_ui.py

echo "Installing UI dependencies and starting app on port 7860..."
ssh -i "${KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -p "${PORT}" root@"${IP}" '
  set -e
  mkdir -p /workspace/logs
  python3 -m pip install -U gradio pillow
  if [ -f /workspace/logs/simple_firered_ui.pid ]; then
    old_pid="$(cat /workspace/logs/simple_firered_ui.pid || true)"
    if [ -n "${old_pid}" ]; then
      kill "${old_pid}" 2>/dev/null || true
    fi
  fi
  nohup python3 /workspace/simple_firered_ui.py > /workspace/logs/simple_firered_ui.log 2>&1 < /dev/null &
  echo $! > /workspace/logs/simple_firered_ui.pid
  sleep 5
  ps -p "$(cat /workspace/logs/simple_firered_ui.pid)" -o pid,cmd || true
  tail -40 /workspace/logs/simple_firered_ui.log || true
'

echo
echo "Simple UI should open here:"
echo "https://${POD_ID}-7860.proxy.runpod.net"
echo
echo "ComfyUI remains here if you need it:"
echo "https://${POD_ID}-8188.proxy.runpod.net"
