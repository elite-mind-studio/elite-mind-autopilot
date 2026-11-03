"""
Chatterbox TTS (Resemble AI, MIT licence): natural, expressive narration that
keeps one channel voice by cloning a short reference clip (voices/<name>.wav).

Runs in its own Python 3.11 environment because Chatterbox pins its own torch
(see the "Install Chatterbox" workflow steps); the pipeline calls it once per
video with every sentence, so the model loads only once.

Usage:
    python Backend/chatterbox_tts.py <reference.wav> <sentences.json> <out_dir> [exaggeration] [cfg_weight]
"""

import json
import sys
import time
from pathlib import Path


def main() -> None:
    reference, sentences_file, out_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    exaggeration = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
    cfg_weight = float(sys.argv[5]) if len(sys.argv) > 5 else 0.5

    import torch
    import torchaudio as ta
    from chatterbox.tts import ChatterboxTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    started = time.time()
    model = ChatterboxTTS.from_pretrained(device=device)
    print(f"[chatterbox] model loaded on {device} in {time.time() - started:.0f}s", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    sentences = json.loads(sentences_file.read_text(encoding="utf-8"))
    for i, text in enumerate(sentences):
        started = time.time()
        kwargs = {"exaggeration": exaggeration, "cfg_weight": cfg_weight}
        if str(reference) != "builtin":  # builtin = Chatterbox's own real-human voice
            kwargs["audio_prompt_path"] = str(reference)
        wav = model.generate(text, **kwargs)
        ta.save(str(out_dir / f"{i:03d}.wav"), wav, model.sr)
        print(f"[chatterbox] {i + 1}/{len(sentences)} in {time.time() - started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
