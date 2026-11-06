"""
Kaggle GPU job: talking-head lip-sync for the drama pipeline (SadTalker + GFPGAN).
A still face + a voice line -> a talking face video (lips, blinks, small head moves).
Inputs are embedded below as base64 by Backend/kaggle_lipsync.py; results go to /kaggle/working/out.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

JOBS = json.loads(r'''__JOBS__''')  # [{"id", "face_b64", "audio_b64", "audio_ext"}]
# Heavy files (code, models, Python env) live in /tmp so the downloaded output is just the videos
WORK = Path("/tmp/lipsync")
WORK.mkdir(parents=True, exist_ok=True)
OUT = Path("/kaggle/working/out")
OUT.mkdir(parents=True, exist_ok=True)


def run(cmd: str) -> None:
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


run("git clone -q --depth 1 https://github.com/OpenTalker/SadTalker.git /tmp/SadTalker")
os.chdir("/tmp/SadTalker")
# Kaggle's Python (3.13) is too new for SadTalker's pinned packages: use a Python 3.10 environment
run("pip install -q uv && uv venv -q --python 3.10 /tmp/st")
PY = "/tmp/st/bin/python"
run(f"uv pip install -q --python {PY} torch==2.1.2 torchvision==0.16.2 torchaudio==2.1.2 "
    "--index-url https://download.pytorch.org/whl/cu121")
run(f"uv pip install -q --python {PY} 'numpy==1.23.5' face_alignment==1.3.5 imageio==2.19.3 imageio-ffmpeg "
    "librosa==0.9.2 numba resampy==0.3.1 pydub scipy==1.10.1 kornia==0.6.8 tqdm yacs==0.1.8 pyyaml joblib "
    "scikit-image==0.19.3 basicsr==1.4.2 facexlib==0.3.0 gfpgan==1.3.8 av safetensors opencv-python==4.8.1.78 "
    "'setuptools<70'")  # librosa 0.9 needs pkg_resources
run("bash scripts/download_models.sh")

for job in JOBS:
    face = WORK / f"{job['id']}_face.jpg"
    audio = WORK / f"{job['id']}.{job.get('audio_ext', 'mp3')}"
    face.write_bytes(base64.b64decode(job["face_b64"]))
    audio.write_bytes(base64.b64decode(job["audio_b64"]))
    wav = WORK / f"{job['id']}.wav"
    run(f"ffmpeg -v error -y -i {audio} -ac 1 -ar 16000 {wav}")
    result = WORK / f"result_{job['id']}"
    try:
        run(f"{PY} inference.py --driven_audio {wav} --source_image {face} --result_dir {result} "
            f"--preprocess crop --enhancer gfpgan --size 512 --expression_scale 1.6")
        videos = sorted(result.rglob("*.mp4"))
        if videos:
            (OUT / f"{job['id']}.mp4").write_bytes(videos[-1].read_bytes())
            print("[+] done", job["id"], flush=True)
    except Exception as err:
        print("[-] failed", job["id"], err, flush=True)
print(json.dumps(sorted(p.name for p in OUT.iterdir())))
