"""
Vertical AI mini-drama maker (ReelShort-style series): a story bible with a fixed
cast, episodes written as dialogue with a cliffhanger each, photorealistic 9:16
images (FLUX on Kaggle), a different Kokoro voice per character, subtitles and
music. Nothing is uploaded; everything lands in dramas/<series>/.

Usage:
    python Backend/drama.py new "African kingdom folktale" --episodes 2 [--style storytime|drama] [--narrator af_heart]
    python Backend/drama.py episode dramas/<series> 3     # make a further episode
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ENV_FILE, PROJECT_ROOT  # noqa: E402

load_dotenv(ENV_FILE)

import imageio_ffmpeg  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

DRAMAS_DIR = PROJECT_ROOT / "dramas"
FONT = PROJECT_ROOT / "fonts" / "bold_font.ttf"
SIZE = (1080, 1920)
GEN_SIZE = (768, 1344)  # FLUX renders at ~1 MP, upscaled to 1080x1920
MALE_VOICES = ["am_onyx", "am_michael", "bm_george", "am_adam", "bm_lewis"]
FEMALE_VOICES = ["af_heart", "af_bella", "bf_emma", "af_nicole", "bf_isabella"]
NARRATOR_VOICE = "am_onyx"
STYLE = (
    "photorealistic cinematic film still from a high-budget vertical drama series, "
    "shot on 35mm, dramatic moody lighting, shallow depth of field, natural skin texture, "
    "sharp focus on faces, rich colors, vertical 9:16 composition"
)
NEGATIVE = "Not a cartoon, not an illustration, not anime, no text, no captions, no watermark."
MUSIC = PROJECT_ROOT / "Songs" / "Investigations.mp3"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _chat_json(prompt: str) -> dict:
    from gpt import hosted_chat

    for attempt in range(4):
        reply = hosted_chat(prompt if attempt == 0 else prompt + "\nReturn strictly valid JSON only.")
        match = re.search(r"\{.*\}", reply or "", re.DOTALL)
        if not match:
            continue
        text = re.sub(r",\s*([\]}])", r"\1", match.group(0)).replace("“", '"').replace("”", '"')
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("The text model did not return valid JSON.")


def write_bible(genre: str, episodes: int = 30, style: str = "storytime") -> dict:
    form = (
        "a hit vertical AI 'storytime' series (90-120 second episodes, 9:16): a warm narrator tells the "
        "story like a griot by the fire, and the characters speak their key lines"
        if style == "storytime" else "a hit vertical mini-drama app (60-90 second episodes, 9:16)"
    )
    bible = _chat_json(f"""
You are a head writer for {form}.
Create an ORIGINAL series in this genre: {genre}.
Use the genre's addictive formula: a shocking first scene, humiliation then reversal,
secrets, a hidden identity, a villain viewers love to hate, and a twist or cliffhanger
ending EVERY episode. Episode {min(8, episodes)} is a huge reveal.

Return ONLY JSON:
{{
 "title": "short catchy series title",
 "logline": "one sentence",
 "synopsis": "one paragraph",
 "cast": [
   {{"id": "UPPERCASE_ID", "name": "first name", "gender": "male|female", "age": 30,
     "role": "hero / heroine / villain / ...",
     "look": "precise fixed appearance for image generation: ethnicity, skin tone, face, hair, build, signature outfit"}}
 ],
 "world": "fixed visual description of the setting for image generation (architecture, clothing, jewelry, landscape, era)",
 "episodes": ["Ep 1: one-line summary", ... {episodes} items]
}}
4-6 cast members. The looks must be specific enough that every image shows the same person.
""")
    bible["style"] = style
    return bible


def write_episode(bible: dict, number: int) -> dict:
    cast = "\n".join(f'- {c["id"]}: {c["name"]}, {c["age"]}, {c["role"]}' for c in bible["cast"])
    outline = "\n".join(bible["episodes"])
    previous = bible.get("written", {}).get(str(number - 1), "")
    if bible.get("style", "drama") == "storytime":
        rules = """- 90-120 seconds spoken: 9-11 scenes. The NARRATOR tells the story in vivid past tense
  (1-2 sentences per scene); in most scenes a character then speaks 1-2 short emotional lines.
- The first NARRATOR line hooks instantly with a shocking event or a mystery.
- Episode 1 opens in the world (e.g. "In the kingdom of ..."); later episodes open with a
  one-sentence recap of the previous cliffhanger.
- Keep sentences short and dramatic, made to be read aloud."""
    else:
        rules = """- 60-90 seconds spoken: 6-8 scenes, 2-4 short lines each, mostly dialogue.
- First line must hook instantly (a slap, a betrayal, a shocking reveal).
- Lines are short and punchy, as people really talk in a drama. No stage directions inside lines."""
    return _chat_json(f"""
Write episode {number} of the vertical mini-drama "{bible['title']}".
Synopsis: {bible['synopsis']}
Cast:
{cast}
Season outline:
{outline}
{f"Previous episode ended with: {previous}" if previous else ""}

Rules:
{rules}
- Last scene ends on a cliffhanger line.
- No stage directions inside lines.
- "who" is a cast id, or NARRATOR.
- "shot" describes ONE camera image: who is in frame (use cast ids in square brackets, at most 2 people),
  their expression and action, the location and lighting. Close-ups and medium shots work best.

Return ONLY JSON:
{{"title": "episode title", "cliffhanger": "one sentence of how it ends",
  "scenes": [{{"shot": "...", "lines": [{{"who": "CAST_ID", "text": "..."}}]}}]}}
""")


def _voices(cast: List[dict], narrator: str = NARRATOR_VOICE) -> Dict[str, str]:
    # The narrator's voice is never given to a character
    voices = {"NARRATOR": narrator}
    male = [v for v in MALE_VOICES if v != narrator]
    female = [v for v in FEMALE_VOICES if v != narrator]
    for member in cast:
        pool = female if member.get("gender", "").lower().startswith("f") else male
        voices[member["id"]] = pool.pop(0) if pool else NARRATOR_VOICE
    return voices


def _shot_prompt(shot: str, bible: dict) -> str:
    for member in bible["cast"]:
        shot = shot.replace(f"[{member['id']}]", f"{member['name']} ({member['look']})")
    world = f" Setting: {bible['world']}." if bible.get("world") else ""
    return f"{shot}.{world} {STYLE}. {NEGATIVE}"


def _plain(text: str) -> str:
    """Drop accent marks (Yoruba Ọ, Ẹ, tones) that the subtitle font and voice can't handle."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _duration(path: Path) -> float:
    import soundfile as sf

    info = sf.info(str(path))
    return info.frames / info.samplerate


def _ass_time(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _subtitles(events: List[tuple], title: str, number: int, total: float, path: Path) -> None:
    def clean(text: str) -> str:
        return text.replace("\\", "").replace("{", "(").replace("}", ")")

    font = ImageFont.truetype(str(FONT), 20).getname()[0]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {SIZE[0]}
PlayResY: {SIZE[1]}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Line,{font},64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,5,2,2,90,90,430,1
Style: Name,{font},46,&H0000D4FF,&H0000D4FF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,4,2,2,90,90,520,1
Style: Card,{font},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,2,0,1,6,3,5,60,60,0,1
Style: Small,{font},52,&H0000D4FF,&H0000D4FF,&H00000000,&HA0000000,0,0,0,0,100,100,4,0,1,4,2,8,60,60,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [
        f"Dialogue: 1,{_ass_time(0)},{_ass_time(2.2)},Small,,0,0,0,,{clean(title.upper())}\\N EPISODE {number}",
        f"Dialogue: 1,{_ass_time(total - 2.0)},{_ass_time(total)},Card,,0,0,0,,{{\\fad(300,0)}}TO BE\\NCONTINUED...",
    ]
    for start, end, name, text in events:
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Line,,0,0,0,,{clean(text)}")
        if name:
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Name,,0,0,0,,{clean(name.upper())}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True)


def _clip_segment(source: Path, duration: float, out: Path) -> None:
    """A real video clip (e.g. Wan 2.2) fitted to the scene: cover-cropped to 1080x1920,
    slowed up to 1.6x when the lines run long, then holding its last frame."""
    import imageio_ffmpeg as iio

    probe = subprocess.run([FFMPEG, "-i", str(source)], capture_output=True, text=True).stderr
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe)
    length = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) if match else 3.4
    speed = min(1.6, max(1.0, duration / length))
    _ffmpeg(
        "-i", str(source), "-an", "-vf",
        f"setpts={speed:.3f}*PTS,scale={SIZE[0]}:{SIZE[1]}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={SIZE[0]}:{SIZE[1]},fps=24,tpad=stop_mode=clone:stop_duration={duration:.2f},format=yuv420p",
        "-t", f"{duration:.3f}", "-c:v", "libx264", "-crf", "15", "-preset", "medium", str(out),
    )


def render_episode(folder: Path, bible: dict, number: int, episode: dict, images: Dict[int, Path]) -> Path:
    from kokorovoice import kokoro_tts_save
    from visuals import _ken_burns_segment

    work = folder / f"ep{number:02d}_work"
    work.mkdir(exist_ok=True)
    voices = _voices(bible["cast"], bible.get("narrator", NARRATOR_VOICE))
    names = {c["id"]: _plain(c["name"]) for c in bible["cast"]}

    events, segments, audio_parts, clock = [], [], [], 0.0
    gap = 0.25
    for i, scene in enumerate(episode["scenes"]):
        scene_start = clock
        for j, line in enumerate(scene["lines"]):
            who = line["who"].strip().upper()
            wav = work / f"s{i:02d}_{j:02d}.wav"
            text = _plain(line["text"])
            kokoro_tts_save(text, voices.get(who, NARRATOR_VOICE), str(wav))
            length = _duration(wav)
            events.append((clock, clock + length, "" if who == "NARRATOR" else names.get(who, ""), text))
            audio_parts.append(wav)
            clock += length + gap
        if i == len(episode["scenes"]) - 1:
            clock += 2.0  # room for the "To be continued" card
        clip = work / f"scene{i:02d}.mp4"
        if Path(images[i]).suffix.lower() == ".mp4":
            _clip_segment(Path(images[i]), clock - scene_start, clip)
        else:
            _ken_burns_segment(str(images[i]), clock - scene_start, i, str(clip), size=SIZE)
        segments.append(clip)

    # Dialogue track: each line followed by a short pause, then silence under the end card
    silence = work / "gap.wav"
    _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(gap), str(silence))
    tail = work / "tail.wav"
    _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "2.0", str(tail))
    audio_list = work / "audio.txt"
    audio_list.write_text(
        "".join(f"file '{p.name}'\nfile '{silence.name}'\n" for p in audio_parts) + f"file '{tail.name}'\n",
        encoding="utf-8",
    )
    voice = work / "voice.wav"
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(audio_list), "-ar", "24000", "-ac", "1", str(voice))

    video_list = work / "video.txt"
    video_list.write_text("".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
    silent = work / "silent.mp4"
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(video_list), "-c", "copy", str(silent))

    subs = work / "subs.ass"
    _subtitles(events, _plain(bible["title"]), number, clock, subs)

    (work / "fonts").mkdir(exist_ok=True)
    (work / "fonts" / FONT.name).write_bytes(FONT.read_bytes())
    out = folder / f"episode_{number:02d}.mp4"
    music = ["-stream_loop", "-1", "-i", str(MUSIC)] if MUSIC.exists() else []
    mix = (
        "[1:a]volume=1.0[v];[2:a]volume=0.12[m];[v][m]amix=inputs=2:duration=first:dropout_transition=0[a]"
        if music else "[1:a]anull[a]"
    )
    # Run from the work folder so the subtitle and font paths need no escaping
    subprocess.run(
        [
            FFMPEG, "-v", "error", "-y", "-i", silent.name, "-i", voice.name, *music,
            "-filter_complex",
            f"[0:v]ass={subs.name}:fontsdir=fonts,unsharp=5:5:0.6[vid];{mix}",
            "-map", "[vid]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out.resolve()),
        ],
        check=True, cwd=work,
    )
    return out


def _make_images(jobs: List[dict], folder: Path) -> Dict[str, Path]:
    import kaggle_images

    print(f"[+] Rendering {len(jobs)} images on Kaggle (FLUX)...", flush=True)
    results = kaggle_images.generate(jobs, timeout_minutes=90)
    paths = {}
    for job_id, data in results.items():
        path = folder / "images" / f"{job_id}.jpg"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        # Upscale to the 1080x1920 frame once, with Lanczos
        Image.open(path).convert("RGB").resize(SIZE, Image.LANCZOS).save(path, quality=95)
        paths[job_id] = path
    return paths


def _poster(folder: Path, bible: dict, image: Path) -> None:
    poster = Image.open(image).convert("RGB")
    draw = ImageDraw.Draw(poster)
    font = ImageFont.truetype(str(FONT), 110)
    words, lines = _plain(bible["title"]).upper().split(), [""]
    for word in words:
        if draw.textlength((lines[-1] + " " + word).strip(), font=font) > SIZE[0] - 120:
            lines.append(word)
        else:
            lines[-1] = (lines[-1] + " " + word).strip()
    y = SIZE[1] - 260 - len(lines) * 125
    for line in lines:
        x = (SIZE[0] - draw.textlength(line, font=font)) / 2
        draw.text((x, y), line, font=font, fill="#ffd400", stroke_width=8, stroke_fill="black")
        y += 125
    poster.save(folder / "poster.jpg", quality=95)


def make_episodes(folder: Path, numbers: List[int]) -> None:
    bible = json.loads((folder / "bible.json").read_text(encoding="utf-8"))
    episodes, jobs = {}, []
    for n in numbers:
        episode = write_episode(bible, n)
        bible.setdefault("written", {})[str(n)] = episode.get("cliffhanger", "")
        episodes[n] = episode
        (folder / f"episode_{n:02d}.json").write_text(json.dumps(episode, indent=2, ensure_ascii=False), encoding="utf-8")
        for i, scene in enumerate(episode["scenes"]):
            jobs.append({"id": f"e{n:02d}s{i:02d}", "prompt": _shot_prompt(scene["shot"], bible),
                         "width": GEN_SIZE[0], "height": GEN_SIZE[1]})
    (folder / "bible.json").write_text(json.dumps(bible, indent=2, ensure_ascii=False), encoding="utf-8")

    images = _make_images(jobs, folder)
    for n, episode in episodes.items():
        scene_images = {}
        for i in range(len(episode["scenes"])):
            # A missing image reuses the previous scene's, so one failure never kills the episode
            scene_images[i] = images.get(f"e{n:02d}s{i:02d}") or scene_images.get(i - 1) or next(iter(images.values()))
        print(f"[+] Episode {n}: {render_episode(folder, bible, n, episode, scene_images)}", flush=True)
        if n == 1:
            _poster(folder, bible, scene_images[0])


def new_series(genre: str, count: int, style: str = "storytime", narrator: str = NARRATOR_VOICE) -> Path:
    bible = write_bible(genre, style=style)
    bible["narrator"] = narrator
    slug = re.sub(r"[^a-z0-9]+", "_", bible["title"].lower()).strip("_")[:40]
    folder = DRAMAS_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "bible.json").write_text(json.dumps(bible, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Series: {bible['title']} -> {folder}", flush=True)
    make_episodes(folder, list(range(1, count + 1)))
    pitch = [f"# {bible['title']}", "", f"**Logline:** {bible['logline']}", "", bible["synopsis"], "", "## Cast"]
    pitch += [f"- **{c['name']}** ({c['age']}), {c['role']}" for c in bible["cast"]]
    pitch += ["", "## Episodes"] + [f"- {e}" for e in bible["episodes"]]
    (folder / "pitch.md").write_text("\n".join(pitch) + "\n", encoding="utf-8")
    return folder


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "new":
        count = int(sys.argv[sys.argv.index("--episodes") + 1]) if "--episodes" in sys.argv else 2
        opt = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
        new_series(sys.argv[2], count, opt("--style", "storytime"), opt("--narrator", NARRATOR_VOICE))
    elif len(sys.argv) >= 4 and sys.argv[1] == "render":
        folder = Path(sys.argv[2])
        bible = json.loads((folder / "bible.json").read_text(encoding="utf-8"))
        for n in [int(x) for x in sys.argv[3].split(",")]:
            episode = json.loads((folder / f"episode_{n:02d}.json").read_text(encoding="utf-8"))
            clips = Path(sys.argv[4]) if len(sys.argv) > 4 else None
            imgs = {i: folder / "images" / f"e{n:02d}s{i:02d}.jpg" for i in range(len(episode["scenes"]))}
            if clips:
                for i in imgs:
                    video = clips / f"e{n:02d}s{i:02d}.mp4"
                    if video.exists():
                        imgs[i] = video
            for i in imgs:
                if not imgs[i].exists():
                    imgs[i] = imgs[i - 1] if i else next((folder / "images").glob("*.jpg"))
            print(render_episode(folder, bible, n, episode, imgs))
            if n == 1:
                _poster(folder, bible, folder / "images" / "e01s00.jpg")
    elif len(sys.argv) >= 4 and sys.argv[1] == "episode":
        make_episodes(Path(sys.argv[2]), [int(sys.argv[3])])
    else:
        print(__doc__)
