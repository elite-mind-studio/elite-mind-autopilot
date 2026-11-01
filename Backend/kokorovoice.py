import os
import re
import urllib.request
from pathlib import Path

from utils import PROJECT_ROOT

MODEL_DIR = Path(os.getenv("KOKORO_MODEL_DIR", str(PROJECT_ROOT / "models")))
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")

# Pause after a sentence, by how it ends (seconds). Questions and exclamations
# get a longer beat so the line lands; "..." / em dashes inside a sentence are
# dramatic breaks.
END_PAUSE = {"?": 0.55, "!": 0.45, ".": 0.3}
DEFAULT_END_PAUSE = 0.2
BREAK_PAUSE = 0.6
BREAK_PATTERN = re.compile(r"\s*(?:\.\.\.|…|—|–| - )\s*")

_kokoro = None


def _load():
    """Load the Kokoro model once, downloading it (~350 MB) on first use."""
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            if not (MODEL_DIR / name).exists():
                urllib.request.urlretrieve(MODEL_URL + name, MODEL_DIR / name)
        _kokoro = Kokoro(str(MODEL_DIR / FILES[0]), str(MODEL_DIR / FILES[1]))
    return _kokoro


def kokoro_tts_save(text: str, voice: str, filename: str) -> None:
    """
    Synthesize `text` with a local Kokoro voice (e.g. "am_onyx", "bm_george")
    and save it as audio at `filename`, with natural pacing: dramatic breaks
    at "..." or em dashes, a slightly slower delivery for questions, and a
    short pause after the sentence so the next line doesn't run into it.

    Args:
        text (str): The text to speak.
        voice (str): Kokoro voice name; "b*" voices are British, others American.
        filename (str): Output path (written as WAV; ffmpeg reads it by content).
    """
    import numpy as np
    import soundfile as sf

    kokoro = _load()
    lang = "en-gb" if voice.startswith("b") else "en-us"
    stripped = text.strip()
    # Questions are delivered a touch slower so they sound deliberate
    base_speed = float(os.getenv("KOKORO_SPEED", "1.0"))
    speed = base_speed * (0.94 if stripped.endswith("?") else 1.0)
    # "Number two." list markers get a clear stop on both sides
    is_marker = bool(re.match(r"^number\s+\w+[.!:]?$", stripped, re.IGNORECASE))

    parts = [p for p in BREAK_PATTERN.split(stripped) if p.strip()] or [stripped]
    audio, sample_rate = [], 24000
    lead = 0.35 if is_marker else 0.0
    for i, part in enumerate(parts):
        samples, sample_rate = kokoro.create(part, voice=voice, speed=speed, lang=lang)
        audio.append(samples)
        if i < len(parts) - 1:
            audio.append(np.zeros(int(sample_rate * BREAK_PAUSE), dtype=samples.dtype))

    if is_marker:
        end_pause = 0.45
    elif stripped.endswith(("...", "…")):
        end_pause = BREAK_PAUSE
    else:
        end_pause = END_PAUSE.get(stripped[-1:], DEFAULT_END_PAUSE)
    audio.append(np.zeros(int(sample_rate * end_pause), dtype=audio[0].dtype))
    if lead:
        audio.insert(0, np.zeros(int(sample_rate * lead), dtype=audio[0].dtype))
    sf.write(filename, np.concatenate(audio), sample_rate, format="WAV")
