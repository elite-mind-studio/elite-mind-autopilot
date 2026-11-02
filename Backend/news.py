"""
Daily documentary from the news, for Headline Tales: pick today's biggest
advertiser-friendly world story from public RSS feeds, read the articles,
write a long script and a Short using only those facts, fact-check them, and
save script files that `channel.py publish` turns into videos.

Nothing is posted from here; a failed fact check raises, so nothing is posted.

Usage:
    python Backend/news.py          # writes channels/headline_tales/scripts/auto/<date>_<slug>_{long,short}.txt
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "channels" / "headline_tales" / "scripts" / "auto"
POSTED = ROOT / "channels" / "headline_tales" / "posted.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (HeadlineTales documentary research; +https://youtube.com/@headlinetales)"}
FEEDS = {
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "BBC Technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "The Guardian World": "https://www.theguardian.com/world/rss",
    "NPR World": "https://feeds.npr.org/1004/rss.xml",
    "DW": "https://rss.dw.com/rdf/rss-en-world",
    "CNBC World": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
}
MAX_SOURCE_WORDS = 2600  # keeps each prompt inside Groq's free per-minute token limit


def chat(prompt: str) -> str:
    """Hosted text model with patience for free-tier rate limits."""
    from gpt import hosted_chat

    for attempt in range(6):
        try:
            return hosted_chat(prompt)
        except Exception as err:
            if attempt == 5:
                raise
            print(f"[-] Text model busy ({str(err)[:80]}), retrying in 40s", flush=True)
            time.sleep(40)


def chat_json(prompt: str):
    for attempt in range(4):
        reply = chat(prompt if attempt == 0 else prompt + "\nReturn strictly valid JSON only.")
        match = re.search(r"[\[{].*[\]}]", reply or "", re.DOTALL)
        if not match:
            continue
        text = re.sub(r",\s*([\]}])", r"\1", match.group(0)).replace("“", '"').replace("”", '"')
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("Text model did not return valid JSON")


def headlines() -> List[dict]:
    items = []
    for source, url in FEEDS.items():
        try:
            root = ET.fromstring(requests.get(url, headers=HEADERS, timeout=20).content)
        except Exception as err:
            print(f"[-] {source} feed failed: {err}", flush=True)
            continue
        for item in root.iter():
            if not item.tag.endswith("item"):
                continue
            get = lambda name: next((c.text or "" for c in item if c.tag.endswith(name)), "").strip()
            title, link = get("title"), get("link")
            if title and link:
                items.append({"source": source, "title": title, "link": link,
                              "summary": re.sub(r"<[^>]+>", "", get("description"))[:300]})
    return items


class _Paragraphs(HTMLParser):
    """Collects the text of <p> elements (article bodies), skipping scripts and menus."""

    def __init__(self):
        super().__init__()
        self.parts, self._in_p, self._skip = [], False, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "aside"):
            self._skip += 1
        elif tag == "p":
            self._in_p = True
            self.parts.append("")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "aside"):
            self._skip = max(0, self._skip - 1)
        elif tag == "p":
            self._in_p = False

    def handle_data(self, data):
        if self._in_p and not self._skip and self.parts:
            self.parts[-1] += data


def article_text(url: str) -> str:
    try:
        parser = _Paragraphs()
        parser.feed(requests.get(url, headers=HEADERS, timeout=25).text)
        paragraphs = [re.sub(r"\s+", " ", p).strip() for p in parser.parts]
        return "\n".join(p for p in paragraphs if len(p.split()) >= 8)
    except Exception:
        return ""


def wikipedia(query: str) -> Optional[dict]:
    try:
        api = "https://en.wikipedia.org/w/api.php"
        hits = requests.get(api, params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 1},
                            headers=HEADERS, timeout=20).json()["query"]["search"]
        if not hits:
            return None
        title = hits[0]["title"]
        pages = requests.get(api, params={"action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "format": "json"},
                             headers=HEADERS, timeout=20).json()["query"]["pages"]
        text = next(iter(pages.values())).get("extract", "")
        return {"source": f"Wikipedia, {title}", "link": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}", "text": text}
    except Exception:
        return None


def recent_topics() -> List[str]:
    topics = []
    for path in sorted(list(SCRIPTS_DIR.glob("*_long.txt")) + list(SCRIPTS_DIR.glob("*_news.txt")))[-40:]:
        match = re.search(r"^SUBJECT: (.+)$", path.read_text(encoding="utf-8"), re.M)
        if match:
            topics.append(match.group(1))
    if POSTED.exists():
        topics += [v.get("title", "") for v in json.loads(POSTED.read_text(encoding="utf-8")).values()]
    return topics


def pick_story(items: List[dict]) -> dict:
    listing = "\n".join(f"{i}. [{it['source']}] {it['title']} - {it['summary'][:140]}" for i, it in enumerate(items[:120]))
    done = "\n".join(f"- {t}" for t in recent_topics()) or "- (none yet)"
    return chat_json(f"""
You choose today's story for "Headline Tales", a YouTube channel of short, neutral, well-sourced
documentaries about what is happening in the world. Pick ONE story from today's headlines.

Choose a story that:
- the whole world is talking about, with big stakes (money, power, technology, energy, trade, sport, science);
- has enough concrete facts and numbers for an 8-minute documentary;
- is advertiser-friendly: NOT mainly about mass deaths, terrorism, sexual violence, child harm or graphic crime;
- is not a near-repeat of these recent topics:
{done}

Headlines:
{listing}

Return ONLY JSON: {{"subject": "short documentary subject", "angle": "one sentence: the story to tell",
"items": [indices of 3-6 headlines about this same story], "wiki_query": "Wikipedia search for background"}}
""")


def gather(story: dict, items: List[dict]) -> List[dict]:
    sources = []
    for i in story.get("items", [])[:6]:
        if 0 <= i < len(items):
            text = article_text(items[i]["link"])
            if len(text.split()) > 120:
                sources.append({"source": items[i]["source"], "title": items[i]["title"], "link": items[i]["link"], "text": text})
    wiki = wikipedia(story.get("wiki_query") or story["subject"])
    if wiki and len(wiki["text"].split()) > 120:
        sources.append({**wiki, "title": wiki["source"]})
    # Share the word budget fairly between sources
    budget = MAX_SOURCE_WORDS // max(1, len(sources))
    for s in sources:
        s["text"] = " ".join(s["text"].split()[:budget])
    return sources


def _sources_block(sources: List[dict]) -> str:
    return "\n\n".join(f"[S{i + 1}] {s['source']}: {s['title']}\n{s['text']}" for i, s in enumerate(sources))


def write_scripts(story: dict, sources: List[dict]) -> dict:
    return chat_json(f"""
You write for "Headline Tales": neutral, gripping YouTube documentaries about current events.
Subject: {story['subject']}. Angle: {story['angle']}

STRICT RULES:
- Use ONLY facts stated in the SOURCES below. No outside facts, no guesses, no invented numbers, quotes or names.
- Attribute claims ("according to ...", "officials said") when sources do. Neutral tone, never take sides.
- Numbers are written in words for narration (e.g. "two hundred and forty-six").
- No graphic descriptions of violence.

LONG script: 1000-1150 words, 11-13 paragraphs separated by blank lines. Paragraph 1 is a hook (the most
striking fact), then context/history, what happened, impact on ordinary people, the latest, what could happen
next, and a closing paragraph asking viewers to subscribe and comment where they are watching from.

SHORT script: 90-110 words, 5-6 short paragraphs. Start directly with the action ("X did Y, and ..."),
each sentence a consequence of the last, then end with: "This is the story of <subject>. For the full
documentary, like, subscribe, and check the description for the full video."

GRAPHICS for the long video: 2-4 animated cards placed on long-script paragraphs (0-based index), using
only numbers from the sources:
- {{"type": "title", "kicker": "HEADLINE TALES", "title": "...", "counter": {{"label": "...", "to": NUMBER}}, "stamp": "ONE WORD"}} (counter and stamp optional; use on paragraph 0)
- {{"type": "stat", "value": NUMBER, "prefix": "", "suffix": "", "caption": "short caption", "sub": "short source note"}}
- {{"type": "chart", "title": "...", "sub": "UNITS", "prefix": "", "suffix": "", "points": [["LABEL", NUMBER], ...]}} (only with 3+ real data points)

SOURCES:
{_sources_block(sources)}

Return ONLY JSON:
{{"long_title": "YouTube title, max 90 chars, curiosity + clarity", "short_title": "YouTube Shorts title, max 80 chars",
"title_bar": "2-4 WORD HOOK", "thumb_word": "ONE OR TWO WORDS", "description": "2-3 sentence description",
"tags": "comma separated tags", "long_script": "...", "short_script": "...",
"graphics": [{{"paragraph": 0, "card": {{...}}}}]}}
""")


def write_short_only(story: dict, sources: List[dict]) -> dict:
    return chat_json(f"""
You write for "Headline Tales": neutral, gripping YouTube Shorts that summarise today's news.
Subject: {story['subject']}. Angle: {story['angle']}

STRICT RULES:
- Use ONLY facts stated in the SOURCES below. No outside facts, no guesses, no invented numbers, quotes or names.
- Neutral tone, never take sides; attribute claims when the sources do. Numbers written in words.
- 90-110 words, 5-6 short paragraphs. Start directly with the action ("X did Y, and ..."), each sentence a
  consequence of the last, the most surprising fact early. End with exactly:
  "Follow Headline Tales for the stories behind the headlines."

SOURCES:
{_sources_block(sources)}

Return ONLY JSON: {{"short_title": "YouTube Shorts title, max 80 chars", "title_bar": "2-4 WORD HOOK",
"description": "1-2 sentence description", "tags": "comma separated tags", "short_script": "..."}}
""")


def fact_check(text: str, sources: List[dict]) -> List[str]:
    """Claims in `text` that the sources do not support (empty list = all supported)."""
    result = chat_json(f"""
Fact-check this narration against the SOURCES. List every factual claim (numbers, dates, names, events,
quotes) that is NOT supported by the sources. Opinions, transitions and calls to subscribe are fine.

NARRATION:
{text}

SOURCES:
{_sources_block(sources)}

Return ONLY JSON: {{"unsupported": ["exact sentence from the narration", ...]}}
""")
    return [s for s in result.get("unsupported", []) if s.strip()]


def remove_sentences(text: str, sentences: List[str]) -> str:
    for sentence in sentences:
        text = text.replace(sentence.strip(), "")
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _checked(text: str, sources: List[dict], label: str, rounds: int = 3) -> str:
    """Remove unsupported sentences; the last round only verifies, so what is posted passed a clean check."""
    for round_ in range(rounds):
        time.sleep(20)  # free-tier token budget
        bad = fact_check(text, sources)
        if not bad:
            return text
        if round_ == rounds - 1:
            break
        print(f"[-] {label}: removing {len(bad)} unsupported claim(s): {bad}", flush=True)
        text = remove_sentences(text, bad)
    raise RuntimeError(f"{label} still has unsupported claims after fact-check; not posting")


def _slug(subject: str) -> str:
    return f"{date.today().isoformat()}_" + re.sub(r"[^a-z0-9]+", "_", subject.lower()).strip("_")[:40]


def prepare_short() -> Path:
    """A standalone news Short on another of today's big stories (no long video)."""
    items = headlines()
    if len(items) < 10:
        raise RuntimeError(f"Only {len(items)} headlines fetched")
    story = pick_story(items)
    print(f"[+] Short story: {story['subject']} - {story['angle']}", flush=True)
    sources = gather(story, items)
    if len(sources) < 2:
        raise RuntimeError("Not enough readable sources for this story")
    draft = write_short_only(story, sources)
    draft["short_script"] = _checked(draft["short_script"], sources, "news short")
    if len(draft["short_script"].split()) < 60:
        raise RuntimeError("Short too short after fact-check; not posting")
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS_DIR / f"{_slug(story['subject'])}_news.txt"
    source_lines = "\n".join(f"SOURCE: {s['source']} - {s['link']}" for s in sources)
    path.write_text(
        f"FORMAT: short\nSUBJECT: {story['subject']}\nTITLE: {draft.get('title_bar', story['subject'])}\n"
        f"YT_TITLE: {draft['short_title']}\nTAGS: {draft.get('tags', '')}\nDESCRIPTION: {draft.get('description', '')}\n"
        f"{source_lines}\n\n" + draft["short_script"].strip() + "\n",
        encoding="utf-8",
    )
    print(f"[+] Script: {path.name}", flush=True)
    return path


def prepare() -> tuple:
    items = headlines()
    if len(items) < 10:
        raise RuntimeError(f"Only {len(items)} headlines fetched")
    story = pick_story(items)
    print(f"[+] Story: {story['subject']} - {story['angle']}", flush=True)
    sources = gather(story, items)
    if len(sources) < 2:
        raise RuntimeError("Not enough readable sources for this story")
    print(f"[+] Sources: {', '.join(s['source'] for s in sources)}", flush=True)

    draft = write_scripts(story, sources)
    for key in ("long_script", "short_script"):
        draft[key] = _checked(draft[key], sources, key, rounds=4)
    if len(draft["long_script"].split()) < 700:
        raise RuntimeError("Long script too short after fact-check; not posting")

    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"{date.today().isoformat()}_" + re.sub(r"[^a-z0-9]+", "_", story["subject"].lower()).strip("_")[:40]
    source_lines = "\n".join(f"SOURCE: {s['source']} - {s['link']}" for s in sources)
    long_path = SCRIPTS_DIR / f"{slug}_long.txt"
    long_path.write_text(
        f"FORMAT: long\nSUBJECT: {story['subject']}\nTITLE: {draft['long_title']}\nYT_TITLE: {draft['long_title']}\n"
        f"THUMB_WORD: {draft.get('thumb_word', 'THE STORY')}\nTAGS: {draft.get('tags', '')}\n"
        f"DESCRIPTION: {draft.get('description', '')}\n{source_lines}\n"
        + "".join(f"GRAPHIC: {g['paragraph']} | {json.dumps(g['card'])}\n" for g in draft.get("graphics", []))
        + "\n" + draft["long_script"].strip() + "\n",
        encoding="utf-8",
    )
    short_path = SCRIPTS_DIR / f"{slug}_short.txt"
    short_path.write_text(
        f"FORMAT: short\nSUBJECT: {story['subject']}\nTITLE: {draft.get('title_bar', story['subject'])}\n"
        f"YT_TITLE: {draft['short_title']}\nTAGS: {draft.get('tags', '')}\nDESCRIPTION: {draft.get('description', '')}\n"
        f"{source_lines}\n\n" + draft["short_script"].strip() + "\n",
        encoding="utf-8",
    )
    print(f"[+] Scripts: {long_path.name}, {short_path.name}", flush=True)
    return long_path, short_path


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "channels" / "headline_tales.env", override=True)
    prepare()
