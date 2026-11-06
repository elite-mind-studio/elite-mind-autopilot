import math
import os
import uuid

import re
import textwrap

import requests
import srt_equalizer
import assemblyai as aai

from typing import List
from pathlib import Path
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from logstream import log
from moviepy.video.tools.subtitles import SubtitlesClip
from utils import ENV_FILE, TEMP_DIR, SUBTITLES_DIR, FONTS_DIR

load_dotenv(ENV_FILE)

ASSEMBLY_AI_API_KEY = os.getenv("ASSEMBLY_AI_API_KEY")
FRAME_EPSILON = 1 / 120


def save_video(video_url: str, directory: str = str(TEMP_DIR)) -> str:
    """
    Saves a video from a given URL and returns the path to the video.

    Args:
        video_url (str): The URL of the video to save.
        directory (str): The path of the temporary directory to save the video to

    Returns:
        str: The path to the saved video.
    """
    destination = Path(directory).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    video_id = uuid.uuid4()
    video_path = destination / f"{video_id}.mp4"

    # Retry, and reject truncated downloads so a flaky connection can't
    # leave a corrupt clip that crashes rendering later
    last_error = None
    for _ in range(3):
        try:
            with requests.get(video_url, stream=True, timeout=60) as response:
                response.raise_for_status()
                expected = int(response.headers.get("Content-Length", 0))
                written = 0
                with open(video_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
                        written += len(chunk)
            if expected and written != expected:
                raise IOError(f"Incomplete download ({written}/{expected} bytes)")

            # Make sure the clip can actually be decoded
            with VideoFileClip(str(video_path)) as clip:
                clip.get_frame(0)
            return str(video_path)
        except Exception as err:
            last_error = err
            video_path.unlink(missing_ok=True)

    raise RuntimeError(f"Could not download {video_url}: {last_error}")


def generate_designed_thumbnail(image_path: str, words: str, size=(1280, 720), fill: str = "") -> str:
    """
    Curiosity-channel thumbnail: a bright, simple scene with 2-3 huge words centred
    across the top (white or yellow, thick black outline). No arrows, no clutter.

    Args:
        image_path (str): Thumbnail scene (its top third should be plain).
        words (str): 2-3 words that tease the answer, e.g. "CAUGHT BY DNA?".
        size (tuple): Output size.
        fill (str): Text colour; THUMB_TEXT_COLOR or yellow by default.

    Returns:
        str: Path to the thumbnail JPEG.
    """
    width, height = size
    image = Image.open(image_path).convert("RGB")
    scale = max(width / image.width, height / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left, top = (image.width - width) // 2, (image.height - height) // 2
    image = image.crop((left, top, left + width, top + height))
    image = ImageEnhance.Brightness(
        ImageEnhance.Contrast(ImageEnhance.Color(image).enhance(1.35)).enhance(1.12)
    ).enhance(1.05)
    draw = ImageDraw.Draw(image)

    text = re.sub(r"[^A-Za-z0-9 !?$%]", "", words).strip().upper()[:18] or "WATCH"
    font_path = str((FONTS_DIR / "bold_font.ttf").resolve())
    font_size = int(height * 0.24)
    while font_size > 30:
        font = ImageFont.truetype(font_path, font_size)
        if draw.textlength(text, font=font) <= width * 0.92:
            break
        font_size -= 4

    x = (width - draw.textlength(text, font=font)) / 2
    y = height * 0.04
    stroke = max(8, font_size // 8)
    fill = fill or os.getenv("THUMB_TEXT_COLOR", "#FFE600")
    # Soft drop shadow, then the words with a thick black outline
    draw.text((x + stroke * 0.6, y + stroke * 0.8), text, font=font, fill="#000000",
              stroke_width=stroke, stroke_fill="#000000")
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill="#000000")

    thumbnail_path = Path(image_path).with_name(f"{uuid.uuid4()}_thumbnail.jpg")
    image.save(thumbnail_path, "JPEG", quality=92, optimize=True)
    return str(thumbnail_path)


def _text_layer(path: str, lines: list, size: int, width: int, border: bool = True) -> None:
    """Centred lines of bold text, each (text, colour), on a transparent PNG as wide as the frame."""
    font_path = str((FONTS_DIR / "bold_font.ttf").resolve())
    font = ImageFont.truetype(font_path, size)
    # Shrink until the longest line fits inside the frame with a margin
    while size > 20 and max(font.getlength(text) for text, _ in lines) > width * 0.9:
        size -= 4
        font = ImageFont.truetype(font_path, size)
    stroke = max(4, size // 9) if border else 0
    line_h = int(size * 1.12) if border else int(size * 1.45)
    image = Image.new("RGBA", (width, line_h * len(lines) + stroke * 2 + 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for n, (text, colour) in enumerate(lines):
        draw.text((width // 2, stroke + n * line_h), text, font=font, fill=colour, anchor="ma",
                  stroke_width=stroke, stroke_fill="#000000")
    image.save(path)


def _chat_bubble(path: str, message: str, mine: bool, width: int) -> None:
    """A phone text bubble: theirs grey on the left, yours blue on the right."""
    import textwrap

    font = ImageFont.truetype(str((FONTS_DIR / "bold_font.ttf").resolve()), int(width * 0.062))
    lines = textwrap.wrap(message, 20)
    pad, line_h = int(width * 0.045), int(width * 0.076)
    box_w = int(max(font.getlength(line) for line in lines) + pad * 2)
    box_h = line_h * len(lines) + pad * 2
    image = Image.new("RGBA", (width, box_h + 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x0 = width - box_w - int(width * 0.06) if mine else int(width * 0.06)
    draw.rounded_rectangle([x0, 0, x0 + box_w, box_h], radius=int(width * 0.045),
                           fill=(0, 122, 255, 240) if mine else (58, 58, 62, 240))
    for n, line in enumerate(lines):
        draw.text((x0 + pad, pad + n * line_h), line, font=font, fill="#FFFFFF")
    image.save(path)


def _phrase_end(phrase: str):
    """When the narrator finishes saying `phrase`, or None."""
    target = [w for w in re.sub(r"[^a-z0-9' ]", " ", phrase.lower()).split() if w]
    words = [(re.sub(r"[^a-z0-9']", "", w.lower()), e) for chunk in LAST_CAPTION_CHUNKS for w, _, e in chunk]
    for i in range(len(words) - len(target) + 1):
        if target and [w for w, _ in words[i:i + len(target)]] == target:
            return words[i + len(target) - 1][1]
    return None


def _phrase_time(phrase: str):
    """When the narrator starts saying `phrase` (from the caption word timings), or None."""
    target = [w for w in re.sub(r"[^a-z0-9' ]", " ", phrase.lower()).split() if w]
    if not target:
        return None
    words = [(re.sub(r"[^a-z0-9']", "", w.lower()), s) for chunk in LAST_CAPTION_CHUNKS for w, s, _ in chunk]
    for i in range(len(words) - len(target) + 1):
        if [w for w, _ in words[i:i + len(target)]] == target:
            return words[i][1]
    return None


def _choice_card(path: str, width: int, *choices: str) -> None:
    """Stacked option pills (A green, B red, C blue, D orange) for choices and quiz answers."""
    font = ImageFont.truetype(str((FONTS_DIR / "bold_font.ttf").resolve()), int(width * 0.062))
    pill_h, gap = int(width * 0.13), int(width * 0.03)
    card = Image.new("RGBA", (width, pill_h * len(choices) + gap * (len(choices) - 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    colours = ("#2BB34A", "#E8323C", "#2F7BFF", "#FF9F1C")
    for n, (letter, label, colour) in enumerate(zip("ABCD", choices, colours)):
        top = n * (pill_h + gap)
        draw.rounded_rectangle([0, top, width - 1, top + pill_h], radius=pill_h // 2 - 4, fill=(12, 14, 20, 225),
                               outline="#FFFFFF", width=max(3, pill_h // 22))
        r = int(pill_h * 0.36)
        cx, cy = pill_h // 2 + 6, top + pill_h // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
        draw.text((cx, cy), letter, font=font, fill="#FFFFFF", anchor="mm")
        text = label.strip().upper()
        room = width - (cx + r + int(width * 0.08))
        size = int(width * 0.062)
        fitted = font
        while fitted.getlength(text) > room and size > int(width * 0.036):
            size -= 2  # long answers get a smaller font so they stay whole inside the pill
            fitted = ImageFont.truetype(str((FONTS_DIR / "bold_font.ttf").resolve()), size)
        while fitted.getlength(text) > room and len(text) > 4:
            text = text[:-2].rstrip() + "..."
        draw.text((cx + r + int(width * 0.04), cy), text, font=fitted, fill="#FFFFFF", anchor="lm")
    card.save(path)


def generate_bold_thumbnail(image_path: str, word: str, size=(1280, 720)) -> str:
    """
    Big-channel style thumbnail: a striking scene image, one huge word in red
    with a white outline, and a red arrow pointing at the subject.

    Args:
        image_path (str): Scene image to use as the background.
        word (str): 1-2 words, e.g. "TRAP" or "PART 2".
        size (tuple): Output size, (1280, 720) for long videos, (1080, 1920) for Shorts.

    Returns:
        str: Path to the thumbnail JPEG.
    """
    width, height = size
    image = Image.open(image_path).convert("RGB")
    scale = max(width / image.width, height / image.height)
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left, top = (image.width - width) // 2, (image.height - height) // 2
    image = image.crop((left, top, left + width, top + height))
    image = ImageEnhance.Contrast(ImageEnhance.Color(image).enhance(1.25)).enhance(1.1)
    draw = ImageDraw.Draw(image)

    text = re.sub(r"[^A-Za-z0-9 !?$%]", "", word).strip().upper()[:14] or "WATCH"
    font_path = str((FONTS_DIR / "bold_font.ttf").resolve())
    font_size = int(height * (0.16 if width > height else 0.15))
    while font_size > 30:
        font = ImageFont.truetype(font_path, font_size)
        if draw.textlength(text, font=font) <= width * 0.88:
            break
        font_size -= 6

    text_width = draw.textlength(text, font=font)
    x = (width - text_width) / 2 if width < height else width * 0.05
    y = height * (0.06 if width > height else 0.08)
    stroke = max(6, font_size // 9)
    # Drop shadow, then red text with a thick white outline
    draw.text((x + stroke, y + stroke), text, font=font, fill="#000000", stroke_width=stroke, stroke_fill="#000000")
    draw.text((x, y), text, font=font, fill="#E62117", stroke_width=stroke, stroke_fill="#FFFFFF")

    # Red arrow from under the word toward the middle of the picture
    start = (x + text_width * (0.55 if width > height else 0.5), y + font_size * 1.25)
    end = (width * (0.5 if width > height else 0.3), height * (0.5 if width > height else 0.6))
    shaft = max(10, max(width, height) // 80)
    for colour, extra in (("#FFFFFF", shaft // 2), ("#E62117", 0)):
        draw.line([start, end], fill=colour, width=shaft + extra)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head = shaft * 3.2
    tip = (end[0] + math.cos(angle) * head * 0.6, end[1] + math.sin(angle) * head * 0.6)
    wing1 = (end[0] + math.cos(angle + 2.5) * head, end[1] + math.sin(angle + 2.5) * head)
    wing2 = (end[0] + math.cos(angle - 2.5) * head, end[1] + math.sin(angle - 2.5) * head)
    draw.polygon([tip, wing1, wing2], fill="#E62117", outline="#FFFFFF", width=max(3, shaft // 4))

    thumbnail_path = Path(image_path).with_name(f"{uuid.uuid4()}_thumbnail.jpg")
    image.save(thumbnail_path, "JPEG", quality=90, optimize=True)
    return str(thumbnail_path)


def generate_thumbnail(
    video_path: str, title: str, at_second: float = 1.0, size=(1080, 1920)
) -> str:
    """
    Creates a 1080x1920 JPEG thumbnail: a darkened frame from the video with
    the title in large bold text.

    Args:
        video_path (str): Video to take the background frame from (ideally
            the clip montage without subtitles).
        title (str): Text to print on the thumbnail.
        at_second (float): Timestamp of the background frame.

    Returns:
        str: Path to the thumbnail image.
    """
    with VideoFileClip(video_path) as clip:
        frame = clip.get_frame(min(at_second, max(clip.duration - 0.1, 0)))

    width, height = size
    image = Image.fromarray(frame).convert("RGB").resize((width, height))
    image = ImageEnhance.Brightness(image).enhance(0.45)
    draw = ImageDraw.Draw(image)

    # Drop hashtags such as "#Shorts" and keep the title readable at a glance
    text = re.sub(r"#\w+", "", title).strip().upper()
    font_path = str((FONTS_DIR / "bold_font.ttf").resolve())

    font_size = 130
    while font_size > 60:
        font = ImageFont.truetype(font_path, font_size)
        lines = textwrap.wrap(text, width=max(8, int((size[0] - 80) / (font_size * 0.55))))
        widths = [draw.textlength(line, font=font) for line in lines]
        if max(widths) <= size[0] - 100 and len(lines) * font_size * 1.15 <= size[1] * 0.68:
            break
        font_size -= 10

    line_height = int(font_size * 1.15)
    y = (height - line_height * len(lines)) // 2
    for i, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        # Alternate white/yellow lines for contrast
        fill = "#FFFFFF" if i % 2 == 0 else "#FFD400"
        draw.text(
            ((size[0] - line_width) / 2, y),
            line,
            font=font,
            fill=fill,
            stroke_width=8,
            stroke_fill="#000000",
        )
        y += line_height

    thumbnail_path = Path(video_path).with_name(f"{uuid.uuid4()}_thumbnail.jpg")
    # YouTube rejects thumbnails over 2 MB
    image.save(thumbnail_path, "JPEG", quality=88, optimize=True)
    return str(thumbnail_path)


def __generate_subtitles_assemblyai(audio_path: str, voice: str) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    Args:
        audio_path (str): The path to the audio file to generate subtitles from.

    Returns:
        str: The generated subtitles
    """

    language_mapping = {
        "br": "pt",
        "id": "en",  # AssemblyAI doesn't have Indonesian
        "jp": "ja",
        "kr": "ko",
    }

    if voice in language_mapping:
        lang_code = language_mapping[voice]
    else:
        lang_code = voice

    aai.settings.api_key = ASSEMBLY_AI_API_KEY
    config = aai.TranscriptionConfig(language_code=lang_code)
    transcriber = aai.Transcriber(config=config)
    transcript = transcriber.transcribe(audio_path)
    subtitles = transcript.export_subtitles_srt()

    return subtitles


def __generate_subtitles_locally(
    sentences: List[str], audio_clips: List[AudioFileClip]
) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    Args:
        sentences (List[str]): all the sentences said out loud in the audio clips
        audio_clips (List[AudioFileClip]): all the individual audio clips which will make up the final audio track
    Returns:
        str: The generated subtitles
    """

    def convert_to_srt_time_format(total_seconds: float) -> str:
        # Convert total seconds to the SRT time format: HH:MM:SS,mmm
        milliseconds_total = int(round(total_seconds * 1000))
        hours, remainder = divmod(milliseconds_total, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    start_time = 0
    subtitles = []

    for i, (sentence, audio_clip) in enumerate(zip(sentences, audio_clips), start=1):
        duration = audio_clip.duration
        end_time = start_time + duration

        # Format: subtitle index, start time --> end time, sentence
        subtitle_entry = f"{i}\n{convert_to_srt_time_format(start_time)} --> {convert_to_srt_time_format(end_time)}\n{sentence}\n"
        subtitles.append(subtitle_entry)

        start_time += duration  # Update start time for the next subtitle

    return "\n".join(subtitles)


# Word timings of the most recent aligned captions: [[(word, start, end), ...], ...]
LAST_CAPTION_CHUNKS: list = []

# Always highlighted: numbers, money and high-emotion words
EMPHASIS = re.compile(
    r"(\d|\$|%|million|billion|trillion|never|secret|killed|dead|died|crash|war|broke|rich|free|"
    r"warning|danger|lie|lies|truth|shocking|nobody|everyone|biggest|worst|first|last|only|trapped|"
    r"thousand|hundred|dollars?|percent|emergenc|explo|blast|disaster|attack|crisis|collapse|chaos|panic|"
    r"catastroph|destroy|hacked|stolen|bomb|invasion|meltdown|missing|"
    r"^(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b)",
    re.IGNORECASE,
)


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    sec, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def _highlight_ass(chunks: list, path: str, width: int, height: int, font: str, size: int,
                   margin_v: int, accent_hex: str) -> None:
    """
    Karaoke-style captions: each 2-3 word caption pops in, the word being spoken
    is enlarged in the accent colour, and numbers/power words stay accented.
    """
    accent = accent_hex.lstrip("#").rjust(6, "F")
    accent_ass = f"&H00{accent[4:6]}{accent[2:4]}{accent[0:2]}&"
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,"
        f"{max(3, size // 12)},{max(1, size // 30)},2,60,60,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    clean = lambda w: w.replace("{", "(").replace("}", ")").replace("\\", "")
    lines = []
    for n, chunk in enumerate(chunks):
        chunk_end = chunks[n + 1][0][1] if n + 1 < len(chunks) else chunk[-1][2] + 0.3
        for j, (_, start, _) in enumerate(chunk):
            end = chunk[j + 1][1] if j + 1 < len(chunk) else max(chunk_end, start + 0.15)
            words = []
            for k, (word, _, _) in enumerate(chunk):
                w = clean(word.upper())
                if k == j:
                    words.append(f"{{\\c{accent_ass}\\fscx118\\fscy118}}{w}{{\\r}}")
                elif EMPHASIS.search(word):
                    words.append(f"{{\\c{accent_ass}}}{w}{{\\r}}")
                else:
                    words.append(w)
            pop = "{\\fscx86\\fscy86\\t(0,90,\\fscx100\\fscy100)}" if j == 0 else ""
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{pop}{' '.join(words)}")
    Path(path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _srt_time(total_seconds: float) -> str:
    ms = int(round(max(0.0, total_seconds) * 1000))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def __generate_subtitles_aligned(audio_path: str, sentences: List[str]) -> str:
    """
    Word-accurate captions: Whisper listens to the finished narration for the
    moment each word is spoken, and the script's own words (exact spelling) are
    shown 2-3 at a time on those timings. Pauses no longer push captions late.
    """
    import difflib
    import re as _re

    from faster_whisper import WhisperModel

    model = WhisperModel(os.getenv("WHISPER_MODEL", "base.en"), device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, word_timestamps=True, language="en", beam_size=1)
    heard = [(w.word, w.start, w.end) for seg in segments for w in (seg.words or [])]
    norm = lambda w: _re.sub(r"[^a-z0-9]", "", w.lower())

    script = [(word, n) for n, sentence in enumerate(sentences) for word in sentence.split()]
    times = [None] * len(script)
    matcher = difflib.SequenceMatcher(a=[norm(w) for w, _ in script], b=[norm(w) for w, _, _ in heard], autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            times[a + k] = (heard[b + k][1], heard[b + k][2])
    # Words Whisper heard differently (names, numbers) get times between their neighbours
    known = [i for i, t in enumerate(times) if t]
    if not known:
        raise RuntimeError("alignment found no words")
    for i, t in enumerate(times):
        if t:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is None:
            times[i] = (max(0.0, times[nxt][0] - 0.3), times[nxt][0])
        elif nxt is None:
            times[i] = (times[prev][1], times[prev][1] + 0.3)
        else:
            span = (times[nxt][0] - times[prev][1]) / (nxt - prev)
            start = times[prev][1] + span * (i - prev - 1)
            times[i] = (start, start + span)

    # 2-3 word captions that never span two sentences or a long pause
    chunks, current = [], []
    for i, (word, sentence) in enumerate(script):
        if current:
            last = current[-1]
            gap = times[i][0] - times[last][1]
            text_len = sum(len(script[k][0]) + 1 for k in current) + len(word)
            # CAPTION_WORDS / CAPTION_CHARS: suspense stories show only 1-2 words at a time
            if (script[last][1] != sentence or len(current) >= int(os.getenv("CAPTION_WORDS", "3"))
                    or text_len > int(os.getenv("CAPTION_CHARS", "16")) or gap > 0.35):
                chunks.append(current)
                current = []
        current.append(i)
    if current:
        chunks.append(current)

    global LAST_CAPTION_CHUNKS
    LAST_CAPTION_CHUNKS = [
        [(script[k][0], times[k][0], times[k][1]) for k in chunk] for chunk in chunks
    ]
    entries = []
    for n, chunk in enumerate(chunks):
        start = times[chunk[0]][0]
        end = times[chunk[-1]][1] + 0.12
        if n + 1 < len(chunks):
            end = min(end, times[chunks[n + 1][0]][0])
        text = " ".join(script[k][0] for k in chunk)
        entries.append(f"{n + 1}\n{_srt_time(start)} --> {_srt_time(max(end, start + 0.2))}\n{text}\n")
    return "\n".join(entries)


def generate_subtitles(
    audio_path: str, sentences: List[str], audio_clips: List[AudioFileClip], voice: str
) -> str:
    """
    Generates subtitles from a given audio file and returns the path to the subtitles.

    Args:
        audio_path (str): The path to the audio file to generate subtitles from.
        sentences (List[str]): all the sentences said out loud in the audio clips
        audio_clips (List[AudioFileClip]): all the individual audio clips which will make up the final audio track

    Returns:
        str: The path to the generated subtitles.
    """

    def equalize_subtitles(srt_path: str, max_chars: int = 10) -> None:
        # Equalize subtitles
        srt_equalizer.equalize_srt_file(srt_path, srt_path, max_chars)

    # Save subtitles
    SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
    subtitles_path = SUBTITLES_DIR / f"{uuid.uuid4()}.srt"

    if ASSEMBLY_AI_API_KEY is not None and ASSEMBLY_AI_API_KEY != "":
        log("[+] Creating subtitles using AssemblyAI", "info")
        subtitles = __generate_subtitles_assemblyai(audio_path, voice)
    else:
        aligned = False
        global LAST_CAPTION_CHUNKS
        LAST_CAPTION_CHUNKS = []
        try:
            log("[+] Creating word-timed subtitles (Whisper alignment)", "info")
            subtitles = __generate_subtitles_aligned(audio_path, sentences)
            aligned = True
        except Exception as err:
            log(f"[-] Word alignment failed, using sentence timing: {err}", "warning")
            subtitles = __generate_subtitles_locally(sentences, audio_clips)
        # print(colored("[-] Local subtitle generation has been disabled for the time being.", "red"))
        # print(colored("[-] Exiting.", "red"))
        # sys.exit(1)

    with open(subtitles_path, "w", encoding="utf-8") as file:
        file.write(subtitles)

    # Aligned captions are already short and word-timed; re-splitting would undo that
    if not (ASSEMBLY_AI_API_KEY is None or ASSEMBLY_AI_API_KEY == "") or not aligned:
        equalize_subtitles(str(subtitles_path))

    log("[+] Subtitles generated.", "success")

    return str(subtitles_path)


def combine_videos(
    video_paths: List[str],
    max_duration: int,
    max_clip_duration: int,
    threads: int,
    size=(1080, 1920),
) -> str:
    """
    Combines a list of videos into one video and returns the path to the combined video.

    Args:
        video_paths (List): A list of paths to the videos to combine.
        max_duration (int): The maximum duration of the combined video.
        max_clip_duration (int): The maximum duration of each clip.
        threads (int): The number of threads to use for the video processing.

    Returns:
        str: The path to the combined video.
    """
    video_id = uuid.uuid4()
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    combined_video_path = TEMP_DIR / f"{video_id}.mp4"

    if not video_paths:
        raise ValueError("No source videos were provided for concatenation.")

    max_duration = float(max_duration)
    max_clip_duration = float(max_clip_duration)

    # Required duration of each clip
    req_dur = max_duration / len(video_paths)

    log("[+] Combining videos...", "info")
    log(f"[+] Each clip will be maximum {req_dur} seconds long.", "info")

    clips = []
    tot_dur = 0
    # Add downloaded clips over and over until the duration of the audio (max_duration) has been reached
    while tot_dur < (max_duration - FRAME_EPSILON):
        progressed = False
        for video_path in video_paths:
            remaining = max_duration - tot_dur
            if remaining <= FRAME_EPSILON:
                break

            clip = VideoFileClip(video_path)
            clip = clip.without_audio()
            max_safe_source_duration = clip.duration - FRAME_EPSILON
            if max_safe_source_duration <= 0:
                clip.close()
                continue

            target_duration = min(req_dur, max_clip_duration, remaining)
            target_duration = min(target_duration, max_safe_source_duration)

            if target_duration <= 0:
                clip.close()
                continue

            if target_duration < clip.duration:
                clip = clip.subclipped(0, target_duration)
            clip = clip.with_fps(30)

            # Not all videos are same size,
            # so we need to resize them
            aspect = size[0] / size[1]
            if round((clip.w / clip.h), 4) < aspect:
                clip = clip.cropped(
                    width=clip.w,
                    height=round(clip.w / aspect),
                    x_center=clip.w / 2,
                    y_center=clip.h / 2,
                )
            else:
                clip = clip.cropped(
                    width=round(aspect * clip.h),
                    height=clip.h,
                    x_center=clip.w / 2,
                    y_center=clip.h / 2,
                )
            clip = clip.resized(new_size=size)

            clips.append(clip)
            tot_dur += clip.duration
            progressed = True

        if not progressed:
            raise RuntimeError("Could not reach target duration from source videos.")

    if not clips:
        raise RuntimeError("No valid clips were produced for concatenation.")

    final_clip = concatenate_videoclips(clips, method="compose")
    final_clip = final_clip.with_fps(30).with_duration(max_duration)
    try:
        final_clip.write_videofile(
            str(combined_video_path),
            threads=threads,
            fps=30,
            codec="libx264",
            preset="medium",
            audio=False,
        )
    finally:
        final_clip.close()
        for clip in clips:
            clip.close()

    return str(combined_video_path)


def _like_badge(path: str, width: int) -> None:
    """White round badge with a blue thumbs-up and LIKE, as a transparent PNG."""
    from PIL import ImageFont

    height = int(width * 0.42)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 2, fill=(255, 255, 255, 245),
                           outline=(20, 20, 20, 255), width=max(3, height // 20))
    blue = (6, 95, 212, 255)
    s = height * 0.62
    ox, oy = height * 0.28, (height - s) / 2
    # Thumbs-up: cuff, palm and raised thumb
    draw.rounded_rectangle((ox, oy + s * 0.42, ox + s * 0.22, oy + s), radius=int(s * 0.05), fill=blue)
    draw.rounded_rectangle((ox + s * 0.28, oy + s * 0.38, ox + s * 0.95, oy + s), radius=int(s * 0.12), fill=blue)
    draw.polygon([(ox + s * 0.30, oy + s * 0.42), (ox + s * 0.52, oy), (ox + s * 0.66, oy + s * 0.06),
                  (ox + s * 0.62, oy + s * 0.42)], fill=blue)
    font = ImageFont.truetype(str((FONTS_DIR / "bold_font.ttf").resolve()), int(height * 0.46))
    draw.text((ox + s * 1.15, height * 0.22), "LIKE", font=font, fill=(20, 20, 20, 255))
    image.save(path)


def _subscribe_button(path: str, width: int) -> None:
    """Red YouTube-style SUBSCRIBE button with a bell, as a transparent PNG."""
    from PIL import ImageFont

    height = int(width * 0.24)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 4, fill=(230, 33, 23, 255),
                           outline=(255, 255, 255, 255), width=max(4, height // 18))
    font = ImageFont.truetype(str((FONTS_DIR / "bold_font.ttf").resolve()), int(height * 0.5))
    text = "SUBSCRIBE"
    text_width = draw.textlength(text, font=font)
    bell = int(height * 0.42)
    x = (width - text_width - bell * 1.4) / 2
    draw.text((x, height * 0.2), text, font=font, fill="white")
    # Simple bell to the right of the word
    bx, by = x + text_width + bell * 0.4, (height - bell) / 2
    draw.pieslice((bx, by, bx + bell, by + bell * 1.5), 180, 360, fill="white")
    draw.rectangle((bx, by + bell * 0.72, bx + bell, by + bell * 0.82), fill="white")
    draw.ellipse((bx + bell * 0.38, by + bell * 0.82, bx + bell * 0.62, by + bell * 1.02), fill="white")
    image.save(path)


def _generate_video_ffmpeg(
    combined_video_path: str,
    tts_path: str,
    subtitles_path: str,
    subtitles_position: str,
    text_color: str,
    font_size: int,
) -> str:
    """Render the final video with ffmpeg's libass subtitle filter."""
    import subprocess

    import imageio_ffmpeg
    from PIL import ImageFont

    with VideoFileClip(str(combined_video_path)) as clip:
        frame_width, frame_height = clip.w, clip.h
    source_height = frame_height
    # Export with a 1440 px short side by default: YouTube serves 1440p/4K uploads
    # with its high-bitrate VP9 stream, which stays visibly sharper than a 1080p
    # upload even when watched at 1080p. VIDEO_OUTPUT_SHORT_SIDE=0 keeps the source size.
    short_side = int(os.getenv("VIDEO_OUTPUT_SHORT_SIDE", "1440") or 0)
    scale_filter = ""
    if short_side and short_side != min(frame_width, frame_height):
        factor = short_side / min(frame_width, frame_height)
        frame_width, frame_height = round(frame_width * factor / 2) * 2, round(frame_height * factor / 2) * 2
        scale_filter = f"scale={frame_width}:{frame_height}:flags=lanczos,"
    with AudioFileClip(str(tts_path)) as narration:
        duration = narration.duration

    font_path = (FONTS_DIR / "bold_font.ttf").resolve()
    font_name = ImageFont.truetype(str(font_path), 20).getname()[0]

    # SRT subtitles are laid out on a 288px-high canvas, so scale the pixel size
    # (font_size is in source pixels; libass scales the 288px canvas to any output size)
    ass_size = max(8, round(font_size * 288 / source_height))
    hex_color = (text_color or "#FFFFFF").lstrip("#").rjust(6, "F")
    ass_color = f"&H00{hex_color[4:6]}{hex_color[2:4]}{hex_color[0:2]}"
    # Always bottom-anchored (alignment 2 means the same in SSA and ASS) and
    # moved up with a vertical margin, on the 288px subtitle canvas
    vertical = (subtitles_position or "center,center").split(",")[-1].strip()
    margin = {
        "bottom": 28,
        "top": 288 - ass_size - 24,
        # letterbox picture spans y=560..1168 of 1920; sit near its bottom edge
        "letterbox": round(288 * (1920 - 1130) / 1920),
    }.get(vertical, 288 // 2 - ass_size // 2)
    style = (
        f"FontName={font_name},FontSize={ass_size},PrimaryColour={ass_color},"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,"
        f"Alignment=2,MarginV={margin}"
    )

    # libass filter paths are fragile on Windows, so run from the project root
    # with relative paths
    root = FONTS_DIR.parent
    output_path = TEMP_DIR / "output.mp4"
    rel = lambda path: os.path.relpath(str(path), str(root)).replace("\\", "/")
    # Light sharpening before the captions restores crisp outlines that zooming
    # and YouTube's re-compression soften
    if LAST_CAPTION_CHUNKS and os.getenv("CAPTION_STYLE", "highlight") == "highlight":
        # Karaoke captions: spoken word pops in the accent colour, power words stay lit
        ass_path = SUBTITLES_DIR / f"{uuid.uuid4()}.ass"
        _highlight_ass(
            LAST_CAPTION_CHUNKS, str(ass_path), frame_width, frame_height, font_name,
            round(font_size * frame_height / source_height), round(margin * frame_height / 288),
            text_color or "#FFE600",
        )
        captions = f"ass={rel(ass_path)}:fontsdir={rel(FONTS_DIR)}"
    else:
        captions = f"subtitles={rel(subtitles_path)}:fontsdir={rel(FONTS_DIR)}:force_style='{style}'"
    # Tragedies: darker, desaturated colour, and a memorial line over the last seconds
    grade = {
        "somber": "eq=saturation=0.72:brightness=-0.04:contrast=1.06,",
        # Suspense: dark night look, cold blue shadows, a vignette pulling the eye to the middle
        "night": "eq=saturation=0.82:brightness=-0.08:contrast=1.14,colorbalance=bs=0.10:bm=0.04:rh=-0.02,vignette=PI/4.5,",
    }.get(os.getenv("VIDEO_GRADE", ""), "")
    # On-screen text is drawn as PNG layers and overlaid (GitHub's ffmpeg build has no drawtext):
    # (png, y, start, end, fade_in, fade_out)
    text_layers = []
    memorial = ""
    if os.getenv("MEMORIAL_TEXT", "").strip():
        import textwrap

        # Short lines, each centred on its own, so the memorial never runs off the screen
        width_chars = 20 if frame_height > frame_width else 34
        lines = textwrap.wrap(os.getenv("MEMORIAL_TEXT").strip(), width_chars)
        start_memo = max(0.0, duration - 4.5)
        size = int(frame_height * 0.026)
        memorial = f",drawbox=x=0:y=0:w=iw:h=ih:color=black@0.55:t=fill:enable='gte(t,{start_memo:.2f})'"
        layer = TEMP_DIR / f"{uuid.uuid4()}_memorial.png"
        _text_layer(str(layer), [(line, "#FFFFFF") for line in lines], size, frame_width, border=False)
        text_layers.append((layer, int(frame_height * 0.27), start_memo, duration, 0.8, 0))
    # Hook headline: big bold 2-4 words over the first ~2.2 s (the first frame is the feed cover)
    if os.getenv("HOOK_TEXT", "").strip():
        import textwrap

        hook_lines = textwrap.wrap(os.getenv("HOOK_TEXT").strip().upper(), 14 if frame_height > frame_width else 24)[:3]
        hook_size = int(frame_height * (0.062 if frame_height > frame_width else 0.08))
        accent = text_color or "#FFE600"
        top = 0.14 if frame_height > frame_width else 0.12
        layer = TEMP_DIR / f"{uuid.uuid4()}_hook.png"
        # Last line in the accent colour
        _text_layer(str(layer), [(line, accent if n == len(hook_lines) - 1 else "#FFFFFF")
                                 for n, line in enumerate(hook_lines)], hook_size, frame_width)
        text_layers.append((layer, int(frame_height * top), 0.0, 2.2, 0, 0.3))
    # Story cues (suspense): on-screen text and glitches at the exact spoken words
    import json as _json

    glitch = ""
    cues_all = _json.loads(os.getenv("STORY_CUES", "") or "[]")
    for cue in cues_all:
        at = _phrase_time(cue.get("phrase", ""))
        if at is None:
            continue
        if cue["kind"] == "glitch":
            window = f"between(t,{at:.2f},{at + 0.35:.2f})"
            glitch += f"rgbashift=rh=14:bh=-14:enable='{window}',noise=alls=45:allf=t:enable='{window}',"
        elif cue["kind"] == "options" and cue.get("value"):
            # OPTIONS: <spoken words> | A text | B text | C text  -> shown until the ANSWER cue that follows
            labels = [o.strip() for o in cue["value"].split("|") if o.strip()]
            later = [_phrase_time(c.get("phrase", "")) for c in cues_all if c["kind"] == "answer"]
            end = min([x for x in later if x is not None and x > at] or [at + 12])
            layer = TEMP_DIR / f"{uuid.uuid4()}_options.png"
            _choice_card(str(layer), int(frame_width * 0.86), *labels)
            text_layers.append((layer, int(frame_height * 0.12), at, end + 0.4, 0.2, 0.2))
        elif cue["kind"] == "countdown":
            # COUNTDOWN: <spoken words> | 5  -> big 5, 4, 3, 2, 1 starting just after those words
            seconds = int(float(cue.get("value") or 5))
            start = (_phrase_end(cue.get("phrase", "")) or at) + 0.15  # right after the narrator stops
            for k in range(seconds):
                layer = TEMP_DIR / f"{uuid.uuid4()}_count.png"
                _text_layer(str(layer), [(str(seconds - k), "#FFE600")], int(frame_height * 0.16), frame_width)
                text_layers.append((layer, int(frame_height * 0.40), start + k, start + k + 0.95, 0.05, 0.15))
        elif cue["kind"] == "answer" and cue.get("value"):
            layer = TEMP_DIR / f"{uuid.uuid4()}_answer.png"
            _text_layer(str(layer), [(f"ANSWER: {cue['value'].upper()}", "#2BD34A")], int(frame_height * 0.075), frame_width)
            text_layers.append((layer, int(frame_height * 0.40), at, at + 2.6, 0.1, 0.25))
        elif cue["kind"] == "chat" and cue.get("value"):
            # CHAT: <spoken words> | them: <message>  (or me: <message>), a text-message bubble
            sender, _, message = cue["value"].partition(":")
            layer = TEMP_DIR / f"{uuid.uuid4()}_chat.png"
            _chat_bubble(str(layer), message.strip(), sender.strip().lower() == "me", frame_width)
            text_layers.append((layer, int(frame_height * 0.27), at, at + 4.0, 0.15, 0.25))
        elif cue["kind"] == "cue" and cue.get("value"):
            colour = "#FF3B30" if any(ch.isdigit() for ch in cue["value"]) else "#FFFFFF"
            layer = TEMP_DIR / f"{uuid.uuid4()}_cue.png"
            _text_layer(str(layer), [(cue["value"].upper(), colour)], int(frame_height * 0.07), frame_width)
            text_layers.append((layer, int(frame_height * 0.24), at, at + 2.4, 0.15, 0.25))
    video_filter = f"[0:v]{scale_filter}{grade}{glitch}unsharp=5:5:0.6:5:5:0.0,{captions}{memorial}[captioned]"
    inputs = ["-i", rel(combined_video_path), "-i", rel(tts_path)]
    output_label = "[captioned]"

    # Sound design: whoosh on scene changes, boom / hit / riser on power words
    audio_map = "1:a"
    if os.getenv("SOUND_EFFECTS", "true").strip().lower() not in ("0", "false", "no"):
        try:
            import sfx
            import visuals as _visuals

            # Rationed: 2-3 heavy hits in a Short (about one per 40 s in long videos), so each one lands
            limit = int(os.getenv("SFX_WORD_LIMIT", "0")) or (3 if duration <= 180 else max(3, int(duration // 40)))
            events = sfx.strongest(sfx.word_events(LAST_CAPTION_CHUNKS), limit) + [
                (cut, "whoosh") for cut in getattr(_visuals, "LAST_SCENE_CUTS", [])
            ]
            # No heavy impacts (boom, blast, hit): the owner found the "gbam" sounds annoying.
            # Only soft whooshes on scene changes and the odd riser are left.
            events = [(t, kind) for t, kind in events if kind in ("whoosh", "riser")]
            if os.getenv("SFX_MODE") == "tragic":
                # Respect: nothing over a tragedy
                events = []
            elif os.getenv("SFX_MODE") == "suspense":
                # Suspense lives on quiet: no whooshes on every cut, only a rare riser
                events = [(t, kind) for t, kind in events if kind == "riser"]
            music_track = None
            if os.getenv("SFX_MODE") == "suspense":
                # Thriller soundscape (drone, ticking clock, heartbeat) with silence after the key line
                silences = []
                for window in os.getenv("SUSPENSE_SILENCE", "").split(","):
                    if "-" in window:
                        a, b = window.split("-")
                        silences.append((float(a), float(b)))
                cue_sounds, plan = [], []
                for cue in _json.loads(os.getenv("STORY_CUES", "") or "[]"):
                    at = _phrase_time(cue.get("phrase", ""))
                    if at is not None and cue["kind"] in ("sound", "glitch"):
                        cue_sounds.append((at, cue.get("value") if cue["kind"] == "sound" else "glitch"))
                    elif at is not None and cue["kind"] == "countdown":
                        end = (_phrase_end(cue.get("phrase", "")) or at) - 0.45  # sfx adds 0.6 s
                        cue_sounds.append((end, f"countdown{int(float(cue.get('value') or 5))}"))
                    elif at is not None and cue["kind"] == "music":
                        plan.append((at, cue.get("value", "")))
                # MUSIC lines: each section runs until the next one starts (the first from 0 s)
                plan.sort()
                segments = [(0.0 if i == 0 else at, plan[i + 1][0] if i + 1 < len(plan) else duration, name)
                            for i, (at, name) in enumerate(plan)]
                if segments:
                    active = [(a, b) for a, b, name in segments if "drone" in name] or [(0.0, 0.0)]
                else:
                    active = [(0.0, duration)]
                track = sfx.suspense_bed(duration, silences, TEMP_DIR / "sfx", cue_sounds, active)
                if segments:
                    music_track = sfx.music_plan(segments, duration, FONTS_DIR.parent / "Songs", TEMP_DIR / "sfx")
                    log(f"[+] Music plan: {[(round(a, 1), round(b, 1), m) for a, b, m in segments]}", "info")
                events = []
            if events or os.getenv("SFX_MODE") == "suspense":
                if events:
                    track = sfx.build_track(events, duration, TEMP_DIR / "sfx")
                inputs += ["-i", rel(track)]
                sfx_index = len([x for x in inputs if x == "-i"]) - 1
                music_mix, music_count = "", 0
                if music_track:
                    inputs += ["-i", rel(music_track)]
                    music_mix, music_count = f"[{sfx_index + 1}:a]", 1
                video_filter += (
                    f";[1:a]aresample=48000,aformat=channel_layouts=stereo[voice];"
                    f"[voice][{sfx_index}:a]{music_mix}amix=inputs={2 + music_count}:normalize=0:duration=first[mixed]"
                )
                audio_map = "[mixed]"
        except Exception as sfx_err:
            log(f"[-] Sound effects skipped: {sfx_err}", "warning")

    for layer, y, start, end, fade_in, fade_out in text_layers:
        length = max(0.1, end - start)
        inputs += ["-loop", "1", "-t", f"{length:.2f}", "-i", rel(layer)]
        index = len([x for x in inputs if x == "-i"]) - 1
        fades = "format=rgba"
        if fade_in:
            fades += f",fade=t=in:st=0:d={fade_in}:alpha=1"
        if fade_out:
            fades += f",fade=t=out:st={max(0.0, length - fade_out):.2f}:d={fade_out}:alpha=1"
        video_filter += (
            f";[{index}:v]{fades},setpts=PTS+{start:.2f}/TB[text{index}];"
            f"{output_label}[text{index}]overlay=x=(W-w)/2:y={y}:eof_action=pass:"
            f"enable='between(t,{start:.2f},{end:.2f})'[texted{index}]"
        )
        output_label = f"[texted{index}]"

    # Silent LIKE badge right after the Short's heaviest moment (skipped for tragedies)
    if frame_height > frame_width and os.getenv("SFX_MODE") != "tragic" and os.getenv("LIKE_BADGE", "true") != "false":
        try:
            import sfx

            moments = [(t, k) for t, k in sfx.strongest(sfx.word_events(LAST_CAPTION_CHUNKS), 1)
                       if 0.2 * duration <= t <= 0.8 * duration]
            like_at = (moments[0][0] + 0.9) if moments else 0.55 * duration
            badge = TEMP_DIR / "like_badge.png"
            _like_badge(str(badge), int(frame_width * 0.34))
            inputs += ["-loop", "1", "-t", "1.6", "-i", rel(badge)]
            like_index = len([x for x in inputs if x == "-i"]) - 1
            video_filter += (
                f";[{like_index}:v]format=rgba,fade=t=in:st=0:d=0.15:alpha=1,fade=t=out:st=1.25:d=0.35:alpha=1,"
                f"setpts=PTS+{like_at:.2f}/TB[like];"
                f"{output_label}[like]overlay=x=W-w-{int(frame_width * 0.05)}:y={int(frame_height * 0.58)}:"
                f"eof_action=pass:enable='between(t,{like_at:.2f},{like_at + 1.6:.2f})'[liked]"
            )
            output_label = "[liked]"
        except Exception as like_err:
            log(f"[-] Like badge skipped: {like_err}", "warning")

    # "What would you do?" ending: two labelled choices from the moment the dilemma is put
    if os.getenv("CHOICE_A", "").strip() and os.getenv("CHOICE_B", "").strip():
        try:
            words = [(w.lower().strip(".,!?"), s) for chunk in LAST_CAPTION_CHUNKS for w, s, _ in chunk]
            choice_at = next((s for w, s in words if w in ("choices", "choice", "options")), max(0.0, duration - 12))
            card = TEMP_DIR / "choice_card.png"
            _choice_card(str(card), int(frame_width * 0.86), os.getenv("CHOICE_A"), os.getenv("CHOICE_B"))
            inputs += ["-i", rel(card)]
            card_index = len([x for x in inputs if x == "-i"]) - 1
            video_filter += (
                f";[{card_index}:v]format=rgba[choice];"
                f"{output_label}[choice]overlay=x=(W-w)/2:y={int(frame_height * 0.12)}:enable='gte(t,{choice_at:.2f})'[chosen]"
            )
            output_label = "[chosen]"
        except Exception as choice_err:
            log(f"[-] Choice card skipped: {choice_err}", "warning")

    # SUBSCRIBE button for the last seconds, where viewers decide to follow
    if os.getenv("SUBSCRIBE_BUTTON", "true").strip().lower() not in ("0", "false", "no"):
        button_width = int(frame_width * (0.62 if frame_height > frame_width else 0.26))
        button_path = TEMP_DIR / "subscribe_button.png"
        _subscribe_button(str(button_path), button_width)
        if frame_height > frame_width:
            # Shorts: centered in the empty area under the letterbox picture
            x, y = f"(W-w)/2", f"{int(frame_height * 0.72)}"
        else:
            x, y = f"W-w-{int(frame_width * 0.03)}", f"H-h-{int(frame_height * 0.18)}"
        start = max(0.0, duration - 3.5)
        inputs += ["-i", rel(button_path)]
        button_index = len([x for x in inputs if x == "-i"]) - 1
        video_filter += f";{output_label}[{button_index}:v]overlay=x={x}:y={y}:enable='gte(t,{start:.2f})'[final]"
        output_label = "[final]"

    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            *inputs,
            "-filter_complex", video_filter,
            "-map", output_label, "-map", audio_map,
            # Higher quality source gives YouTube more detail to keep
            "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
            rel(output_path),
        ],
        check=True,
        cwd=str(root),
    )
    return "output.mp4"


def generate_video(
    combined_video_path: str,
    tts_path: str,
    subtitles_path: str,
    threads: int,
    subtitles_position: str,
    text_color: str,
    font_size: int = 100,
) -> str:
    """
    This function creates the final video, with subtitles and audio.

    Args:
        combined_video_path (str): The path to the combined video.
        tts_path (str): The path to the text-to-speech audio.
        subtitles_path (str): The path to the subtitles.
        threads (int): The number of threads to use for the video processing.
        subtitles_position (str): The position of the subtitles.

    Returns:
        str: The path to the final video.
    """
    # Fast path: burn subtitles with ffmpeg/libass. Much faster and lighter on
    # memory than compositing every frame in MoviePy, which matters for long videos.
    try:
        return _generate_video_ffmpeg(
            combined_video_path, tts_path, subtitles_path, subtitles_position, text_color, font_size
        )
    except Exception as err:
        log(f"[-] ffmpeg render failed, falling back to MoviePy: {err}", "warning")

    # Make a generator that returns a TextClip when called with consecutive
    font_path = str((FONTS_DIR / "bold_font.ttf").resolve())
    generator = lambda txt: TextClip(
        font=font_path,
        text=txt,
        font_size=font_size,
        color=text_color,
        stroke_color="black",
        stroke_width=5,
    )

    # Split the subtitles position into horizontal and vertical
    horizontal_subtitles_position, vertical_subtitles_position = (
        subtitles_position.split(",")
    )

    # Burn the subtitles into the video
    subtitles = SubtitlesClip(subtitles_path, make_textclip=generator)
    subtitle_vertical_position = vertical_subtitles_position
    if vertical_subtitles_position == "top":
        subtitle_vertical_position = 80

    base_video = VideoFileClip(str(combined_video_path))
    audio = AudioFileClip(tts_path)
    target_duration = min(base_video.duration, audio.duration)

    result = CompositeVideoClip(
        [
            base_video.subclipped(0, target_duration),
            subtitles.with_position(
                (horizontal_subtitles_position, subtitle_vertical_position)
            ).with_duration(target_duration),
        ]
    )

    # Clamp audio/video to exactly the same duration to avoid end-frame overreads.
    result = result.with_audio(audio.subclipped(0, target_duration)).with_duration(
        target_duration
    )

    output_path = TEMP_DIR / "output.mp4"
    try:
        result.write_videofile(
            str(output_path),
            threads=threads or 2,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
        )
    finally:
        result.close()
        subtitles.close()
        audio.close()
        base_video.close()

    return "output.mp4"
