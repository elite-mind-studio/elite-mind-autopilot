"""
Batch image generation on a free Kaggle GPU (FLUX.1-schnell, ~35-40 s/image).

All prompts of a video are sent in one Kaggle run: the script below is filled
with the jobs, pushed as a private kernel, polled until it finishes, and its
output images are downloaded. Needs the Kaggle CLI with a token
(~/.kaggle/access_token) on a phone-verified account (GPU + internet).
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List

from utils import PROJECT_ROOT

TEMPLATE = Path(__file__).resolve().parent / "kaggle_imagegen_template.py"
KERNEL_SLUG = os.getenv("KAGGLE_IMAGE_KERNEL", "elite-mind-imagegen")
# Kaggle's CLI is installed in the project's virtualenv
KAGGLE_CLI = str(Path(sys.executable).with_name("kaggle.exe" if os.name == "nt" else "kaggle"))


def _kaggle(*args: str, timeout: int = 300) -> str:
    result = subprocess.run(
        [KAGGLE_CLI, *args], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args[:2])} failed: {(result.stderr or result.stdout)[-300:]}")
    return result.stdout


def _username() -> str:
    username = os.getenv("KAGGLE_USERNAME", "").strip()
    if username:
        return username
    for line in _kaggle("config", "view").splitlines():
        if "username" in line:
            return line.split(":", 1)[1].strip()
    raise RuntimeError("Could not determine the Kaggle username")


def available() -> bool:
    """True when the Kaggle CLI and a token are configured."""
    token = Path.home() / ".kaggle" / "access_token"
    return Path(KAGGLE_CLI).exists() and (token.exists() or os.getenv("KAGGLE_API_TOKEN"))


def generate(jobs: List[dict], on_log=None, timeout_minutes: int = 60) -> Dict[str, bytes]:
    """
    Generate images on Kaggle.

    Args:
        jobs: [{"id": str, "prompt": str, "width": int, "height": int}, ...]
        on_log: optional callback(message, level).
        timeout_minutes: give up waiting after this long.

    Returns:
        Dict[str, bytes]: JPEG bytes for every job id that succeeded.
    """
    emit = on_log or (lambda message, level: print(message, flush=True))
    work = PROJECT_ROOT / "kaggle" / f"run_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        kernel_id = f"{_username()}/{KERNEL_SLUG}"
        token = os.getenv("HF_TOKEN", "").strip()
        if not token:
            raise RuntimeError("HF_TOKEN not set (needed to download FLUX.1-schnell)")
        # The kernel is private, so the read-only token is only visible to this Kaggle account
        script = (
            TEMPLATE.read_text(encoding="utf-8")
            .replace("__JOBS__", json.dumps(jobs))
            .replace("__HF_TOKEN__", token)
        )
        (work / "imagegen.py").write_text(script, encoding="utf-8")
        (work / "kernel-metadata.json").write_text(json.dumps({
            "id": kernel_id,
            "title": KERNEL_SLUG,
            "code_file": "imagegen.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
            "machine_shape": "NvidiaTeslaT4",
            "dataset_sources": [],
            "competition_sources": [],
            "kernel_sources": [],
        }), encoding="utf-8")

        emit(f"[+] Sending {len(jobs)} images to Kaggle GPU...", "info")
        _kaggle("kernels", "push", "-p", str(work))

        deadline = time.time() + timeout_minutes * 60
        status = ""
        while time.time() < deadline:
            time.sleep(20)
            status = _kaggle("kernels", "status", kernel_id).lower()
            if any(word in status for word in ("complete", "error", "cancel")):
                break
        if "complete" not in status:
            raise RuntimeError(f"Kaggle run did not complete: {status.strip()[-200:]}")

        out = work / "out"
        _kaggle("kernels", "output", kernel_id, "-p", str(out), timeout=600)
        images = {}
        for job in jobs:
            path = out / "images" / f"{job['id']}.jpg"
            if path.exists():
                images[job["id"]] = path.read_bytes()
        emit(f"[+] Kaggle returned {len(images)}/{len(jobs)} images.", "info")
        return images
    finally:
        shutil.rmtree(work, ignore_errors=True)
