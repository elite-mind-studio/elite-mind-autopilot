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
VOLUME = {"whoosh": 0.12, "boom": 0.95, "hit": 1.0, "riser": 0.6, "blast": 1.0}

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


# How heavy each sound is: the strongest moments win when sounds are rationed
WEIGHT = {"blast": 4, "hit": 3, "boom": 2, "riser": 1}


def strongest(events: List[Tuple[float, str]], limit: int, min_spacing: float = 4.0) -> List[Tuple[float, str]]:
    """Keep only the `limit` heaviest power-word sounds, at least `min_spacing` s apart."""
    chosen = []
    for t, kind in sorted(events, key=lambda e: (-WEIGHT.get(e[1], 0), e[0])):
        if len(chosen) >= limit:
            break
        if all(abs(t - c) >= min_spacing for c, _ in chosen):
            chosen.append((t, kind))
    return sorted(chosen)


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


def suspense_bed(duration: float, silences: List[Tuple[float, float]], work: Path,
                  cues: List[Tuple[float, str]] = (), active: List[Tuple[float, float]] = ()) -> Path:
    """
    A thriller soundscape instead of music: a dark breathing drone, a clock that ticks
    faster as time runs out, a quiet heartbeat building in the last part, and total
    silence in the given (start, end) windows. No booms.
    """
    import wave

    import numpy as np

    work.mkdir(parents=True, exist_ok=True)
    n = int(duration * RATE)
    t = np.arange(n) / RATE
    rng = np.random.default_rng(7)

    # Drone: low fifths that slowly swell, plus soft rumble (smoothed noise)
    breath = 0.55 + 0.45 * np.sin(2 * np.pi * 0.07 * t - np.pi / 2)
    drone = (np.sin(2 * np.pi * 55 * t) + 0.6 * np.sin(2 * np.pi * 82.4 * t + 1.0)
             + 0.35 * np.sin(2 * np.pi * 110.3 * t + 2.0)) * breath
    rumble = np.convolve(rng.standard_normal(n), np.ones(400) / 400, mode="same") * 6
    bed = 0.012 * drone + 0.007 * rumble  # owner: background 60% quieter

    # A cold high tone that creeps in over the last 40%
    climax = np.clip((t / duration - 0.6) / 0.4, 0, 1)
    bed += 0.012 * climax * (np.sin(2 * np.pi * 1318.5 * t) + np.sin(2 * np.pi * 1975.5 * t)) \
        * (0.6 + 0.4 * np.sin(2 * np.pi * 4.5 * t))

    def k_time(length: int) -> "np.ndarray":
        return np.arange(length) / RATE

    def place(sound: "np.ndarray", at: float, gain: float) -> None:
        start = int(at * RATE)
        end = min(n, start + len(sound))
        if 0 <= start < n:
            bed[start:end] += gain * sound[: end - start]

    # Clock tick: a tiny bright click, from a quarter of the way in, speeding up near the end
    k = np.arange(int(0.025 * RATE)) / RATE
    tick = np.sin(2 * np.pi * 2400 * k) * np.exp(-k * 260)
    at = duration * 0.22
    while at < duration - 0.5:
        place(tick, at, 0.035)
        at += 1.0 if at < duration * 0.75 else 0.6

    # Heartbeat in the last 40%: a soft "lub-dub", quiet, getting a little faster
    h = np.arange(int(0.16 * RATE)) / RATE
    thump = np.sin(2 * np.pi * 48 * h) * np.exp(-h * 28)
    at = duration * 0.6
    while at < duration - 0.8:
        place(thump, at, 0.12)
        place(thump, at + 0.22, 0.08)
        at += 1.05 - 0.35 * (at - duration * 0.6) / (duration * 0.4)

    # Music plan: drone, ticks and heartbeat only inside the "tension" sections
    if active:
        mask = np.zeros(n)
        edge = int(1.2 * RATE)
        for start, end in active:
            a, b = max(0, int(start * RATE)), min(n, int(end * RATE))
            if b > a:
                mask[a:b] = 1
                mask[a:min(b, a + edge)] *= np.linspace(0, 1, min(b, a + edge) - a)
                mask[max(a, b - edge):b] *= np.linspace(1, 0, b - max(a, b - edge))
        bed *= mask

    # Story sound cues: phone buzz, cash counter, digital glitch, rain, breathing
    for at, kind in cues:
        if kind == "vibrate":  # three short phone buzzes
            b = np.arange(int(0.32 * RATE)) / RATE
            buzz = np.sign(np.sin(2 * np.pi * 160 * b)) * 0.6 * np.minimum(1, b * 40) * np.minimum(1, (0.32 - b) * 40)
            for k in range(3):
                place(buzz, at + k * 0.5, 0.18)
        elif kind == "cash":  # a fast money-counter flutter
            for k in range(16):
                place(tick * np.sin(2 * np.pi * 3600 * k_time(len(tick))), at + k * 0.05, 0.10)
        elif kind.startswith("countdown"):  # one clear tick per second ("countdown5")
            for k in range(int(kind[9:] or 5)):
                place(tick, at + 0.6 + k, 0.12)
        elif kind == "rain":  # soft rain on a window for about 14 s, with a few drips
            r = np.convolve(rng.standard_normal(int(14 * RATE)), np.ones(30) / 30, mode="same")
            secs = np.arange(len(r)) / RATE
            r *= np.minimum(1, secs / 2) * np.minimum(1, (len(r) / RATE - secs) / 3)
            place(r, at, 0.045)
            for _ in range(18):
                place(tick * 0.6, at + rng.random() * 13, 0.05)
        elif kind == "breath":  # four slow, shaky breaths
            for k in range(4):
                length = int(1.7 * RATE)
                env = np.sin(np.linspace(0, np.pi, length)) ** 2
                noise = np.convolve(rng.standard_normal(length), np.ones(60) / 60, mode="same")
                place(noise * env * 3, at + k * 2.1, 0.06)
        elif kind == "glitch":  # a short burst of digital crackle
            g = rng.standard_normal(int(0.3 * RATE)) * (rng.random(int(0.3 * RATE)) > 0.7)
            place(g, at, 0.05)

    # Silence windows (after the key line): fade everything out and back in
    gate = np.ones(n)
    ramp = int(0.08 * RATE)
    for start, end in silences:
        a, b = int(start * RATE), int(end * RATE)
        gate[max(0, a):max(0, b)] = 0
        gate[max(0, a - ramp):max(0, a)] *= np.linspace(1, 0, max(0, a) - max(0, a - ramp))
        gate[b:min(n, b + ramp)] *= np.linspace(0, 1, min(n, b + ramp) - b)
    bed *= gate
    bed[: int(1.5 * RATE)] *= np.linspace(0, 1, int(1.5 * RATE))  # fade in

    pcm = (np.clip(bed, -1, 1) * 32767).astype(np.int16)
    out = work / "suspense_bed.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())
    return out


def music_plan(segments: List[Tuple[float, float, str]], duration: float, songs: Path, work: Path) -> Path:
    """
    Different music for each part of a story: (start, end, track) with soft crossfades.
    track is a file name in Songs/ without .mp3, optionally "+drone"; "silence" means no music.
    """
    work.mkdir(parents=True, exist_ok=True)
    out = work / "music_plan.wav"
    inputs, chains = [], []
    for start, end, track in segments:
        name = track.split("+")[0].strip()
        path = songs / f"{name}.mp3"
        if name.lower() == "silence" or not path.exists() or end <= start:
            continue
        length = end - start + 1.5
        k = len(chains)
        inputs += ["-stream_loop", "-1", "-i", str(path)]
        # Auto-level each section well under the voice (about -16 LUFS): piano -26, dark tension -29
        level = "loudnorm=I=-37:LRA=7:TP=-8" if "drone" in track else "loudnorm=I=-34:LRA=7:TP=-8"
        delay = int(start * 1000)
        chains.append(f"[{k}:a]atrim=0:{length:.2f},afade=t=in:d=1.2,afade=t=out:st={max(0.0, length - 1.6):.2f}:d=1.6,"
                      f"{level},aresample={RATE},aformat=channel_layouts=stereo,adelay={delay}|{delay}[m{k}]")
    if not chains:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={RATE}:cl=stereo",
                        "-t", f"{duration:.2f}", str(out)], check=True)
        return out
    mix = "".join(f"[m{k}]" for k in range(len(chains)))
    graph = ";".join(chains) + f";{mix}amix=inputs={len(chains)}:normalize=0,apad,atrim=0:{duration:.2f}[out]"
    subprocess.run([FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-ar", str(RATE), str(out)],
                   check=True)
    return out
