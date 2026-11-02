"""
HyperFrames motion graphics (maps, counters, charts, title cards) for
documentaries. Cards are rendered in 4K from motion/templates/card.html with
the HyperFrames CLI (headless Chrome, no GPU) and cut into the scene video over
the narration of the paragraph they belong to.

A script asks for a card with a header line:
    GRAPHIC: <paragraph index> | <json>
e.g. GRAPHIC: 0 | {"type": "title", "title": "The Strait of Hormuz", "counter": {"label": "DAY", "to": 246}, "stamp": "CLOSED"}
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "motion" / "templates" / "card.html"
HYPERFRAMES = os.getenv("HYPERFRAMES_VERSION", "hyperframes@0.9.3")
MAX_CARD_SECONDS = 8.0


def render_card(card: dict, duration: float, size: Tuple[int, int], out_path: Path, image: Optional[str] = None) -> Path:
    """Render one card to an MP4 of exactly `duration` seconds (4K, scaled later)."""
    portrait = size[1] > size[0]
    w, h = (1080, 1920) if portrait else (1920, 1080)
    project = Path(tempfile.mkdtemp(prefix="hf_card_"))
    try:
        (project / "assets").mkdir()
        card = dict(card)
        if image and card.get("type") == "title":
            shutil.copy(image, project / "assets" / "bg.jpg")
            card["image"] = "assets/bg.jpg"
        html = (
            TEMPLATE.read_text(encoding="utf-8")
            .replace("__CARD__", json.dumps(card))
            .replace("__DUR__", f"{duration:.2f}")
            .replace("__W__", str(w)).replace("__H__", str(h))
            .replace("__RESOLUTION__", "portrait" if portrait else "landscape")
        )
        (project / "index.html").write_text(html, encoding="utf-8")
        (project / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "assets"}}), encoding="utf-8")
        (project / "meta.json").write_text(json.dumps({"id": "card", "name": "card"}), encoding="utf-8")
        subprocess.run(
            f'npx --yes {HYPERFRAMES} render --resolution {"portrait-4k" if portrait else "landscape-4k"} '
            f'--quality delivery --fps 30 -o "{out_path.resolve()}"',
            shell=True, cwd=project, check=True, capture_output=True, timeout=900,
        )
    finally:
        shutil.rmtree(project, ignore_errors=True)
    return out_path


def paragraph_times(script: str, sentences: List[str], durations: List[float]) -> List[Tuple[float, float]]:
    """(start, length) of each paragraph, from the per-sentence narration timings."""
    import re
    from pipeline import SENTENCE_END

    times, index, clock = [], 0, 0.0
    for paragraph in [p for p in re.split(r"\n\s*\n", script.strip()) if p.strip()]:
        count = len([s for s in re.split(SENTENCE_END + r"|\n+", paragraph) if s.strip()])
        length = sum(durations[index:index + count])
        times.append((clock, length))
        clock += length
        index += count
    return times


def insert_cards(video: str, cards: List[Tuple[float, float, Path]], size: Tuple[int, int], out_path: Path) -> Path:
    """Replace [start, start+length) of the silent scene video with each card clip."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cards = sorted(cards)
    inputs, filters, labels, cursor = ["-i", video], [], [], 0.0
    for i, (start, length, clip) in enumerate(cards):
        if start > cursor:
            filters.append(f"[0:v]trim={cursor:.3f}:{start:.3f},setpts=PTS-STARTPTS,fps=30,format=yuv420p[p{i}]")
            labels.append(f"[p{i}]")
        inputs += ["-i", str(clip)]
        filters.append(
            f"[{i + 1}:v]scale={size[0]}:{size[1]}:flags=lanczos,fps=30,format=yuv420p,"
            f"tpad=stop_mode=clone:stop_duration=2,trim=0:{length:.3f},setpts=PTS-STARTPTS[c{i}]"
        )
        labels.append(f"[c{i}]")
        cursor = start + length
    filters.append(f"[0:v]trim=start={cursor:.3f},setpts=PTS-STARTPTS,fps=30,format=yuv420p[tail]")
    labels.append("[tail]")
    filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[out]")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "14", str(out_path)],
        check=True,
    )
    return out_path


def add_graphics(video: str, graphics: List[dict], script: str, sentences: List[str], durations: List[float],
                 size: Tuple[int, int], work_dir: Path, image: Optional[str] = None, emit=print) -> str:
    """Render every requested card and cut it into the scene video; returns the new video path."""
    times = paragraph_times(script, sentences, durations)
    cards = []
    for n, graphic in enumerate(graphics):
        index = graphic["paragraph"]
        if index >= len(times):
            continue
        start, length = times[index]
        length = min(MAX_CARD_SECONDS, length)
        clip = work_dir / f"card_{n:02d}.mp4"
        try:
            emit(f"[+] Rendering motion graphic {n + 1}/{len(graphics)} ({graphic['card'].get('type')})", "info")
            render_card(graphic["card"], length, size, clip, image=image)
            cards.append((start, length, clip))
        except Exception as err:  # a failed card just leaves the illustrated scene in place
            emit(f"[-] Motion graphic {n + 1} skipped: {err}", "warning")
    if not cards:
        return video
    out = work_dir / "with_graphics.mp4"
    insert_cards(video, cards, size, out)
    return str(out)
