# FireRed Simple UI on RunPod

This repo contains the FireRed image-editing Gradio UI and the operational scripts needed to start, stop, and maintain the RunPod setup.

## Main Files

- `simple_firered_ui.py` - FireRed Simple Editor UI.
- `simple_wan_video_ui.py` - Optional video UI loaded by the simple editor when present on the pod.
- `start_image_ui.sh` - Starts the saved RunPod pod, uploads the UI files, starts ComfyUI, and starts the Gradio UI.
- `stop_image_ui.sh` - Stops the saved RunPod pod. Includes a REST API fallback because some local `runpodctl` copies can silently return no status.
- `scripts/setup_pod_firered.sh` - Installs/downloads FireRed model files and useful ComfyUI nodes on a pod.
- `scripts/run_simple_ui_on_pod.sh` - Older helper for starting the UI on a pod.

The launcher disables ComfyUI DynamicVRAM because it can stall FireRed/Qwen model initialization on A100 80 GB pods. Normal FireRed launches also disable custom nodes because the image-edit workflow uses ComfyUI's built-in nodes; this avoids unrelated Manager downloads and broken optional-node imports. Before reporting ready, the launcher verifies the exact nodes required by FireRed.

The launcher pins the verified Gradio and Pillow versions so restarts do not silently change the runtime. Before ComfyUI starts, any crash-leftover `simple_*` working inputs are moved, never permanently deleted, to:

```text
/workspace/simple_firered/ready_to_delete/comfy_temp_inputs/<timestamp>/
```

Each archive includes `manifest.tsv`. Legacy reference intermediates are archived the same way under `ready_to_delete/reference_intermediates/`. During normal operation, per-image working inputs are removed as soon as ComfyUI reports completion, and optional references are limited to the current run.

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
b621803zv3tzet
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
