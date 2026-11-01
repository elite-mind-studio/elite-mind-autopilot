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
        log("[+] Creating subtitles locally", "info")
        subtitles = __generate_subtitles_locally(sentences, audio_clips)
        # print(colored("[-] Local subtitle generation has been disabled for the time being.", "red"))
        # print(colored("[-] Exiting.", "red"))
        # sys.exit(1)

    with open(subtitles_path, "w", encoding="utf-8") as file:
        file.write(subtitles)

    # Equalize subtitles
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
        frame_height = clip.h

    font_path = (FONTS_DIR / "bold_font.ttf").resolve()
    font_name = ImageFont.truetype(str(font_path), 20).getname()[0]

    # SRT subtitles are laid out on a 288px-high canvas, so scale the pixel size
    ass_size = max(8, round(font_size * 288 / frame_height))
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
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", rel(combined_video_path),
            "-i", rel(tts_path),
            "-vf", f"subtitles={rel(subtitles_path)}:fontsdir={rel(FONTS_DIR)}:force_style='{style}'",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
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
