"""LTX Lab editor: timeline projects and ffmpeg export.

Page: /ltxlab/editor. Projects are saved as JSON in ltx_lab/projects/, exports go to
ComfyUI/output/ltxlab_exports/ and show up in the editor library.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import av
import imageio_ffmpeg
from aiohttp import web

import folder_paths
from server import PromptServer

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS = os.path.join(HERE, "projects")
EXPORT_SUB = "ltxlab_exports"
MEDIA_SUB = "ltxlab_media"
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
FONT = r"C:\Windows\Fonts\arialbd.ttf"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

_probe_cache = {}
_jobs = {}
_jobs_lock = threading.Lock()


# ---------- files ----------
def _base(kind):
    return folder_paths.get_output_directory() if kind == "output" else folder_paths.get_input_directory()


def resolve(f):
    """{"filename","subfolder","type"} -> absolute path, refusing anything outside input/output."""
    base = os.path.realpath(_base(f.get("type", "output")))
    path = os.path.realpath(os.path.join(base, f.get("subfolder") or "", f["filename"]))
    if not path.startswith(base + os.sep) or not os.path.isfile(path):
        raise ValueError(f"file not found: {f.get('subfolder', '')}/{f['filename']}")
    return path


def probe(path):
    key = (path, os.path.getmtime(path))
    if key not in _probe_cache:
        with av.open(path) as c:
            v = c.streams.video[0] if c.streams.video else None
            dur = float(c.duration / 1e6) if c.duration else (float(v.duration * v.time_base) if v and v.duration else 0.0)
            _probe_cache[key] = {
                "duration": round(dur, 3),
                "width": v.codec_context.width if v else 0,
                "height": v.codec_context.height if v else 0,
                "has_audio": bool(c.streams.audio),
                "has_video": v is not None,
            }
    return _probe_cache[key]


def _scan(kind, sub, exts, tag):
    root = os.path.join(_base(kind), sub)
    items = []
    if not os.path.isdir(root):
        return items
    for n in os.listdir(root):
        full = os.path.join(root, n)
        if not n.lower().endswith(exts) or not os.path.isfile(full):
            continue
        try:
            meta = probe(full)
        except Exception:
            continue
        items.append({"filename": n, "subfolder": sub, "type": kind, "kind": tag,
                      "mtime": os.path.getmtime(full), **meta})
    return items


def library(runs):
    by_file = {}
    for r in runs:
        v = r.get("video")
        if v:
            by_file[(v.get("subfolder", ""), v["filename"])] = r
    items = _scan("output", "ltxlab", VIDEO_EXTS, "generated")
    for it in items:
        r = by_file.get((it["subfolder"], it["filename"]))
        if r:
            it["prompt"] = r.get("prompt", "")
            it["model"] = r.get("model", "")
            it["seed"] = r.get("seed")
    items += _scan("output", EXPORT_SUB, VIDEO_EXTS, "export")
    items += _scan("input", MEDIA_SUB, VIDEO_EXTS, "upload")
    audio = []
    root = os.path.join(_base("input"), MEDIA_SUB)
    if os.path.isdir(root):
        for n in os.listdir(root):
            if n.lower().endswith(AUDIO_EXTS):
                full = os.path.join(root, n)
                try:
                    with av.open(full) as c:
                        dur = float(c.duration / 1e6) if c.duration else 0.0
                except Exception:
                    continue
                audio.append({"filename": n, "subfolder": MEDIA_SUB, "type": "input", "kind": "audio",
                              "duration": round(dur, 3), "mtime": os.path.getmtime(full)})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    audio.sort(key=lambda x: x["mtime"], reverse=True)
    return {"videos": items, "audio": audio}


# ---------- render ----------
def _num(v, lo, hi, default):
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def build_command(project, work):
    W, H = int(project.get("width", 720)), int(project.get("height", 1280))
    W, H = W - W % 2, H - H % 2
    fps = _num(project.get("fps"), 1, 60, 24)
    clips = project.get("clips") or []
    if not clips:
        raise ValueError("timeline is empty")
    fade = _num(project.get("fade"), 0.1, 3, 0.5) if project.get("transition") == "fade" and len(clips) > 1 else 0.0

    args, filters, durs = [FFMPEG, "-hide_banner", "-y"], [], []
    n_in = 0
    for i, c in enumerate(clips):
        path = resolve(c["file"])
        meta = probe(path)
        t_in = _num(c.get("in"), 0, meta["duration"], 0)
        t_out = _num(c.get("out"), t_in + 0.05, meta["duration"], meta["duration"])
        speed = _num(c.get("speed"), 0.25, 4, 1)
        vol = _num(c.get("volume"), 0, 4, 1)
        seg = t_out - t_in
        d = seg / speed
        durs.append(d)
        args += ["-ss", f"{t_in:.3f}", "-t", f"{seg:.3f}", "-i", path]
        vi = n_in
        n_in += 1
        if meta["has_audio"]:
            ai = f"{vi}:a"
        else:
            args += ["-f", "lavfi", "-t", f"{seg:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            ai = f"{n_in}:a"
            n_in += 1
        # near-identical shape (e.g. 704x1280 into 720x1280): fill and crop a few pixels instead of
        # adding thin black bars; clearly different shapes are letterboxed like CapCut's "fit"
        src_ar = meta["width"] / max(1, meta["height"])
        if abs(src_ar / (W / H) - 1) < 0.04:
            fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        else:
            fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"
        filters.append(f"[{vi}:v]setpts=(PTS-STARTPTS)/{speed},{fit},setsar=1,fps={fps},format=yuv420p[v{i}]")
        tempo = ""
        s = speed
        while s > 2.0:          # atempo accepts 0.5..2 per stage
            tempo += "atempo=2.0,"; s /= 2.0
        while s < 0.5:
            tempo += "atempo=0.5,"; s /= 0.5
        tempo += f"atempo={s:.4f},"
        filters.append(
            f"[{ai}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,"
            f"{tempo}volume={vol:.3f},apad,atrim=0:{d:.3f}[a{i}]")

    if fade > 0:
        fade = min(fade, min(durs) / 2)
        vprev, aprev, offset = "v0", "a0", 0.0
        for i in range(1, len(clips)):
            offset += durs[i - 1] - fade
            filters.append(f"[{vprev}][v{i}]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}[vx{i}]")
            filters.append(f"[{aprev}][a{i}]acrossfade=d={fade:.3f}[ax{i}]")
            vprev, aprev = f"vx{i}", f"ax{i}"
        vcur, acur = vprev, aprev
        total = sum(durs) - fade * (len(clips) - 1)
    else:
        filters.append("".join(f"[v{i}][a{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=1[vc][ac]")
        vcur, acur = "vc", "ac"
        total = sum(durs)

    # captions: text files + local font copy keep ffmpeg's filter escaping trivial
    caps = [c for c in project.get("captions") or [] if str(c.get("text", "")).strip()]
    if caps:
        shutil.copy(FONT, os.path.join(work, "font.ttf"))
        parts = []
        for k, c in enumerate(caps):
            with open(os.path.join(work, f"cap{k}.txt"), "w", encoding="utf-8") as f:
                f.write(str(c["text"]))
            size = int(_num(c.get("size"), 12, 200, 64))
            # shrink to fit 90% of the frame width (the browser preview wraps, ffmpeg doesn't)
            from PIL import ImageFont
            widest = max(ImageFont.truetype(FONT, size).getlength(line) for line in str(c["text"]).split("\n"))
            if widest > W * 0.9:
                size = max(12, int(size * W * 0.9 / widest))
            a = _num(c.get("start"), 0, total, 0)
            b = _num(c.get("end"), a + 0.1, total, min(total, a + 2))
            y = {"top": "h*0.10", "center": "(h-text_h)/2"}.get(c.get("pos"), "h*0.80-text_h")
            color = re.sub(r"[^#0-9a-zA-Z]", "", str(c.get("color") or "white")) or "white"
            parts.append(
                f"drawtext=fontfile=font.ttf:textfile=cap{k}.txt:fontsize={size}:fontcolor={color}:"
                f"borderw={max(2, size // 12)}:bordercolor=black:line_spacing=0:text_align=C:"
                f"x=(w-text_w)/2:y={y}:enable='between(t,{a:.3f},{b:.3f})'")
        filters.append(f"[{vcur}]" + ",".join(parts) + "[vt]")
        vcur = "vt"

    music = project.get("music")
    if music and music.get("file"):
        args += ["-stream_loop", "-1", "-i", resolve(music["file"])]
        mv = _num(music.get("volume"), 0, 2, 0.3)
        filters.append(f"[{n_in}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"volume={mv:.3f},atrim=0:{total:.3f},afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[mus]")
        filters.append(f"[{acur}][mus]amix=inputs=2:duration=first:normalize=0[am]")
        acur = "am"
        n_in += 1
    if project.get("normalize", True):
        filters.append(f"[{acur}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[an]")
        acur = "an"

    with open(os.path.join(work, "graph.txt"), "w", encoding="utf-8") as f:
        f.write(";\n".join(filters))
    args += ["-filter_complex_script", "graph.txt", "-map", f"[{vcur}]", "-map", f"[{acur}]",
             "-c:v", "libx264", "-preset", "medium", "-crf", str(int(_num(project.get("crf"), 14, 30, 18))),
             "-pix_fmt", "yuv420p", "-r", f"{fps}", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-movflags", "+faststart", "-progress", "pipe:1", "-nostats"]
    return args, total


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s or "edit")).strip("_")[:60] or "edit"


def _run_export(job_id, project):
    job = _jobs[job_id]
    work = tempfile.mkdtemp(prefix="ltxedit_")
    try:
        args, total = build_command(project, work)
        out_dir = os.path.join(folder_paths.get_output_directory(), EXPORT_SUB)
        os.makedirs(out_dir, exist_ok=True)
        base = _safe_name(project.get("name"))
        n = 1
        while os.path.exists(os.path.join(out_dir, f"{base}_{n:03d}.mp4")):
            n += 1
        name = f"{base}_{n:03d}.mp4"
        tmp_out = os.path.join(out_dir, name + ".part.mp4")
        job.update(total=total, status="rendering")
        p = subprocess.Popen(args + [tmp_out], cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job["pid"] = p.pid
        err_tail = []
        threading.Thread(target=lambda: err_tail.extend(p.stderr.readlines()[-25:]), daemon=True).start()
        for line in p.stdout:
            if line.startswith("out_time_us="):
                try:
                    job["progress"] = min(0.999, int(line.split("=")[1]) / 1e6 / max(total, 0.01))
                except ValueError:
                    pass
        p.wait()
        if job.get("cancel"):
            job["status"] = "cancelled"
        elif p.returncode != 0:
            job.update(status="error", error="".join(err_tail)[-1500:])
        else:
            os.replace(tmp_out, os.path.join(out_dir, name))
            job.update(status="done", progress=1.0,
                       file={"filename": name, "subfolder": EXPORT_SUB, "type": "output"})
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
    except Exception as e:
        job.update(status="error", error=str(e))
    finally:
        job["finished_at"] = time.time()
        shutil.rmtree(work, ignore_errors=True)


# ---------- routes ----------
routes = PromptServer.instance.routes


@routes.get("/ltxlab/editor")
async def editor_page(request):
    return web.FileResponse(os.path.join(HERE, "web", "editor.html"))


@routes.get("/ltxlab/api/library")
async def api_library(request):
    from . import _runs  # run history from the lab
    return web.json_response(library(list(_runs.values())))


_thumbs = {}


def _thumb(path, t, h):
    key = (path, os.path.getmtime(path), t, h)
    if key in _thumbs:
        return _thumbs[key]
    import io
    with av.open(path) as c:
        v = c.streams.video[0]
        if t > 0:
            c.seek(int(t / v.time_base), stream=v, any_frame=False, backward=True)
        img = None
        for frame in c.decode(v):
            img = frame.to_image()
            if frame.time is None or frame.time >= t - 0.05:
                break
    w = max(1, round(img.width * h / img.height))
    buf = io.BytesIO()
    img.convert("RGB").resize((w, h)).save(buf, "JPEG", quality=72)
    data = buf.getvalue()
    if len(_thumbs) > 800:
        _thumbs.clear()
    _thumbs[key] = data
    return data


@routes.get("/ltxlab/api/thumb")
async def api_thumb(request):
    q = request.query
    try:
        path = resolve({"filename": q["filename"], "subfolder": q.get("subfolder", ""), "type": q.get("type", "output")})
    except (KeyError, ValueError):
        return web.Response(status=404)
    t = round(max(0.0, float(q.get("t", 0))) * 2) / 2   # 0.5 s steps keep the cache small
    h = min(400, max(40, int(q.get("h", 120))))
    import asyncio
    data = await asyncio.get_running_loop().run_in_executor(None, _thumb, path, t, h)
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@routes.post("/ltxlab/api/media")
async def api_media(request):
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file" or not field.filename:
        return web.json_response({"error": "no file"}, status=400)
    name = os.path.basename(field.filename)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in VIDEO_EXTS + AUDIO_EXTS:
        return web.json_response({"error": f"unsupported file type {ext}"}, status=400)
    root = os.path.join(_base("input"), MEDIA_SUB)
    os.makedirs(root, exist_ok=True)
    stem = _safe_name(stem)
    target, i = os.path.join(root, stem + ext.lower()), 1
    while os.path.exists(target):  # never overwrite earlier uploads
        target = os.path.join(root, f"{stem}_{i}{ext.lower()}")
        i += 1
    with open(target, "wb") as f:
        while chunk := await field.read_chunk(1 << 20):
            f.write(chunk)
    return web.json_response({"filename": os.path.basename(target), "subfolder": MEDIA_SUB, "type": "input"})


@routes.get("/ltxlab/api/projects")
async def api_projects(request):
    os.makedirs(PROJECTS, exist_ok=True)
    out = []
    for n in os.listdir(PROJECTS):
        if n.endswith(".json"):
            p = os.path.join(PROJECTS, n)
            try:
                d = json.load(open(p, encoding="utf-8"))
            except ValueError:
                continue
            out.append({"id": n[:-5], "name": d.get("name", n[:-5]), "clips": len(d.get("clips", [])),
                        "mtime": os.path.getmtime(p)})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return web.json_response(out)


@routes.get("/ltxlab/api/projects/{pid}")
async def api_project_get(request):
    p = os.path.join(PROJECTS, _safe_name(request.match_info["pid"]) + ".json")
    if not os.path.exists(p):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(json.load(open(p, encoding="utf-8")))


@routes.post("/ltxlab/api/projects")
async def api_project_save(request):
    d = await request.json()
    os.makedirs(PROJECTS, exist_ok=True)
    pid = _safe_name(d.get("id") or uuid.uuid4().hex[:8])
    d["id"] = pid
    tmp = os.path.join(PROJECTS, pid + ".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(PROJECTS, pid + ".json"))
    return web.json_response({"id": pid})


@routes.post("/ltxlab/api/export")
async def api_export(request):
    project = await request.json()
    job_id = uuid.uuid4().hex[:10]
    with _jobs_lock:
        _jobs[job_id] = {"id": job_id, "status": "queued", "progress": 0.0, "name": project.get("name"),
                         "started_at": time.time()}
    threading.Thread(target=_run_export, args=(job_id, project), daemon=True).start()
    return web.json_response(_jobs[job_id])


@routes.get("/ltxlab/api/export/{job}")
async def api_export_status(request):
    job = _jobs.get(request.match_info["job"])
    if not job:
        return web.json_response({"error": "unknown job"}, status=404)
    return web.json_response({k: v for k, v in job.items() if k != "pid"})


@routes.post("/ltxlab/api/export/{job}/cancel")
async def api_export_cancel(request):
    job = _jobs.get(request.match_info["job"])
    if job and job.get("pid") and job["status"] == "rendering":
        job["cancel"] = True
        subprocess.run(["taskkill", "/PID", str(job["pid"]), "/F"], capture_output=True)
    return web.json_response({"ok": True})
