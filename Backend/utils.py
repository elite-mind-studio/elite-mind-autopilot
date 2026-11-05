import os
import sys
import random
import logging
import shutil

from pathlib import Path
from typing import Optional
from termcolor import colored


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
TEMP_DIR = PROJECT_ROOT / "temp"
SUBTITLES_DIR = PROJECT_ROOT / "subtitles"
SONGS_DIR = PROJECT_ROOT / "Songs"
FONTS_DIR = PROJECT_ROOT / "fonts"
ENV_FILE = PROJECT_ROOT / ".env"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def clean_dir(path: str) -> None:
    """
    Removes every file in a directory.

    Args:
        path (str): Path to directory.

    Returns:
        None
    """
    try:
        directory = Path(path).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured directory exists: {directory}")

        for entry in directory.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
                logger.info(f"Removed directory: {entry}")
            else:
                entry.unlink(missing_ok=True)
                logger.info(f"Removed file: {entry}")

        logger.info(colored(f"Cleaned {directory} directory", "green"))
    except Exception as e:
        logger.error(f"Error occurred while cleaning directory {path}: {str(e)}")


# Background music by story tone (Kevin MacLeod, CC BY 4.0; downloaded by the workflows)
MOOD_SONGS = {
    "tragic": ("Long Note Four", "Dark Times", "Lightless Dawn", "Sad Trio"),
    "tense": ("Volatile Reaction", "Ossuary 6 - Air", "Epic Unease", "Investigations"),
    "suspense": ("Ossuary 6 - Air", "Epic Unease", "Investigations"),
    "upbeat": ("Sneaky Snitch", "Carefree", "Wallpaper", "Fluffing a Duck", "Monkeys Spinning Monkeys"),
}


def choose_random_song(mood: Optional[str] = None) -> Optional[str]:
    """
    Chooses a random MP3 from the Songs/ directory.

    Returns:
        str: The path to the chosen song, or None if no MP3 files found.
    """
    try:
        if not SONGS_DIR.exists():
            return None

        songs = [
            song
            for song in SONGS_DIR.iterdir()
            if song.is_file() and song.suffix.lower() == ".mp3"
        ]

        if not songs:
            return None

        # Prefer a track that fits the story's tone, if one was downloaded
        fitting = [s for s in songs if s.stem in MOOD_SONGS.get(mood or "", ())]
        song = random.choice(fitting or songs)
        logger.info(colored(f"Chose song: {song}", "green"))
        return str(song)
    except Exception as e:
        logger.error(
            colored(f"Error occurred while choosing random song: {str(e)}", "red")
        )


def resolve_imagemagick_binary() -> Optional[str]:
    """
    Resolves an ImageMagick executable path across Linux, macOS, and Windows.

    Returns:
        Optional[str]: Absolute executable path if found.
    """
    configured_binary = os.getenv("IMAGEMAGICK_BINARY", "").strip().strip('"')
    if configured_binary:
        expanded = Path(configured_binary).expanduser()
        if expanded.exists():
            return str(expanded.resolve())
        logger.warning(
            colored("Configured IMAGEMAGICK_BINARY was not found on disk.", "yellow")
        )

    candidate_names = [
        "magick",
        "magick.exe",
        "convert",
        "convert.exe",
    ]

    for candidate in candidate_names:
        found = shutil.which(candidate)
        if found:
            return found

    return None


def check_env_vars() -> None:
    """
    Checks if the necessary environment variables are set.

    Returns:
        None

    Raises:
        SystemExit: If any required environment variables are missing.
    """
    try:
        required_vars = ["TIKTOK_SESSION_ID"]
        missing_vars = []
        for var in required_vars:
            value = os.getenv(var)
            if value is None or len(value) == 0:
                missing_vars.append(var)

        # Either stock video provider is enough
        if not os.getenv("PEXELS_API_KEY") and not os.getenv("PIXABAY_API_KEY"):
            missing_vars.append("PEXELS_API_KEY or PIXABAY_API_KEY")

        if missing_vars:
            missing_vars_str = ", ".join(missing_vars)
            logger.error(
                colored(
                    f"The following environment variables are missing: {missing_vars_str}",
                    "red",
                )
            )
            logger.error(
                colored(
                    "Please consult 'docs/configuration.md' for setup instructions.",
                    "yellow",
                )
            )
            sys.exit(1)  # Aborts the program

        imagemagick_binary = resolve_imagemagick_binary()
        if not imagemagick_binary:
            logger.error(
                colored(
                    "IMAGEMAGICK_BINARY is not set and no ImageMagick executable was detected in PATH.",
                    "red",
                )
            )
            logger.error(
                colored(
                    "Set IMAGEMAGICK_BINARY in .env or install ImageMagick and add it to PATH.",
                    "yellow",
                )
            )
            sys.exit(1)

        os.environ["IMAGEMAGICK_BINARY"] = imagemagick_binary
    except Exception as e:
        logger.error(f"Error occurred while checking environment variables: {str(e)}")
        sys.exit(1)  # Aborts the program if an unexpected error occurs


def song_credit(song_path: str) -> str:
    """
    Attribution line for a background track, required by Creative Commons
    licensed music. Tracks listed in Songs/credits.txt ("file name | credit")
    use that credit; others get none.

    Args:
        song_path (str): Path to the chosen song.

    Returns:
        str: The credit line, or an empty string.
    """
    credits_file = SONGS_DIR / "credits.txt"
    if not credits_file.exists():
        return ""
    name = Path(song_path).name
    for line in credits_file.read_text(encoding="utf-8").splitlines():
        if "|" in line:
            file_name, credit = (part.strip() for part in line.split("|", 1))
            if file_name == name:
                return credit
    return ""
