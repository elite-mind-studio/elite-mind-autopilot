"""
Phone alerts through ntfy.sh (free app: subscribe to the topic in NTFY_TOPIC), and
the owner's hand-made thumbnails.

Thumbnails: upload `thumbnails/<channel>_<YYYY-MM-DD>.png` (or .jpg/.jpeg/.webp) to the
repo before the long video posts; channel is "headline" or "elite". The run pulls the
newest copy just before posting, so an upload made after the run started still counts.
"""
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
THUMBS_DIR = ROOT / "thumbnails"
REPO = os.getenv("GITHUB_REPOSITORY", "elite-mind-studio/elite-mind-autopilot")


def notify(title: str, message: str, click: str = "") -> None:
    """Send a push notification; does nothing when NTFY_TOPIC isn't set."""
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        return
    headers = {"Title": title.encode("utf-8"), "Tags": "movie_camera"}
    if click:
        headers["Click"] = click
    try:
        requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, timeout=20)
    except Exception as err:
        print(f"[-] Notification failed: {err}", flush=True)


def thumb_name(channel: str, day: Optional[date] = None) -> str:
    return f"{channel}_{(day or date.today()).isoformat()}"


def upload_link(channel: str) -> str:
    return f"https://github.com/{REPO}/upload/main/thumbnails"


def custom_thumbnail(channel: str) -> Optional[Path]:
    """The owner's thumbnail for today, converted to a 1280x720 JPEG, or None."""
    try:  # pick up an upload made while this run was rendering
        subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=ROOT, timeout=60, check=True)
        subprocess.run(["git", "checkout", "origin/main", "--", "thumbnails"], cwd=ROOT, timeout=60,
                       capture_output=True)
    except Exception:
        pass
    name = thumb_name(channel)
    for found in sorted(THUMBS_DIR.glob(f"{name}.*")):
        if found.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            from PIL import Image

            out = ROOT / "channel_art" / "custom" / f"{name}.jpg"
            out.parent.mkdir(parents=True, exist_ok=True)
            image = Image.open(found).convert("RGB").resize((1280, 720), Image.LANCZOS)
            quality = 92
            image.save(out, "JPEG", quality=quality, optimize=True)
            while out.stat().st_size > 2_000_000 and quality > 60:
                quality -= 8
                image.save(out, "JPEG", quality=quality, optimize=True)
            print(f"[+] Using the owner's thumbnail {found.name}", flush=True)
            return out
    return None


def remind_thumbnail(channel: str, title: str, when: str) -> None:
    """Morning heads-up: today's long video title, so the owner can design the thumbnail."""
    name = thumb_name(channel)
    notify(
        f"Thumbnail needed: {title}"[:120],
        f"Today's long video ({when}):\n{title}\n\n"
        f"Paste your thumbnail prompt with this title, then upload the image named\n{name}.png\n"
        f"Tap this notification to upload. No upload = the automatic thumbnail is used.",
        click=upload_link(channel),
    )


def posted_alert(channel: str, title: str, url: str, used_custom: bool) -> None:
    extra = ("Your thumbnail is on it." if used_custom else
             f"Automatic thumbnail used. To swap it: upload {thumb_name(channel)}.png and tell Claude, "
             "or run Backend/thumb.py.")
    notify(f"Posted: {title}"[:120], f"{url}\n\n{extra}", click=url)
