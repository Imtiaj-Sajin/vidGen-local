"""Time-sculpture effect: a subject's past frames trail behind it as fading, particle-like echoes,
with occasional glitch bursts. Works best on footage with a plain light background.

Usage (ComfyUI's Python has numpy, av and Pillow):
  ComfyUI_windows_portable\\python_embeded\\python.exe -I scripts\\timesculpt.py IN.mp4 OUT.mp4
      [--echoes 14] [--step 2] [--offset 9] [--glitch 1] [--seed 7]

The original audio track is copied to the output.
"""
import argparse
import collections
import os
import subprocess
import sys

import av
import numpy as np
from PIL import Image, ImageFilter

DARK = np.array([26, 26, 30], np.float32)


def background(frame):
    """Per-row median works for a plain studio void with a vertical floor gradient."""
    return np.median(frame, axis=1, keepdims=True).astype(np.float32)          # H x 1 x 3


def subject_mask(frame, bg, thresh=30):
    diff = np.abs(frame.astype(np.float32) - bg).max(axis=2)
    m = Image.fromarray(((diff > thresh) * 255).astype(np.uint8))
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(5))  # drop specks, close holes
    return np.asarray(m) > 127


def shift(a, dx, dy):
    """Shift an H x W (x C) array, filling with zeros."""
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    out[yd, xd] = a[ys, xs]
    return out


def glitch(img, rng, strength):
    out = img.copy()
    h, w = img.shape[:2]
    s = int(6 + 10 * strength)
    out[..., 0] = np.roll(img[..., 0], s, axis=1)          # RGB split
    out[..., 2] = np.roll(img[..., 2], -s, axis=1)
    for _ in range(rng.integers(3, 8)):                     # displaced horizontal slices
        y0 = rng.integers(0, h - 20)
        y1 = min(h, y0 + rng.integers(8, 60))
        out[y0:y1] = np.roll(out[y0:y1], rng.integers(-40, 41), axis=1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--echoes", type=int, default=14)
    ap.add_argument("--step", type=int, default=2, help="frames between echoes")
    ap.add_argument("--offset", type=float, default=9, help="px per echo when the subject is not moving across frame")
    ap.add_argument("--glitch", type=float, default=1.0, help="0 = off")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    inp = av.open(a.src)
    vs = inp.streams.video[0]
    fps = vs.average_rate
    nframes = vs.frames
    tmp = a.dst + ".video.mp4"
    out = av.open(tmp, "w")
    ov = out.add_stream("libx264", rate=fps)
    ov.width, ov.height, ov.pix_fmt = vs.codec_context.width, vs.codec_context.height, "yuv420p"
    ov.options = {"crf": "17", "preset": "medium"}

    hist = collections.deque(maxlen=a.echoes * a.step + 1)   # (rgb, mask, centroid)
    prev_small, burst_left, next_burst = None, 0, int(fps * rng.uniform(2, 4))
    stutter = None
    for i, frame in enumerate(inp.decode(vs)):
        rgb = frame.to_ndarray(format="rgb24")
        small = rgb[::16, ::16].astype(np.int16)
        if prev_small is not None and np.abs(small - prev_small).mean() > 40:
            hist.clear()                                     # hard cut: don't smear across shots
        prev_small = small
        bg = background(rgb)
        m = subject_mask(rgb, bg)
        ys, xs = np.nonzero(m)
        cen = (xs.mean(), ys.mean()) if len(xs) > 200 else None
        hist.append((rgb, m, cen))

        # is the subject travelling across the frame? then its real past positions make the trail
        moving = False
        if cen is not None and len(hist) > 6 and hist[-7][2] is not None:
            moving = abs(cen[0] - hist[-7][2][0]) / 6 > 1.5

        canvas = np.broadcast_to(bg, rgb.shape).astype(np.float32).copy()
        for k in range(a.echoes, 0, -1):                     # oldest first
            idx = len(hist) - 1 - k * a.step
            if idx < 0:
                continue
            e_rgb, e_m, _ = hist[idx]
            age = k / a.echoes
            dx = 0 if moving else int(round(k * a.offset))
            if dx:
                e_rgb, e_m = shift(e_rgb, dx, 0), shift(e_m, dx, 0)
            keep = 0.92 * (1 - age) ** 1.15 + 0.06
            sel = e_m & (rng.random(e_m.shape) < keep)       # particles: sparser with age
            col = e_rgb[sel].astype(np.float32)
            mix = min(0.9, age * 1.1)
            canvas[sel] = col * (1 - mix) + DARK * mix
        canvas[m] = rgb[m]                                   # the present moment, full colour
        img = np.clip(canvas, 0, 255).astype(np.uint8)

        if a.glitch > 0:                                     # short "hyperspeed" glitch bursts
            if i >= next_burst and burst_left == 0:
                burst_left = int(rng.integers(3, 7))
                stutter = img if rng.random() < 0.5 else None
                next_burst = i + int(fps * rng.uniform(2.5, 5.5))
            if burst_left:
                img = glitch(stutter if stutter is not None and burst_left % 2 else img, rng, a.glitch)
                burst_left -= 1

        for pkt in ov.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            out.mux(pkt)
        if i % 48 == 0:
            print(f"frame {i}/{nframes}", flush=True)
    for pkt in ov.encode():
        out.mux(pkt)
    out.close()
    inp.close()

    # copy the original audio back in
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    has_audio = bool(av.open(a.src).streams.audio)
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", tmp]
    cmd += (["-i", a.src, "-map", "0:v", "-map", "1:a", "-c:a", "copy"] if has_audio else ["-map", "0:v"])
    cmd += ["-c:v", "copy", "-movflags", "+faststart", a.dst]
    subprocess.run(cmd, check=True)
    os.remove(tmp)
    print("saved", a.dst)


if __name__ == "__main__":
    sys.exit(main())
