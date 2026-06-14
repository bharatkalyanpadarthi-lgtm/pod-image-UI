#!/usr/bin/env bash
set -euo pipefail

echo "== FireRed-Image-Edit-1.1 ComfyUI RunPod setup =="

export HF_HUB_ENABLE_HF_TRANSFER=1

find_comfy_dir() {
  if [ -d "/workspace/ComfyUI" ]; then
    echo "/workspace/ComfyUI"
    return
  fi
  if [ -d "/workspace/madapps/ComfyUI" ]; then
    echo "/workspace/madapps/ComfyUI"
    return
  fi
  find /workspace -maxdepth 4 -type d -name ComfyUI 2>/dev/null | head -n 1
}

COMFY_DIR="${COMFY_DIR:-$(find_comfy_dir)}"

if [ -z "${COMFY_DIR}" ] || [ ! -d "${COMFY_DIR}" ]; then
  echo "Could not find ComfyUI under /workspace."
  echo "Set COMFY_DIR manually, for example:"
  echo "  export COMFY_DIR=/workspace/ComfyUI"
  exit 1
fi

echo "Using ComfyUI directory: ${COMFY_DIR}"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "${PYTHON_BIN}" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "Could not find python or python3."
    exit 1
  fi
fi
echo "Using Python: ${PYTHON_BIN}"

mkdir -p /workspace/input/batch_001
mkdir -p /workspace/output/batch_001
mkdir -p /workspace/workflows
mkdir -p /workspace/logs
mkdir -p /workspace/scripts
mkdir -p /workspace/archive

cd "${COMFY_DIR}"

echo "== Updating ComfyUI =="
if [ -d .git ]; then
  CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD || true)"
  if [ -n "${CURRENT_BRANCH}" ] && [ "${CURRENT_BRANCH}" != "HEAD" ]; then
    git pull origin "${CURRENT_BRANCH}" || true
  else
    git pull || true
  fi
fi

echo "== Activating venv if present =="
if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
elif [ -f "/workspace/venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "/workspace/venv/bin/activate"
fi

echo "== Installing Python helpers =="
"${PYTHON_BIN}" -m pip install -U pip
"${PYTHON_BIN}" -m pip install -U "huggingface_hub[cli]" hf_transfer
if [ -f requirements.txt ]; then
  "${PYTHON_BIN}" -m pip install -r requirements.txt
fi

echo "== Creating model folders =="
mkdir -p models/diffusion_models
mkdir -p models/text_encoders
mkdir -p models/vae
mkdir -p models/loras
mkdir -p custom_nodes

echo "== Downloading FireRed official files =="
hf download FireRedTeam/FireRed-Image-Edit-1.1-ComfyUI \
  FireRed-Image-Edit-1.1-transformer.safetensors \
  --local-dir models/diffusion_models

hf download FireRedTeam/FireRed-Image-Edit-1.1-ComfyUI \
  qwen2.5vl-7b-bf16.safetensors \
  --local-dir models/text_encoders

hf download FireRedTeam/FireRed-Image-Edit-1.1-ComfyUI \
  qwen_image_vae.safetensors \
  --local-dir models/vae

hf download FireRedTeam/FireRed-Image-Edit-1.1-ComfyUI \
  FireRed-Image-Edit-1.1-Lightning-8steps-v1.2.safetensors \
  --local-dir models/loras

hf download FireRedTeam/FireRed-Image-Edit-1.1-ComfyUI \
  firered-image-edit-1.1.json \
  --local-dir /workspace/workflows

echo "== Installing useful batch custom nodes =="
cd "${COMFY_DIR}/custom_nodes"
if [ ! -d "was-node-suite-comfyui" ]; then
  git clone https://github.com/WASasquatch/was-node-suite-comfyui.git || true
fi
if [ ! -d "ComfyUI-KJNodes" ]; then
  git clone https://github.com/kijai/ComfyUI-KJNodes.git || true
fi

cd "${COMFY_DIR}"
"${PYTHON_BIN}" -m pip install -r custom_nodes/was-node-suite-comfyui/requirements.txt || true
"${PYTHON_BIN}" -m pip install -r custom_nodes/ComfyUI-KJNodes/requirements.txt || true

echo "== Pointing ComfyUI output to persistent /workspace/output =="
if [ -d output ] && [ ! -L output ]; then
  mv output "output.backup.$(date +%Y%m%d_%H%M%S)"
fi
if [ ! -L output ]; then
  ln -s /workspace/output output
fi

echo "== Setup complete =="
echo "Open ComfyUI, load /workspace/workflows/firered-image-edit-1.1.json, and restart ComfyUI if nodes/models do not appear."
