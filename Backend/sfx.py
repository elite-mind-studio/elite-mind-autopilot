"""
Sound design for retention: synthesized sound effects (no samples, so no
copyright) placed on the narration timeline.

  whoosh : every scene change
  boom   : money / numbers ("$118", "five miles", "a million")
  blast  : the strongest alarm words ("emergency", "explosion", "war", "crash", "crisis")
  hit    : death and danger ("killed", "trapped", "warning") - darker and quieter, out of respect
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
    # Phone speakers can't play much below ~150 Hz, so every impact layers a mid-range
    # "body" (150-600 Hz) and a short transient click on top of the sub-bass.
    "whoosh": "anoisesrc=d=0.5:c=pink:a=0.9:r=48000,bandpass=f=1800:width_type=o:w=2.5,"
              "afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.2",
    "boom": "aevalsrc='0.7*sin(2*PI*(55+80*exp(-7*t))*t)*exp(-3*t)"
            "+0.55*sin(2*PI*(180+120*exp(-10*t))*t)*exp(-6*t)"
            "+0.5*(random(0)-0.5)*exp(-40*t)':d=1.4:s=48000,lowpass=f=4000",
    "hit": "aevalsrc='0.55*sin(2*PI*(90+60*exp(-8*t))*t)*exp(-4*t)"
           "+0.8*sin(2*PI*240*t)*exp(-6*t)+0.55*(random(0)-0.5)*exp(-25*t)':d=1.3:s=48000,"
           "lowpass=f=3000,aecho=0.6:0.4:120:0.35",
    "riser": "aevalsrc='0.45*sin(2*PI*(300+1600*t*t)*t)*(t/0.7)+0.25*(random(0)-0.5)*(t/0.7)':d=0.7:s=48000,"
             "highpass=f=250,afade=t=out:st=0.62:d=0.08",
    # Explosion-style blast: a loud noise burst with body, then a rumble tail
    "blast": "aevalsrc='0.75*sin(2*PI*(45+110*exp(-6*t))*t)*exp(-1.8*t)"
             "+0.5*sin(2*PI*(160+200*exp(-9*t))*t)*exp(-4*t)"
             "+0.75*(random(0)-0.5)*exp(-4*t)':d=2.4:s=48000,lowpass=f=3500,afade=t=out:st=1.8:d=0.6",
}
# Where the sound's peak sits relative to its start (the riser builds up to the word)
LEAD = {"whoosh": 0.22, "boom": 0.0, "hit": 0.0, "riser": 0.62, "blast": 0.0}
VOLUME = {"whoosh": 0.5, "boom": 0.95, "hit": 1.0, "riser": 0.6, "blast": 1.0}

MONEY = re.compile(
    r"(\d|\$|%|dollar|million|billion|trillion|thousand|hundred|percent|"
    r"^(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b)",
    re.I,
)
# The strongest alarm words get the big blast
BLAST = re.compile(
    r"^(emergenc|explosion|explod|blast|crash|disaster|attack|war$|wars$|crisis|collapse|chaos|panic|"
    r"catastroph|destroy|hacked|stolen|bomb|invasion|meltdown)", re.I)
# Death and danger get a darker, quieter hit (never an explosion over a tragedy)
DANGER = re.compile(r"^(killed|kill|dead|died|death|trapped|warning|danger|fire|missing)", re.I)
TWIST = re.compile(r"^(secret|secretly|nobody|truth|lie|lies|lied|never|shocking|hidden|real|actually|but)$", re.I)


def word_events(chunks: List[list], min_gap: float = 1.6) -> List[Tuple[float, str]]:
    """(time, sound) for power words, at most one every `min_gap` seconds."""
    # A line about deaths gets the dark hit first; then blast, twist, money
    priority = {"hit": 0, "blast": 1, "riser": 2, "boom": 3}
    events, last = [], -10.0
    for chunk in chunks:
        found = []
        for n, (word, start, _) in enumerate(chunk):
            bare = re.sub(r"[^\w$%-]", "", word)
            kind = ("hit" if DANGER.search(bare) else "blast" if BLAST.search(bare)
                    else "riser" if TWIST.match(bare) else "boom" if MONEY.search(bare) else None)
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
