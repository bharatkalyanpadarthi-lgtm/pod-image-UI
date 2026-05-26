#!/usr/bin/env python3
import datetime as dt
import json
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

import gradio as gr
from PIL import Image

sys.path.append("/workspace")
try:
    import simple_wan_video_ui as wan_video
except Exception:
    wan_video = None


COMFY = "http://127.0.0.1:8188"
BASE_DIR = Path("/workspace/simple_firered")
OUTPUT_DIR = BASE_DIR / "outputs"
COMFY_INPUT_CANDIDATES = [
    Path("/workspace/runpod-slim/ComfyUI/input"),
    Path("/workspace/ComfyUI/input"),
    Path("/ComfyUI/input"),
]
COMFY_INPUT_DIR = next((path for path in COMFY_INPUT_CANDIDATES if path.parent.exists()), COMFY_INPUT_CANDIDATES[0])
COMFY_OUTPUT_DIR = Path("/workspace/output")
COMFY_OUTPUT_CANDIDATES = [
    Path("/workspace/output"),
    Path("/workspace/runpod-slim/ComfyUI/output"),
    Path("/workspace/ComfyUI/output"),
    Path("/ComfyUI/output"),
]
WORKSPACE_INPUT_DIR = Path("/workspace/input")
WORKSPACE_OUTPUT_DIR = Path("/workspace/output")
LORA_NAME = "FireRed-Image-Edit-1.1-Lightning-8steps-v1.2.safetensors"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
RUN_STATE = {
    "stop_requested": False,
    "run_dir": None,
    "results": [],
    "label": "No active run.",
    "last_error": "",
}

PROMPT_PRESETS = {
    "Custom": "",
    "Clean studio background": "Replace the background with a clean white studio background. Keep the same person, face, pose, clothing, and lighting natural.",
    "Enhance portrait": "Improve the photo quality, make the portrait clean and professional, keep the same person and identity unchanged.",
    "Outfit reference": "Use reference image 2 as the clothing reference. Keep the same person and face from the original image.",
    "Style reference": "Use reference image 3 as the visual style reference. Keep the same person and main subject unchanged.",
}

QUALITY_PRESETS = {
    "Fast test": (4, 1.0),
    "Normal": (8, 1.0),
    "Quality": (12, 1.2),
}

DISALLOWED_PROMPT_TERMS = {
    "nude",
    "naked",
    "undress",
    "undressed",
    "remove all clothes",
    "remove her clothes",
    "remove his clothes",
    "fully nude",
    "topless",
    "explicit",
    "porn",
    "sex",
}

BASE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_INPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class StopRequested(Exception):
    pass


def set_run_state(run_dir=None, results=None, label=None, stop_requested=False):
    RUN_STATE["stop_requested"] = stop_requested
    if run_dir is not None:
        RUN_STATE["run_dir"] = Path(run_dir)
    if results is not None:
        RUN_STATE["results"] = list(results)
    if label is not None:
        RUN_STATE["label"] = label


def set_last_error(message=""):
    RUN_STATE["last_error"] = message or ""


def interrupt_comfy():
    try:
        http_json("/interrupt", {})
    except Exception:
        pass
    try:
        http_json("/queue", {"clear": True})
    except Exception:
        pass


def partial_zip_from_state():
    results = [Path(path) for path in RUN_STATE.get("results", []) if Path(path).exists()]
    run_dir = RUN_STATE.get("run_dir")
    if not results or run_dir is None:
        return None
    zip_path = Path(run_dir).with_name(f"{Path(run_dir).name}_partial_{len(results)}.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in results:
            archive.write(path, arcname=path.name)
    return str(zip_path)


def stop_current_run():
    RUN_STATE["stop_requested"] = True
    interrupt_comfy()
    zip_path = partial_zip_from_state()
    count = len(RUN_STATE.get("results", []))
    if zip_path:
        return f"Stop requested. Saved {count} completed image(s) into a partial ZIP.", zip_path
    return "Stop requested. No completed images have been copied yet.", None


def stop_after_current_image():
    RUN_STATE["stop_requested"] = True
    zip_path = partial_zip_from_state()
    count = len(RUN_STATE.get("results", []))
    if zip_path:
        return f"Will stop after the current image finishes. {count} completed image(s) are already available.", zip_path
    return "Will stop after the current image finishes. No completed images are available yet.", None


def raise_if_stopped():
    if RUN_STATE.get("stop_requested"):
        raise StopRequested()


def safe_name(name):
    stem = Path(name or "image").stem
    keep = [char if char.isalnum() or char in "-_." else "_" for char in stem]
    return "".join(keep).strip("_") or "image"


def apply_prompt_preset(choice, current_prompt):
    preset = PROMPT_PRESETS.get(choice or "Custom", "")
    return preset or current_prompt


def apply_quality_preset(choice):
    return QUALITY_PRESETS.get(choice or "Normal", QUALITY_PRESETS["Normal"])


def image_count(folder):
    if not folder.exists() or not folder.is_dir():
        return 0
    return sum(1 for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTS)


def list_input_batches():
    batches = []
    if WORKSPACE_INPUT_DIR.exists():
        for folder in sorted(WORKSPACE_INPUT_DIR.iterdir()):
            if folder.is_dir():
                count = image_count(folder)
                if count:
                    batches.append(f"{folder.name} ({count} images)")
    return batches


def batch_name_from_choice(choice):
    return (choice or "").split(" (", 1)[0].strip()


def refresh_batches():
    choices = list_input_batches()
    value = choices[0] if choices else None
    input_path, output_path, status = select_batch(value)
    return gr.update(choices=choices, value=value), input_path, output_path, status


def select_batch(choice):
    batch_name = batch_name_from_choice(choice)
    if not batch_name:
        return "", "", "No uploaded image batch found yet. Upload or sync a batch first."
    input_path = WORKSPACE_INPUT_DIR / batch_name
    output_path = WORKSPACE_OUTPUT_DIR / batch_name
    count = image_count(input_path)
    return str(input_path), str(output_path), f"Selected {batch_name}: {count} image(s)."


def output_image_paths(folder):
    folder = Path(folder)
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted(
        [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTS],
        key=lambda path: path.name.lower(),
    )


def list_output_runs():
    choices = []
    for root in [OUTPUT_DIR, WORKSPACE_OUTPUT_DIR]:
        if not root.exists():
            continue
        for folder in sorted(root.iterdir(), reverse=True):
            if not folder.is_dir():
                continue
            count = len(output_image_paths(folder))
            if count:
                choices.append(f"{folder} ({count} images)")
    return choices


def output_folder_from_choice(choice):
    return Path((choice or "").rsplit(" (", 1)[0])


def make_or_find_zip(folder):
    folder = Path(folder)
    existing = sorted(folder.glob("*.zip"), reverse=True)
    if existing:
        return existing[0]
    images = output_image_paths(folder)
    if not images:
        return None
    return make_zip_at(folder / f"{folder.name}.zip", images)


def refresh_output_history():
    choices = list_output_runs()
    value = choices[0] if choices else None
    gallery, zip_path, status = select_output_run(value)
    return gr.update(choices=choices, value=value), gallery, zip_path, status


def select_output_run(choice):
    folder = output_folder_from_choice(choice)
    if not choice or not folder.exists():
        return [], None, "No completed output folders found yet."
    images = output_image_paths(folder)
    zip_path = make_or_find_zip(folder)
    status = f"Selected {folder.name}: {len(images)} completed image(s)."
    return [str(path) for path in images], str(zip_path) if zip_path else None, status


def http_json(path, payload=None):
    url = f"{COMFY}{path}"
    if payload is None:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read())

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def queue_prompt(prompt):
    return http_json("/prompt", {"prompt": prompt, "client_id": str(uuid.uuid4())})["prompt_id"]


def wait_for_prompt(prompt_id, poll_seconds=2):
    while True:
        raise_if_stopped()
        history = http_json(f"/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(poll_seconds)


def copy_to_comfy_input(source_path):
    dest_name = f"simple_{int(time.time() * 1000)}_{safe_name(source_path.name)}{source_path.suffix.lower()}"
    dest = COMFY_INPUT_DIR / dest_name
    shutil.copy2(source_path, dest)
    return dest_name


def parse_prompts(prompt_text):
    prompts = [line.strip() for line in (prompt_text or "").splitlines() if line.strip()]
    if not prompts:
        raise gr.Error("Enter at least one prompt. For multiple edits, put one prompt per line.")
    blocked = sorted(term for term in DISALLOWED_PROMPT_TERMS if any(term in prompt.lower() for prompt in prompts))
    if blocked:
        raise gr.Error(
            "I can't process nude, undressing, or explicit sexual edits of a real person. "
            "Use a non-explicit edit such as background, lighting, outfit color, retouching, or style."
        )
    return prompts


def copy_optional_ref(image, name):
    if image is None:
        return None
    ref_dir = BASE_DIR / "refs"
    ref_dir.mkdir(parents=True, exist_ok=True)
    source = ref_dir / f"{name}_{int(time.time() * 1000)}.png"
    image.save(source)
    return copy_to_comfy_input(source)


def add_reference_nodes(prompt_dict, ref2_name=None, ref3_name=None):
    if ref2_name:
        prompt_dict["133"] = {
            "class_type": "LoadImage",
            "inputs": {"image": ref2_name},
        }
        prompt_dict["118"]["inputs"]["image2"] = ["133", 0]
        prompt_dict["117"]["inputs"]["image2"] = ["133", 0]

    if ref3_name:
        prompt_dict["135"] = {
            "class_type": "LoadImage",
            "inputs": {"image": ref3_name},
        }
        prompt_dict["118"]["inputs"]["image3"] = ["135", 0]
        prompt_dict["117"]["inputs"]["image3"] = ["135", 0]


def build_prompt(input_image_name, prompt, output_prefix, steps, guidance, seed, ref2_name=None, ref3_name=None):
    steps = int(steps)
    guidance = float(guidance)
    seed = int(seed) if seed is not None and int(seed) >= 0 else int(time.time() * 1000) % 2**32

    return {
        "115": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen2.5vl-7b-bf16.safetensors",
                "type": "qwen_image",
                "device": "default",
            },
        },
        "116": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "qwen_image_vae.safetensors"},
        },
        "128": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "FireRed-Image-Edit-1.1-transformer.safetensors",
                "weight_dtype": "default",
            },
        },
        "151": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["128", 0],
                "lora_name": LORA_NAME,
                "strength_model": 1.0,
            },
        },
        "120": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["151", 0], "shift": 3.1},
        },
        "123": {
            "class_type": "CFGNorm",
            "inputs": {"model": ["120", 0], "strength": 1.0},
        },
        "143": {
            "class_type": "LoadImage",
            "inputs": {"image": input_image_name},
        },
        "147": {
            "class_type": "FluxKontextImageScale",
            "inputs": {"image": ["143", 0]},
        },
        "125": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["147", 0], "vae": ["116", 0]},
        },
        "118": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {
                "clip": ["115", 0],
                "prompt": prompt,
                "vae": ["116", 0],
                "image1": ["147", 0],
            },
        },
        "117": {
            "class_type": "TextEncodeQwenImageEditPlus",
            "inputs": {
                "clip": ["115", 0],
                "prompt": "",
                "vae": ["116", 0],
                "image1": ["147", 0],
            },
        },
        "130": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["123", 0],
                "seed": seed,
                "steps": steps,
                "cfg": guidance,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["118", 0],
                "negative": ["117", 0],
                "latent_image": ["125", 0],
                "denoise": 1.0,
            },
        },
        "126": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["130", 0], "vae": ["116", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["126", 0], "filename_prefix": output_prefix},
        },
    }


def build_prompt_with_refs(input_image_name, prompt, output_prefix, steps, guidance, seed, ref2_name=None, ref3_name=None):
    prompt_dict = build_prompt(input_image_name, prompt, output_prefix, steps, guidance, seed)
    add_reference_nodes(prompt_dict, ref2_name, ref3_name)
    return prompt_dict


def history_output_paths(history):
    paths = []
    for node_output in history.get("outputs", {}).values():
        for image in node_output.get("images", []):
            filename = image["filename"]
            subfolder = image.get("subfolder", "")
            image_type = image.get("type", "output")
            if image_type == "output":
                for output_dir in COMFY_OUTPUT_CANDIDATES:
                    candidate = output_dir / subfolder / filename
                    if candidate.exists():
                        paths.append(candidate)
                        break
                else:
                    paths.append(COMFY_OUTPUT_DIR / subfolder / filename)
    return paths


def history_error_message(history):
    status = history.get("status") or {}
    messages = status.get("messages") or []
    for message_type, message in reversed(messages):
        if message_type == "execution_error":
            node = message.get("node_type") or message.get("node_id") or "unknown node"
            error = message.get("exception_message") or message.get("exception_type") or "unknown error"
            return f"ComfyUI error at {node}: {error}"
    status_text = status.get("status_str")
    if status_text and status_text != "success":
        return f"ComfyUI ended with status: {status_text}"
    return "ComfyUI finished but did not report an output image. Try refreshing the page and run again."


def run_comfy_one(source_path, prompt, steps, guidance, seed, output_prefix, ref2_name=None, ref3_name=None):
    input_name = copy_to_comfy_input(source_path)
    api_prompt = build_prompt_with_refs(input_name, prompt, output_prefix, steps, guidance, seed, ref2_name, ref3_name)
    prompt_id = queue_prompt(api_prompt)
    history = wait_for_prompt(prompt_id)
    outputs = history_output_paths(history)
    if not outputs:
        if RUN_STATE.get("stop_requested"):
            raise StopRequested()
        raise gr.Error(history_error_message(history))
    return outputs[-1]


def make_zip(run_dir, paths):
    zip_path = run_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, arcname=Path(path).name)
    return zip_path


def make_zip_at(zip_path, paths):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, arcname=Path(path).name)
    return zip_path


def finish_partial(run_dir, results):
    if not results:
        return None
    zip_path = Path(run_dir).with_name(f"{Path(run_dir).name}_partial_{len(results)}.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in results:
            archive.write(path, arcname=Path(path).name)
    return zip_path


def edit_single(image, prompt, ref2, ref3, steps, guidance, seed):
    if image is None:
        raise gr.Error("Upload an image first.")
    prompts = parse_prompts(prompt)

    run_dir = OUTPUT_DIR / dt.datetime.now().strftime("single_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    set_run_state(run_dir=run_dir, results=[], label=f"Running {run_dir.name}", stop_requested=False)
    source = run_dir / "input.png"
    image.save(source)

    ref2_name = copy_optional_ref(ref2, "ref2")
    ref3_name = copy_optional_ref(ref3, "ref3")
    results = []

    try:
        for prompt_index, one_prompt in enumerate(prompts, 1):
            raise_if_stopped()
            prompt_seed = int(seed) + prompt_index - 1 if seed is not None and int(seed) >= 0 else -1
            prefix = f"simple_firered/{run_dir.name}/prompt_{prompt_index:02d}"
            output_path = run_comfy_one(source, one_prompt, steps, guidance, prompt_seed, prefix, ref2_name, ref3_name)
            local_copy = run_dir / output_path.name
            shutil.copy2(output_path, local_copy)
            results.append(str(local_copy))
            set_run_state(results=results)
    except StopRequested:
        zip_path = finish_partial(run_dir, results)
        return results, str(zip_path) if zip_path else None

    zip_path = make_zip(run_dir, results)
    set_run_state(results=results, label=f"Finished {run_dir.name}", stop_requested=False)
    return results, str(zip_path)


def edit_batch(files, prompt, ref2, ref3, steps, guidance, seed, progress=gr.Progress(track_tqdm=True)):
    if not files:
        raise gr.Error("Upload one or more images first.")
    if len(files) > 100:
        raise gr.Error("Please upload 100 images or fewer per run.")
    prompts = parse_prompts(prompt)

    run_dir = OUTPUT_DIR / dt.datetime.now().strftime("batch_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    set_run_state(run_dir=run_dir, results=[], label=f"Running {run_dir.name}", stop_requested=False)
    ref2_name = copy_optional_ref(ref2, "ref2")
    ref3_name = copy_optional_ref(ref3, "ref3")
    results = []

    try:
        for index, file_obj in enumerate(progress.tqdm(files, desc="Editing images"), 1):
            raise_if_stopped()
            source_path = Path(file_obj.name)
            for prompt_index, one_prompt in enumerate(prompts, 1):
                raise_if_stopped()
                offset = (index - 1) * len(prompts) + prompt_index - 1
                item_seed = int(seed) + offset if seed is not None and int(seed) >= 0 else -1
                prefix = f"simple_firered/{run_dir.name}/{index:04d}_p{prompt_index:02d}_{safe_name(source_path.name)}"
                output_path = run_comfy_one(source_path, one_prompt, steps, guidance, item_seed, prefix, ref2_name, ref3_name)
                local_copy = run_dir / output_path.name
                shutil.copy2(output_path, local_copy)
                results.append(str(local_copy))
                set_run_state(results=results)
    except StopRequested:
        zip_path = finish_partial(run_dir, results)
        return results, str(zip_path) if zip_path else None

    zip_path = make_zip(run_dir, results)
    set_run_state(results=results, label=f"Finished {run_dir.name}", stop_requested=False)

    return results, str(zip_path)


def uploaded_folder_info(files, batch_size, start_index, output_name):
    files = files or []
    image_files = sorted(
        [Path(file_obj.name) for file_obj in files if Path(file_obj.name).suffix.lower() in IMAGE_EXTS],
        key=lambda path: path.name.lower(),
    )
    total = len(image_files)
    batch_size = max(1, int(batch_size or 50))
    start_index = max(1, int(start_index or 1))
    end_index = min(total, start_index + batch_size - 1)
    batch_number = ((start_index - 1) // batch_size) + 1
    clean_output = safe_name(output_name or f"edited_batch_{batch_number:03d}")

    if total == 0:
        return "No images detected yet. Click 'Select folder' and choose a folder containing JPG/PNG/WebP images."
    if start_index > total:
        return f"Detected {total} image(s). Start image is beyond the folder size."
    estimated = estimate_minutes(end_index - start_index + 1)
    return f"Detected {total} image(s). Next run will process image {start_index} to {end_index} into output folder '{clean_output}'. Estimated time: {estimated}."


def estimate_minutes(source_count, prompt_count=1):
    jobs = max(0, int(source_count or 0)) * max(1, int(prompt_count or 1))
    low = max(1, round(jobs * 1.5))
    high = max(low, round(jobs * 3.0))
    if jobs == 0:
        return "unknown until images are detected"
    if high < 60:
        return f"{low}-{high} minutes"
    return f"{round(low / 60, 1)}-{round(high / 60, 1)} hours"


def auto_folder_output_name(files, batch_size, start_index, prefix):
    files = files or []
    total = sum(1 for file_obj in files if Path(file_obj.name).suffix.lower() in IMAGE_EXTS)
    batch_size = max(1, int(batch_size or 50))
    start_index = max(1, int(start_index or 1))
    batch_number = ((start_index - 1) // batch_size) + 1
    clean_prefix = safe_name(prefix or "edited_batch")
    output_name = f"{clean_prefix}_{batch_number:03d}"
    end_index = min(total, start_index + batch_size - 1) if total else start_index + batch_size - 1
    status = (
        f"Auto name: '{output_name}'. "
        f"Batch {batch_number} will process image {start_index} to {end_index}. "
        f"Estimated time: {estimate_minutes(max(0, end_index - start_index + 1))}."
    )
    if total:
        status = f"Detected {total} image(s). " + status
    return output_name, status


def next_folder_batch(files, batch_size, start_index, prefix):
    batch_size = max(1, int(batch_size or 50))
    next_start = max(1, int(start_index or 1)) + batch_size
    output_name, status = auto_folder_output_name(files, batch_size, next_start, prefix)
    return next_start, output_name, status


def edit_uploaded_folder(files, output_name, batch_size, start_index, skip_completed, prompt, ref2, ref3, steps, guidance, seed, progress=gr.Progress(track_tqdm=True)):
    if not files:
        raise gr.Error("Select a folder first.")
    prompts = parse_prompts(prompt)
    set_last_error("")

    image_files = sorted(
        [Path(file_obj.name) for file_obj in files if Path(file_obj.name).suffix.lower() in IMAGE_EXTS],
        key=lambda path: path.name.lower(),
    )
    if not image_files:
        raise gr.Error("No supported images found in the selected folder.")

    batch_size = max(1, int(batch_size or 50))
    start_index = max(1, int(start_index or 1))
    start = start_index - 1
    end = min(len(image_files), start + batch_size)
    selected = image_files[start:end]
    if not selected:
        raise gr.Error("Start image is beyond the number of detected images.")

    batch_number = ((start_index - 1) // batch_size) + 1
    clean_output = safe_name(output_name or f"edited_batch_{batch_number:03d}")
    run_dir = OUTPUT_DIR / clean_output
    if run_dir.exists() and not skip_completed:
        run_dir = OUTPUT_DIR / f"{clean_output}_{dt.datetime.now().strftime('%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    set_run_state(run_dir=run_dir, results=[], label=f"Running {run_dir.name}", stop_requested=False)

    ref2_name = copy_optional_ref(ref2, "ref2")
    ref3_name = copy_optional_ref(ref3, "ref3")
    results = []

    existing_paths = output_image_paths(run_dir) if skip_completed else []
    existing_by_name = {path.name: path for path in existing_paths}
    existing_names = set(existing_by_name)
    total_jobs = len(selected) * len(prompts)
    completed_jobs = 0
    yield [], None, f"Starting {run_dir.name}. Processing {len(selected)} source image(s), {total_jobs} total edit(s).", ""

    try:
        for local_index, source_path in enumerate(progress.tqdm(selected, desc="Editing selected folder batch"), start_index):
            raise_if_stopped()
            for prompt_index, one_prompt in enumerate(prompts, 1):
                raise_if_stopped()
                expected_marker = f"{local_index:04d}_p{prompt_index:02d}_{safe_name(source_path.name)}"
                if skip_completed and any(expected_marker in name for name in existing_names):
                    skipped_path = next(existing_by_name[name] for name in existing_names if expected_marker in name)
                    if str(skipped_path) not in results:
                        results.append(str(skipped_path))
                        set_run_state(results=results)
                    completed_jobs += 1
                    status = f"Skipped existing result for image {local_index}. Completed/skipped {completed_jobs} of {total_jobs} edit(s)."
                    yield results, None, status, ""
                    continue
                status = (
                    f"Processing image {local_index} of {start_index + len(selected) - 1}; "
                    f"prompt {prompt_index} of {len(prompts)}. Completed {completed_jobs} of {total_jobs} edit(s)."
                )
                yield results, None, status, ""
                offset = (local_index - 1) * len(prompts) + prompt_index - 1
                item_seed = int(seed) + offset if seed is not None and int(seed) >= 0 else -1
                prefix = f"simple_firered/{run_dir.name}/{local_index:04d}_p{prompt_index:02d}_{safe_name(source_path.name)}"
                output_path = run_comfy_one(source_path, one_prompt, steps, guidance, item_seed, prefix, ref2_name, ref3_name)
                final_path = run_dir / output_path.name
                shutil.copy2(output_path, final_path)
                results.append(str(final_path))
                set_run_state(results=results)
                completed_jobs += 1
                yield results, None, f"Completed {completed_jobs} of {total_jobs} edit(s). Latest: {final_path.name}", ""
    except StopRequested:
        zip_path = finish_partial(run_dir, results)
        status = f"Stopped. Completed {len(results)} output image(s)."
        yield results, str(zip_path) if zip_path else None, status, ""
        return
    except Exception as exc:
        zip_path = finish_partial(run_dir, results)
        message = f"{type(exc).__name__}: {exc}"
        set_last_error(message)
        status = f"Error after {len(results)} completed output image(s). Partial ZIP is available if anything finished."
        yield results, str(zip_path) if zip_path else None, status, message
        return

    zip_path = make_zip_at(run_dir / f"{run_dir.name}.zip", results)
    set_run_state(results=results, label=f"Finished {run_dir.name}", stop_requested=False)
    status = f"Done. Processed {len(selected)} source image(s), created {len(results)} output image(s). Output folder: {run_dir}"
    yield results, str(zip_path), status, ""


def edit_directory(input_dir, output_dir, prompt, ref2, ref3, steps, guidance, seed, limit, progress=gr.Progress(track_tqdm=True)):
    in_dir = Path(input_dir or "").expanduser()
    out_dir = Path(output_dir or "").expanduser()
    if not in_dir.exists() or not in_dir.is_dir():
        raise gr.Error("Input directory does not exist on the pod. Example: /workspace/input/batch_001")
    if not str(out_dir).startswith("/workspace/"):
        raise gr.Error("For safety, output directory must be inside /workspace. Example: /workspace/output/batch_001")

    prompts = parse_prompts(prompt)
    out_dir.mkdir(parents=True, exist_ok=True)
    ref2_name = copy_optional_ref(ref2, "ref2")
    ref3_name = copy_optional_ref(ref3, "ref3")

    images = sorted(path for path in in_dir.iterdir() if path.suffix.lower() in IMAGE_EXTS)
    if limit and int(limit) > 0:
        images = images[: int(limit)]
    if not images:
        raise gr.Error("No images found in the input directory.")

    results = []
    run_name = dt.datetime.now().strftime("dir_%Y%m%d_%H%M%S")
    comfy_prefix_base = f"simple_firered/{run_name}"
    set_run_state(run_dir=out_dir / run_name, results=[], label=f"Running {run_name}", stop_requested=False)

    try:
        for index, source_path in enumerate(progress.tqdm(images, desc="Editing directory"), 1):
            raise_if_stopped()
            for prompt_index, one_prompt in enumerate(prompts, 1):
                raise_if_stopped()
                offset = (index - 1) * len(prompts) + prompt_index - 1
                item_seed = int(seed) + offset if seed is not None and int(seed) >= 0 else -1
                prefix = f"{comfy_prefix_base}/{index:04d}_p{prompt_index:02d}_{safe_name(source_path.name)}"
                output_path = run_comfy_one(source_path, one_prompt, steps, guidance, item_seed, prefix, ref2_name, ref3_name)
                final_path = out_dir / output_path.name
                shutil.copy2(output_path, final_path)
                results.append(str(final_path))
                set_run_state(results=results)
    except StopRequested:
        zip_path = out_dir / f"{run_name}_partial_{len(results)}.zip"
        if results:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in results:
                    archive.write(path, arcname=Path(path).name)
            return results, str(zip_path)
        return results, None

    zip_path = out_dir / f"{run_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in results:
            archive.write(path, arcname=Path(path).name)

    set_run_state(results=results, label=f"Finished {run_name}", stop_requested=False)
    return results, str(zip_path)


with gr.Blocks(title="FireRed Simple Editor") as demo:
    gr.Markdown("# FireRed Simple Editor")
    gr.Markdown("Upload image(s), enter one edit prompt, run, then download the result.")

    prompt = gr.Textbox(
        label="Edit prompt(s). Put one prompt per line if you want multiple edits.",
        lines=3,
        placeholder="Example: Replace the background with a clean white studio background. Keep the subject unchanged.",
    )
    with gr.Row():
        prompt_preset = gr.Dropdown(
            choices=list(PROMPT_PRESETS.keys()),
            value="Custom",
            label="Prompt preset",
        )
        quality_preset = gr.Dropdown(
            choices=list(QUALITY_PRESETS.keys()),
            value="Normal",
            label="Speed / quality preset",
        )

    with gr.Accordion("Optional reference images", open=False):
        gr.Markdown("Use these when your prompt says things like: use reference image 2 as the clothing reference, or use reference image 3 as the accessory/style reference.")
        with gr.Row():
            ref2 = gr.Image(label="Reference image 2", type="pil")
            ref3 = gr.Image(label="Reference image 3", type="pil")

    with gr.Row():
        steps = gr.Slider(4, 40, value=8, step=1, label="Steps")
        guidance = gr.Slider(0.0, 6.0, value=1.0, step=0.1, label="Guidance")
        seed = gr.Number(value=12345, precision=0, label="Seed (-1 for random)")
    prompt_preset.change(
        apply_prompt_preset,
        inputs=[prompt_preset, prompt],
        outputs=[prompt],
    )
    quality_preset.change(
        apply_quality_preset,
        inputs=[quality_preset],
        outputs=[steps, guidance],
    )
    with gr.Row():
        stop_after_button = gr.Button("Stop after current image")
        stop_button = gr.Button("Stop now", variant="stop")
        stop_status = gr.Markdown("No active stop request.")
        stop_file = gr.File(label="Download partial ZIP")
    stop_after_button.click(stop_after_current_image, inputs=[], outputs=[stop_status, stop_file], queue=False)
    stop_button.click(stop_current_run, inputs=[], outputs=[stop_status, stop_file], queue=False)

    with gr.Tab("One image"):
        with gr.Row():
            single_input = gr.Image(label="Upload image", type="pil")
            single_output = gr.Gallery(label="Edited result(s)", columns=2, height=520)
        single_button = gr.Button("Run edit", variant="primary")
        single_file = gr.File(label="Download ZIP")
        single_button.click(
            edit_single,
            inputs=[single_input, prompt, ref2, ref3, steps, guidance, seed],
            outputs=[single_output, single_file],
        )

    with gr.Tab("Batch up to 100"):
        batch_files = gr.File(label="Upload images", file_count="multiple", file_types=["image"])
        batch_button = gr.Button("Run batch", variant="primary")
        batch_gallery = gr.Gallery(label="Edited images", columns=4, height=520)
        batch_zip = gr.File(label="Download ZIP")
        batch_button.click(
            edit_batch,
            inputs=[batch_files, prompt, ref2, ref3, steps, guidance, seed],
            outputs=[batch_gallery, batch_zip],
        )

    with gr.Tab("Select folder batch"):
        folder_files = gr.File(
            label="Select folder",
            file_count="directory",
            file_types=["image"],
        )
        with gr.Row():
            folder_output_prefix = gr.Textbox(label="Output prefix", value="edited_batch")
            folder_output_name = gr.Textbox(label="Output folder name (auto)", value="edited_batch_001")
        with gr.Row():
            folder_batch_size = gr.Number(label="Batch size", value=50, precision=0)
            folder_start_index = gr.Number(label="Start image number", value=1, precision=0)
            folder_skip_completed = gr.Checkbox(label="Resume: skip completed results", value=True)
        folder_status = gr.Markdown("Select a folder to detect image count.")
        folder_error = gr.Textbox(label="Error details", lines=3, interactive=False, visible=True)
        with gr.Row():
            folder_refresh = gr.Button("Detect images / auto name")
            folder_next = gr.Button("Next batch")
            folder_run = gr.Button("Run this batch", variant="primary")
        folder_gallery = gr.Gallery(label="Completed images", columns=4, height=520)
        folder_zip = gr.File(label="Download completed batch ZIP")
        folder_files.change(
            auto_folder_output_name,
            inputs=[folder_files, folder_batch_size, folder_start_index, folder_output_prefix],
            outputs=[folder_output_name, folder_status],
        )
        folder_batch_size.change(
            auto_folder_output_name,
            inputs=[folder_files, folder_batch_size, folder_start_index, folder_output_prefix],
            outputs=[folder_output_name, folder_status],
        )
        folder_start_index.change(
            auto_folder_output_name,
            inputs=[folder_files, folder_batch_size, folder_start_index, folder_output_prefix],
            outputs=[folder_output_name, folder_status],
        )
        folder_output_prefix.change(
            auto_folder_output_name,
            inputs=[folder_files, folder_batch_size, folder_start_index, folder_output_prefix],
            outputs=[folder_output_name, folder_status],
        )
        folder_refresh.click(
            auto_folder_output_name,
            inputs=[folder_files, folder_batch_size, folder_start_index, folder_output_prefix],
            outputs=[folder_output_name, folder_status],
        )
        folder_next.click(
            next_folder_batch,
            inputs=[folder_files, folder_batch_size, folder_start_index, folder_output_prefix],
            outputs=[folder_start_index, folder_output_name, folder_status],
        )
        folder_run.click(
            edit_uploaded_folder,
            inputs=[
                folder_files,
                folder_output_name,
                folder_batch_size,
                folder_start_index,
                folder_skip_completed,
                prompt,
                ref2,
                ref3,
                steps,
                guidance,
                seed,
            ],
            outputs=[folder_gallery, folder_zip, folder_status, folder_error],
        )

    with gr.Tab("Batch history"):
        gr.Markdown("Use this to download completed folders again or check what already finished.")
        with gr.Row():
            history_choice = gr.Dropdown(label="Completed output folder", choices=list_output_runs(), interactive=True)
            history_refresh = gr.Button("Refresh history")
        history_status = gr.Markdown("Refresh after a batch finishes.")
        history_gallery = gr.Gallery(label="Completed images", columns=4, height=520)
        history_zip = gr.File(label="Download ZIP")
        demo.load(
            refresh_output_history,
            inputs=[],
            outputs=[history_choice, history_gallery, history_zip, history_status],
        )
        history_refresh.click(
            refresh_output_history,
            inputs=[],
            outputs=[history_choice, history_gallery, history_zip, history_status],
        )
        history_choice.change(
            select_output_run,
            inputs=[history_choice],
            outputs=[history_gallery, history_zip, history_status],
        )

    with gr.Tab("Folder on pod"):
        gr.Markdown("Pick an uploaded batch, then run it. The output folder is filled automatically.")
        with gr.Row():
            batch_choice = gr.Dropdown(
                label="Uploaded image batch",
                choices=list_input_batches(),
                value=(list_input_batches()[0] if list_input_batches() else None),
                interactive=True,
            )
            refresh_button = gr.Button("Refresh batches")
        batch_status = gr.Markdown()
        input_dir = gr.Textbox(label="Input folder on pod", value="/workspace/input/batch_001", interactive=False)
        output_dir = gr.Textbox(label="Output folder on pod", value="/workspace/output/batch_001", interactive=True)
        limit = gr.Number(label="Max images to process, 0 means all", value=0, precision=0)
        dir_button = gr.Button("Run folder", variant="primary")
        dir_gallery = gr.Gallery(label="Edited images", columns=4, height=520)
        dir_zip = gr.File(label="Download ZIP")
        demo.load(
            refresh_batches,
            inputs=[],
            outputs=[batch_choice, input_dir, output_dir, batch_status],
        )
        refresh_button.click(
            refresh_batches,
            inputs=[],
            outputs=[batch_choice, input_dir, output_dir, batch_status],
        )
        batch_choice.change(
            select_batch,
            inputs=[batch_choice],
            outputs=[input_dir, output_dir, batch_status],
        )
        dir_button.click(
            edit_directory,
            inputs=[input_dir, output_dir, prompt, ref2, ref3, steps, guidance, seed, limit],
            outputs=[dir_gallery, dir_zip],
        )

    with gr.Tab("Video from image"):
        if wan_video is None:
            gr.Markdown("Video UI is not installed yet.")
        else:
            gr.Markdown("Upload one image, describe the motion, run, then download the MP4.")
            with gr.Row():
                video_input = gr.Image(label="Input image", type="pil")
                video_output = gr.Video(label="Result video")
            video_prompt = gr.Textbox(
                label="Motion prompt",
                lines=3,
                value="natural slow head movement, subtle smile, realistic motion, cinematic lighting",
            )
            video_negative = gr.Textbox(label="Negative prompt", lines=2, value=wan_video.NEGATIVE_PROMPT)
            with gr.Row():
                video_width = gr.Dropdown([480, 640, 704, 832], value=480, label="Width")
                video_height = gr.Dropdown([480, 640, 704, 832], value=480, label="Height")
                video_frames = gr.Slider(17, 81, value=33, step=8, label="Frames")
                video_fps = gr.Slider(8, 24, value=16, step=1, label="FPS")
            with gr.Row():
                video_steps = gr.Slider(2, 12, value=4, step=1, label="Steps")
                video_guidance = gr.Slider(1.0, 8.0, value=1.0, step=0.1, label="Guidance")
                video_seed = gr.Number(value=12345, precision=0, label="Seed (-1 random)")
            video_button = gr.Button("Run video", variant="primary")
            video_file = gr.File(label="Download MP4")
            video_button.click(
                wan_video.run_video,
                inputs=[
                    video_input,
                    video_prompt,
                    video_negative,
                    video_width,
                    video_height,
                    video_frames,
                    video_steps,
                    video_guidance,
                    video_seed,
                    video_fps,
                ],
                outputs=[video_output, video_file],
            )


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch(
        server_name="0.0.0.0",
        server_port=7860,
        allowed_paths=[str(OUTPUT_DIR), str(COMFY_OUTPUT_DIR), "/workspace"],
    )
