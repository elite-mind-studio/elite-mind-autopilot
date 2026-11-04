"""
Put a hand-made thumbnail (and optionally a new title) on an uploaded video.

Usage:
    python Backend/thumb.py <elite_mind|headline_tales> <video id, URL or "latest-long"> <image> [--title "New title"]

The image can be any size or format; it is converted to a 1280x720 JPEG under 2 MB.
"""
import os
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TOKENS = {"elite_mind": "youtube-oauth2.json", "headline_tales": "youtube-oauth2-headline_tales.json"}


def video_id(value: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", value)
    return match.group(1) if match else value


def main(argv: list) -> None:
    if len(argv) < 3 or argv[0] not in TOKENS:
        sys.exit(__doc__)
    channel, target, image_path = argv[0], argv[1], Path(argv[2])
    title = argv[argv.index("--title") + 1] if "--title" in argv else ""
    os.environ["YOUTUBE_TOKEN_FILE"] = TOKENS[channel]
    import youtube

    vid = youtube.latest_long_video()[0] if target == "latest-long" else video_id(target)
    out = ROOT / "channel_art" / "custom" / f"{vid}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(image_path).convert("RGB").resize((1280, 720), Image.LANCZOS)
    quality = 92
    image.save(out, "JPEG", quality=quality, optimize=True)
    while out.stat().st_size > 2_000_000 and quality > 60:
        quality -= 8
        image.save(out, "JPEG", quality=quality, optimize=True)
    youtube.set_thumbnail(vid, str(out))
    print(f"[+] Thumbnail set on https://youtu.be/{vid}")

    if title:
        service = youtube.get_authenticated_service()
        snippet = service.videos().list(part="snippet", id=vid).execute()["items"][0]["snippet"]
        snippet["title"] = title[:100]
        service.videos().update(
            part="snippet",
            body={"id": vid, "snippet": {k: snippet[k] for k in ("title", "description", "tags", "categoryId") if k in snippet}},
        ).execute()
        print(f"[+] Title changed to: {title[:100]}")


if __name__ == "__main__":
    main(sys.argv[1:])
