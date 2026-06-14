#!/usr/bin/env python3
import datetime as dt
import json
import os
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

import gradio as gr
from PIL import Image


COMFY = "http://127.0.0.1:8188"
BASE_DIR = Path("/workspace/simple_wan_video")
RUN_DIR = BASE_DIR / "runs"
COMFY_INPUT_DIR = Path("/workspace/runpod-slim/ComfyUI/input")
COMFY_OUTPUT_DIRS = [
    Path("/workspace/runpod-slim/ComfyUI/output"),
    Path("/workspace/output"),
]

NEGATIVE_PROMPT = (
    "low quality, worst quality, blurry, distorted face, extra fingers, "
    "deformed hands, bad anatomy, text, watermark, flicker, unnatural motion"
)

BASE_DIR.mkdir(parents=True, exist_ok=True)
RUN_DIR.mkdir(parents=True, exist_ok=True)
COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)


def safe_name(name):
    stem = Path(name or "image").stem
    chars = [c if c.isalnum() or c in "-_." else "_" for c in stem]
    return "".join(chars).strip("_") or "image"


def http_json(path, payload=None, timeout=30):
    url = f"{COMFY}{path}"
    if payload is None:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def queue_prompt(prompt):
    return http_json("/prompt", {"prompt": prompt, "client_id": str(uuid.uuid4())})["prompt_id"]


def wait_for_prompt(prompt_id, poll_seconds=4):
    while True:
        history = http_json(f"/history/{prompt_id}", timeout=30)
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(poll_seconds)


def copy_image_to_comfy(image):
    name = f"wan_{int(time.time() * 1000)}.png"
    source = RUN_DIR / name
    image.save(source)
    dest = COMFY_INPUT_DIR / name
    shutil.copy2(source, dest)
    return name


def output_paths_from_history(history):
    paths = []
    for node_output in history.get("outputs", {}).values():
        for key in ("gifs", "videos", "images"):
            for item in node_output.get(key, []):
                filename = item.get("filename")
                if not filename:
                    continue
                subfolder = item.get("subfolder", "")
                for root in COMFY_OUTPUT_DIRS:
                    candidate = root / subfolder / filename
                    if candidate.exists():
                        paths.append(candidate)
                        break
    return paths


def history_error(history):
    for message_type, message in reversed(history.get("status", {}).get("messages", [])):
        if message_type == "execution_error":
            node = message.get("node_type") or message.get("node_id") or "unknown node"
            error = message.get("exception_message") or message.get("exception_type") or "unknown error"
            return f"ComfyUI error at {node}: {error}"
    return "ComfyUI finished but did not report a video file."


def build_wan_prompt(image_name, prompt, negative_prompt, width, height, frames, steps, cfg, seed, fps):
    width = int(width)
    height = int(height)
    frames = int(frames)
    steps = int(steps)
    cfg = float(cfg)
    seed = int(seed) if int(seed) >= 0 else int(time.time() * 1000) % 2**32
    split_step = max(1, min(steps - 1, steps // 2))
    prefix = f"simple_wan_video/{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    return {
        "11": {
            "class_type": "LoadWanVideoT5TextEncoder",
            "inputs": {
                "model_name": "umt5-xxl-enc-bf16.safetensors",
                "precision": "bf16",
                "load_device": "offload_device",
                "quantization": "disabled",
            },
        },
        "16": {
            "class_type": "WanVideoTextEncode",
            "inputs": {
                "t5": ["11", 0],
                "positive_prompt": prompt,
                "negative_prompt": negative_prompt or NEGATIVE_PROMPT,
                "force_offload": True,
                "use_disk_cache": False,
                "device": "gpu",
            },
        },
        "35": {
            "class_type": "WanVideoTorchCompileSettings",
            "inputs": {
                "backend": "inductor",
                "fullgraph": False,
                "mode": "default",
                "dynamic": False,
                "dynamo_cache_size_limit": 64,
                "compile_transformer_blocks_only": True,
                "dynamo_recompile_limit": 128,
            },
        },
        "22": {
            "class_type": "WanVideoModelLoader",
            "inputs": {
                "model": "WanVideo/2_2/Wan2_2-I2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors",
                "base_precision": "fp16_fast",
                "quantization": "fp8_e4m3fn_scaled",
                "load_device": "offload_device",
                "attention_mode": "sdpa",
            },
        },
        "71": {
            "class_type": "WanVideoModelLoader",
            "inputs": {
                "model": "WanVideo/2_2/Wan2_2-I2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors",
                "base_precision": "fp16_fast",
                "quantization": "fp8_e4m3fn_scaled",
                "load_device": "offload_device",
                "attention_mode": "sdpa",
            },
        },
        "39": {
            "class_type": "WanVideoBlockSwap",
            "inputs": {
                "blocks_to_swap": 20,
                "offload_img_emb": False,
                "offload_txt_emb": False,
                "use_non_blocking": False,
                "prefetch_blocks": 1,
            },
        },
        "92": {"class_type": "WanVideoSetBlockSwap", "inputs": {"model": ["22", 0], "block_swap_args": ["39", 0]}},
        "93": {"class_type": "WanVideoSetBlockSwap", "inputs": {"model": ["71", 0], "block_swap_args": ["39", 0]}},
        "56": {
            "class_type": "WanVideoLoraSelect",
            "inputs": {
                "lora": "WanVideo/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
                "strength": 3.0,
                "low_mem_load": False,
                "merge_loras": False,
            },
        },
        "97": {
            "class_type": "WanVideoLoraSelect",
            "inputs": {
                "lora": "WanVideo/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
                "strength": 1.0,
                "low_mem_load": False,
                "merge_loras": False,
            },
        },
        "80": {"class_type": "WanVideoSetLoRAs", "inputs": {"model": ["92", 0], "lora": ["56", 0]}},
        "79": {"class_type": "WanVideoSetLoRAs", "inputs": {"model": ["93", 0], "lora": ["97", 0]}},
        "38": {
            "class_type": "WanVideoVAELoader",
            "inputs": {"model_name": "wanvideo/Wan2_1_VAE_bf16.safetensors", "precision": "bf16"},
        },
        "67": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "68": {
            "class_type": "ImageResizeKJv2",
            "inputs": {
                "image": ["67", 0],
                "width": width,
                "height": height,
                "upscale_method": "lanczos",
                "keep_proportion": "crop",
                "pad_color": "0, 0, 0",
                "crop_position": "center",
                "divisible_by": 32,
                "device": "cpu",
            },
        },
        "89": {
            "class_type": "WanVideoImageToVideoEncode",
            "inputs": {
                "vae": ["38", 0],
                "start_image": ["68", 0],
                "width": width,
                "height": height,
                "num_frames": frames,
                "noise_aug_strength": 0,
                "start_latent_strength": 1,
                "end_latent_strength": 1,
                "force_offload": True,
            },
        },
        "27": {
            "class_type": "WanVideoSampler",
            "inputs": {
                "model": ["80", 0],
                "image_embeds": ["89", 0],
                "text_embeds": ["16", 0],
                "steps": steps,
                "cfg": cfg,
                "shift": 8,
                "seed": seed,
                "force_offload": True,
                "scheduler": "dpm++_sde",
                "riflex_freq_index": 0,
                "denoise_strength": 1,
                "rope_function": "comfy",
                "end_step": split_step,
            },
        },
        "90": {
            "class_type": "WanVideoSampler",
            "inputs": {
                "model": ["79", 0],
                "image_embeds": ["89", 0],
                "text_embeds": ["16", 0],
                "samples": ["27", 0],
                "steps": steps,
                "cfg": cfg,
                "shift": 8,
                "seed": seed,
                "force_offload": True,
                "scheduler": "dpm++_sde",
                "riflex_freq_index": 0,
                "denoise_strength": 1,
                "rope_function": "comfy",
                "start_step": split_step,
            },
        },
        "28": {
            "class_type": "WanVideoDecode",
            "inputs": {
                "vae": ["38", 0],
                "samples": ["90", 0],
                "enable_vae_tiling": False,
                "tile_x": 272,
                "tile_y": 272,
                "tile_stride_x": 144,
                "tile_stride_y": 128,
                "normalization": "default",
            },
        },
        "60": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "images": ["28", 0],
                "frame_rate": float(fps),
                "loop_count": 0,
                "filename_prefix": prefix,
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 19,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
            },
        },
    }


def run_video(image, prompt, negative_prompt, width, height, frames, steps, cfg, seed, fps):
    if image is None:
        raise gr.Error("Upload an image first.")
    if not prompt or not prompt.strip():
        raise gr.Error("Enter a motion prompt.")

    image_name = copy_image_to_comfy(image)
    api_prompt = build_wan_prompt(image_name, prompt.strip(), negative_prompt.strip(), width, height, frames, steps, cfg, seed, fps)
    prompt_id = queue_prompt(api_prompt)
    history = wait_for_prompt(prompt_id)
    paths = output_paths_from_history(history)
    if not paths:
        raise gr.Error(history_error(history))
    return str(paths[-1]), str(paths[-1])


with gr.Blocks(title="Wan Simple Video") as demo:
    gr.Markdown("# Wan Simple Video")
    gr.Markdown("Upload one image, describe the motion, run, then download the MP4.")

    with gr.Row():
        image = gr.Image(label="Input image", type="pil")
        video = gr.Video(label="Result video")

    prompt = gr.Textbox(
        label="Motion prompt",
        lines=3,
        value="natural slow head movement, subtle smile, realistic motion, cinematic lighting",
    )
    negative = gr.Textbox(label="Negative prompt", lines=2, value=NEGATIVE_PROMPT)

    with gr.Row():
        width = gr.Dropdown([480, 640, 704, 832], value=480, label="Width")
        height = gr.Dropdown([480, 640, 704, 832], value=480, label="Height")
        frames = gr.Slider(17, 81, value=33, step=8, label="Frames")
        fps = gr.Slider(8, 24, value=16, step=1, label="FPS")

    with gr.Row():
        steps = gr.Slider(2, 12, value=4, step=1, label="Steps")
        cfg = gr.Slider(1.0, 8.0, value=1.0, step=0.1, label="Guidance")
        seed = gr.Number(value=12345, precision=0, label="Seed (-1 random)")

    run = gr.Button("Run video", variant="primary")
    file = gr.File(label="Download MP4")
    run.click(run_video, inputs=[image, prompt, negative, width, height, frames, steps, cfg, seed, fps], outputs=[video, file])


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "8080")),
        allowed_paths=[str(BASE_DIR), "/workspace/runpod-slim/ComfyUI/output", "/workspace/output"],
    )
