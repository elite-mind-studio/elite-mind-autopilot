# Autopilot (scheduled posting)

`Backend/autopilot.py` queues videos without the frontend, so generation and YouTube upload can run on a schedule.

## How it works

1. A scheduled task runs `autopilot.py tick`.
2. It takes the first line of `topics.txt` (project root), moves it to `topics_used.txt`, and queues a job.
3. When `topics.txt` is empty, it asks Ollama for new topics based on `AUTOPILOT_NICHE`.
4. The worker generates the video and uploads it if `Backend/client_secret.json` exists.

## Setup

1. Set up YouTube upload: put your OAuth `client_secret.json` (Desktop app) in `Backend/`.
2. Log in once (opens a browser, saves `Backend/youtube-oauth2.json`):

   ```bash
   uv run python Backend/autopilot.py auth
   ```

3. Register the Windows scheduled tasks (worker at logon + a check every 30 minutes):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\setup_autopilot.ps1
   powershell -ExecutionPolicy Bypass -File scripts\setup_autopilot.ps1 -Remove
   ```

   The check runs `autopilot.py tick`. Each day it picks one random time inside each posting window
   (`AUTOPILOT_WINDOWS`, default `07:00,12:00,17:00`, each `AUTOPILOT_WINDOW_MINUTES` long, default 180),
   and queues any post that is due. If the PC was off, missed posts are caught up one at a time once it is
   back on, and a failed post is retried once. Today's times are stored in `autopilot_state.json`.

   On Linux/macOS, use cron instead, e.g. `0 8,13,19 * * * cd /path/to/MoneyPrinter && uv run python Backend/autopilot.py tick`.

## GitHub Actions

`.github/workflows/autopilot.yml` runs the same flow on GitHub's servers (3x a day with a random 0-3 hour delay), using `autopilot.py run`. Store keys as repository secrets: `PIXABAY_API_KEY`, `TIKTOK_SESSION_ID`, `YOUTUBE_CLIENT_SECRET_JSON` (contents of `client_secret.json`) and `YOUTUBE_OAUTH_JSON` (contents of `youtube-oauth2.json`). Don't run it alongside the local scheduler, or videos will be posted twice.

## Long-form videos

Set `AUTOPILOT_LONG_DAYS` (e.g. `mon,wed,fri`) and `tick` adds a long-form slot on those days: a 1920x1080,
8-12 minute video with a deeper, multi-chapter script. Run one by hand with `autopilot.py run --long`
(or `queue --long`). Long videos count toward YouTube's watch-hour requirement; Shorts views do not.
The GitHub workflow posts one Short daily and a long video on Mon/Wed/Fri.

## YouTube compliance

- `YOUTUBE_SYNTHETIC_MEDIA=true` sets YouTube's "altered or synthetic content" disclosure on every upload.
- `YOUTUBE_DESCRIPTION_FOOTER` is appended to every description (e.g. "Educational content only - not financial advice").
- Scripts are told as education (psychology, history, examples), never personal financial advice, and avoid invented statistics.
- `YOUTUBE_PLAYLIST` adds every upload to one playlist, created on first use.

## AI visuals

Set `VISUAL_MODE="ai_images"` to replace stock footage with one AI-generated image per ~5 seconds of narration,
animated with a slow zoom/pan and cut on sentence boundaries. Ollama writes a visual description per scene;
images come from the first working provider in `IMAGE_PROVIDERS`:

1. **Pollinations** - free key at https://enter.pollinations.ai/keys -> `POLLINATIONS_API_KEY`.
2. **Cloudflare Workers AI** (Flux Schnell) - free account at https://dash.cloudflare.com, then
   `CLOUDFLARE_ACCOUNT_ID` and an API token using the "Workers AI" template -> `CLOUDFLARE_API_TOKEN`.
3. **Local** Stable Diffusion Turbo on CPU - install once with
   `uv pip install torch --index-url https://download.pytorch.org/whl/cpu` and `uv pip install diffusers transformers accelerate`.

If too few images can be made, the video falls back to stock footage.

Each sentence (long ones split at natural pauses) gets its own image, so the picture changes when
the narration does. The prompt writer picks the key word of each line and shows it literally
(places, objects, people named). With `STORY_CHARACTER` and `STORY_CHARACTER_LOOK` set, scripts are told
as a story about that character and he appears in most images with the same described look.

## Notes

- Test without posting: `uv run python Backend/autopilot.py queue --no-upload`.
- YouTube treats vertical videos up to 3 minutes as Shorts; keep `AUTOPILOT_PARAGRAPHS` low enough to stay under that.

- Set `VIDEO_CALL_TO_ACTION` to add a spoken outro (e.g. "Like, share and subscribe!") to every video.

- The PC must be on, with Ollama and the worker running, at posting times.
- Set the Google OAuth consent screen to **In production**. In **Testing** mode, refresh tokens expire after 7 days and uploads stop until you run `auth` again.
- Videos uploaded from an unverified Google API project are locked to private. Request a YouTube API audit to publish publicly.

## Owner thumbnails and phone alerts

- `NTFY_TOPIC` (GitHub secret): ntfy.sh topic for push alerts. Subscribe to it in the ntfy app.
- About 6 am WAT, Vercel Cron starts `headline.yml` with `mode=plan`. It picks and writes the day's documentary
  (`scripts/auto/<date>_planned.json`) and sends the title. The midday run posts that planned script.
- On Fridays the same alert also gives Elite Mind's next long topic (first line of `topics_long.txt`).
- Upload on the dashboard page `/thumb` (saves `thumbnails/<channel>_<YYYY-MM-DD>.jpg`; no password, today or tomorrow only; the token needs Contents write) before the long video posts
  and it replaces the automatic thumbnail. The run pulls the newest copy just before posting.
- After posting, `Backend/thumb.py <channel> <video|latest-long> <image> [--title "..."]` swaps a thumbnail or title.
