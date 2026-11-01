import os
import re
import shutil
import subprocess
import time

from apiclient.errors import HttpError
from moviepy import (
    AudioFileClip,
    CompositeAudioClip,
    VideoFileClip,
    afx,
    concatenate_audioclips,
)
from uuid import uuid4

from gpt import (
    generate_image_prompts,
    generate_stock_queries,
    generate_metadata,
    generate_script,
    get_search_terms,
    thumbnail_word,
    unload_model,
)
from logstream import log
from search import search_for_stock_videos
from edgevoice import edge_tts_save
from kokorovoice import kokoro_tts_save
from tiktokvoice import tts
from visuals import build_ai_video, build_stock_video, group_scenes
from utils import (
    song_credit,
    BASE_DIR,
    PROJECT_ROOT,
    SONGS_DIR,
    SUBTITLES_DIR,
    TEMP_DIR,
    choose_random_song,
)
from video import (
    combine_videos,
    generate_subtitles,
    generate_bold_thumbnail,
    generate_thumbnail,
    generate_video,
    save_video,
)
from youtube import add_to_playlist, latest_long_video, set_thumbnail, upload_video


# Sentence boundary: . ! ? followed by whitespace, except after titles like
# "Mr." so "Mr. Black" isn't split into two sentences
SENTENCE_END = r"(?<=[.!?])(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)\s+"


class PipelineCancelled(Exception):
    pass


def run_generation_pipeline(
    data: dict,
    is_cancelled,
    on_log,
    amount_of_stock_videos: int = 5,
) -> str:
    def emit(message: str, level: str = "info") -> None:
        log(message, level)
        if on_log:
            on_log(message, level)

    def guard_cancelled() -> None:
        if is_cancelled and is_cancelled():
            raise PipelineCancelled("Video generation was cancelled.")

    paragraph_number = int(data.get("paragraphNumber", 1))
    # "short": 1080x1920 YouTube Short; "long": 1920x1080 long-form video
    video_format = data.get("format", "short")
    frame_size = (1920, 1080) if video_format == "long" else (1080, 1920)
    # Longer videos need more distinct clips so footage doesn't loop visibly
    amount_of_stock_videos = int(data.get("stockVideos", amount_of_stock_videos))
    ai_model = data.get("aiModel")
    n_threads = data.get("threads")
    subtitles_position = data.get("subtitlesPosition")
    text_color = data.get("color")
    use_music = data.get("useMusic", False)
    automate_youtube_upload = data.get("automateYoutubeUpload", False)

    emit("[Video to be generated]", "info")
    emit("   Subject: " + data["videoSubject"], "info")
    emit("   AI Model: " + str(ai_model), "info")
    emit("   Custom Prompt: " + data["customPrompt"], "info")

    guard_cancelled()

    voice = data.get("voice", "")
    voice_prefix = voice[:2]

    if not voice:
        emit('[!] No voice was selected. Defaulting to "en_us_001"', "warning")
        voice = "en_us_001"
        voice_prefix = voice[:2]

    script = generate_script(
        data["videoSubject"],
        paragraph_number,
        ai_model,
        voice,
        data["customPrompt"],
        video_format=video_format,
        # Long videos can use their own style (e.g. "pov" stories)
        script_style=data.get("scriptStyle")
        or (os.getenv("LONG_SCRIPT_STYLE") if video_format == "long" else None),        series=data.get("series"),
    )

    if not script:
        raise RuntimeError(
            "Could not generate a script. Try a different model or prompt."
        )

    # Hard length cap: TikTok voices speak ~3 words/second, so trim the script
    # at a sentence boundary once it passes VIDEO_MAX_WORDS
    max_words = int(data.get("maxWords") or os.getenv("VIDEO_MAX_WORDS", "0") or 0)
    if max_words and len(script.split()) > max_words:
        original_words = len(script.split())
        # Shorten every paragraph evenly (dropping closing sentences from the
        # longest one first) so numbered list items are never cut off the end
        paragraphs = [
            [s for s in re.split(SENTENCE_END, p.strip()) if s.strip()]
            for p in re.split(r"\n\s*\n", script.strip())
            if p.strip()
        ]
        count = lambda: sum(len(s.split()) for p in paragraphs for s in p)
        while count() > max_words:
            longest = max(paragraphs, key=lambda p: sum(len(s.split()) for s in p))
            # Keep at least the first two sentences ("Number one." + item name)
            if len(longest) <= 2:
                break
            longest.pop()
        script = "\n\n".join(" ".join(p) for p in paragraphs)
        emit(f"[*] Script trimmed from {original_words} to {count()} words.", "info")

    # Spoken outro appended to every video, e.g. "Like, share and subscribe!"
    call_to_action = os.getenv("VIDEO_CALL_TO_ACTION", "").strip()
    series = data.get("series")
    if series and series["part"] < series["parts"]:
        # Series parts end with a reason to follow instead of the generic outro
        call_to_action = (
            f"Follow Elite Mind Decoder so you don't miss Part {series['part'] + 1}!"
        )
    if call_to_action:
        script = f"{script.rstrip().rstrip('.')}. {call_to_action}"

    def fetch_stock_videos() -> list:
        search_terms = get_search_terms(
            data["videoSubject"], amount_of_stock_videos, script, ai_model
        )

        video_urls = []
        it = 15
        min_dur = 10

        for search_term in search_terms:
            guard_cancelled()
            found_urls = search_for_stock_videos(search_term, it, min_dur)
            for url in found_urls:
                if url not in video_urls:
                    video_urls.append(url)
                    break

        if not video_urls:
            raise RuntimeError("No videos found to download.")

        video_paths = []
        emit(f"[+] Downloading {len(video_urls)} videos...", "info")

        for video_url in video_urls:
            guard_cancelled()
            try:
                saved_video_path = save_video(video_url)
                video_paths.append(saved_video_path)
            except Exception:
                emit(f"[-] Could not download video: {video_url}", "error")

        emit("[+] Videos downloaded!", "success")
        return video_paths

    # "ai_images": AI-generated stills per scene; "stock": stock footage
    visual_mode = (
        data.get("visualMode")
        or (os.getenv("LONG_VISUAL_MODE") if video_format == "long" else None)
        or os.getenv("VISUAL_MODE", "stock")
    ).strip().lower()
    # Scene-matched modes fetch their own visuals after the voiceover
    video_paths = fetch_stock_videos() if visual_mode == "stock" else []

    emit("[+] Script generated!", "success")

    guard_cancelled()

    # Split on sentence endings (. ! ?) and line breaks, keeping chunks short for TTS
    sentences = [s.strip() for s in re.split(SENTENCE_END + r"|\n+", script)]
    sentences = [s for s in sentences if s]
    paths = []
    sentence_durations = []

    for sentence in sentences:
        guard_cancelled()
        current_tts_path = str(TEMP_DIR / f"{uuid4()}.mp3")
        # The TTS endpoints and network fail intermittently, so retry before giving up
        for attempt in range(5):
            try:
                if voice.startswith("kokoro:"):
                    kokoro_tts_save(sentence, voice.split(":", 1)[1], current_tts_path)
                elif voice.endswith("Neural"):
                    edge_tts_save(sentence, voice, current_tts_path)
                else:
                    tts(sentence, voice, filename=current_tts_path)
            except Exception as err:
                emit(f"[-] TTS attempt {attempt + 1} failed: {err}", "warning")
            if os.path.exists(current_tts_path):
                break
            time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"Text-to-speech failed for sentence: {sentence}")
        audio_clip = AudioFileClip(current_tts_path)
        paths.append(audio_clip)
        sentence_durations.append(audio_clip.duration)

    final_audio = concatenate_audioclips(paths)
    tts_path = str(TEMP_DIR / f"{uuid4()}.mp3")
    try:
        final_audio.write_audiofile(tts_path)
    finally:
        final_audio.close()
        for audio_clip in paths:
            audio_clip.close()

    try:
        subtitles_path = generate_subtitles(
            audio_path=tts_path,
            sentences=sentences,
            audio_clips=paths,
            voice=voice_prefix,
        )
    except Exception as err:
        emit(f"[-] Error generating subtitles: {err}", "error")
        subtitles_path = None

    if not subtitles_path:
        raise RuntimeError(
            "Could not generate subtitles. Check AssemblyAI key or local subtitle settings."
        )

    combined_video_path = None
    if visual_mode == "stock_scenes":
        # One stock clip per scene, matched to what is being said
        scenes = group_scenes(
            sentences,
            sentence_durations,
            target=float(data.get("sceneSeconds") or os.getenv("IMAGE_SCENE_SECONDS", "5")),
        )
        emit(f"[+] Finding stock footage for {len(scenes)} scenes...", "info")
        queries = generate_stock_queries(data["videoSubject"], [text for text, _ in scenes])
        unload_model(ai_model)
        try:
            combined_video_path = build_stock_video(
                scenes, queries, on_log=emit, size=frame_size
            )
        except Exception as err:
            emit(f"[-] Scene stock footage failed, using a stock montage: {err}", "warning")
            video_paths = fetch_stock_videos()
    elif visual_mode == "ai_images":
        scenes = group_scenes(
            sentences,
            sentence_durations,
            target=float(data.get("sceneSeconds") or os.getenv("IMAGE_SCENE_SECONDS", "5")),
        )
        emit(f"[+] Writing image prompts for {len(scenes)} scenes...", "info")
        prompts = generate_image_prompts(
            data["videoSubject"], [text for text, _ in scenes], ai_model
        )
        # Free the model's RAM before image generation and rendering
        unload_model(ai_model)
        try:
            # Shorts can use the letterbox "explainer" layout with a title bar
            layout = os.getenv("VIDEO_LAYOUT", "full") if video_format == "short" else "full"
            combined_video_path = build_ai_video(
                scenes,
                prompts,
                on_log=emit,
                size=frame_size,
                layout=layout,
                title=data.get("title") or data["videoSubject"],
            )
            if layout == "letterbox":
                subtitles_position = "center,letterbox"
        except Exception as err:
            # AI_IMAGES_FALLBACK=none: never post a lower-quality video; fail the run
            # so the topic is retried at the next slot
            if os.getenv("AI_IMAGES_FALLBACK", "stock").strip().lower() == "none":
                raise RuntimeError(f"Not enough sharp AI images, skipping this video: {err}") from err
            emit(f"[-] AI images failed, falling back to stock footage: {err}", "warning")
            video_paths = fetch_stock_videos()
    else:
        # Free the model's RAM before the memory-heavy rendering steps
        unload_model(ai_model)

    if not combined_video_path:
        temp_audio = AudioFileClip(tts_path)
        try:
            combined_video_path = combine_videos(
                video_paths, temp_audio.duration, 5, n_threads or 2, size=frame_size
            )
        finally:
            temp_audio.close()

    try:
        final_video_path = generate_video(
            combined_video_path,
            tts_path,
            subtitles_path,
            n_threads or 2,
            subtitles_position,
            text_color or "#FFFF00",
            font_size=70 if video_format == "long" else 100,
        )
    except Exception as err:
        raise RuntimeError(
            f"Could not render final video. Check subtitle/font/ImageMagick setup. ({err})"
        ) from err

    title, description, keywords = generate_metadata(
        data["videoSubject"], script, ai_model
    )
    if series:
        # Series parts keep a consistent, numbered title so viewers can binge in order
        title = data["videoSubject"][:100]
    elif video_format == "long" and data["videoSubject"].lower().startswith("pov:"):
        # "POV: You're ..." titles are the hook itself, keep them exactly
        title = data["videoSubject"][:100]

    emit("[-] Metadata for YouTube upload:", "info")
    emit("   Title:", "info")
    emit(f"   {title}", "info")
    emit("   Description:", "info")
    emit(f"   {description}", "info")
    emit("   Keywords:", "info")
    emit(f"  {', '.join(keywords)}", "info")

    final_output_path = str(PROJECT_ROOT / final_video_path)
    rendered_video_path = str(TEMP_DIR / final_video_path)
    render_threads = n_threads or (os.cpu_count() or 2)

    guard_cancelled()

    music_credit = ""
    if use_music:
        song_path = choose_random_song()

        if not song_path:
            emit(
                "[-] Could not find songs in Songs/. Continuing without background music.",
                "warning",
            )
            use_music = False

        if use_music:
            music_credit = song_credit(song_path)
            video_clip = VideoFileClip(rendered_video_path)
            song_clip = None
            mixed_audio = None
            mixed_audio_path = str(TEMP_DIR / f"{uuid4()}_mixed_audio.m4a")
            try:
                original_duration = video_clip.duration
                original_audio = video_clip.audio
                song_clip = AudioFileClip(song_path).with_fps(44100)
                song_clip = song_clip.with_effects(
                    [afx.AudioLoop(duration=original_duration)]
                )
                song_clip = song_clip.with_volume_scaled(float(os.getenv("MUSIC_VOLUME", "0.1"))).with_fps(44100)

                mixed_audio = CompositeAudioClip(
                    [original_audio, song_clip]
                ).with_duration(original_duration)
                mixed_audio.write_audiofile(
                    mixed_audio_path,
                    fps=44100,
                    codec="aac",
                    bitrate="192k",
                )
            finally:
                video_clip.close()
                if mixed_audio is not None:
                    mixed_audio.close()
                if song_clip is not None:
                    song_clip.close()

            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        rendered_video_path,
                        "-i",
                        mixed_audio_path,
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-shortest",
                        final_output_path,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                emit(
                    "[!] ffmpeg remux failed. Falling back to MoviePy render for music mix.",
                    "warning",
                )
                video_clip = VideoFileClip(rendered_video_path)
                song_clip = None
                try:
                    original_duration = video_clip.duration
                    original_audio = video_clip.audio
                    song_clip = AudioFileClip(song_path).with_fps(44100)
                    song_clip = song_clip.with_effects(
                        [afx.AudioLoop(duration=original_duration)]
                    )
                    song_clip = song_clip.with_volume_scaled(float(os.getenv("MUSIC_VOLUME", "0.1"))).with_fps(44100)
                    comp_audio = CompositeAudioClip(
                        [original_audio, song_clip]
                    ).with_duration(original_duration)
                    video_clip = (
                        video_clip.with_audio(comp_audio)
                        .with_fps(30)
                        .with_duration(original_duration)
                    )
                    video_clip.write_videofile(
                        final_output_path,
                        threads=render_threads,
                        fps=30,
                        codec="libx264",
                        audio_codec="aac",
                        preset="medium",
                    )
                finally:
                    video_clip.close()
                    if song_clip is not None:
                        song_clip.close()
            finally:
                if os.path.exists(mixed_audio_path):
                    os.remove(mixed_audio_path)

    if not use_music:
        shutil.copy2(rendered_video_path, final_output_path)

    if automate_youtube_upload:
        client_secrets_file = str((BASE_DIR / "client_secret.json").resolve())
        skip_yt_upload = False
        if not os.path.exists(client_secrets_file):
            skip_yt_upload = True
            emit(
                "[-] Client secrets file missing. YouTube upload will be skipped.",
                "warning",
            )
            emit(
                "[-] Please download the client_secret.json from Google Cloud Platform and store this inside the /Backend directory.",
                "error",
            )

        if not skip_yt_upload:
            video_category_id = os.getenv("YOUTUBE_CATEGORY_ID", "28")
            privacy_status = os.getenv("YOUTUBE_PRIVACY_STATUS", "private")
            # e.g. "Educational content only, not financial advice."
            footer = os.getenv("YOUTUBE_DESCRIPTION_FOOTER", "").strip()
            # Shorts point viewers to the newest long video (Shorts -> long-form funnel)
            if video_format == "short":
                try:
                    latest = latest_long_video()
                    if latest:
                        description = f"🎬 Watch the full story: https://youtu.be/{latest[0]}\n\n{description}"
                except Exception as link_err:
                    emit(f"[-] Could not find a long video to link: {link_err}", "warning")
            video_metadata = {
                "video_path": final_output_path,
                "title": title,
                "description": "\n\n".join(
                    part for part in (description, music_credit, footer) if part
                ),
                "category": video_category_id,
                "keywords": ",".join(keywords),
                "privacyStatus": privacy_status,
            }

            try:
                video_response = upload_video(
                    video_path=video_metadata["video_path"],
                    title=video_metadata["title"],
                    description=video_metadata["description"],
                    category=video_metadata["category"],
                    keywords=video_metadata["keywords"],
                    privacy_status=video_metadata["privacyStatus"],
                )
                emit(f"Uploaded video ID: {video_response.get('id')}", "success")

                # Thumbnail failures shouldn't fail an already-uploaded video
                try:
                    thumb_size = (1280, 720) if video_format == "long" else (1080, 1920)
                    import visuals as _visuals

                    if visual_mode == "ai_images" and _visuals.LAST_SCENE_IMAGES:
                        word = f"PART {series['part']}" if series else thumbnail_word(title)
                        thumbnail_path = generate_bold_thumbnail(
                            _visuals.LAST_SCENE_IMAGES[0], word, size=thumb_size
                        )
                    else:
                        thumbnail_path = generate_thumbnail(combined_video_path, title, size=thumb_size)
                    set_thumbnail(video_response.get("id"), thumbnail_path)
                    emit("[+] Custom thumbnail set.", "success")
                except Exception as thumb_err:
                    emit(
                        f"[-] Could not set thumbnail (is the channel phone-verified?): {thumb_err}",
                        "warning",
                    )

                # Group uploads in a playlist so viewers binge (more watch time)
                playlist_titles = [os.getenv("YOUTUBE_PLAYLIST", "").strip()]
                if series:
                    playlist_titles.append(series["title"].title())
                for playlist_title in [t for t in playlist_titles if t]:
                    try:
                        add_to_playlist(video_response.get("id"), playlist_title)
                        emit(f"[+] Added to playlist: {playlist_title}", "success")
                    except Exception as playlist_err:
                        emit(f"[-] Could not add to playlist: {playlist_err}", "warning")
            except HttpError as err:
                emit(
                    f"An HTTP error {err.resp.status} occurred:\n{err.content}", "error"
                )


    emit(f"[+] Video generated: {final_video_path}!", "success")

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/f", "/im", "ffmpeg.exe"],
            check=False,
            capture_output=True,
            text=True,
        )
    elif shutil.which("pkill"):
        subprocess.run(
            ["pkill", "-f", "ffmpeg"],
            check=False,
            capture_output=True,
            text=True,
        )

    return final_video_path
