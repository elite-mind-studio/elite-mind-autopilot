"""
Autopilot: queue videos on a schedule without using the frontend.

Usage:
    python Backend/autopilot.py queue   # queue one video from the next unused topic
    python Backend/autopilot.py queue --no-upload   # same, but never upload (for testing)
    python Backend/autopilot.py run [--no-upload]   # generate + upload now, no worker needed
    (add --long to queue/run for an 8-12 minute horizontal video,
     --series to take the next "Part N/M" topic from topics_series.txt)
    python Backend/autopilot.py tick    # queue a post if one is due today (run every ~30 min)
    python Backend/autopilot.py auth    # one-time YouTube login (opens a browser)

Topics are read from topics.txt in the project root (one per line). Used topics
are moved to topics_used.txt. When topics.txt runs out, new topics are
generated with Ollama based on AUTOPILOT_NICHE.

Run `queue` from Windows Task Scheduler (or cron) as often as you want to post.
The worker must be running to actually generate the queued videos.
"""

import sys
from pathlib import Path

# pythonw.exe (used by the Windows scheduled tasks) has no console, so
# sys.stdout/sys.stderr are None and any print crashes. Log to a file instead.
if sys.stdout is None or sys.stderr is None:
    _log_dir = Path(__file__).resolve().parent.parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _log_file = open(_log_dir / "autopilot.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log_file
    sys.stderr = sys.stderr or _log_file

# Child processes (ffmpeg) started from pythonw.exe would each open a console
# window; closing one kills the render (exit 0xC000013A). Start them hidden.
import os as _os
if _os.name == "nt":
    import subprocess as _subprocess

    _original_popen_init = _subprocess.Popen.__init__

    def _popen_no_window(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        _original_popen_init(self, *args, **kwargs)

    _subprocess.Popen.__init__ = _popen_no_window


import json
import os
import random
import re
from datetime import datetime, timedelta
from typing import List, Optional

from dotenv import load_dotenv

from utils import BASE_DIR, ENV_FILE, PROJECT_ROOT

load_dotenv(ENV_FILE)

from db import SessionLocal, init_db  # noqa: E402
from gpt import generate_response  # noqa: E402
from repository import create_job, get_job  # noqa: E402
from models import GenerationJob  # noqa: E402
from sqlalchemy import select  # noqa: E402

TOPICS_FILE = PROJECT_ROOT / "topics.txt"
USED_TOPICS_FILE = PROJECT_ROOT / "topics_used.txt"
LONG_TOPICS_FILE = PROJECT_ROOT / "topics_long.txt"
LONG_USED_TOPICS_FILE = PROJECT_ROOT / "topics_long_used.txt"
SERIES_TOPICS_FILE = PROJECT_ROOT / "topics_series.txt"
SERIES_USED_TOPICS_FILE = PROJECT_ROOT / "topics_series_used.txt"
STATE_FILE = PROJECT_ROOT / "autopilot_state.json"

# Posting windows for `tick`: each day one random time is picked inside each
# window (start .. start + AUTOPILOT_WINDOW_MINUTES).
WINDOWS = [w.strip() for w in os.getenv("AUTOPILOT_WINDOWS", "07:00,12:00,17:00").split(",") if w.strip()]
WINDOW_MINUTES = int(os.getenv("AUTOPILOT_WINDOW_MINUTES", "180"))
# Long-form (8-12 min, horizontal) videos on these weekdays, inside their own window
LONG_DAYS = [d.strip().lower()[:3] for d in os.getenv("AUTOPILOT_LONG_DAYS", "").split(",") if d.strip()]
LONG_WINDOW = os.getenv("AUTOPILOT_LONG_WINDOW", "14:00")
LONG_WINDOW_MINUTES = int(os.getenv("AUTOPILOT_LONG_WINDOW_MINUTES", "240"))
MAX_ATTEMPTS_PER_SLOT = 2

NICHE = os.getenv(
    "AUTOPILOT_NICHE",
    "mindset, discipline, self-improvement, stoicism and success habits",
)
NEW_TOPICS_BATCH = 10


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _topic_files(video_format: str = "short"):
    """Explainer Shorts, series Shorts and long videos keep separate topic queues."""
    if video_format == "long":
        return LONG_TOPICS_FILE, LONG_USED_TOPICS_FILE
    if video_format == "series":
        return SERIES_TOPICS_FILE, SERIES_USED_TOPICS_FILE
    return TOPICS_FILE, USED_TOPICS_FILE


def _generate_topics(used: List[str], video_format: str = "short") -> List[str]:
    recent = "\n".join(f"- {t}" for t in used[-50:])
    if video_format == "long":
        kind = (
            "long-form YouTube video topics in the viral 'POV' format, where the viewer "
            "lives an escalating money scenario, for example: 'POV: You're a billionaire "
            "for 24 hours' or 'POV: You won 10 million dollars in the lottery'"
        )
    else:
        kind = (
            "YouTube Shorts topics as short numbered-list titles, for example: "
            "'7 pricing tricks stores use on you' or '6 money habits that keep you broke'"
        )
    prompt = (
        f"Generate {NEW_TOPICS_BATCH} new, specific, catchy {kind}, "
        f"for a channel about {NICHE}.\n"
        f"Do not repeat any of these already-used topics:\n{recent}\n\n"
        "Return ONLY a JSON array of strings, nothing else."
    )
    response = generate_response(prompt, os.getenv("OLLAMA_MODEL", ""))

    match = re.search(r"\[.*\]", response, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse topics from model response: {response}")

    used_lower = {t.lower() for t in used}
    topics = [str(t).strip() for t in json.loads(match.group(0))]
    return [t for t in topics if t and t.lower() not in used_lower]


def peek_topic(video_format: str = "short") -> str:
    """Return the next topic without consuming it, generating more if needed."""
    topics_file, used_file = _topic_files(video_format)
    topics = _read_lines(topics_file)

    if not topics:
        print(f"[*] {topics_file.name} is empty, generating new topics...")
        topics = _generate_topics(_read_lines(used_file), video_format)
        if not topics:
            raise RuntimeError("The model did not return any new topics.")
        topics_file.write_text("\n".join(topics) + "\n", encoding="utf-8")

    return topics[0]


def mark_topic_used(topic: str, video_format: str = "short") -> None:
    """Move a topic from the topics file to the used-topics file."""
    topics_file, used_file = _topic_files(video_format)
    remaining = [t for t in _read_lines(topics_file) if t != topic]
    topics_file.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    with used_file.open("a", encoding="utf-8") as f:
        f.write(topic + "\n")


def next_topic(video_format: str = "short") -> str:
    topic = peek_topic(video_format)
    mark_topic_used(topic, video_format)
    return topic


SERIES_PATTERN = re.compile(
    r"^(?P<series>.+?)\s*\(Part\s*(?P<part>\d+)\s*/\s*(?P<parts>\d+)\)\s*:?\s*(?P<episode>[^|]*?)\s*(?:\|\s*Next:\s*(?P<next>.+))?$",
    re.IGNORECASE,
)


def parse_series(topic: str) -> Optional[dict]:
    """
    Parse a series topic line such as
    "Psychology tricks to control any room (Part 1/5): First impressions | Next: Body language".
    """
    match = SERIES_PATTERN.match(topic.strip())
    if not match:
        return None
    return {
        "title": match.group("series").strip(),
        "part": int(match.group("part")),
        "parts": int(match.group("parts")),
        "episode": (match.group("episode") or "").strip(),
        "next": (match.group("next") or "").strip(),
    }


def build_payload(topic: str, upload: bool, video_format: str = "short") -> dict:
    if video_format == "long":
        return {
            **build_payload(topic, upload),
            "format": "long",
            "paragraphNumber": int(os.getenv("AUTOPILOT_LONG_PARAGRAPHS", "14")),
            "maxWords": int(os.getenv("AUTOPILOT_LONG_MAX_WORDS", "1600")),
            "sceneSeconds": float(os.getenv("AUTOPILOT_LONG_SCENE_SECONDS", "6")),
            "stockVideos": int(os.getenv("AUTOPILOT_LONG_STOCK_VIDEOS", "40")),
            "subtitlesPosition": "center,bottom",
        }
    # "Topic details || Title: Exact YouTube title || Playlist: Name || Ask: Comment question"
    # fixes those instead of letting the AI or the defaults pick them
    options = {}
    topic, *extras = topic.split("||")
    topic = topic.strip()
    for extra in extras:
        key, _, value = extra.partition(":")
        options[key.strip().lower()] = value.strip()
    yt_title = options.get("title", "")
    series = parse_series(topic)
    if series:
        # The "| Next: ..." hint is for the script, not the title
        topic = topic.split("|")[0].strip()
    return {
        "ytTitle": yt_title,
        "playlist": options.get("playlist", ""),
        "commentAsk": options.get("ask", ""),
        "layout": options.get("layout", "").lower(),  # "versus" = do-this-not-that split screen
        "series": series,
        "format": "short",
        "videoSubject": topic,
        "aiModel": os.getenv("OLLAMA_MODEL", ""),
        # Comma-separated list: one voice is picked at random per video
        "voice": random.choice(
            [v.strip() for v in os.getenv("AUTOPILOT_VOICE", "en_us_006").split(",") if v.strip()]
        ),
        "paragraphNumber": int(os.getenv("AUTOPILOT_PARAGRAPHS", "3")),
        "stockVideos": int(os.getenv("AUTOPILOT_STOCK_VIDEOS", "5")),
        "subtitlesPosition": "center,center",
        # Comma-separated list: the caption accent colour changes from video to video
        "color": random.choice([c.strip() for c in os.getenv("AUTOPILOT_CAPTION_COLOR", "#FFFFFF").split(",") if c.strip()]),
        "customPrompt": "",
        "useMusic": True,
        "automateYoutubeUpload": upload,
    }


def queue_video(
    allow_upload: bool = True, topic: Optional[str] = None, video_format: str = "short"
) -> str:
    topic = topic or next_topic(video_format)
    upload = allow_upload and (BASE_DIR / "client_secret.json").exists()

    init_db()
    with SessionLocal() as session:
        job = create_job(session, payload=build_payload(topic, upload, video_format))

    print(f"[+] Queued {video_format} job {job.id}: {topic}")
    if not allow_upload:
        print("[!] --no-upload set, video will NOT be uploaded.")
    elif not upload:
        print("[!] Backend/client_secret.json not found, video will NOT be uploaded.")
    return job.id


def _random_time(window: str, minutes: int) -> str:
    start = datetime.strptime(window, "%H:%M")
    return (start + timedelta(minutes=random.randint(0, minutes))).strftime("%H:%M")


def _new_day_state(now: datetime) -> dict:
    slots = [_random_time(w, WINDOW_MINUTES) for w in WINDOWS]
    formats = ["short"] * len(slots)
    if now.strftime("%a").lower() in LONG_DAYS:
        slots.append(_random_time(LONG_WINDOW, LONG_WINDOW_MINUTES))
        formats.append("long")
    return {
        "date": now.strftime("%Y-%m-%d"),
        "slots": slots,
        "formats": formats,
        "jobs": [None] * len(slots),
        "attempts": [0] * len(slots),
    }


def tick() -> None:
    """
    Called every ~30 minutes while the PC is on. Picks random posting times
    for each day and queues any post whose time has passed but hasn't been
    made yet, one at a time, so missed posts are caught up after the PC was off.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    state = None
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not state or state.get("date") != today:
        state = _new_day_state(now)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"[*] Posting times for {today}: {', '.join(state['slots'])}")

    init_db()
    with SessionLocal() as session:
        busy = session.execute(
            select(GenerationJob.id).where(GenerationJob.status.in_(["queued", "running"]))
        ).first()
    if busy and os.getenv("AUTOPILOT_TICK_MODE", "queue").strip().lower() != "run":
        print("[*] A video is already being made, checking again later.")
        return

    formats = state.get("formats") or ["short"] * len(state["slots"])
    for i, slot in enumerate(state["slots"]):
        if now.strftime("%H:%M") < slot:
            continue
        video_format = formats[i]

        job_id = state["jobs"][i]

        # "run" mode: make the video in this process (always the newest code,
        # no long-running worker needed). A failed run is retried on a later tick.
        if os.getenv("AUTOPILOT_TICK_MODE", "queue").strip().lower() == "run":
            if job_id == "done" or state["attempts"][i] >= MAX_ATTEMPTS_PER_SLOT:
                continue
            # Another tick is already making this post; only take over if it
            # looks abandoned (e.g. the PC was shut down mid-render)
            started = (state.get("started") or {}).get(str(i))
            if job_id == "running" and started and (
                datetime.now() - datetime.fromisoformat(started)
            ) < timedelta(hours=4):
                print(f"[*] Post {i + 1} is already being made, checking again later.")
                return
            print(f"[+] Post {i + 1} ({video_format}) was due at {slot}, making it now.")
            state.setdefault("started", {})[str(i)] = datetime.now().isoformat(timespec="seconds")
            state["jobs"][i] = "running"
            state["attempts"][i] += 1
            STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
            try:
                run_video(video_format=video_format)
                state["jobs"][i] = "done"
            except Exception as err:
                print(f"[-] Post {i + 1} failed: {err}")
                state["jobs"][i] = "failed"
            STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
            return

        if job_id:
            with SessionLocal() as session:
                job = get_job(session, job_id)
                status = job.status if job else "completed"
                topic = job.payload.get("videoSubject") if job else None
            if status != "failed" or state["attempts"][i] >= MAX_ATTEMPTS_PER_SLOT:
                continue
            print(f"[!] Post {i + 1} failed earlier, retrying: {topic}")
            state["jobs"][i] = queue_video(topic=topic, video_format=video_format)
        else:
            print(f"[+] Post {i + 1} ({video_format}) was due at {slot}, queueing now.")
            state["jobs"][i] = queue_video(video_format=video_format)

        state["attempts"][i] += 1
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return

    print("[*] Nothing due right now.")


def run_video(
    allow_upload: bool = True, video_format: str = "short", queue: Optional[str] = None
) -> None:
    """
    Generate (and upload) one video in this process, without the queue/worker.
    The topic is only marked as used when the video succeeds, so a failed run
    retries the same topic next time. Used by the GitHub Actions workflow.
    """
    from pipeline import run_generation_pipeline
    from utils import SUBTITLES_DIR, TEMP_DIR, check_env_vars, clean_dir

    check_env_vars()
    if os.name == "nt":
        # Keep Windows awake until this process exits (sleep paused a long render once)
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    clean_dir(str(TEMP_DIR))
    clean_dir(str(SUBTITLES_DIR))

    # The "series" queue holds "Part N/M" Shorts; when it runs dry, fall back
    # to the regular explainer queue
    queue = queue or video_format
    if queue == "series" and not _read_lines(SERIES_TOPICS_FILE):
        queue = "short"
    topic = peek_topic(queue)
    upload = allow_upload and (BASE_DIR / "client_secret.json").exists()
    print(f"[+] Generating: {topic} (upload: {upload})")

    uploaded = []

    def on_log(message: str, level: str) -> None:
        print(message, flush=True)
        if message.startswith("Uploaded video ID:"):
            uploaded.append(message)

    payload = build_payload(topic, upload, video_format)
    # End on a specific promise: the next topic in the same queue ("Coming up next: ...")
    if video_format == "short" and queue != "series":
        upcoming = _read_lines(_topic_files(queue)[0])[1:2]
        if upcoming:
            line, _, fixed = upcoming[0].partition("|| Title:")
            fixed = fixed.split("||")[0]
            payload["nextTitle"] = (fixed or re.split(r"\s[(:]|[.;]\s|\|\|", line, maxsplit=1)[0]).strip().rstrip(".")
    run_generation_pipeline(data=payload, is_cancelled=None, on_log=on_log)

    if upload and not uploaded:
        raise RuntimeError("Video was generated but the YouTube upload failed.")

    record = None
    if uploaded:
        script_file = TEMP_DIR / "script.txt"
        script = script_file.read_text(encoding="utf-8") if script_file.exists() else ""
        record = {
            "id": uploaded[0].split(":", 1)[1].strip(),
            "posted": datetime.now().isoformat(timespec="minutes"),
            "topic": topic,
            "format": video_format,
            "queue": queue or video_format,
            "hook": re.split(r"(?<=[.!?][\"”])\s+|(?<=[.!?])\s+", script.strip(), maxsplit=1)[0] if script else "",
            "words": len(script.split()),
        }
        _record_history(record)

    # Test runs don't consume the topic, so it still gets posted for real later
    if allow_upload:
        mark_topic_used(topic, queue)
        # Lets CI re-apply this after pulling a newer topic list (see mark_posted)
        (PROJECT_ROOT / ".last_posted.json").write_text(
            json.dumps({"topic": topic, "queue": queue, "record": record}), encoding="utf-8"
        )
    print(f"[+] Done: {topic}")


def _record_history(record: dict) -> None:
    """Append a posted video to history.jsonl (read by learning.py); once per video."""
    history = PROJECT_ROOT / "history.jsonl"
    lines = _read_lines(history) if history.exists() else []
    if any(f'"id": "{record["id"]}"' in line for line in lines):
        return
    with history.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def mark_posted() -> None:
    """Re-apply the last posted topic to a freshly pulled topic list."""
    marker = PROJECT_ROOT / ".last_posted.json"
    if not marker.exists():
        return
    posted = json.loads(marker.read_text(encoding="utf-8"))
    topics_file, _ = _topic_files(posted["queue"])
    if posted["topic"] in _read_lines(topics_file):
        mark_topic_used(posted["topic"], posted["queue"])
        print(f"[+] Marked as posted: {posted['topic']}")
    if posted.get("record"):
        _record_history(posted["record"])


def youtube_auth() -> None:
    from youtube import get_authenticated_service

    # Start from a clean login so newly added permissions are requested
    (BASE_DIR / "youtube-oauth2.json").unlink(missing_ok=True)
    get_authenticated_service()
    print("[+] YouTube login saved. Scheduled uploads can now run unattended.")


def premiere_nearby(hours: float = 4) -> bool:
    """True when an Elite Mind story Short (elite_suspense) premieres within `hours` of now."""
    posted = PROJECT_ROOT / "channels" / "elite_suspense" / "posted.json"
    if not posted.exists():
        return False
    from datetime import timezone

    now = datetime.now(timezone.utc)
    for entry in json.loads(posted.read_text(encoding="utf-8")).values():
        when = (entry.get("publish_at") or "").strip()
        if when:
            at = datetime.fromisoformat(when.replace("Z", "+00:00"))
            if abs((at - now).total_seconds()) <= hours * 3600:
                return True
    return False


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    video_format = "long" if "--long" in sys.argv[2:] else "short"
    if command == "queue":
        queue_video(allow_upload="--no-upload" not in sys.argv[2:], video_format=video_format)
    elif command == "tick":
        tick()
    elif command == "run":
        if premiere_nearby(4 if "--series" in sys.argv[2:] else 2):
            # The evening slot belongs to tonight's story premiere: max 3 Elite posts a day
            print("[*] A scheduled Elite premiere takes this slot; this Short moves to the next run.")
            sys.exit(0)
        run_video(
            allow_upload="--no-upload" not in sys.argv[2:],
            video_format=video_format,
            queue="series" if "--series" in sys.argv[2:] else None,
        )
    elif command == "mark-posted":
        mark_posted()
    elif command == "auth":
        youtube_auth()
    else:
        print(__doc__)
        sys.exit(1)
