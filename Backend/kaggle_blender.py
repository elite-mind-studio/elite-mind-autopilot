"""
3D scenes rendered in Blender on a free Kaggle GPU (the laptop needs no Blender).

A scene is a Blender Python script (Backend/blender/*.py) that builds everything in code and
takes "<mode> <out_dir> [width height seconds samples]" after "--". The kernel downloads
Blender, runs each requested render and returns the out/ folder (stills, scene.mp4, render.log).

Usage (test):
    python Backend/kaggle_blender.py Backend/blender/spotlight_test.py <local_out_dir>
"""
import base64
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_images import _kaggle, _username  # noqa: E402
from utils import PROJECT_ROOT  # noqa: E402

KERNEL_SLUG = "elite-blender"
TEMPLATE = Path(__file__).resolve().parent / "kaggle_blender_template.py"


def render(scene: Path, runs: List[List[str]], out_dir: Path, timeout_minutes: int = 120) -> Path:
    """Render `scene` once per argument list in `runs`; returns out_dir with the downloaded results."""
    work = PROJECT_ROOT / "kaggle" / f"blender_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        kernel_id = f"{_username()}/{KERNEL_SLUG}"
        code = (TEMPLATE.read_text(encoding="utf-8")
                .replace("__SCENE__", base64.b64encode(Path(scene).read_bytes()).decode())
                .replace("__RUNS__", json.dumps(runs)))
        (work / "render.py").write_text(code, encoding="utf-8")
        (work / "kernel-metadata.json").write_text(json.dumps({
            "id": kernel_id, "title": KERNEL_SLUG, "code_file": "render.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "machine_shape": "NvidiaTeslaT4", "dataset_sources": [], "competition_sources": [], "kernel_sources": [],
        }), encoding="utf-8")
        print(f"[+] Sending {Path(scene).name} to the Kaggle GPU ({len(runs)} render(s))...", flush=True)
        _kaggle("kernels", "push", "-p", str(work))
        deadline, status = time.time() + timeout_minutes * 60, ""
        while time.time() < deadline:
            time.sleep(30)
            status = _kaggle("kernels", "status", kernel_id).lower()
            if any(word in status for word in ("complete", "error", "cancel")):
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
    scene_path, target = Path(sys.argv[1]), Path(sys.argv[2])
    # Quick stills to check the look, then a 10-second clip
    result = render(scene_path, [["stills"], ["video", "1080", "1920", "10", "64"]], target)
    print(f"[+] Results in {result}: {[p.name for p in result.rglob('*') if p.is_file()]}")
