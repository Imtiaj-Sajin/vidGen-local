"""LTX Lab: a test page for LTX-2.5 quants/settings with live VRAM/RAM telemetry.

Open http://127.0.0.1:8188/ltxlab once ComfyUI is running.
"""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque

import psutil
import torch
from aiohttp import web

import folder_paths
from server import PromptServer

from . import graph

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(HERE, "results.json")
SAMPLE_PERIOD = 0.5
HISTORY_SECONDS = 600

_lock = threading.Lock()
_samples = deque(maxlen=int(HISTORY_SECONDS / SAMPLE_PERIOD))
_runs = {}          # prompt_id -> run record (pending/running/finished)
_nvsmi = {"util": None, "temp": None, "power": None, "t": 0}


def _load_results():
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            for r in json.load(f):
                if r["status"] in ("queued", "running"):
                    r["status"] = "cancelled"   # ComfyUI restarted before it ran
                    r["error"] = "ComfyUI was restarted before this run finished."
                _runs[r["prompt_id"]] = r
    except (OSError, ValueError):
        pass


def _save_results():
    # every run is kept (history), including queued ones
    tmp = RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(_runs.values(), key=lambda r: r["queued_at"]), f, indent=1)
    os.replace(tmp, RESULTS_FILE)


def _poll_nvidia_smi():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return
    try:
        out = subprocess.run(
            [exe, "--query-gpu=utilization.gpu,temperature.gpu,power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().split(",")
        _nvsmi.update(util=float(out[0]), temp=float(out[1]), power=float(out[2]))
    except Exception:
        pass


def _read_sample():
    free, total = torch.cuda.mem_get_info()
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    proc = psutil.Process()
    return {
        "t": time.time(),
        "vram_used": (total - free) / 2**30,
        "vram_total": total / 2**30,
        "torch_alloc": torch.cuda.memory_allocated() / 2**30,
        "ram_used": vm.used / 2**30,
        "ram_total": vm.total / 2**30,
        "proc_rss": proc.memory_info().rss / 2**30,
        "swap_used": sw.used / 2**30,
        "gpu_util": _nvsmi["util"],
        "gpu_temp": _nvsmi["temp"],
        "gpu_power": _nvsmi["power"],
    }


def _running_prompt_ids():
    running, _ = PromptServer.instance.prompt_queue.get_current_queue()
    return {item[1] for item in running}


def _finish(run):
    hist = PromptServer.instance.prompt_queue.get_history(prompt_id=run["prompt_id"]).get(run["prompt_id"], {})
    status = hist.get("status", {})
    run["status"] = "ok" if status.get("completed") else ("error" if status else "cancelled")
    for msg in status.get("messages", []):
        if msg[0] == "execution_error":
            run["error"] = msg[1].get("exception_message", "")[:500]
        if msg[0] == "execution_interrupted":
            run["status"] = "cancelled"
    for out in hist.get("outputs", {}).values():
        for item in out.get("images", []) + out.get("videos", []) + out.get("gifs", []):
            if str(item.get("filename", "")).lower().endswith((".mp4", ".webm", ".mov")):
                run["video"] = item


def _sampler_loop():
    last_smi = 0
    while True:
        try:
            if time.time() - last_smi > 2:
                _poll_nvidia_smi()
                last_smi = time.time()
            s = _read_sample()
            active = _running_prompt_ids()
            with _lock:
                _samples.append(s)
                changed = False
                for run in _runs.values():
                    pid = run["prompt_id"]
                    if run["status"] == "queued" and pid in active:
                        run["status"] = "running"
                        run["started_at"] = s["t"]
                        torch.cuda.reset_peak_memory_stats()
                        changed = True
                    if run["status"] == "running":
                        run["peak_vram"] = max(run.get("peak_vram", 0), s["vram_used"])
                        run["peak_ram"] = max(run.get("peak_ram", 0), s["ram_used"])
                        run["peak_rss"] = max(run.get("peak_rss", 0), s["proc_rss"])
                        run["peak_swap"] = max(run.get("peak_swap", 0), s["swap_used"])
                        run["peak_torch"] = max(run.get("peak_torch", 0), torch.cuda.max_memory_allocated() / 2**30)
                        if pid not in active:
                            run["finished_at"] = s["t"]
                            run["seconds"] = round(s["t"] - run["started_at"], 1)
                            _finish(run)
                            changed = True
                    elif run["status"] == "queued" and run["queued_at"] < s["t"] - 5 and pid not in active:
                        # never seen running: finished between samples or failed validation
                        pending = {item[1] for item in PromptServer.instance.prompt_queue.get_current_queue()[1]}
                        if pid not in pending:
                            run["started_at"] = run["queued_at"]
                            run["seconds"] = None
                            _finish(run)
                            changed = True
                if changed:
                    _save_results()
        except Exception as e:  # keep telemetry alive no matter what
            print(f"[ltx_lab] sampler error: {e}")
        time.sleep(SAMPLE_PERIOD)


def _list(folder, exts):
    try:
        return sorted(f for f in folder_paths.get_filename_list(folder) if f.lower().endswith(exts))
    except Exception:
        return []


routes = PromptServer.instance.routes


@routes.get("/ltxlab")
async def page(request):
    return web.FileResponse(os.path.join(HERE, "web", "index.html"))


@routes.get("/ltxlab/api/options")
async def options(request):
    models = _list("diffusion_models", (".safetensors", ".gguf"))
    models += [m for m in _list("unet_gguf", (".gguf",)) if m not in models]
    return web.json_response({
        "models": [m for m in models if "ltx" in m.lower()],
        "text_encoders": [t for t in _list("text_encoders", (".safetensors", ".gguf")) if "ltx" in t.lower()],
        "upscalers": _list("latent_upscale_models", (".safetensors",)),
        "defaults": graph.DEFAULTS,
    })


@routes.get("/ltxlab/api/stats")
async def stats(request):
    since = float(request.query.get("since", 0))
    with _lock:
        pts = [s for s in _samples if s["t"] > since]
        runs = sorted(_runs.values(), key=lambda r: r["queued_at"])
    return web.json_response({"samples": pts, "runs": runs, "now": time.time()})


@routes.post("/ltxlab/api/run")
async def run(request):
    body = await request.json()
    g, info = graph.build(body)
    pid = str(uuid.uuid4())
    # hand the graph to ComfyUI's own /prompt handler so validation is identical to the UI
    import aiohttp
    port = PromptServer.instance.port
    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"http://127.0.0.1:{port}/prompt",
                             json={"prompt": g, "prompt_id": pid, "client_id": "ltxlab"}) as resp:
            res = await resp.json(content_type=None)
            if resp.status != 200:
                return web.json_response({"error": res}, status=400)
    rec = {
        "prompt_id": pid, "status": "queued", "queued_at": time.time(),
        "model": body.get("model"), "text_encoder": body.get("text_encoder"),
        "two_stage": body.get("two_stage", True), "duration": body.get("duration"),
        "fps": body.get("fps"), "seed": body.get("seed"), "image": body.get("image"),
        "prompt": body.get("prompt") or "", "negative": body.get("negative") or "",
        "settings": body,   # everything the page sent, so the run can be reused exactly
        **info,
    }
    with _lock:
        _runs[pid] = rec
        _save_results()
    return web.json_response(rec)


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


@routes.get("/ltxlab/api/uploads")
async def uploads(request):
    root = folder_paths.get_input_directory()
    files = []
    for dirpath, _, names in os.walk(root):
        for n in names:
            if n.lower().endswith(IMAGE_EXTS) and n != "example.png":
                full = os.path.join(dirpath, n)
                rel = os.path.relpath(full, root).replace("\\", "/")
                files.append({"name": rel, "mtime": os.path.getmtime(full)})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return web.json_response(files[:200])


@routes.post("/ltxlab/api/delete")
async def delete(request):
    body = await request.json()
    with _lock:
        for pid in body.get("ids", []):
            _runs.pop(pid, None)
        _save_results()
    return web.json_response({"ok": True})


_load_results()
threading.Thread(target=_sampler_loop, daemon=True, name="ltx_lab_sampler").start()

from . import editor  # noqa: E402,F401  (registers /ltxlab/editor routes)

print("[ltx_lab] LTX Lab ready at /ltxlab (editor at /ltxlab/editor)")
