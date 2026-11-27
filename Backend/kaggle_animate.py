"""
"Alive 2D": animate still pictures (the character acts, not just a camera move) with the open
LTX-Video image-to-video model on a free Kaggle GPU.

Usage (test):
    python Backend/kaggle_animate.py <out_dir> <image> "<motion prompt>" [<image> "<prompt>" ...]
"""
import base64
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_images import _kaggle, _username  # noqa: E402
from utils import PROJECT_ROOT  # noqa: E402

KERNEL_SLUG = "elite-animate"
TEMPLATE = Path(__file__).resolve().parent / "kaggle_animate_template.py"


def _small_jpeg(path) -> str:
    """The kernel's code file has a size limit (~300 KB): send each picture already cut to the
    clip size (512x896, 9:16) as a ~50 KB JPEG."""
    import io

    from PIL import Image, ImageOps

    image = ImageOps.fit(Image.open(path).convert("RGB"), (512, 896), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


def animate(jobs: List[dict], out_dir: Path, timeout_minutes: int = 150, model: str = "ltx") -> Path:
    """jobs: [{"id", "image": Path, "prompt"}] -> out_dir with <id>.mp4 for every success."""
    work = PROJECT_ROOT / "kaggle" / f"animate_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        kernel_id = f"{_username()}/{KERNEL_SLUG}"
        payload = [{"id": j["id"], "prompt": j["prompt"], "image_b64": _small_jpeg(j["image"])} for j in jobs]
        code = TEMPLATE.read_text(encoding="utf-8").replace(
            "__JOBS__", base64.b64encode(json.dumps(payload).encode()).decode()).replace("__MODEL__", model)
        (work / "animate.py").write_text(code, encoding="utf-8")
        (work / "kernel-metadata.json").write_text(json.dumps({
            "id": kernel_id, "title": KERNEL_SLUG, "code_file": "animate.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "machine_shape": "NvidiaTeslaT4", "dataset_sources": [], "competition_sources": [], "kernel_sources": [],
        }), encoding="utf-8")
        print(f"[+] Sending {len(jobs)} picture(s) to animate on the Kaggle GPU...", flush=True)
        _kaggle("kernels", "push", "-p", str(work))
        # Right after a push the status can still show the previous run's "complete": only
        # accept a finished status once this run was seen queued or running (or after 10 minutes)
        deadline, status, started, seen = time.time() + timeout_minutes * 60, "", time.time(), False
        while time.time() < deadline:
            time.sleep(30)
            try:
                status = _kaggle("kernels", "status", kernel_id).lower()
            except RuntimeError as err:  # a short internet drop must not end the wait
                print(f"[-] Status check failed, retrying: {str(err)[-120:]}", flush=True)
                continue
            seen = seen or "queued" in status or "running" in status
            if any(word in status for word in ("complete", "error", "cancel")) and (seen or time.time() - started > 600):
                break
        print(f"[*] Kaggle status: {status.strip()[-120:]}", flush=True)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        _kaggle("kernels", "output", kernel_id, "-p", str(out_dir), "--file-pattern", r"out/.*|.*\.log", timeout=900)
        return out_dir
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    model_arg = os.getenv("ANIMATE_MODEL", "ltx")
    target, rest = Path(sys.argv[1]), sys.argv[2:]
    test_jobs = [{"id": Path(rest[i]).stem, "image": rest[i], "prompt": rest[i + 1]} for i in range(0, len(rest), 2)]
    result = animate(test_jobs, target, model=model_arg)
    print(f"[+] Results in {result}: {[p.name for p in result.rglob('*') if p.is_file()]}")
