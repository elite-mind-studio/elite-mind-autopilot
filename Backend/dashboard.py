"""
Builds the data for the public dashboard (site/data.json + site/history.json):
channel stats, every video's views/likes/comments, recent automation runs,
upcoming topics and a few community-post ideas.

Run by .github/workflows/dashboard.yml every few hours and after each video.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ENV_FILE, PROJECT_ROOT  # noqa: E402

load_dotenv(ENV_FILE)

SITE = PROJECT_ROOT / "site"
DATA_FILE = SITE / "data.json"
HISTORY_FILE = SITE / "history.json"


def _seconds(duration: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not match:
        return 0
    hours, minutes, secs = (int(x or 0) for x in match.groups())
    return hours * 3600 + minutes * 60 + secs


def youtube_stats() -> dict:
    from youtube import get_authenticated_service

    youtube = get_authenticated_service()
    channel = youtube.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()["items"][0]
    stats = channel["statistics"]
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    ids, page = [], None
    while True:
        response = youtube.playlistItems().list(
            part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page
        ).execute()
        ids += [item["contentDetails"]["videoId"] for item in response.get("items", [])]
        page = response.get("nextPageToken")
        if not page or len(ids) >= 200:
            break

    videos = []
    for start in range(0, len(ids), 50):
        response = youtube.videos().list(
            part="snippet,statistics,contentDetails,status", id=",".join(ids[start:start + 50])
        ).execute()
        for video in response.get("items", []):
            seconds = _seconds(video["contentDetails"]["duration"])
            video_stats = video.get("statistics", {})
            videos.append({
                "id": video["id"],
                "title": video["snippet"]["title"],
                "published": video["snippet"]["publishedAt"],
                "thumbnail": video["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
                "seconds": seconds,
                "type": "short" if seconds <= 180 else "long",
                "privacy": video["status"]["privacyStatus"],
                "views": int(video_stats.get("viewCount", 0)),
                "likes": int(video_stats.get("likeCount", 0)),
                "comments": int(video_stats.get("commentCount", 0)),
            })

    return {
        "title": channel["snippet"]["title"],
        "subscribers": int(stats.get("subscriberCount", 0)),
        "views": int(stats.get("viewCount", 0)),
        "videos": int(stats.get("videoCount", 0)),
        "list": sorted(videos, key=lambda v: v["published"], reverse=True),
    }


def recent_runs(limit: int = 15) -> list:
    """Recent autopilot runs from the GitHub API (needs GITHUB_TOKEN + GITHUB_REPOSITORY)."""
    import requests

    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        return []
    response = requests.get(
        f"https://api.github.com/repos/{repo}/actions/workflows/autopilot.yml/runs",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        params={"per_page": limit},
        timeout=30,
    )
    if not response.ok:
        return []
    return [
        {
            "started": run["run_started_at"],
            "event": run["event"],
            "status": run["status"],
            "conclusion": run["conclusion"],
            "url": run["html_url"],
        }
        for run in response.json().get("workflow_runs", [])
    ]


def upcoming(path: str, count: int = 5) -> list:
    file = PROJECT_ROOT / path
    if not file.exists():
        return []
    lines = [line.split("|")[0].strip() for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[:count]


def community_ideas(previous: dict) -> list:
    """Three community-post ideas, refreshed at most once a week."""
    old = previous.get("community", {})
    if old.get("ideas") and old.get("week") == datetime.now(timezone.utc).strftime("%G-W%V"):
        return old["ideas"]
    try:
        from gpt import hosted_chat

        reply = hosted_chat(
            "Write 3 short YouTube community posts for 'Elite Mind Decoder', a channel about "
            "money psychology and mind tricks. Mix: one poll (question + 3-4 short options), one "
            "question that invites comments, one teaser for the next video. Casual, punchy, max "
            "40 words each. Return ONLY a JSON array of 3 strings."
        )
        match = re.search(r"\[.*\]", reply, re.DOTALL)
        ideas = json.loads(match.group(0))[:3] if match else []
    except Exception:
        ideas = []
    return ideas or old.get("ideas", [])


def main() -> None:
    SITE.mkdir(exist_ok=True)
    previous = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {}
    now = datetime.now(timezone.utc)

    channel = youtube_stats()
    ideas = community_ideas(previous)
    data = {
        "updated": now.isoformat(timespec="minutes"),
        "channel": {k: channel[k] for k in ("title", "subscribers", "views", "videos")},
        "videos": channel["list"],
        "runs": recent_runs(),
        "next": {
            "series": upcoming("topics_series.txt"),
            "explainers": upcoming("topics.txt"),
            "long": upcoming("topics_long.txt", 3),
        },
        "community": {"week": now.strftime("%G-W%V"), "ideas": ideas},
    }
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # One growth point per hour
    history = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
    stamp = now.strftime("%Y-%m-%dT%H:00Z")
    history = [h for h in history if h["time"] != stamp]
    history.append({"time": stamp, "subscribers": channel["subscribers"], "views": channel["views"]})
    HISTORY_FILE.write_text(json.dumps(history[-2000:], indent=1), encoding="utf-8")
    print(f"Dashboard updated: {channel['subscribers']} subs, {channel['views']} views, {len(channel['list'])} videos")


if __name__ == "__main__":
    main()
