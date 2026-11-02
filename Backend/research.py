"""
Viral research: find recent videos in our niches that massively outperformed
their channel size ("outliers"), measure what they have in common, and turn
that into topic ideas in our formats.

Uses only the official YouTube Data API (search.list costs 100 quota units per
query, so a default run uses about 2,000 of the daily 10,000).

Usage:
    python Backend/research.py            # write research/report.json + report.md
    python Backend/research.py --apply    # also put the best ideas at the front of the topic queues
"""

import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ENV_FILE, PROJECT_ROOT  # noqa: E402

load_dotenv(ENV_FILE)

OUT_DIR = PROJECT_ROOT / "research"
DEFAULT_QUERIES = [
    "money psychology", "psychology tricks", "dark psychology", "body language tricks",
    "rich vs poor mindset", "money habits keeping you broke", "pricing tricks stores use",
    "how the rich think", "POV you are a billionaire", "discipline animated short film",
]


def _seconds(duration: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not match:
        return 0
    hours, minutes, secs = (int(x or 0) for x in match.groups())
    return hours * 3600 + minutes * 60 + secs


def collect(youtube, queries, days=90, per_query=25):
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = {}
    for query in queries:
        for duration in ("short", "medium"):  # under 4 min, and 4-20 min
            response = youtube.search().list(
                part="id", q=query, type="video", order="viewCount", maxResults=per_query,
                publishedAfter=published_after, videoDuration=duration, relevanceLanguage="en",
            ).execute()
            for item in response.get("items", []):
                ids.setdefault(item["id"]["videoId"], query)

    videos = []
    id_list = list(ids)
    for start in range(0, len(id_list), 50):
        response = youtube.videos().list(part="snippet,statistics,contentDetails", id=",".join(id_list[start:start + 50])).execute()
        videos += response.get("items", [])

    channel_ids = list({v["snippet"]["channelId"] for v in videos})
    subs = {}
    for start in range(0, len(channel_ids), 50):
        response = youtube.channels().list(part="statistics", id=",".join(channel_ids[start:start + 50])).execute()
        for channel in response.get("items", []):
            subs[channel["id"]] = int(channel["statistics"].get("subscriberCount", 0) or 0)

    now = datetime.now(timezone.utc)
    rows = []
    for video in videos:
        stats = video.get("statistics", {})
        views = int(stats.get("viewCount", 0))
        if views < 20000:
            continue
        published = datetime.fromisoformat(video["snippet"]["publishedAt"].replace("Z", "+00:00"))
        age_days = max(1.0, (now - published).total_seconds() / 86400)
        channel_subs = subs.get(video["snippet"]["channelId"], 0)
        likes = int(stats.get("likeCount", 0) or 0)
        comments = int(stats.get("commentCount", 0) or 0)
        seconds = _seconds(video["contentDetails"]["duration"])
        rows.append({
            "id": video["id"],
            "title": video["snippet"]["title"],
            "channel": video["snippet"]["channelTitle"],
            "query": ids[video["id"]],
            "views": views,
            "views_per_day": round(views / age_days),
            "subs": channel_subs,
            # Views relative to channel size: high = the video itself went viral
            "outlier": round(views / max(channel_subs, 1000), 1),
            "like_rate": round(100 * likes / views, 2),
            "comment_rate": round(100 * comments / views, 3),
            "seconds": seconds,
            "type": "short" if seconds <= 180 else "long",
            "thumbnail": video["snippet"]["thumbnails"].get("high", {}).get("url", ""),
        })
    return sorted(rows, key=lambda r: r["outlier"], reverse=True)


def title_patterns(rows):
    def share(predicate):
        return round(100 * sum(1 for r in rows if predicate(r["title"])) / max(1, len(rows)))

    return {
        "videos_analysed": len(rows),
        "starts_with_number_%": share(lambda t: bool(re.match(r"^\d", t))),
        "has_number_%": share(lambda t: bool(re.search(r"\d", t))),
        "says_you_%": share(lambda t: bool(re.search(r"\byou(r)?\b", t, re.I))),
        "pov_%": share(lambda t: "pov" in t.lower()),
        "why_how_%": share(lambda t: bool(re.match(r"^(why|how)\b", t, re.I))),
        "question_%": share(lambda t: "?" in t),
        "all_caps_word_%": share(lambda t: bool(re.search(r"\b[A-Z]{3,}\b", t))),
        "median_title_chars": statistics.median([len(r["title"]) for r in rows]) if rows else 0,
        "median_short_seconds": statistics.median([r["seconds"] for r in rows if r["type"] == "short"] or [0]),
        "median_long_seconds": statistics.median([r["seconds"] for r in rows if r["type"] == "long"] or [0]),
    }


def ai_insights(top, patterns):
    from gpt import hosted_chat

    listing = "\n".join(
        f'- "{r["title"]}" | {r["views"]:,} views | {r["outlier"]}x channel size | {r["seconds"]}s | like rate {r["like_rate"]}%'
        for r in top[:40]
    )
    from learning import load_lessons

    lessons = load_lessons()
    own = ""
    if lessons.get("summary"):
        # Our own analytics outrank other channels' patterns
        own = (
            "What OUR OWN viewers respond to (weigh this above the list above): "
            + lessons["summary"]
            + " Topic tips: " + " ".join(lessons.get("topic_tips", []))
            + " Make more like: " + "; ".join(lessons.get("more_like", []))
            + ". Avoid the style of: " + "; ".join(lessons.get("less_like", []))
        )
    prompt = f"""
You are a YouTube growth analyst for "Elite Mind Decoder", a faceless channel with a cartoon
character called Black. Formats: 60-90 second numbered explainer Shorts, 5-part Shorts series,
and long "POV: You're..." stories. Topics: money psychology, mind tricks, wealth, discipline.
No financial advice.

Here are recent videos in our niches that outperformed their channel size the most:
{listing}

Title stats: {json.dumps(patterns)}
{own}

Return ONLY JSON with these keys:
"patterns": 6 short bullet strings on what the winners have in common (topic, angle, emotion, title formula),
"title_formulas": 5 reusable title templates,
"hooks": 5 opening lines in their style, adapted to our niche,
"shorts": 10 new explainer Short topics in our numbered style (3-5 items, e.g. "4 dark psychology tricks ..."),
"series": one 5-part series as 5 strings "Series title (Part N/5): episode | Next: next episode" (last has no Next),
"long": 5 new long video topics starting with "POV: You're" or "POV: You".
"""
    for attempt in range(5):
        reply = hosted_chat(
            prompt if attempt == 0
            else prompt + "\nReturn strictly valid JSON: double quotes, commas between items, no trailing commas, no comments."
        )
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        if not match:
            continue
        text = match.group(0)
        # Common model slips: trailing commas and smart quotes
        text = re.sub(r",\s*([\]}])", r"\1", text).replace("“", '"').replace("”", '"')
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return {}


def apply_ideas(insights):
    def prepend(path, lines):
        file = PROJECT_ROOT / path
        existing = [l for l in file.read_text(encoding="utf-8").splitlines() if l.strip()] if file.exists() else []
        new = [l for l in lines if l and l not in existing]
        file.write_text("\n".join(new + existing) + "\n", encoding="utf-8")
        return len(new)

    # Shorts must fit 45-60 s, so keep ideas with at most 5 items
    shorts = [t for t in insights.get("shorts", []) if not re.match(r"^([6-9]|\d{2,})\b", t)]
    added = {
        "shorts": prepend("topics.txt", shorts[:3]),
        "long": prepend("topics_long.txt", insights.get("long", [])[:1]),
    }
    series_file = PROJECT_ROOT / "topics_series.txt"
    existing_series = [l for l in series_file.read_text(encoding="utf-8").splitlines() if l.strip()] if series_file.exists() else []
    # New series goes after the one in progress so it never interrupts a running series
    # Only well-formed "Title (Part N/M): episode" lines; skip placeholder titles
    new_series = [
        line for line in insights.get("series", [])
        if re.search(r"\(Part \d+/\d+\)", line) and not line.lower().startswith("series title")
    ]
    if len(new_series) >= 3:
        series_file.write_text("\n".join(existing_series + new_series) + "\n", encoding="utf-8")
    added["series"] = len(new_series) if len(new_series) >= 3 else 0
    return added


def main():
    from youtube import get_authenticated_service

    queries = [q.strip() for q in os.getenv("RESEARCH_QUERIES", "").split(",") if q.strip()] or DEFAULT_QUERIES
    raw_file = OUT_DIR / "raw.json"
    OUT_DIR.mkdir(exist_ok=True)
    if "--reuse" in sys.argv and raw_file.exists():
        # Re-run the analysis without spending search quota again
        rows = json.loads(raw_file.read_text(encoding="utf-8"))
    else:
        youtube = get_authenticated_service()
        rows = collect(youtube, queries)
        raw_file.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    patterns = title_patterns(rows[:100])
    insights = ai_insights(rows, patterns)

    OUT_DIR.mkdir(exist_ok=True)
    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "queries": queries,
        "patterns": patterns,
        "insights": insights,
        "top": rows[:40],
    }
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Viral research - {report['generated']}", "", "## Top outliers"]
    lines += [f"- **{r['outlier']}x** | {r['views']:,} views | {r['seconds']}s | [{r['title']}](https://youtu.be/{r['id']})" for r in rows[:20]]
    lines += ["", "## Title stats", "```", json.dumps(patterns, indent=2), "```"]
    for key in ("patterns", "title_formulas", "hooks", "shorts", "series", "long"):
        lines += ["", f"## {key.replace('_', ' ').title()}"] + [f"- {x}" for x in insights.get(key, [])]
    (OUT_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if "--apply" in sys.argv:
        print("Added to queues:", apply_ideas(insights))
    print(f"Analysed {len(rows)} videos; report in research/report.md")


if __name__ == "__main__":
    main()
