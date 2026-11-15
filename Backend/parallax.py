"""
"Living picture" animation: turn a still image into a short clip with real-looking 3D camera
movement (depth parallax), instead of a flat Ken Burns zoom.

A small depth model (Depth Anything V2, runs on CPU) estimates how far each pixel is; the
virtual camera then moves so near things (the character) slide more than the background.

Usage (test):
    python Backend/parallax.py <image> <out.mp4> [dolly|orbit|rise] [seconds]
"""
import math
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Tuple

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageFilter

DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


@lru_cache(maxsize=1)
def _depth_pipe():
    from transformers import pipeline

    return pipeline("depth-estimation", model=DEPTH_MODEL, device="cpu")


def depth_map(image: Image.Image) -> np.ndarray:
    """0 = far, 1 = near, smoothed so edges don't tear when the camera moves."""
    depth = _depth_pipe()(image)["depth"].resize(image.size, Image.BICUBIC)
    depth = depth.filter(ImageFilter.GaussianBlur(radius=max(2, image.size[0] // 200)))
    d = np.asarray(depth, dtype=np.float32)
    return (d - d.min()) / max(1e-6, d.max() - d.min())


def animate(image_path: str, out_path: str, motion: str = "dolly", seconds: float = 3.0,
            size: Tuple[int, int] = (1080, 1920), fps: int = 30, strength: float = 1.0) -> str:
    """Render `seconds` of camera movement over the picture to an MP4 (no audio)."""
    image = Image.open(image_path).convert("RGB")
    # Cover the frame (crop to its aspect), with margin so the moving camera never shows edges
    margin = 1.12
    scale = max(size[0] * margin / image.width, size[1] * margin / image.height)
    image = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.LANCZOS)
    depth = depth_map(image)

    src = torch.from_numpy(np.asarray(image, dtype=np.float32) / 255).permute(2, 0, 1)[None]
    dep = torch.from_numpy(depth)[None, None]
    h, w = image.height, image.width
    # Output pixel grid in source coordinates (-1..1), centred crop of the frame size
    ys = torch.linspace(-size[1] / h, size[1] / h, size[1])
    xs = torch.linspace(-size[0] / w, size[0] / w, size[0])
    gy, gx = torch.meshgrid(ys, xs, indexing="ij")
    base = torch.stack((gx, gy), -1)[None]
    # Depth sampled at the output pixels (good enough for small camera moves)
    d = F.grid_sample(dep, base, align_corners=False)[0, 0]
    near = d - 0.35  # the focus plane: background moves against the camera, foreground with it

    frames = max(1, round(seconds * fps))  # same count as the zoom version, so scenes stay in sync
    ffmpeg = subprocess.Popen(
        [os.getenv("PARALLAX_FFMPEG", "ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", out_path],
        stdin=subprocess.PIPE,
    )
    amp = 0.045 * strength
    for i in range(frames):
        t = i / max(1, frames - 1)
        ease = 0.5 - 0.5 * math.cos(math.pi * t)
        if motion == "orbit":  # drone-style sideways arc around the subject
            dx, dy, zoom = amp * (2 * ease - 1), 0.0, 1.0 + 0.04 * ease
        elif motion == "rise":  # camera lifts up and pushes in
            dx, dy, zoom = 0.0, amp * (1 - 2 * ease), 1.0 + 0.06 * ease
        else:  # dolly: push in toward the subject, near things grow faster
            dx, dy, zoom = 0.0, 0.0, 1.0 + 0.08 * ease
        grid = base.clone()
        # Parallax: shift by depth; dolly: near pixels also scale up more than far ones
        grid[..., 0] = base[..., 0] / (zoom + 0.06 * ease * near * (motion == "dolly")) - dx * near
        grid[..., 1] = base[..., 1] / (zoom + 0.06 * ease * near * (motion == "dolly")) - dy * near
        frame = F.grid_sample(src, grid, mode="bilinear", padding_mode="border", align_corners=False)
        ffmpeg.stdin.write((frame[0].permute(1, 2, 0).clamp(0, 1) * 255).byte().numpy().tobytes())
    ffmpeg.stdin.close()
    ffmpeg.wait()
    return out_path


if __name__ == "__main__":
    image_arg, out_arg = sys.argv[1], sys.argv[2]
    motion_arg = sys.argv[3] if len(sys.argv) > 3 else "dolly"
    seconds_arg = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
    size_arg = (int(sys.argv[5]), int(sys.argv[6])) if len(sys.argv) > 6 else (1080, 1920)
    fps_arg = int(sys.argv[7]) if len(sys.argv) > 7 else 30
    print(animate(image_arg, out_arg, motion_arg, seconds_arg, size_arg, fps_arg))
