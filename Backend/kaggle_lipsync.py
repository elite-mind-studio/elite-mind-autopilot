"""
Talking-head lip-sync on a free Kaggle GPU (SadTalker + GFPGAN), for the drama pipeline.

Each job is a still face plus a voice line; the result is a talking-face MP4.
The kernel script is Backend/kaggle_lipsync_template.py with the inputs embedded.

Usage (test):
    python Backend/kaggle_lipsync.py <face.jpg> <line.mp3> <out.mp4>
"""
import base64
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaggle_images import _kaggle, _username  # noqa: E402
from utils import PROJECT_ROOT  # noqa: E402

KERNEL_SLUG = "drama-lipsync"
TEMPLATE = Path(__file__).resolve().parent / "kaggle_lipsync_template.py"


def lipsync(jobs: List[dict], on_log=None, timeout_minutes: int = 90) -> Dict[str, bytes]:
    """jobs: [{"id": str, "face": Path, "audio": Path}] -> {id: mp4 bytes} for every success."""
    emit = on_log or (lambda message, level="info": print(message, flush=True))
    work = PROJECT_ROOT / "kaggle" / f"lipsync_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        kernel_id = f"{_username()}/{KERNEL_SLUG}"
        payload = [{
            "id": job["id"],
            "face_b64": base64.b64encode(Path(job["face"]).read_bytes()).decode(),
            "audio_b64": base64.b64encode(Path(job["audio"]).read_bytes()).decode(),
            "audio_ext": Path(job["audio"]).suffix.lstrip(".") or "mp3",
        } for job in jobs]
        (work / "lipsync.py").write_text(TEMPLATE.read_text(encoding="utf-8").replace("__JOBS__", json.dumps(payload)),
                                         encoding="utf-8")
        (work / "kernel-metadata.json").write_text(json.dumps({
            "id": kernel_id, "title": KERNEL_SLUG, "code_file": "lipsync.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "machine_shape": "NvidiaTeslaT4", "dataset_sources": [], "competition_sources": [], "kernel_sources": [],
        }), encoding="utf-8")
        emit(f"[+] Sending {len(jobs)} lip-sync shots to Kaggle GPU...")
        _kaggle("kernels", "push", "-p", str(work))
        deadline, status = time.time() + timeout_minutes * 60, ""
        while time.time() < deadline:
            time.sleep(30)
            status = _kaggle("kernels", "status", kernel_id).lower()
            if any(word in status for word in ("complete", "error", "cancel")):
                break
        out = work / "out"
        _kaggle("kernels", "output", kernel_id, "-p", str(out), "--file-pattern", r"out/.*|.*.log", timeout=900)
        if "complete" not in status:
            log = next(out.glob("*.log"), None)
            raise RuntimeError(f"Kaggle lip-sync did not complete: {status.strip()[-200:]}"
                               + (f"\n{log.read_text(errors='ignore')[-1500:]}" if log else ""))
        results = {}
        for job in jobs:
            path = out / "out" / f"{job['id']}.mp4"
            if path.exists():
                results[job["id"]] = path.read_bytes()
        emit(f"[+] Kaggle returned {len(results)}/{len(jobs)} talking shots.")
        if len(results) < len(jobs):
            log = next(out.glob("*.log"), None)
            if log:  # show why: the end of the GPU job's output, without the noise
                text = log.read_text(encoding="utf-8", errors="ignore")
                try:
                    text = "".join(item.get("data", "") for item in json.loads(text))
                except ValueError:
                    pass
                emit("\n".join(text.splitlines()[-25:]))
        return results
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    face, audio, target = sys.argv[1:4]
    result = lipsync([{"id": "test", "face": face, "audio": audio}])
    if "test" in result:
        Path(target).write_bytes(result["test"])
        print(f"[+] Saved {target}")
    else:
        print("[-] No video came back")
