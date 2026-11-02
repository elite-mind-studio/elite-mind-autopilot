# Generates images for Elite Mind Decoder on a Kaggle GPU with FLUX.1-schnell
# (open source, Apache 2.0) - the same model Cloudflare Workers AI runs, quantized
# (GGUF Q5) so it fits Kaggle's free T4. ~35-40 s per 1024x576 image.
# JOBS and the Hugging Face token are filled in by Backend/kaggle_images.py.
import json, os, subprocess, sys, time

os.environ["HF_TOKEN"] = "__HF_TOKEN__"
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "diffusers", "transformers",
                "accelerate", "gguf", "sentencepiece", "protobuf"], check=True)

import numpy as np
import torch
from diffusers import FluxPipeline, FluxTransformer2DModel, GGUFQuantizationConfig

JOBS = json.loads(r"""__JOBS__""")
GGUF = "https://huggingface.co/city96/FLUX.1-schnell-gguf/blob/main/flux1-schnell-Q5_K_S.gguf"

transformer = FluxTransformer2DModel.from_single_file(
    GGUF, quantization_config=GGUFQuantizationConfig(compute_dtype=torch.float16), torch_dtype=torch.float16
)
pipe = FluxPipeline.from_pretrained("black-forest-labs/FLUX.1-schnell", transformer=transformer, torch_dtype=torch.float16)
pipe.enable_model_cpu_offload()

os.makedirs("/kaggle/working/images", exist_ok=True)
done = []
for i, job in enumerate(JOBS):
    started = time.time()
    try:
        image = pipe(
            job["prompt"], num_inference_steps=4, guidance_scale=0.0, max_sequence_length=256,
            width=job.get("width", 1024), height=job.get("height", 576),
        ).images[0]
        if float(np.asarray(image).mean()) < 5:  # fp16 overflow gives a black frame
            raise RuntimeError("blank image")
        image.save(f"/kaggle/working/images/{job['id']}.jpg", quality=95)
        done.append(job["id"])
        print(f"[{i + 1}/{len(JOBS)}] {job['id']} in {time.time() - started:.1f}s", flush=True)
    except Exception as err:
        print(f"[{i + 1}/{len(JOBS)}] {job['id']} FAILED: {err}", flush=True)

json.dump(done, open("/kaggle/working/done.json", "w"))
