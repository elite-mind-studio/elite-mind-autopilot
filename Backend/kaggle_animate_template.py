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
subprocess.run("pip install -q -U diffusers accelerate transformers sentencepiece imageio imageio-ffmpeg",
               shell=True, check=False)

import torch  # noqa: E402
from diffusers import LTXImageToVideoPipeline  # noqa: E402
from diffusers.utils import export_to_video  # noqa: E402
from PIL import Image  # noqa: E402

JOBS = json.loads(base64.b64decode("__JOBS__"))
NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted, morphing, extra limbs, text"


def load(dtype):
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
                      num_frames=job.get("frames", 65), num_inference_steps=job.get("steps", 40),
                      guidance_scale=3.0, generator=torch.Generator("cpu").manual_seed(7)).frames[0]
        brightness = sum(sum(f.convert("L").resize((16, 16)).getdata()) for f in frames[::12])
        print(f"{job['id']}: {dtype} {time.time() - start:.0f}s, brightness {brightness}", flush=True)
        if brightness > 1000:  # a real picture, not black frames
            export_to_video(frames, str(OUT / f"{job['id']}.mp4"), fps=24)
            break
        dtype = None  # black output: try the next precision
print("done", flush=True)
