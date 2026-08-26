# FireRed Simple UI on RunPod

This repo contains the FireRed image-editing Gradio UI and the operational scripts needed to start, stop, and maintain the RunPod setup.

## Main Files

- `simple_firered_ui.py` - FireRed Simple Editor UI.
- `simple_wan_video_ui.py` - Optional video UI loaded by the simple editor when present on the pod.
- `start_image_ui.sh` - Starts the saved RunPod pod, uploads the UI files, starts ComfyUI, and starts the Gradio UI.
- `stop_image_ui.sh` - Stops the saved RunPod pod. Includes a REST API fallback because some local `runpodctl` copies can silently return no status.
- `scripts/setup_pod_firered.sh` - Installs/downloads FireRed model files and useful ComfyUI nodes on a pod.
- `scripts/run_simple_ui_on_pod.sh` - Older helper for starting the UI on a pod.

The launcher disables ComfyUI DynamicVRAM because it can stall FireRed/Qwen model initialization on A100 80 GB pods. ComfyUI's standard model loader is used instead. ComfyUI Manager uses its local offline cache during normal launches, and the launcher waits for startup work to finish before reporting the UI as ready.

## Required Local Setup

Install/configure RunPod CLI or place `runpodctl` at:

```bash
.bin/runpodctl
```

The scripts also support a global `runpodctl` on `PATH`.

SSH defaults to:

```bash
~/.ssh/id_ed25519
```

Override when needed:

```bash
SSH_KEY=/path/to/key ./start_image_ui.sh
```

## Start

```bash
./start_image_ui.sh
```

Current default pod:

```text
2bjgj04g6hdkci
```

Override:

```bash
RUNPOD_POD_ID=your_pod_id ./start_image_ui.sh
```

## Stop

```bash
./stop_image_ui.sh
```

Override:

```bash
RUNPOD_POD_ID=your_pod_id ./stop_image_ui.sh
```

## Important Security Rule

Do not commit local secret/config files:

```text
config.env
vast.env
r2.env
rclone.conf
.bin/
```

These are intentionally ignored.
