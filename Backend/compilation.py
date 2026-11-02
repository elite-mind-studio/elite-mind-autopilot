"""
Weekly compilation: the week's Shorts of a channel stitched into one long
(16:9, 1440p) video with an intro, numbered chapter cards, YouTube chapters in
the description and links to every Short. Long videos are what count toward the
4,000 watch hours, and these are built from Shorts that already worked.

The Shorts come from the GitHub Actions artifacts of the past week (see
.github/workflows/compilation.yml), matched to their YouTube IDs:
  - Elite Mind Decoder: each autopilot artifact holds output.mp4 + .last_posted.json
  - Headline Tales:     artifacts hold <slug>.mp4, IDs from channels/headline_tales/posted.json

Usage:
    python Backend/compilation.py <elite|headline_tales> <artifacts dir> [--dry-run]
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
FRAME = (2560, 1440)
FONT = ROOT / "fonts" / "bold_font.ttf"
MIN_SHORTS = 5
CHANNELS = {
    "elite": {
        "profile": None,
        "title": "Every Mind Trick Black Decoded This Week ({dates})",
        "intro": ("THIS WEEK ON", "ELITE MIND DECODER"),
        "description": "Every Short from this week in one video: the money psychology and mind tricks Black decoded, back to back.",
        "category": "27",
        "tags": "money psychology, mind tricks, psychology facts, compilation, elite mind decoder",
    },
    "headline_tales": {
        "profile": "headline_tales",
        "title": "This Week in the World, Explained ({dates})",
        "intro": ("THIS WEEK IN", "THE WORLD"),
        "description": "The week's biggest stories from Headline Tales in one video, each one explained in under a minute.",
        "category": "25",
        "tags": "world news explained, this week, current events, documentary, headline tales",
    },
}


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _duration(path: Path) -> float:
    info = subprocess.run([_ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def collect(channel: str, artifacts: Path) -> List[Tuple[str, Path]]:
    """(video_id, file) for every Short of this channel found in the artifacts."""
    found = []
    if channel == "elite":
        for marker in artifacts.rglob(".last_posted.json"):
            record = json.loads(marker.read_text(encoding="utf-8")).get("record") or {}
            video = marker.parent / "output.mp4"
            if record.get("id") and video.exists() and _duration(video) <= 180:
                found.append((record["id"], video))
    else:
        posted_file = ROOT / "channels" / "headline_tales" / "posted.json"
        posted = json.loads(posted_file.read_text(encoding="utf-8")) if posted_file.exists() else {}
        for slug, info in posted.items():
            if info.get("format") != "short":
                continue
            video = next(iter(artifacts.rglob(f"{slug}.mp4")), None)
            if video:
                found.append((info["id"], video))
    return found


def _card(lines: List[Tuple[str, int, str]], seconds: float, out: Path, work: Path) -> None:
    """A dark title card; lines are (text, size, colour), centred vertically."""
    filters, total = [], sum(size * 1.25 for _, size, _ in lines)
    y = (FRAME[1] - total) / 2
    for i, (text, size, colour) in enumerate(lines):
        textfile = work / f"card_{out.stem}_{i}.txt"
        textfile.write_text(text, encoding="utf-8")
        filters.append(
            f"drawtext=fontfile='{FONT.as_posix().replace(':', chr(92) + ':')}':textfile='{textfile.as_posix().replace(':', chr(92) + ':')}':"
            f"fontsize={size}:fontcolor={colour}:borderw=6:bordercolor=black:x=(w-text_w)/2:y={y:.0f}"
        )
        y += size * 1.25
    subprocess.run(
        [_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x07111f:s={FRAME[0]}x{FRAME[1]}:r=30:d={seconds}",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", str(seconds),
         "-vf", ",".join(filters) + ",fade=t=in:st=0:d=0.3,fade=t=out:st=" + f"{seconds - 0.3:.2f}:d=0.3",
         "-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(out)],
        check=True,
    )


def _framed(short: Path, out: Path) -> None:
    """The vertical Short centred on a blurred, darkened copy of itself (16:9)."""
    w, h = FRAME
    graph = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=40:2,eq=brightness=-0.25[bg];"
        f"[0:v]scale=-2:{h}:flags=lanczos[fg];[bg][fg]overlay=(W-w)/2:0,fps=30,format=yuv420p[v];"
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo[a]"
    )
    subprocess.run(
        [_ffmpeg(), "-v", "error", "-y", "-i", str(short), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-c:a", "aac", "-b:a", "192k", str(out)],
        check=True,
    )


def build(channel: str, shorts: List[dict], work: Path) -> Tuple[Path, str]:
    """Returns (video, chapters text). shorts: [{"id", "title", "path"}] oldest first."""
    cfg = CHANNELS[channel]
    work.mkdir(parents=True, exist_ok=True)
    parts, chapters, clock = [], [], 0.0
    intro = work / "00_intro.mp4"
    _card([(cfg["intro"][0], 70, "0xffb547"), (cfg["intro"][1], 150, "white"), (f"{len(shorts)} STORIES", 60, "0x8fa3b5")], 3.5, intro, work)
    parts.append(intro)
    clock += 3.5
    for n, short in enumerate(shorts, 1):
        title = re.sub(r"\s*#\w+", "", short["title"]).strip()
        card = work / f"{n:02d}_card.mp4"
        _card([(f"#{n}", 120, "0xffb547"), (title.upper()[:60], 64, "white")], 1.6, card, work)
        clip = work / f"{n:02d}_short.mp4"
        _framed(Path(short["path"]), clip)
        chapters.append(f"{int(clock // 60)}:{int(clock % 60):02d} {title}")
        clock += 1.6 + _duration(clip)
        parts += [card, clip]
    outro = work / "99_outro.mp4"
    _card([("NEW SHORTS EVERY DAY", 70, "0xffb547"), ("SUBSCRIBE", 160, "white")], 4.0, outro, work)
    parts.append(outro)
    chapters[0:0] = ["0:00 Intro"]

    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    out = work / "compilation.mp4"
    subprocess.run(
        [_ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing.name,
         "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out.name],
        check=True, cwd=work,
    )
    return out, "\n".join(chapters)


def run(channel: str, artifacts: Path, dry_run: bool = False) -> None:
    import os

    cfg = CHANNELS[channel]
    load_dotenv(ROOT / ".env")
    if cfg["profile"]:
        load_dotenv(ROOT / "channels" / f"{cfg['profile']}.env", override=True)
    sys.path.insert(0, str(ROOT / "Backend"))
    from youtube import add_to_playlist, get_authenticated_service, upload_video

    found = dict(collect(channel, artifacts))
    if not found:
        print(f"[-] {channel}: no Shorts found in the artifacts")
        return
    youtube = get_authenticated_service()
    since = datetime.now(timezone.utc) - timedelta(days=8)
    shorts = []
    ids = list(found)
    for start in range(0, len(ids), 50):
        for v in youtube.videos().list(part="snippet,status", id=",".join(ids[start:start + 50])).execute()["items"]:
            published = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
            if v["status"]["privacyStatus"] == "public" and published >= since:
                shorts.append({"id": v["id"], "title": v["snippet"]["title"], "path": found[v["id"]], "published": published})
    shorts.sort(key=lambda s: s["published"])
    if len(shorts) < MIN_SHORTS:
        print(f"[-] {channel}: only {len(shorts)} Shorts this week, skipping the compilation")
        return

    first, last = shorts[0]["published"], shorts[-1]["published"]
    dates = f"{first:%b %d}-{last:%d}" if first.month == last.month else f"{first:%b %d}-{last:%b %d}"
    video, chapters = build(channel, shorts, ROOT / "temp" / f"compilation_{channel}")
    links = "\n".join(f"#{n} {s['title']}: https://youtube.com/shorts/{s['id']}" for n, s in enumerate(shorts, 1))
    description = "\n\n".join(p for p in (
        cfg["description"], "Chapters:\n" + chapters, "Watch each Short:\n" + links,
        os.getenv("YOUTUBE_DESCRIPTION_FOOTER", ""),
    ) if p)
    title = cfg["title"].format(dates=dates)[:100]
    print(f"[+] {title}: {len(shorts)} Shorts, {_duration(video) / 60:.1f} min -> {video}")
    if dry_run:
        return
    response = upload_video(video_path=str(video), title=title, description=description, category=cfg["category"],
                            keywords=cfg["tags"], privacy_status=os.getenv("YOUTUBE_PRIVACY_STATUS", "public"))
    if not (response or {}).get("id"):
        raise RuntimeError("Compilation upload failed")
    print(f"[+] Posted https://youtu.be/{response['id']}")
    try:
        add_to_playlist(response["id"], "Weekly Recaps")
    except Exception as err:
        print(f"[-] Playlist: {err}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in CHANNELS:
        print(__doc__)
        sys.exit(1)
    run(sys.argv[1], Path(sys.argv[2]), "--dry-run" in sys.argv)
