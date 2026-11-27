# Kaggle GPU job: bring still pictures to life with LTX-Video (open image-to-video model).
# Filled in by Backend/kaggle_animate.py (__JOBS__ = [{id, image_b64, prompt}]).
import base64
import io
import json
import subprocess
import time
from pathlib import Path

OUT = Path("/kaggle/working/out")
OUT.mkdir(parents=True, exist_ok=True)
subprocess.run("pip install -q -U diffusers accelerate transformers sentencepiece imageio imageio-ffmpeg ftfy",
               shell=True, check=False)

import torch  # noqa: E402
from diffusers import LTXImageToVideoPipeline, WanImageToVideoPipeline  # noqa: E402
from diffusers.utils import export_to_video  # noqa: E402
from PIL import Image  # noqa: E402

JOBS = json.loads(base64.b64decode("__JOBS__"))
NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted, morphing, extra limbs, text"


MODEL = "__MODEL__"  # "ltx" (fast) or "wan" (Wan 2.2 TI2V-5B: slower, keeps faces together better)


def load(dtype):
    if MODEL == "wan":
        pipe = WanImageToVideoPipeline.from_pretrained("Wan-AI/Wan2.2-TI2V-5B-Diffusers", torch_dtype=dtype)
        pipe.enable_model_cpu_offload()
        return pipe
    pipe = LTXImageToVideoPipeline.from_pretrained("Lightricks/LTX-Video", torch_dtype=dtype)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    return pipe


def cover(image: Image.Image, w: int, h: int) -> Image.Image:
    scale = max(w / image.width, h / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left, top = (image.width - w) // 2, (image.height - h) // 2
    return image.crop((left, top, left + w, top + h))


# T4 has no fast bfloat16: try float16 first and fall back if it produces empty (NaN/black) frames
pipe, dtype = None, None
for job in JOBS:
    image = Image.open(io.BytesIO(base64.b64decode(job["image_b64"]))).convert("RGB")
    w, h = job.get("size", [512, 896])  # Shorts shape (9:16)
    image = cover(image, w, h)
    for candidate in ([dtype] if dtype else [torch.float16, torch.bfloat16]):
        if pipe is None or dtype != candidate:
            pipe, dtype = None, candidate
            torch.cuda.empty_cache()
            pipe = load(candidate)
        start = time.time()
        frames = pipe(image=image, prompt=job["prompt"], negative_prompt=NEGATIVE, width=w, height=h,
                      num_frames=job.get("frames", 49 if MODEL == "wan" else 65), num_inference_steps=job.get("steps", 30 if MODEL == "wan" else 40),
                      guidance_scale=5.0 if MODEL == "wan" else 3.0, generator=torch.Generator("cpu").manual_seed(7)).frames[0]
        # LTX returns PIL images (0-255), Wan returns float arrays (0-1): average brightness on 0-255
        import numpy as np
        means = [float(np.nan_to_num(np.asarray(f, dtype=np.float32)).mean()) for f in frames[::12]]
        brightness = sum(m * (255 if m <= 1.0 else 1) for m in means) * 1000
        print(f"{job['id']}: {dtype} {time.time() - start:.0f}s, brightness {brightness}", flush=True)
        if brightness > 1000:  # a real picture, not black frames
            export_to_video(frames, str(OUT / f"{job['id']}.mp4"), fps=24)
            break
        dtype = None  # black output: try the next precision
print("done", flush=True)
