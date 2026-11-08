# Configuration

MoneyPrinter reads configuration from `.env` (project root).

Use `.env.example` as your template.

## Required

| Variable | Description |
|---|---|
| `TIKTOK_SESSION_ID` | TikTok session cookie (`sessionid`) used for TTS voice endpoint calls. |
| `PEXELS_API_KEY` or `PIXABAY_API_KEY` | API key used to fetch stock video clips. Set at least one; Pexels is used when both are set. |

## Optional

| Variable | Description | Default |
|---|---|---|
| `IMAGEMAGICK_BINARY` | Absolute path to ImageMagick executable. If empty, auto-detected from `PATH`. | auto-detect |
| `OLLAMA_BASE_URL` | Ollama server base URL used for model listing and chat generation. | `http://localhost:11434` |
| `OLLAMA_MODEL` | Fallback model if frontend does not send a model value. | `llama3.1:8b` |
| `ASSEMBLY_AI_API_KEY` | If set, subtitles are generated with AssemblyAI; otherwise local subtitle generation is used. | empty |
| `POSTGRES_DB` | Database name for Docker Postgres service. | `moneyprinter` |
| `POSTGRES_USER` | Database user for Docker Postgres service. | `moneyprinter` |
| `POSTGRES_PASSWORD` | Database password for Docker Postgres service. | `moneyprinter` |
| `DATABASE_URL` | SQLAlchemy DSN used by API and worker (`postgresql+psycopg://...` or `sqlite:///...`). | `sqlite:///moneyprinter.db` |
| `OLLAMA_TIMEOUT` | Seconds to wait for an Ollama response. Raise it (e.g. `600`) on CPU-only machines. | `180` |
| `YOUTUBE_CATEGORY_ID` | YouTube category for uploads (`27` Education, `22` People & Blogs, `28` Science & Technology). | `28` |
| `YOUTUBE_PRIVACY_STATUS` | `private`, `unlisted` or `public`. Unverified Google API projects can only upload private videos. | `private` |
| `AUTOPILOT_NICHE` | Niche used by `autopilot.py` to generate new topics when `topics.txt` runs out. | mindset/self-improvement |
| `AUTOPILOT_VOICE` | Voice(s) for autopilot videos: TikTok names (`en_us_006`), Microsoft Edge neural voices (`en-US-GuyNeural`) or local Kokoro voices (`kokoro:am_onyx`; model auto-downloads to `models/`, adds natural pauses at `...`, questions and sentence ends). Comma-separated list picks one at random per video. | `en_us_006` |
| `AUTOPILOT_PARAGRAPHS` | Script length (paragraphs) for autopilot videos. About 25-30 seconds per paragraph; `4` gives roughly 1-2 minutes. | `3` |
| `AUTOPILOT_WINDOWS` | Comma-separated start times of the daily posting windows used by `autopilot.py tick`. | `07:00,12:00,17:00` |
| `AUTOPILOT_WINDOW_MINUTES` | Length of each posting window; one random time is picked inside it each day. | `180` |
| `AUTOPILOT_LONG_DAYS` | Weekdays (e.g. `mon,wed,fri`) on which `tick` also posts a long-form (8-12 min, 1920x1080) video. Empty disables long videos. | empty |
| `AUTOPILOT_LONG_WINDOW` / `AUTOPILOT_LONG_WINDOW_MINUTES` | Start and length of the daily window for long videos. | `14:00` / `240` |
| `AUTOPILOT_LONG_PARAGRAPHS` / `AUTOPILOT_LONG_MAX_WORDS` | Script length for long videos (~150 spoken words per minute). | `14` / `1600` |
| `AUTOPILOT_LONG_SCENE_SECONDS` | Seconds of narration per AI image in long videos. | `6` |
| `AUTOPILOT_STOCK_VIDEOS` | Number of distinct stock clips per autopilot video. Raise it for longer videos so footage doesn't loop. | `5` |
| `VIDEO_MAX_WORDS` | Hard cap on script length; longer scripts are cut at a sentence boundary. ~3 spoken words per second, so `290` keeps videos under about 2 minutes. `0` disables. | `0` |
| `VISUAL_MODE` | `stock` (Pexels/Pixabay footage) or `ai_images` (AI-generated image per scene with slow zoom/pan; falls back to stock if images fail). | `stock` |
| `IMAGE_PROVIDERS` | Order of image providers to try for `ai_images`: `pollinations`, `cloudflare`, `local`. A provider that fails twice is skipped for the rest of the video. | `pollinations,cloudflare,local` |
| `IMAGE_SCENE_SECONDS` | Approximate seconds of narration per AI image; lower = images change more often and follow the words more closely. | `5` |
| `IMAGE_STYLE` | Style text appended to every image prompt. | cinematic, clean, photorealistic... |
| `POLLINATIONS_API_KEY` | Free key from https://enter.pollinations.ai/keys. | empty |
| `POLLINATIONS_MODELS` | Pollinations models to try in order. | `zimage,flux` |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI credentials (free tier, token with Workers AI permission). | empty |
| `LOCAL_SD_MODEL` | Hugging Face model for the `local` provider (needs `torch` and `diffusers` installed separately). | `stabilityai/sd-turbo` |
| `TEXT_PROVIDER` | `ollama` (local) or `pollinations` (hosted model via `POLLINATIONS_API_KEY`, falls back to Ollama on failure). Used for scripts, search terms, image prompts and metadata. | `ollama` |
| `POLLINATIONS_TEXT_MODEL` | Pollinations text model when `TEXT_PROVIDER=pollinations`. | `openai-mini` |
| `STORY_CHARACTER` | Optional recurring character the lesson is told through (e.g. `Black`). | empty |
| `STORY_CHARACTER_LOOK` | Fixed appearance inserted into every image prompt so the character stays recognizable. | empty |
| `YOUTUBE_SYNTHETIC_MEDIA` | `true` marks uploads with YouTube's "altered or synthetic content" disclosure (required for realistic AI visuals). | empty |
| `YOUTUBE_DESCRIPTION_FOOTER` | Text appended to every upload's description, e.g. a not-financial-advice note. | empty |
| `YOUTUBE_PLAYLIST` | Playlist title every upload is added to (created if missing). | empty |
| `VIDEO_CALL_TO_ACTION` | Spoken outro appended to every script, e.g. `Like, share and subscribe!`. | empty |

## Notes

- Ollama models shown in the frontend are fetched from backend endpoint `/api/models`, which queries `OLLAMA_BASE_URL/api/tags`.
- Pull models before use, for example:

```bash
ollama pull llama3.1:8b
```

- If ImageMagick is not discovered automatically, set `IMAGEMAGICK_BINARY` explicitly.
- New architecture uses a database-backed job queue. In Docker, use Postgres via `DATABASE_URL`.
