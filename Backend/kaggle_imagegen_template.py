# Generates images for Elite Mind Decoder on a Kaggle GPU.
# JOBS is filled in by Backend/kaggle_images.py before the notebook is pushed.
import json, os, subprocess, sys, time

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "diffusers>=0.30", "transformers", "accelerate", "safetensors"], check=True)

import torch
from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline, UNet2DConditionModel
from huggingface_hub import hf_hub_download
from safetensors.torch import load_file

JOBS = json.loads(r"""__JOBS__""")
NEGATIVE = "text, words, letters, watermark, logo, signature, blurry, deformed, extra fingers, extra limbs"

base = "stabilityai/stable-diffusion-xl-base-1.0"
unet = UNet2DConditionModel.from_config(base, subfolder="unet").to("cuda", torch.float16)
unet.load_state_dict(load_file(hf_hub_download("ByteDance/SDXL-Lightning", "sdxl_lightning_4step_unet.safetensors"), device="cuda"))
pipe = StableDiffusionXLPipeline.from_pretrained(base, unet=unet, torch_dtype=torch.float16, variant="fp16").to("cuda")
pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")

os.makedirs("/kaggle/working/images", exist_ok=True)
done = []
for i, job in enumerate(JOBS):
    started = time.time()
    try:
        image = pipe(
            job["prompt"], negative_prompt=job.get("negative", NEGATIVE),
            num_inference_steps=4, guidance_scale=0,
            width=job.get("width", 1344), height=job.get("height", 768),
        ).images[0]
        image.save(f"/kaggle/working/images/{job['id']}.jpg", quality=95)
        done.append(job["id"])
        print(f"[{i + 1}/{len(JOBS)}] {job['id']} in {time.time() - started:.1f}s", flush=True)
    except Exception as err:
        print(f"[{i + 1}/{len(JOBS)}] {job['id']} FAILED: {err}", flush=True)

json.dump(done, open("/kaggle/working/done.json", "w"))
