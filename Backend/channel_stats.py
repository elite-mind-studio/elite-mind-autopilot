"""
Print a channel's subscriber count (0 if it can't be read), for the workflows' decisions.

Usage:
    python Backend/channel_stats.py <token file in Backend/, e.g. youtube-oauth2.json>
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def subscribers(token_file: str) -> int:
    os.environ["YOUTUBE_TOKEN_FILE"] = token_file
    try:
        import youtube

        stats = youtube.get_authenticated_service().channels().list(part="statistics", mine=True).execute()
        return int(stats["items"][0]["statistics"].get("subscriberCount", 0))
    except Exception as err:  # never block a posting run on this
        print(f"[-] Could not read subscribers: {err}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    print(subscribers(sys.argv[1] if len(sys.argv) > 1 else "youtube-oauth2.json"))
