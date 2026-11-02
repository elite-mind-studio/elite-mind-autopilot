"""
Learning loop: read our own videos' YouTube Analytics (how long people watch,
where they drop off, which videos bring subscribers), compare winners with
losers, and write lessons that the script and topic generators follow.

Output:
    research/lessons.json   read by gpt.generate_script and research.py
    research/learning.md    human-readable report

Usage:
    python Backend/learning.py
"""

import json
import re
import statistics
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ENV_FILE, PROJECT_ROOT  # noqa: E402

load_dotenv(ENV_FILE)

OUT_DIR = PROJECT_ROOT / "research"
LESSONS_FILE = OUT_DIR / "lessons.json"
HISTORY_FILE = PROJECT_ROOT / "history.jsonl"
MIN_VIEWS = 20  # fewer views than this is noise, not a signal
START_DATE = "2026-01-01"


def _seconds(duration: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    hours, minutes, secs = (int(x or 0) for x in match.groups()) if match else (0, 0, 0)
    return hours * 3600 + minutes * 60 + secs


def _history() -> dict:
    if not HISTORY_FILE.exists():
        return {}
    records = {}
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["id"]] = record
    return records


def _kind(title: str, seconds: int) -> str:
    if seconds > 180:
        return "long"
    if re.search(r"\bpart \d", title, re.I):
        return "series short"
    if title.lower().startswith("pov"):
        return "pov short"
    return "explainer short"


def collect(youtube, analytics) -> list:
    channel = youtube.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        response = youtube.playlistItems().list(
            part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page
        ).execute()
        ids += [item["contentDetails"]["videoId"] for item in response["items"]]
        page = response.get("nextPageToken")
        if not page or len(ids) >= 200:
            break

    videos = {}
    for start in range(0, len(ids), 50):
        response = youtube.videos().list(
            part="snippet,contentDetails,status", id=",".join(ids[start:start + 50])
        ).execute()
        for video in response["items"]:
            if video["status"]["privacyStatus"] == "public":
                videos[video["id"]] = video

    today = date.today().isoformat()
    report = analytics.reports().query(
        ids="channel==MINE", startDate=START_DATE, endDate=today,
        metrics="views,averageViewDuration,averageViewPercentage,likes,shares,subscribersGained",
        dimensions="video", sort="-views", maxResults=200,
    ).execute()
    headers = [h["name"] for h in report.get("columnHeaders", [])]

    history = _history()
    rows = []
    for values in report.get("rows", []):
        stats = dict(zip(headers, values))
        video = videos.get(stats["video"])
        if not video or stats["views"] < MIN_VIEWS:
            continue
        seconds = _seconds(video["contentDetails"]["duration"])
        title = video["snippet"]["title"]
        record = history.get(video["id"], {})
        rows.append({
            "id": video["id"],
            "title": title,
            "kind": _kind(title, seconds),
            "seconds": seconds,
            "views": stats["views"],
            "avg_watch_s": stats["averageViewDuration"],
            "avg_watch_pct": round(stats["averageViewPercentage"], 1),
            "likes_per_100": round(100 * stats["likes"] / stats["views"], 1),
            "shares": stats["shares"],
            "subs_gained": stats["subscribersGained"],
            "hook": record.get("hook", ""),
            "topic": record.get("topic", ""),
            **_retention(analytics, video["id"], today),
        })
    return rows


def _retention(analytics, video_id: str, today: str) -> dict:
    """Share of viewers still watching at the start, middle and end of the video."""
    try:
        report = analytics.reports().query(
            ids="channel==MINE", startDate=START_DATE, endDate=today,
            metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video_id}",
        ).execute()
    except Exception:
        return {}
    curve = {round(row[0], 2): row[1] for row in report.get("rows", [])}
    if not curve:
        return {}

    def at(point: float) -> float:
        nearest = min(curve, key=lambda x: abs(x - point))
        return round(100 * curve[nearest])

    # Above 100% at the end means people rewatch (the loop ending works)
    return {"kept_at_10pct": at(0.1), "kept_at_50pct": at(0.5), "kept_at_end": at(0.97)}


def ai_lessons(rows: list) -> dict:
    from gpt import hosted_chat

    ranked = sorted(rows, key=lambda r: r["avg_watch_pct"], reverse=True)
    listing = "\n".join(
        f'- [{r["kind"]}, {r["seconds"]}s] "{r["title"]}" | {r["views"]} views | '
        f'watched {r["avg_watch_pct"]}% ({r["avg_watch_s"]}s) | still watching: '
        f'{r.get("kept_at_10pct", "?")}% at 10%, {r.get("kept_at_50pct", "?")}% at half, '
        f'{r.get("kept_at_end", "?")}% at end | {r["likes_per_100"]} likes/100 views | '
        f'+{r["subs_gained"]} subs'
        + (f' | hook: "{r["hook"]}"' if r["hook"] else "")
        for r in ranked
    )
    prompt = f"""
You are the growth analyst for "Elite Mind Decoder", a faceless YouTube channel: a cartoon
character called Black explains money psychology and mind tricks. Formats: numbered explainer
Shorts (45-60 s), 5-part Shorts series, and long "POV: You're..." stories.

Here are OUR OWN videos, best average watch percentage first:
{listing}

Compare the best with the worst. Look at hooks, topics, title style, length, and where viewers
drop off (a big drop before 10% means the hook failed; a drop in the middle means the body is
slow; above 100% at the end means people rewatch). Only claim what the numbers support.

Return ONLY JSON with these keys:
"summary": 2 sentences on what is working and what is not,
"script_tips": 4 concrete instructions for writing the next scripts (hook, pacing, length, ending),
"topic_tips": 3 concrete instructions for choosing the next topics and titles,
"more_like": up to 3 of our titles that we should make more videos like,
"less_like": up to 3 of our titles whose style we should avoid.
"""
    for attempt in range(4):
        reply = hosted_chat(prompt if attempt == 0 else prompt + "\nReturn strictly valid JSON.")
        match = re.search(r"\{.*\}", reply or "", re.DOTALL)
        if not match:
            continue
        text = re.sub(r",\s*([\]}])", r"\1", match.group(0)).replace("“", '"').replace("”", '"')
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return {}


def load_lessons() -> dict:
    """Lessons for the generators; empty when there is no report yet."""
    try:
        return json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    from youtube import get_analytics_service, get_authenticated_service

    rows = collect(get_authenticated_service(), get_analytics_service())
    OUT_DIR.mkdir(exist_ok=True)
    enough = len(rows) >= 4
    # Too few videos to draw lessons from: still report the numbers, keep old lessons
    lessons = ai_lessons(rows) if enough else {
        "summary": f"Only {len(rows)} videos with {MIN_VIEWS}+ views so far; lessons start at 4."
    }
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row["avg_watch_pct"])
    lessons["generated"] = date.today().isoformat()
    lessons["avg_watch_pct_by_kind"] = {k: round(statistics.mean(v), 1) for k, v in by_kind.items()}
    lessons["videos"] = sorted(rows, key=lambda r: r["avg_watch_pct"], reverse=True)
    if enough:
        LESSONS_FILE.write_text(json.dumps(lessons, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# What our own videos teach us - {lessons['generated']}", "", lessons.get("summary", ""), ""]
    lines += ["## Average % watched by format"]
    lines += [f"- {k}: {v}%" for k, v in lessons["avg_watch_pct_by_kind"].items()]
    for key in ("script_tips", "topic_tips", "more_like", "less_like"):
        lines += ["", f"## {key.replace('_', ' ').capitalize()}"] + [f"- {x}" for x in lessons.get(key, [])]
    lines += ["", "## Videos (best watch % first)", "",
              "| Video | Format | Views | Watched | Kept at 10% / half / end | Subs |", "|---|---|---|---|---|---|"]
    lines += [
        f"| [{r['title']}](https://youtu.be/{r['id']}) | {r['kind']} | {r['views']} | "
        f"{r['avg_watch_pct']}% ({r['avg_watch_s']}s) | {r.get('kept_at_10pct', '-')}% / "
        f"{r.get('kept_at_50pct', '-')}% / {r.get('kept_at_end', '-')}% | +{r['subs_gained']} |"
        for r in lessons["videos"]
    ]
    (OUT_DIR / "learning.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Learned from {len(rows)} videos; report in research/learning.md")


if __name__ == "__main__":
    main()
