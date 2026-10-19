"""Story mode: render several clips in a row, each starting from the last frame of the previous one.

Usage (ComfyUI must be running):
  ComfyUI_windows_portable\\python_embeded\\python.exe -I scripts\\chain.py stories\\nugget.json [--start-part N]

The story JSON has shared "settings" (any LTX Lab run field) and a list of "parts" with a "prompt"
(and optionally its own settings). Part 1 uses settings.image if given, otherwise text-to-video.
Every run is queued through LTX Lab, so all parts show up in its history.
"""
import json
import os
import sys
import time
import urllib.request
import uuid

import av
from PIL import Image

API = "http://127.0.0.1:8188"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "ComfyUI_windows_portable", "ComfyUI", "output")
LOG = os.path.join(ROOT, "scripts", "chain.log")


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def post_json(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get_run(prompt_id):
    with urllib.request.urlopen(API + "/ltxlab/api/stats?since=9999999999", timeout=60) as r:
        runs = json.load(r)["runs"]
    return next((r for r in runs if r["prompt_id"] == prompt_id), None)


def upload_image(path, name):
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\nfalse\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(API + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]


def last_frame(video_path, out_png):
    frame = None
    with av.open(video_path) as c:
        for frame in c.decode(c.streams.video[0]):
            pass
    frame.to_image().save(out_png)
    return out_png


def main():
    story_path = sys.argv[1]
    start = int(sys.argv[sys.argv.index("--start-part") + 1]) if "--start-part" in sys.argv else 1
    story = json.load(open(story_path, encoding="utf-8"))
    progress_path = os.path.splitext(story_path)[0] + ".progress.json"  # per machine, not committed
    progress = json.load(open(progress_path, encoding="utf-8")) if os.path.exists(progress_path) else {}
    name = story.get("name", os.path.splitext(os.path.basename(story_path))[0])
    image = story["settings"].get("image")
    tmp_dir = os.path.join(ROOT, "cache", "chain")
    os.makedirs(tmp_dir, exist_ok=True)

    if start > 1:  # resume: continue from the last frame of the previous part's video
        prev = progress.get(str(start - 1))
        if not prev:
            sys.exit(f"no saved video for part {start - 1} in {progress_path}")
        png = last_frame(prev, os.path.join(tmp_dir, f"{name}_part{start}_start.png"))
        image = upload_image(png, os.path.basename(png))

    for i, part in enumerate(story["parts"], 1):
        if i < start:
            continue
        settings = {**story["settings"], **part, "image": image}
        settings.pop("title", None)
        log(f"{name} part {i}/{len(story['parts'])}: queueing ({'image ' + image if image else 'text-to-video'})")
        run = post_json("/ltxlab/api/run", settings)
        pid = run["prompt_id"]
        while True:
            time.sleep(10)
            r = get_run(pid)
            if r and r["status"] not in ("queued", "running"):
                break
        if r["status"] != "ok" or not r.get("video"):
            log(f"{name} part {i} ended with status {r['status']}: {r.get('error', '')}. Stopping.")
            sys.exit(1)
        video = os.path.join(OUTPUT, r["video"].get("subfolder", ""), r["video"]["filename"])
        log(f"{name} part {i} done in {r['seconds']}s -> {video}")
        progress[str(i)] = video
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump(progress, f, indent=1)
        if i < len(story["parts"]):
            png = last_frame(video, os.path.join(tmp_dir, f"{name}_part{i + 1}_start.png"))
            image = upload_image(png, os.path.basename(png))
    log(f"{name}: all {len(story['parts'])} parts finished")


if __name__ == "__main__":
    main()
