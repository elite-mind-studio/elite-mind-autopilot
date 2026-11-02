"""
AI-generated visuals: one image per scene, animated with a slow zoom/pan
("Ken Burns") and cut to match the narration.

Image providers are tried in order (IMAGE_PROVIDERS, default
"pollinations,cloudflare,local"). A provider that fails twice in a row is
skipped for the rest of the video, so a slow or broken service doesn't stall
generation.
"""

import base64
import io
import os
import random
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

import imageio_ffmpeg
import requests
from PIL import Image, ImageFilter

from logstream import log
from utils import TEMP_DIR

WIDTH, HEIGHT = 1080, 1920
FPS = 30

IMAGE_STYLE = os.getenv(
    "IMAGE_STYLE",
    "cinematic, clean, photorealistic, dramatic lighting, shallow depth of field, "
    "sharp focus, highly detailed, 35mm film still, vertical composition",
)
NEGATIVE = "no text, no words, no letters, no watermark, no logo"

_local_pipe = None
# Scene images of the most recent AI video (used to build its thumbnail)
LAST_SCENE_IMAGES: List[str] = []


class ProviderError(Exception):
    pass


def _pollinations(prompt: str, size=(WIDTH, HEIGHT)) -> bytes:
    key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    if not key:
        raise ProviderError("POLLINATIONS_API_KEY not set")

    models = [m.strip() for m in os.getenv("POLLINATIONS_MODELS", "zimage,flux").split(",") if m.strip()]
    last_error = None
    for model in models:
        try:
            response = requests.get(
                f"https://gen.pollinations.ai/image/{quote(prompt)}",
                params={
                    "model": model,
                    "width": size[0],
                    "height": size[1],
                    "nologo": "true",
                    "seed": random.randint(1, 10**9),
                },
                headers={"Authorization": f"Bearer {key}"},
                timeout=120,
            )
            if response.ok and response.headers.get("content-type", "").startswith("image/"):
                return response.content
            last_error = f"{model}: HTTP {response.status_code} {response.text[:200]}"
        except requests.RequestException as err:
            last_error = f"{model}: {err}"
    raise ProviderError(last_error)


def _cloudflare(prompt: str, size=(WIDTH, HEIGHT)) -> bytes:
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise ProviderError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")

    response = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/"
        "@cf/black-forest-labs/flux-1-schnell",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt, "steps": int(os.getenv("CLOUDFLARE_STEPS", "8"))},
        timeout=120,
    )
    if not response.ok:
        raise ProviderError(f"HTTP {response.status_code} {response.text[:200]}")
    image_b64 = response.json().get("result", {}).get("image")
    if not image_b64:
        raise ProviderError(f"No image in response: {response.text[:200]}")
    return base64.b64decode(image_b64)


def _gemini(prompt: str, size=(WIDTH, HEIGHT)) -> bytes:
    """Google Gemini image generation (free tier with an AI Studio key)."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise ProviderError("GEMINI_API_KEY not set")

    model = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    aspect = "16:9" if size[0] > size[1] else ("9:16" if size[0] < size[1] else "1:1")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": aspect},
            },
        },
        timeout=120,
    )
    if not response.ok:
        raise ProviderError(f"HTTP {response.status_code} {response.text[:200]}")
    for candidate in response.json().get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            data = part.get("inlineData") or part.get("inline_data")
            if data and data.get("data"):
                return base64.b64decode(data["data"])
    raise ProviderError(f"No image in response: {response.text[:200]}")


def _local(prompt: str, size=(WIDTH, HEIGHT)) -> bytes:
    """Stable Diffusion Turbo on the local CPU (needs torch + diffusers)."""
    global _local_pipe
    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except ImportError as err:
        raise ProviderError(f"torch/diffusers not installed: {err}")

    if _local_pipe is None:
        log("[*] Loading local Stable Diffusion model (first run downloads ~2.5 GB)...", "info")
        _local_pipe = AutoPipelineForText2Image.from_pretrained(
            os.getenv("LOCAL_SD_MODEL", "stabilityai/sd-turbo"), torch_dtype=torch.float32
        )
        _local_pipe.to("cpu")

    image = _local_pipe(
        prompt=prompt,
        num_inference_steps=2,
        guidance_scale=0.0,
        width=512 if size[0] < size[1] else 896,
        height=896 if size[0] < size[1] else 512,
    ).images[0]
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


PROVIDERS = {
    "pollinations": _pollinations,
    "cloudflare": _cloudflare,
    "gemini": _gemini,
    "local": _local,
}


def _cover(image: Image.Image, size=(WIDTH, HEIGHT)) -> Image.Image:
    """Scale and center-crop an image to fill the frame."""
    width, height = size
    image = image.convert("RGB")

    # A square image in a wide frame would lose its top and bottom (heads get
    # cut off), so show it whole over a blurred, stretched copy of itself
    if image.width / image.height < (width / height) * 0.75:
        background = image.resize((width, height), Image.LANCZOS).filter(
            ImageFilter.GaussianBlur(radius=40)
        )
        fitted = image.resize(
            (round(image.width * height / image.height), height), Image.LANCZOS
        )
        if fitted.width > image.width:
            fitted = fitted.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=2))
        background.paste(fitted, ((width - fitted.width) // 2, 0))
        return background

    scale = max(width / image.width, height / image.height)
    image = image.resize(
        (round(image.width * scale), round(image.height * scale)), Image.LANCZOS
    )
    left = (image.width - width) // 2
    top = (image.height - height) // 2
    image = image.crop((left, top, left + width, top + height))
    # Upscaled images (e.g. 1024px Cloudflare output in a 1080p frame) look soft;
    # a light unsharp mask restores edge detail without halos
    if scale > 1.05:
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=2))
    return image


class ImageGenerator:
    def __init__(self, on_log=None, size=(WIDTH, HEIGHT)) -> None:
        self.size = size
        order = os.getenv("IMAGE_PROVIDERS", "pollinations,cloudflare,local")
        self.providers = [p.strip() for p in order.split(",") if p.strip() in PROVIDERS]
        self.failures = {p: 0 for p in self.providers}
        self.on_log = on_log or (lambda message, level: log(message, level))

    def generate(self, prompt: str, path: str) -> Optional[str]:
        """Create one image at `path`. Returns the provider used, or None."""
        full_prompt = f"{prompt}. {IMAGE_STYLE}. {NEGATIVE}"
        for provider in self.providers:
            if self.failures[provider] >= 2:
                continue
            started = time.time()
            try:
                data = PROVIDERS[provider](full_prompt, self.size)
                _cover(Image.open(io.BytesIO(data)), self.size).save(path, "JPEG", quality=92)
                self.failures[provider] = 0
                self.on_log(
                    f"   [{provider}] image ready in {time.time() - started:.0f}s", "info"
                )
                return provider
            except Exception as err:
                message = str(err)
                # A content-filter rejection is about this prompt, not the provider
                if not re.search(r"nsfw|safety|content policy|blocked", message, re.IGNORECASE):
                    self.failures[provider] += 1
                self.on_log(f"   [{provider}] failed: {message[:200]}", "warning")
        return None


def group_scenes(sentences: List[str], durations: List[float], target: float = 3.5):
    """
    Build one scene per sentence so each image matches the words being spoken.
    Sentences longer than `target` seconds are split at commas/conjunctions into
    smaller phrases, with time shared out by word count. Very short sentences
    (under 1.2 s, e.g. "Why?") are merged into the next one.
    """
    scenes = []
    carry_text, carry_time = "", 0.0
    for sentence, duration in zip(sentences, durations):
        text = f"{carry_text} {sentence}".strip()
        duration += carry_time
        # Merge short sentences until a scene is ~70% of the target, so a
        # longer target means fewer images (stays within free image quotas)
        if duration < max(1.2, target * 0.7):
            carry_text, carry_time = text, duration
            continue
        carry_text, carry_time = "", 0.0

        parts = [text]
        if duration > target:
            pieces = [p.strip() for p in re.split(r",\s+|;\s+|\s+(?=(?:but|and|so|because|while|which)\s)", text) if p.strip()]
            # Merge pieces until each is at least ~2 seconds of speech
            words_total = max(1, len(text.split()))
            parts, current = [], ""
            for piece in pieces:
                current = f"{current} {piece}".strip()
                if len(current.split()) / words_total * duration >= 2.0:
                    parts.append(current)
                    current = ""
            if current:
                if parts:
                    parts[-1] = f"{parts[-1]} {current}"
                else:
                    parts.append(current)

        words_total = max(1, sum(len(p.split()) for p in parts))
        for part in parts:
            scenes.append((part, duration * len(part.split()) / words_total))

    if carry_text:
        if scenes:
            text, d = scenes.pop()
            scenes.append((f"{text} {carry_text}", d + carry_time))
        else:
            scenes.append((carry_text, carry_time))
    return scenes


def _ken_burns_segment(
    image_path: str, duration: float, index: int, out_path: str, size=(WIDTH, HEIGHT)
) -> None:
    frames = max(1, round(duration * FPS))
    zoom_step = 0.08 / frames
    # Alternate zoom in / zoom out and drift direction for variety
    if index % 2 == 0:
        zoom = f"min(1+{zoom_step}*on,1.08)"
    else:
        zoom = f"max(1.08-{zoom_step}*on,1)"
    x = "iw/2-(iw/zoom/2)" if index % 3 == 0 else (
        "(iw-iw/zoom)*on/{f}".format(f=frames) if index % 3 == 1 else "(iw-iw/zoom)*(1-on/{f})".format(f=frames)
    )
    y = "ih/2-(ih/zoom/2)"
    vf = (
        # Lanczos supersampling keeps the slow zoom sharp
        f"scale={size[0] * 2}:{size[1] * 2}:flags=lanczos,"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={size[0]}x{size[1]}:fps={FPS},"
        "format=yuv420p"
    )
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
            "-loop", "1", "-i", image_path,
            "-vf", vf, "-frames:v", str(frames),
            # Near-lossless intermediate; the final pass does the real compression
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
            out_path,
        ],
        check=True,
    )


# Letterbox layout: a 16:9 picture in the middle of a 1080x1920 frame, with a
# title in the black bar above it (the classic animated-explainer Shorts look)
LETTERBOX_FRAME = (1080, 1920)
LETTERBOX_PICTURE = (1080, 608)
LETTERBOX_TOP = 560

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
}
NUMBER_PATTERN = re.compile(r"^\s*number\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)


def _font(size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(Path(__file__).resolve().parent.parent / "fonts" / "bold_font.ttf"), size)


def _draw_number(image_path: str, number: int) -> None:
    """Stamp a big list number on the left of a scene image ("Number 3" scenes)."""
    from PIL import ImageDraw

    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font(int(image.height * 0.55))
    text = str(number)
    x, y = int(image.width * 0.05), int(image.height * 0.18)
    draw.text((x, y), text, font=font, fill="#FFFFFF", stroke_width=max(6, image.height // 60), stroke_fill="#000000")
    image.save(image_path, "JPEG", quality=95)


def _title_frame(title: str, path: str) -> None:
    """Black 1080x1920 frame with the video title in the top bar."""
    import textwrap

    from PIL import ImageDraw

    frame = Image.new("RGB", LETTERBOX_FRAME, "#000000")
    draw = ImageDraw.Draw(frame)
    text = re.sub(r"#\w+", "", title).strip().upper()
    size = 92
    while size > 44:
        font = _font(size)
        lines = textwrap.wrap(text, width=max(8, int(1000 / (size * 0.55))))
        if len(lines) * size * 1.15 <= LETTERBOX_TOP - 120 and max(draw.textlength(l, font=font) for l in lines) <= 1000:
            break
        size -= 6
    line_height = int(size * 1.15)
    y = (LETTERBOX_TOP - line_height * len(lines)) // 2 + 20
    for i, line in enumerate(lines):
        width = draw.textlength(line, font=font)
        draw.text(
            ((LETTERBOX_FRAME[0] - width) / 2, y),
            line,
            font=font,
            fill="#FFFFFF" if i % 2 == 0 else "#FFD400",
            stroke_width=4,
            stroke_fill="#000000",
        )
        y += line_height
    frame.save(path)


def _sdxl_prompt(prompt: str) -> str:
    """
    SDXL reads only ~77 tokens, so put the scene first, swap the long character
    description for a short one, and finish with a short style.
    """
    long_look = os.getenv("STORY_CHARACTER_LOOK", "").strip()
    short_look = os.getenv("STORY_CHARACTER_LOOK_SHORT", "").strip()
    if long_look and short_look:
        prompt = prompt.replace(long_look, short_look)
    style = os.getenv("IMAGE_STYLE_SHORT", "flat 2D cartoon illustration, clean bold outlines, simple light background")
    # Keeps the main character big and in focus (SDXL tends to shrink him into crowds)
    return f"{prompt}, main character large and centered in frame, medium shot, {style}"


def build_ai_video(
    scenes: List[tuple],
    prompts: List[str],
    on_log=None,
    min_success_ratio: float = 0.6,
    size=(WIDTH, HEIGHT),
    layout: str = "full",
    title: str = "",
) -> str:
    """
    Generate one image per scene and render them into a silent video
    (default 1080x1920) whose length matches the narration.

    layout="letterbox" renders 16:9 pictures inside a 1080x1920 frame with the
    title in the top bar; scenes that start with "Number N" get a big N.

    Raises RuntimeError if too few images could be generated, so the caller
    can fall back to stock footage.
    """
    emit = on_log or (lambda message, level: log(message, level))
    picture_size = LETTERBOX_PICTURE if layout == "letterbox" else size
    generator = ImageGenerator(on_log=emit, size=(1920, 1080) if layout == "letterbox" else size)
    work_dir = Path(TEMP_DIR) / f"ai_{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)

    image_paths: List[Optional[str]] = [None] * len(prompts)

    order = [p.strip() for p in os.getenv("IMAGE_PROVIDERS", "").split(",") if p.strip()]
    kaggle_first = bool(order) and order[0] == "kaggle"

    def kaggle_batch(indices: List[int]) -> None:
        """Generate the given scenes in one free Kaggle GPU run."""
        if "kaggle" not in order or not indices:
            return
        try:
            import kaggle_images

            if not kaggle_images.available():
                return
            gen_size = (1024, 576) if generator.size[0] >= generator.size[1] else (576, 1024)
            jobs = [
                {"id": f"s{i:03d}", "prompt": f"{prompts[i]}. {IMAGE_STYLE}. {NEGATIVE}", "width": gen_size[0], "height": gen_size[1]}
                for i in indices
            ]
            results = kaggle_images.generate(jobs, on_log=emit)
            for i in indices:
                data = results.get(f"s{i:03d}")
                if data:
                    path = str(work_dir / f"scene_{i:02d}.jpg")
                    _cover(Image.open(io.BytesIO(data)), generator.size).save(path, "JPEG", quality=92)
                    image_paths[i] = path
        except Exception as err:
            emit(f"[-] Kaggle images failed: {str(err)[:200]}", "warning")

    # Kaggle first only if it leads IMAGE_PROVIDERS; otherwise it fills gaps at the end
    if kaggle_first:
        kaggle_batch(list(range(len(prompts))))

    for i, prompt in enumerate(prompts):
        if image_paths[i]:
            continue
        emit(f"[+] Image {i + 1}/{len(prompts)}: {prompt[:90]}", "info")
        path = str(work_dir / f"scene_{i:02d}.jpg")
        image_paths[i] = path if generator.generate(prompt, path) else None

    if not kaggle_first:
        missing = [i for i, path in enumerate(image_paths) if not path]
        if missing:
            emit(f"[+] {len(missing)} images missing, generating them on Kaggle...", "info")
            kaggle_batch(missing)

    made = sum(1 for p in image_paths if p)
    if made < max(1, int(len(prompts) * min_success_ratio)):
        raise RuntimeError(f"Only {made}/{len(prompts)} images could be generated.")

    # Reuse the previous image for any scene whose image failed
    last = next(p for p in image_paths if p)
    for i, path in enumerate(image_paths):
        if path:
            last = path
        else:
            image_paths[i] = last

    global LAST_SCENE_IMAGES
    LAST_SCENE_IMAGES = [p for p in image_paths if p]

    if layout == "letterbox":
        for i, (text, _) in enumerate(scenes):
            match = NUMBER_PATTERN.match(text)
            if match:
                word = match.group(1).lower()
                number = int(word) if word.isdigit() else NUMBER_WORDS[word]
                numbered = str(work_dir / f"scene_{i:02d}_n.jpg")
                Image.open(image_paths[i]).save(numbered, "JPEG", quality=95)
                _draw_number(numbered, number)
                image_paths[i] = numbered

    emit("[+] Animating images...", "info")
    segment_list = work_dir / "segments.txt"
    with open(segment_list, "w", encoding="utf-8") as f:
        for i, ((_, duration), image_path) in enumerate(zip(scenes, image_paths)):
            segment = str(work_dir / f"segment_{i:02d}.mp4")
            _ken_burns_segment(image_path, duration, i, segment, picture_size)
            f.write(f"file '{Path(segment).as_posix()}'\n")

    concatenated = str(work_dir / "pictures.mp4")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(segment_list),
            "-c", "copy", concatenated,
        ],
        check=True,
    )

    output = str(Path(TEMP_DIR) / f"{uuid.uuid4()}.mp4")
    if layout != "letterbox":
        os.replace(concatenated, output)
        return output

    frame_path = str(work_dir / "frame.png")
    _title_frame(title, frame_path)
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
            "-loop", "1", "-i", frame_path,
            "-i", concatenated,
            "-filter_complex", f"[0:v][1:v]overlay=0:{LETTERBOX_TOP}:shortest=1,format=yuv420p",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
            output,
        ],
        check=True,
    )
    return output


def build_stock_video(
    scenes: List[tuple],
    queries: List[str],
    on_log=None,
    size=(WIDTH, HEIGHT),
    min_success_ratio: float = 0.6,
) -> str:
    """
    Stock-footage version of build_ai_video: for each scene, search a clip
    that matches what is being said, trim it to the scene's length and crop it
    to `size`, then join the clips. Unlimited and free (Pixabay/Pexels).

    Raises RuntimeError if too few scenes found a clip, so the caller can fall
    back to the generic stock montage.
    """
    from search import search_for_stock_videos
    from video import save_video

    emit = on_log or (lambda message, level: log(message, level))
    work_dir = Path(TEMP_DIR) / f"stock_{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    used_urls = set()
    clip_paths: List[Optional[str]] = []

    for i, ((text, duration), query) in enumerate(zip(scenes, queries)):
        emit(f"[+] Clip {i + 1}/{len(scenes)}: \"{query}\"", "info")
        clip = None
        # Try the full phrase, then a shorter version if nothing matches
        for attempt in (query, " ".join(query.split()[-2:])):
            try:
                urls = search_for_stock_videos(attempt, 20, max(1, int(duration)))
            except Exception as err:
                emit(f"   search failed: {err}", "warning")
                urls = []
            for url in urls:
                if url in used_urls:
                    continue
                try:
                    clip = save_video(url, str(work_dir))
                    used_urls.add(url)
                    break
                except Exception as err:
                    emit(f"   download failed: {str(err)[:120]}", "warning")
            if clip:
                break
        clip_paths.append(clip)

    found = sum(1 for c in clip_paths if c)
    if found < max(1, int(len(scenes) * min_success_ratio)):
        raise RuntimeError(f"Only {found}/{len(scenes)} scenes found a stock clip.")

    # Reuse the previous clip for any scene that found nothing
    last = next(c for c in clip_paths if c)
    for i, clip in enumerate(clip_paths):
        if clip:
            last = clip
        else:
            clip_paths[i] = last

    emit("[+] Cutting clips...", "info")
    segment_list = work_dir / "segments.txt"
    width, height = size
    with open(segment_list, "w", encoding="utf-8") as f:
        for i, ((_, duration), clip) in enumerate(zip(scenes, clip_paths)):
            segment = str(work_dir / f"segment_{i:03d}.mp4")
            subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
                    # Loop clips that are shorter than their scene
                    "-stream_loop", "-1", "-i", clip,
                    "-t", f"{duration:.3f}",
                    "-vf", (
                        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                        f"crop={width}:{height},fps={FPS},format=yuv420p"
                    ),
                    "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                    segment,
                ],
                check=True,
            )
            f.write(f"file '{Path(segment).as_posix()}'\n")

    output = str(Path(TEMP_DIR) / f"{uuid.uuid4()}.mp4")
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(segment_list),
            "-c", "copy", output,
        ],
        check=True,
    )
    return output
