"""
Sound design for retention: synthesized sound effects (no samples, so no
copyright) placed on the narration timeline.

  whoosh : every scene change
  boom   : money / numbers ("$118", "five miles", "a million")
  hit    : danger words ("killed", "crash", "war", "trapped")
  riser  : twists and secrets ("secret", "nobody", "the truth") - peaks on the word
"""

import re
import subprocess
from pathlib import Path
from typing import List, Tuple

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
RATE = 48000

SOUNDS = {
    "whoosh": "anoisesrc=d=0.5:c=pink:a=0.7:r=48000,highpass=f=350,lowpass=f=5000,"
              "afade=t=in:d=0.28:curve=exp,afade=t=out:st=0.28:d=0.22",
    "boom": "aevalsrc='0.95*sin(2*PI*(42+70*exp(-7*t))*t)*exp(-3.2*t)':d=1.5:s=48000,lowpass=f=900",
    "hit": "aevalsrc='0.8*sin(2*PI*68*t)*exp(-4.5*t)+0.35*(random(0)-0.5)*exp(-14*t)':d=1.1:s=48000,lowpass=f=2500",
    "riser": "aevalsrc='0.4*sin(2*PI*(180+1300*t*t)*t)*(t/0.7)':d=0.7:s=48000,afade=t=out:st=0.6:d=0.1",
}
# Where the sound's peak sits relative to its start (the riser builds up to the word)
LEAD = {"whoosh": 0.22, "boom": 0.0, "hit": 0.0, "riser": 0.62}
VOLUME = {"whoosh": 0.35, "boom": 0.75, "hit": 0.6, "riser": 0.3}

MONEY = re.compile(
    r"(\d|\$|%|dollar|million|billion|trillion|thousand|hundred|percent|"
    r"^(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b)",
    re.I,
)
DANGER = re.compile(r"^(killed|kill|dead|died|death|crash|crashed|war|attack|attacked|trapped|warning|danger|bomb|explod|fire)", re.I)
TWIST = re.compile(r"^(secret|secretly|nobody|truth|lie|lies|lied|never|shocking|hidden|real|actually|but)$", re.I)


def word_events(chunks: List[list], min_gap: float = 1.6) -> List[Tuple[float, str]]:
    """(time, sound) for power words, at most one every `min_gap` seconds."""
    priority = {"hit": 0, "riser": 1, "boom": 2}  # a tragedy line gets the dark hit, not a money boom
    events, last = [], -10.0
    for chunk in chunks:
        found = []
        for n, (word, start, _) in enumerate(chunk):
            bare = re.sub(r"[^\w$%-]", "", word)
            kind = "hit" if DANGER.search(bare) else "riser" if TWIST.match(bare) else "boom" if MONEY.search(bare) else None
            # "but" only counts at the start of a caption (a turn in the story)
            if kind == "riser" and bare.lower() == "but" and n != 0:
                kind = None
            if kind:
                found.append((priority[kind], start, kind))
        if found:
            _, start, kind = min(found)
            if start - last >= min_gap:
                events.append((start, kind))
                last = start
    return events


def build_track(events: List[Tuple[float, str]], duration: float, work: Path) -> Path:
    """Mix every event into one WAV as long as the narration."""
    work.mkdir(parents=True, exist_ok=True)
    for name, source in SOUNDS.items():
        path = work / f"{name}.wav"
        if not path.exists():
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-ac", "2", str(path)], check=True)
    out = work / "sfx_track.wav"
    events = [(t, k) for t, k in events if 0 <= t - LEAD[k] < duration]
    if not events:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={RATE}:cl=stereo",
                        "-t", f"{duration:.2f}", str(out)], check=True)
        return out
    inputs, chains = [], []
    for i, (t, kind) in enumerate(events):
        inputs += ["-i", str(work / f"{kind}.wav")]
        delay = int(max(0.0, t - LEAD[kind]) * 1000)
        chains.append(f"[{i}:a]volume={VOLUME[kind]},adelay={delay}|{delay}[e{i}]")
    mix = "".join(f"[e{i}]" for i in range(len(events)))
    graph = ";".join(chains) + f";{mix}amix=inputs={len(events)}:normalize=0,apad,atrim=0:{duration:.2f}[out]"
    subprocess.run([FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-ar", str(RATE), str(out)],
                   check=True)
    return out
