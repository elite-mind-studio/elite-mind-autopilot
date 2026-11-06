"""
Videos for another channel profile (channels/<name>.env loaded on top of .env)
from hand-written scripts, with a guardian: every render is checked for errors
(length, sound, black frames, size) and remade if it fails, before anything is
posted. Posted videos are recorded in channels/<name>/posted.json so a rerun
never posts twice.

Script file (channels/<name>/scripts/<slug>.txt), header then a blank line:
    FORMAT: short | long
    SUBJECT: The Strait of Hormuz
    TITLE: 246 DAYS CLOSED                 (title bar / internal title)
    YT_TITLE: ...                          (YouTube title)
    THUMB_WORD: CLOSED                     (long videos: big thumbnail word)
    TAGS: a, b, c
    DESCRIPTION: one paragraph
    SOURCE: https://...                    (repeatable)
    GRAPHIC: 0 | {"type": "title", ...}    (repeatable; HyperFrames card over that paragraph)
    VOICE: calm | emotional                (optional; otherwise chosen from the topic)
    TONE: tragic | tense | upbeat          (optional; music, pacing, colour, sounds)
    MEMORIAL: In memory of ...             (tragedies: shown over the last seconds)

Usage:
    python Backend/channel.py <name> auth                  # one-time login for that channel
    python Backend/channel.py <name> make <script>         # render only, for review
    python Backend/channel.py <name> publish <long> <short>  # render, check, post (long first)
    python Backend/channel.py headline_tales auto           # daily news documentary, end to end
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 3


def load_profile(name: str) -> None:
    # Profile values must be in the environment before the pipeline modules import
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "channels" / f"{name}.env", override=True)
    sys.path.insert(0, str(ROOT / "Backend"))


def parse_script(path: Path) -> dict:
    header, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    meta = {"source": [], "graphics": []}
    for line in header.splitlines():
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key == "source":
            meta["source"].append(value.strip())
        elif key in ("cue", "sound", "glitch", "chat", "music", "options", "countdown", "answer"):
            # CUE: <spoken words> | <on-screen text>, SOUND: <spoken words> | vibrate|cash, GLITCH: <spoken words>
            phrase, _, extra = value.partition("|")
            meta.setdefault("cues", []).append({"kind": key, "phrase": phrase.strip(), "value": extra.strip()})
        elif key == "graphic":  # GRAPHIC: <paragraph> | <card json>, see Backend/motion.py
            index, _, card = value.partition("|")
            meta["graphics"].append({"paragraph": int(index), "card": json.loads(card)})
        elif key:
            meta[key] = value.strip()
    return {**meta, "script": body.strip(), "slug": path.stem}


def _probe(path: Path) -> dict:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    info = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, text=True).stderr
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    size = re.search(r"Video:.*?(\d{3,4})x(\d{3,4})", info)
    volume = subprocess.run(
        [ffmpeg, "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"], capture_output=True, text=True
    ).stderr
    black = subprocess.run(
        [ffmpeg, "-i", str(path), "-vf", "blackdetect=d=0.5:pix_th=0.08", "-an", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", volume)
    return {
        "seconds": int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else 0,
        "size": (int(size[1]), int(size[2])) if size else None,
        "has_audio": "Audio:" in info,
        "mean_db": float(mean[1]) if mean else -99.0,
        "black_seconds": sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", black)),
    }


def check_video(path: Path, video_format: str) -> list:
    """Return a list of problems; empty means the video is fit to post."""
    if not path.exists() or path.stat().st_size < 500_000:
        return ["file missing or too small"]
    p = _probe(path)
    problems = []
    # 1080p sources are exported at 1440p (see video._generate_video_ffmpeg)
    expected = [(1080, 1920), (1440, 2560)] if video_format == "short" else [(1920, 1080), (2560, 1440)]
    if p["size"] not in expected:
        problems.append(f"size {p['size']} instead of {expected}")
    # YouTube treats vertical videos up to 3 minutes as Shorts
    low, high = (20, 175) if video_format == "short" else (180, 1200)
    if not low <= p["seconds"] <= high:
        problems.append(f"length {p['seconds']:.0f}s outside {low}-{high}s")
    if not p["has_audio"] or p["mean_db"] < -35:
        problems.append(f"narration missing or too quiet ({p['mean_db']} dB)")
    if p["black_seconds"] > max(2.0, 0.05 * p["seconds"]):
        problems.append(f"{p['black_seconds']:.1f}s of black frames")
    return problems


def make(name: str, script_path: Path) -> Path:
    from autopilot import build_payload
    from pipeline import run_generation_pipeline
    from utils import SUBTITLES_DIR, TEMP_DIR, clean_dir
    import visuals

    clean_dir(str(TEMP_DIR))
    clean_dir(str(SUBTITLES_DIR))
    spec = parse_script(script_path)
    video_format = spec.get("format", "short")
    data = build_payload(spec["subject"], False, video_format)
    data.update({"script": spec["script"], "title": spec.get("title") or spec["subject"], "series": None,
                 "graphics": spec["graphics"], "voiceMood": spec.get("voice"),  # VOICE: calm | emotional
                 "tone": spec.get("tone"), "memorial": spec.get("memorial", ""),  # TONE: tragic | tense | upbeat | suspense
                 "choice": spec.get("choice", ""),  # CHOICE: Sign it | Walk away (suspense ending)
                 "cast": spec.get("cast", ""),  # CAST: how the story's other people look
                 "cues": spec.get("cues", [])})  # CUE / SOUND / GLITCH lines, timed to spoken words

    output = run_generation_pipeline(data=data, is_cancelled=None, on_log=lambda m, l: print(m, flush=True))
    out_dir = ROOT / "channels" / name / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{spec['slug']}.mp4"
    shutil.copy(output, target)
    if video_format == "long" and visuals.LAST_SCENE_IMAGES:
        from pipeline import designed_thumbnail

        thumb = designed_thumbnail(spec.get("yt_title") or data["title"], spec["script"],
                                   visuals.LAST_SCENE_IMAGES[0], words=spec.get("thumb_word", ""))
        shutil.copy(thumb, out_dir / f"{spec['slug']}_thumb.jpg")
    print(f"[+] Saved {target}", flush=True)
    return target


def make_checked(name: str, script_path: Path, reuse: bool = True) -> Path:
    """Render until the video passes every check (up to MAX_ATTEMPTS)."""
    video_format = parse_script(script_path).get("format", "short")
    # Videos the owner reviewed and approved (committed to the repo) are posted as they are
    approved = ROOT / "channels" / name / "approved" / f"{script_path.stem}.mp4"
    if approved.exists() and not check_video(approved, video_format):
        print(f"[+] Using the approved {approved.name}", flush=True)
        return approved
    target = ROOT / "channels" / name / "videos" / f"{script_path.stem}.mp4"
    if reuse and target.exists() and not check_video(target, video_format):
        print(f"[+] {target.name} already rendered and passes all checks", flush=True)
        return target
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            make(name, script_path)
            problems = check_video(target, video_format)
        except Exception as err:  # a failed render is retried like a failed check
            problems = [f"render failed: {err}"]
        if not problems:
            print(f"[+] {target.name} passed all checks (attempt {attempt})", flush=True)
            return target
        print(f"[-] Attempt {attempt} rejected: {'; '.join(problems)}", flush=True)
        if all(p.startswith("length") for p in problems):
            break  # the same script always renders the same length; remaking won't help
    raise RuntimeError(f"{script_path.name} failed {MAX_ATTEMPTS} attempts; nothing was posted")


def _posted_file(name: str) -> Path:
    return ROOT / "channels" / name / "posted.json"


def _posted(name: str) -> dict:
    file = _posted_file(name)
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}


def post(name: str, script_path: Path, video: Path, long_url: str = "") -> str:
    import os
    from youtube import add_to_playlist, set_thumbnail, upload_video

    spec = parse_script(script_path)
    # LONG_URL: <link> in a queued Short's header links it to an already-posted documentary
    long_url = long_url or spec.get("long_url", "")
    posted = _posted(name)
    if spec["slug"] in posted:
        print(f"[*] {spec['slug']} already posted: {posted[spec['slug']]['url']}", flush=True)
        return posted[spec["slug"]]["url"]

    subscribe_link = os.getenv("YOUTUBE_SUBSCRIBE_LINK", "").strip()
    subscribe_line = f"🔔 Subscribe for a new video every day: {subscribe_link}" if subscribe_link else ""
    # The subscribe link is the very first line of every description
    parts = [subscribe_line, spec.get("description", "")]
    if long_url:
        parts.append(f"▶ Watch the full documentary: {long_url}")
    if spec["source"]:
        parts.append("Sources:\n" + "\n".join(f"- {s}" for s in spec["source"]))
    parts.append(os.getenv("YOUTUBE_DESCRIPTION_FOOTER", ""))
    title = spec.get("yt_title") or spec["subject"]
    if spec.get("format") == "short" and "#shorts" not in title.lower():
        title = f"{title} #shorts"[:100]

    # PUBLISH_AT: 2026-11-06T01:00:00Z in the header = scheduled premiere (private until then)
    os.environ["YOUTUBE_PUBLISH_AT"] = spec.get("publish_at", "")
    response = upload_video(
        video_path=str(video), title=title[:100], description="\n\n".join(p for p in parts if p),
        category=os.getenv("YOUTUBE_CATEGORY_ID", "25"),
        keywords=spec.get("tags", ""), privacy_status=os.getenv("YOUTUBE_PRIVACY_STATUS", "public"),
    )
    video_id = (response or {}).get("id")
    if not video_id:
        raise RuntimeError(f"Upload of {spec['slug']} failed")
    url = f"https://youtu.be/{video_id}"
    print(f"[+] Posted {spec['slug']}: {url}", flush=True)
    # First comment: the full documentary link (if any) and an invitation to comment and subscribe
    try:
        from youtube import post_comment

        lines = [f"▶ Watch the full documentary: {long_url}"] if long_url else []
        lines.append("👇 Which story should we explain next? We post one big story every single day, so subscribe and don't miss tomorrow's.")
        if subscribe_link:
            lines.append(f"🔔 Subscribe: {subscribe_link}")
        post_comment(video_id, "\n\n".join(lines))
    except Exception as err:
        print(f"[-] First comment failed: {err}", flush=True)

    thumb = ROOT / "channels" / name / "videos" / f"{spec['slug']}_thumb.jpg"
    custom = None
    if spec.get("format") == "long":
        # The owner's own design (uploaded to thumbnails/) beats the automatic one
        from notify import custom_thumbnail

        custom = custom_thumbnail(os.getenv("THUMB_CHANNEL", "headline"))
        thumb = custom or thumb
    # Shorts and full documentaries each get their own playlist (series)
    playlist = (
        os.getenv("YOUTUBE_PLAYLIST_SHORT") if spec.get("format") == "short" else None
    ) or os.getenv("YOUTUBE_PLAYLIST")
    for step in (
        lambda: thumb.exists() and set_thumbnail(video_id, str(thumb)),
        lambda: playlist and add_to_playlist(video_id, playlist),
    ):
        try:  # extras must never fail an uploaded video
            step()
        except Exception as err:
            print(f"[-] {err}", flush=True)

    if spec.get("format") == "long":
        from notify import posted_alert

        posted_alert(os.getenv("THUMB_CHANNEL", "headline"), title, url, bool(custom))
    posted[spec["slug"]] = {"id": video_id, "url": url, "title": title, "format": spec.get("format", "short"),
                            "publish_at": spec.get("publish_at", "")}
    _posted_file(name).write_text(json.dumps(posted, indent=2), encoding="utf-8")
    return url


def publish(name: str, long_script: Path, short_script: Optional[Path] = None) -> None:
    # Long first, so the Short's description can link to it
    long_url = post(name, long_script, make_checked(name, long_script))
    if short_script:
        post(name, short_script, make_checked(name, short_script), long_url=long_url)


def auth(name: str) -> None:
    import os
    from youtube import get_authenticated_service, token_file

    token_file().unlink(missing_ok=True)
    print(f"[*] In the browser, choose the channel: {os.getenv('CHANNEL_NAME', name)}")
    youtube = get_authenticated_service()
    channel = youtube.channels().list(mine=True, part="snippet").execute()["items"][0]
    expected = os.getenv("CHANNEL_ID", "")
    if expected and channel["id"] != expected:
        token_file().unlink(missing_ok=True)
        raise SystemExit(f"[-] You picked '{channel['snippet']['title']}'. Run again and choose {os.getenv('CHANNEL_NAME')}.")
    print(f"[+] Logged in as {channel['snippet']['title']}; saved {token_file().name}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    profile, command = sys.argv[1], sys.argv[2]
    load_profile(profile)
    if command == "auth":
        auth(profile)
    elif command == "make":
        make(profile, Path(sys.argv[3]))
    elif command == "check":
        print(check_video(Path(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "short") or "OK")
    elif command == "plan":  # early morning: pick and write today's documentary, alert the owner for a thumbnail
        from datetime import date
        from news import SCRIPTS_DIR, prepare
        from notify import remind_thumbnail

        try:
            long_script, short_script = prepare()
            (SCRIPTS_DIR / f"{date.today().isoformat()}_planned.json").write_text(
                json.dumps({"long": long_script.name, "short": short_script.name}), encoding="utf-8")
            spec = parse_script(long_script)
            remind_thumbnail("headline", spec.get("yt_title") or spec["subject"], "Headline Tales, posts about 11 pm-midnight Nigeria time (5-6 pm US Eastern)")
        except RuntimeError as err:
            print(f"[-] No documentary planned ({err}); the midday run will try again", flush=True)
            from notify import notify

            notify("No documentary title yet today",
                   "This morning's story didn't pass the fact-check, so the midday run will pick another one "
                   "and use the automatic thumbnail. You'll get a 'Posted' alert with the link.")
        if date.today().weekday() == 4:  # Friday: Elite Mind's long video
            topics = [l.strip() for l in (ROOT / "topics_long.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
            if topics:
                topic, _, fixed = topics[0].partition("|| Title:")
                remind_thumbnail("elite", (fixed or topic).strip(), "Elite Mind, posts about 6:30-8 pm Nigeria time")
    elif command == "auto":  # daily: pick a news story, write + fact-check, then make, check and post
        from datetime import date
        from news import SCRIPTS_DIR, prepare

        planned = SCRIPTS_DIR / f"{date.today().isoformat()}_planned.json"
        try:
            if planned.exists():  # written by this morning's "plan" run (the owner got its title)
                names = json.loads(planned.read_text(encoding="utf-8"))
                long_script, short_script = SCRIPTS_DIR / names["long"], SCRIPTS_DIR / names["short"]
                print(f"[+] Using this morning's planned documentary {long_script.name}", flush=True)
            else:
                long_script, short_script = prepare()
        except RuntimeError as err:
            # The documentary fell through (e.g. too few facts): keep the slot alive with a news Short
            print(f"[-] Documentary skipped ({err}); posting a news Short instead", flush=True)
            from news import prepare_short

            script = prepare_short()
            if "--dry-run" not in sys.argv:
                post(profile, script, make_checked(profile, script, reuse=False))
            sys.exit(0)
        if "--dry-run" in sys.argv:  # make and check both videos, post nothing
            for script in (long_script, short_script):
                make_checked(profile, script, reuse=False)
        else:
            publish(profile, long_script, short_script)
    elif command == "auto-short":  # a standalone news Short on another big story of the day
        from news import prepare_short

        # A hand-written, fact-checked Short waiting in the queue goes first
        queue = sorted((ROOT / "channels" / profile / "scripts" / "queue").glob("*.txt"))
        if queue:
            auto_dir = ROOT / "channels" / profile / "scripts" / "auto"
            auto_dir.mkdir(parents=True, exist_ok=True)
            from datetime import date

            script = auto_dir / f"{date.today().isoformat()}_{queue[0].stem}_news.txt"
            if "--dry-run" in sys.argv:  # a test leaves the queued script for the real run
                script.write_bytes(queue[0].read_bytes())
            else:
                queue[0].replace(script)
            print(f"[+] Using queued script {queue[0].name}", flush=True)
            # End on a specific promise (the next queued story) instead of a generic "subscribe"
            if len(queue) > 1:
                upcoming = parse_script(queue[1])
                teaser = re.split(r"(?<=[.!?…])\s", (upcoming.get("yt_title") or upcoming["subject"]).replace("#shorts", "").strip())[0]
                teaser = teaser.rstrip(".…!? ")
                header, _, body = script.read_text(encoding="utf-8").partition("\n\n")
                paragraphs = body.strip().split("\n\n")
                if paragraphs and "subscribe" in paragraphs[-1].lower():
                    # Keep a story question such as "Did they make it?" (comments), drop the generic ask
                    first = re.split(r"(?<=[?])\s", paragraphs[-1].strip())[0]
                    keep = f"{first} " if first.endswith("?") and not first.lower().startswith("which") else ""
                    paragraphs[-1] = (f"{keep}Coming up next: {teaser}. Tap the subscribe button right below this video, "
                                      "so you don't miss it.")
                    script.write_text(header + "\n\n" + "\n\n".join(paragraphs) + "\n", encoding="utf-8")
                    print(f"[+] Ending teases the next story: {teaser}", flush=True)
        else:
            script = prepare_short()
        if "--dry-run" in sys.argv:
            make_checked(profile, script, reuse=False)
        else:
            post(profile, script, make_checked(profile, script, reuse=False))
    elif command == "publish":
        if "--dry-run" in sys.argv:  # make and check for review, post nothing
            for arg in sys.argv[3:5]:
                if arg and not arg.startswith("--"):
                    make_checked(profile, Path(arg), reuse=False)
        else:
            publish(profile, Path(sys.argv[3]), Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None)
    elif command == "post":  # post an already rendered/approved video, e.g. a Short linking to the long one
        script = Path(sys.argv[3])
        post(profile, script, make_checked(profile, script), long_url=sys.argv[4] if len(sys.argv) > 4 else "")
    else:
        print(__doc__)
