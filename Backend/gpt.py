import re
import os
import json
from ollama import Client, ResponseError

from dotenv import load_dotenv
from logstream import log
from typing import Tuple, List, Optional
from utils import ENV_FILE

# Load environment variables
load_dotenv(ENV_FILE)

# Set environment variables
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "180"))


def _ollama_client() -> Client:
    return Client(host=OLLAMA_BASE_URL, timeout=OLLAMA_TIMEOUT)


def _extract_model_name(model_obj) -> str:
    if hasattr(model_obj, "model") and getattr(model_obj, "model"):
        return str(getattr(model_obj, "model")).strip()
    if hasattr(model_obj, "name") and getattr(model_obj, "name"):
        return str(getattr(model_obj, "name")).strip()
    if isinstance(model_obj, dict):
        return str(model_obj.get("model") or model_obj.get("name") or "").strip()
    return ""


def list_ollama_models() -> Tuple[List[str], str]:
    """
    Returns available Ollama model names and configured default model.

    Returns:
        Tuple[List[str], str]: (available model names, default model)
    """
    try:
        response = _ollama_client().list()
    except Exception as err:
        raise RuntimeError(f"Failed to fetch Ollama models: {err}") from err

    models = []
    if hasattr(response, "models") and getattr(response, "models") is not None:
        models = list(getattr(response, "models"))
    elif isinstance(response, dict):
        models = response.get("models") or []

    model_names = [_extract_model_name(model) for model in models]
    model_names = [name for name in model_names if name]

    unique_names = list(dict.fromkeys(model_names))

    if OLLAMA_MODEL and OLLAMA_MODEL in unique_names:
        default_model = OLLAMA_MODEL
    elif unique_names:
        default_model = unique_names[0]
    else:
        default_model = OLLAMA_MODEL if OLLAMA_MODEL else ""

    return unique_names, default_model


def unload_model(ai_model: str) -> None:
    """
    Ask Ollama to free the model from memory (it is reloaded on next use).
    Rendering video needs that RAM on machines without a GPU.

    Args:
        ai_model (str): The model to unload.
    """
    model_name = (ai_model or "").strip() or OLLAMA_MODEL
    try:
        _ollama_client().generate(model=model_name, prompt="", keep_alive=0)
    except Exception as err:
        log(f"[-] Could not unload Ollama model: {err}", "warning")


def generate_response(prompt: str, ai_model: str) -> str:
    """
    Generate a script for a video, depending on the subject of the video.

    Args:
        video_subject (str): The subject of the video.
        ai_model (str): The AI model to use for generation.


    Returns:

        str: The response from the AI model.

    """

    model_name = (ai_model or "").strip() or OLLAMA_MODEL

    # Optional: use a fast hosted model, falling back to local Ollama on failure
    if os.getenv("TEXT_PROVIDER", "ollama").strip().lower() in ("pollinations", "hosted"):
        try:
            return hosted_chat(prompt).strip()
        except Exception as err:
            log(f"[-] Hosted text failed, using Ollama: {err}", "warning")

    try:
        client = _ollama_client()
        try:
            response = client.chat(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
            )
        except ResponseError as err:
            if err.status_code == 404:
                try:
                    response = client.generate(
                        model=model_name, prompt=prompt, stream=False
                    )
                except ResponseError as fallback_err:
                    if (
                        fallback_err.status_code == 404
                        and "not found" in str(fallback_err).lower()
                    ):
                        available_models, _ = list_ollama_models()
                        available = (
                            ", ".join(available_models) if available_models else "none"
                        )
                        raise RuntimeError(
                            f"Ollama model '{model_name}' is not installed. Available models: {available}. "
                            f"Install it with: ollama pull {model_name}"
                        ) from fallback_err
                    raise
            else:
                raise
    except RuntimeError:
        raise
    except Exception as err:
        raise RuntimeError(f"Failed to connect to Ollama: {err}") from err

    content = ""
    if hasattr(response, "message") and getattr(response, "message") is not None:
        message = getattr(response, "message")
        if hasattr(message, "content") and getattr(message, "content"):
            content = str(getattr(message, "content")).strip()
        elif isinstance(message, dict):
            content = str(message.get("content") or "").strip()

    if not content:
        if hasattr(response, "response") and getattr(response, "response"):
            content = str(getattr(response, "response")).strip()
        elif isinstance(response, dict):
            content = (
                str(response.get("message", {}).get("content") or "")
                or str(response.get("response") or "")
            ).strip()

    if not content:
        raise RuntimeError("Ollama returned an empty response.")

    return content


def generate_script(
    video_subject: str,
    paragraph_number: int,
    ai_model: str,
    voice: str,
    customPrompt: str,
    video_format: str = "short",
    script_style: Optional[str] = None,
    series: Optional[dict] = None,
) -> Optional[str]:
    """
    Generate a script for a video, depending on the subject of the video, the number of paragraphs, and the AI model.



    Args:

        video_subject (str): The subject of the video.

        paragraph_number (int): The number of paragraphs to generate.

        ai_model (str): The AI model to use for generation.



    Returns:

        str: The script for the video.

    """

    # Build prompt

    if customPrompt:
        prompt = customPrompt
    else:
        prompt = """
            Generate a script for a video, depending on the subject of the video.

            The script is to be returned as a string with the specified number of paragraphs.

            Here is an example of a string:
            "This is an example string."

            Do not under any circumstance reference this prompt in your response.

            Get straight to the point, don't start with unnecessary things like, "welcome to this video".

            The very first sentence MUST be a strong hook that stops viewers from scrolling:
            a bold claim, a surprising fact, or a direct question to the viewer.
            Keep sentences short and punchy.
            Write for the ear, like a gripping narrator: mix short and long sentences, use
            rhetorical questions, and put "..." where a dramatic pause belongs, right before a
            reveal or twist (e.g. "He looked at the price again... and smiled."). Use "..." at
            most two or three times per paragraph.
            Every paragraph must have 4 to 6 sentences.

            Obviously, the script should be related to the subject of the video.

            YOU MUST NOT INCLUDE ANY TYPE OF MARKDOWN OR FORMATTING IN THE SCRIPT, NEVER USE A TITLE.
            YOU MUST WRITE THE SCRIPT IN THE LANGUAGE SPECIFIED IN [LANGUAGE].
            ONLY RETURN THE RAW CONTENT OF THE SCRIPT. DO NOT INCLUDE "VOICEOVER", "NARRATOR" OR SIMILAR INDICATORS OF WHAT SHOULD BE SPOKEN AT THE BEGINNING OF EACH PARAGRAPH OR LINE. YOU MUST NOT MENTION THE PROMPT, OR ANYTHING ABOUT THE SCRIPT ITSELF. ALSO, NEVER TALK ABOUT THE AMOUNT OF PARAGRAPHS OR LINES. JUST WRITE THE SCRIPT.

        """

    # Keep content educational: YouTube won't monetize AI personas giving financial
    # advice, and invented numbers hurt trust
    prompt += """
            Explain how and why the idea works (psychology, history, real-world examples).
            Never give personal financial, investment, legal or medical advice, and never tell
            the viewer what to buy, sell or invest in.
            Do not invent statistics or percentages. Only use numbers that are part of a
            well-known principle or rule (e.g. the 80/20 rule, the rule of 72).
        """

    if video_format == "long":
        prompt += """
            This is a LONG-FORM video of about 8 to 12 minutes. Go deeper: tell a fuller story
            in several chapters, connect the main idea to two or three related principles,
            and use multiple concrete real-world examples. Keep every paragraph moving forward.
        """

    # Recurring story character, e.g. "Mr. Black", who the lesson is told through
    character = os.getenv("STORY_CHARACTER", "").strip()
    script_style = (script_style or os.getenv("SCRIPT_STYLE", "story")).strip().lower()
    if series:
        part, parts = series["part"], series["parts"]
        recap = (
            f"Right after the opening line, add ONE short sentence that recaps the previous "
            f"part of the series, then move on."
            if part > 1 else "This is the first part, so briefly promise the viewer the whole series."
        )
        ending = (
            f'End with a one-sentence cliffhanger that teases the next part: "{series["next"]}", '
            f"making the viewer curious, but do NOT ask them to follow or subscribe (that is added separately)."
            if part < parts and series.get("next")
            else "This is the final part: end with a strong one-sentence takeaway that sums up the whole series."
        )
        prompt += f"""
            This video is PART {part} OF {parts} of the series "{series['title']}".
            This part covers: {series.get('episode') or video_subject}. Cover exactly 3 items in it.
            The opening line should mention it is part {part} of {parts}.
            {recap}
            {ending}
            Keep it practical, ethical and realistic: confidence and social skills, not manipulation.
        """

    if script_style == "pov":
        prompt += """
            Style: an immersive second-person "POV" story, like the hit YouTube channel
            format "POV: You're a Trillionaire". The viewer IS the main character: always
            say "you" ("You wake up...", "You walk into..."), present tense.
            1. Open with one gripping sentence that drops the viewer straight into the
               scenario (e.g. "You wake up, check your phone, and your bank account now
               has ten million dollars in it.").
            2. Build the story in clear stages that ESCALATE - each stage bigger, crazier
               or more surprising than the last (first hour, first day, first week, first
               year...). Every few lines add a twist, a consequence or a funny detail.
            3. Weave real facts and money psychology into the story naturally (how things
               actually work: taxes, lifestyle inflation, how the rich protect wealth),
               without telling the viewer what to buy or invest in.
            4. Describe concrete, visual moments (places, objects, people, actions) so every
               line can be shown on screen.
            5. End with a twist or a reflective line that makes the viewer think.
            LENGTH: this is a long video. Write about {target_words} words in total, with
            6 to 10 sentences per stage. Do not stop early.
            Paragraphs: one per stage.
        """.replace("{target_words}", str(int(os.getenv("AUTOPILOT_LONG_MAX_WORDS", "1500")) - 100))
        character = ""

    if script_style == "listicle":
        prompt += """
            Style: a fast, punchy, casual numbered-list explainer, talking directly to the
            viewer as "you", like a confident friend.
            0. LENGTH IS STRICT: the whole script must be under {max_words} words and must
               cover EVERY item promised in the title. Keep each item to about 25-35 words.
            1. Open with ONE bold-claim sentence that states how many items and challenges
               the viewer, e.g. "Stores are stealing from you every day - here are 4 tricks
               they use, and I bet you've fallen for at least 3." Then go straight to
               "Number one." - no extra intro sentences.
            2. Then each item paragraph MUST start exactly like this, as two separate short
               sentences: "Number one. Living paycheck to paycheck." - the number spelled out
               and followed by a full stop, then the item name and a full stop. Never write
               "Number one is..." or "Number one, ..." and never mention a number in the opening.
               After that, 3 to 5 punchy sentences explaining it with
               funny, concrete everyday examples (a coffee shop, a sale at the mall, a 3 a.m.
               online order). Use lists of three and rhetorical questions.
            3. Repeat a short catchy phrase tied to the topic across items.
            4. End with one short, quotable line that sums it all up and leads naturally
               back into the opening line, so the video loops seamlessly when replayed.
            Paragraphs: one per item (the opening is its own paragraph).
        """.replace("{max_words}", str(int(os.getenv("VIDEO_MAX_WORDS", "450") or 450) - 20))
        # The opening + one paragraph per item must all fit
        numbers = re.findall(r"\b(\d+)\b", video_subject)
        if numbers:
            paragraph_number = max(paragraph_number, int(numbers[0]) + 1)
        character = ""

    if character:
        prompt += f"""
            Structure:
            1. The FIRST sentence is a hook about the theory itself, not about the character:
               a "Why does...?" / "Why do...?" question or a bold claim that makes the viewer
               curious (e.g. "Why does the medium coffee cost almost as much as the large one?").
               Do not mention {character} in the first sentence.
            2. Then tell the lesson as a short, vivid story about a businessman named {character}
               who appears in every video (always call him just "{character}", never "Mr.").
               Show what {character} sees, does and learns, with concrete places, people and
               objects (cities, streets, shops, offices, money). Weave the explanation of the idea
               into his story. {character} is a character in the story, not a presenter: he never
               speaks to the viewer and never gives advice.
            3. End by landing the lesson clearly for the viewer.
        """

    # A spoken outro is appended by the pipeline, so the model must not add its own
    if os.getenv("VIDEO_CALL_TO_ACTION", "").strip():
        prompt += """
            Do not end with a call to action such as "like and subscribe", it is added separately.
        """

    prompt += f"""

    Subject: {video_subject}
    Number of paragraphs: {paragraph_number}
    Language: English

    """

    # Generate script
    response = generate_response(prompt, ai_model)

    log(response, "info")

    # Reject provider error text or a suspiciously short reply instead of
    # narrating it
    if response and (_looks_like_service_error(response) or len(response.split()) < 40):
        log(f"[-] Rejected invalid script: {response[:120]}", "error")
        return None

    # Return the generated script
    if response:
        # Clean the script
        # Remove asterisks, hashes
        response = response.replace("*", "")
        response = response.replace("#", "")

        # Remove markdown syntax
        response = re.sub(r"\[.*\]", "", response)
        response = re.sub(r"\(.*\)", "", response)

        # Drop chatty preambles like "Here is the script:" so they aren't narrated
        response = re.sub(
            r"^\s*((here('s| is| are)|sure|certainly|okay)\b[^\n.!?]*:|(sure|certainly|okay)[!.,])\s*",
            "",
            response,
            flags=re.IGNORECASE,
        )

        # Split the script into paragraphs
        paragraphs = response.split("\n\n")

        # Select the specified number of paragraphs
        selected_paragraphs = paragraphs[:paragraph_number]

        # Join the selected paragraphs into a single string
        final_script = "\n\n".join(selected_paragraphs)

        # Print to console the number of paragraphs used
        log(f"Number of paragraphs used: {len(selected_paragraphs)}", "success")

        return final_script
    else:
        log("[-] GPT returned an empty response.", "error")
        return None


def get_search_terms(
    video_subject: str, amount: int, script: str, ai_model: str
) -> List[str]:
    """
    Generate a JSON-Array of search terms for stock videos,
    depending on the subject of a video.

    Args:
        video_subject (str): The subject of the video.
        amount (int): The amount of search terms to generate.
        script (str): The script of the video.
        ai_model (str): The AI model to use for generation.

    Returns:
        List[str]: The search terms for the video subject.
    """

    # Build prompt
    prompt = f"""
    Generate {amount} search terms for stock videos,
    depending on the subject of a video.
    Subject: {video_subject}

    The search terms are to be returned as
    a JSON-Array of strings.

    Each search term should consist of 1-3 words,
    always add the main subject of the video.
    
    YOU MUST ONLY RETURN THE JSON-ARRAY OF STRINGS.
    YOU MUST NOT RETURN ANYTHING ELSE. 
    YOU MUST NOT RETURN THE SCRIPT.
    
    The search terms must be related to the subject of the video.
    Here is an example of a JSON-Array of strings:
    ["search term 1", "search term 2", "search term 3"]

    For context, here is the full text:
    {script}
    """

    # Generate search terms
    response = generate_response(prompt, ai_model)
    log(response, "info")

    # Parse response into a list of search terms
    search_terms = []

    try:
        search_terms = json.loads(response)
        if not isinstance(search_terms, list) or not all(
            isinstance(term, str) for term in search_terms
        ):
            raise ValueError("Response is not a list of strings.")

    except (json.JSONDecodeError, ValueError):
        log("[*] GPT returned an unformatted response. Attempting to clean...", "warning")

        # Attempt to extract JSON array first
        match = re.search(r"\[[\s\S]*\]", response)
        if match:
            try:
                search_terms = json.loads(match.group())
            except json.JSONDecodeError:
                search_terms = []

        # Last-resort fallback: collect quoted strings
        if not search_terms:
            search_terms = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', response)
            search_terms = [term.strip() for term in search_terms if term.strip()]

    # Let user know
    log(f"\nGenerated {len(search_terms)} search terms: {', '.join(search_terms)}", "info")

    # Return search terms
    return search_terms


def pollinations_chat(prompt: str, timeout: int = 90) -> str:
    """
    Run a prompt on a Pollinations text model (free with POLLINATIONS_API_KEY).
    Much faster and stronger than a local CPU model for short structured tasks.

    Args:
        prompt (str): The user prompt.
        timeout (int): Request timeout in seconds.

    Returns:
        str: The model's reply.
    """
    import requests

    key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("POLLINATIONS_API_KEY not set")
    response = requests.post(
        "https://gen.pollinations.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.getenv("POLLINATIONS_TEXT_MODEL", "openai-mini"),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    # Pollinations sometimes answers "out of credits" as a normal chat reply;
    # never let that text be used as a script or prompt
    if _looks_like_service_error(content):
        raise RuntimeError(f"Pollinations returned a service message: {content[:120]}")
    return content


def cloudflare_chat(prompt: str, timeout: int = 120) -> str:
    """
    Run a prompt on a Cloudflare Workers AI text model (free daily allowance,
    same CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN as image generation).

    Args:
        prompt (str): The user prompt.
        timeout (int): Request timeout in seconds.

    Returns:
        str: The model's reply.
    """
    import requests

    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")
    model = os.getenv("CLOUDFLARE_TEXT_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    response = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}",
        headers={"Authorization": f"Bearer {token}"},
        json={"messages": [{"role": "user", "content": prompt}], "max_tokens": 4096},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"Cloudflare text HTTP {response.status_code}: {response.text[:200]}")
    content = response.json().get("result", {}).get("response", "")
    if not isinstance(content, str):
        content = json.dumps(content)
    if not content.strip() or _looks_like_service_error(content):
        raise RuntimeError(f"Cloudflare text returned no usable reply: {content[:120]}")
    return content


def groq_chat(prompt: str, timeout: int = 120) -> str:
    """
    Run a prompt on Groq (free tier, no card; key from console.groq.com).

    Args:
        prompt (str): The user prompt.
        timeout (int): Request timeout in seconds.

    Returns:
        str: The model's reply.
    """
    import requests

    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            "messages": [{"role": "user", "content": prompt}],
            # gpt-oss is a reasoning model: keep its thinking short and leave
            # room for long scripts, or the answer can come back empty
            "reasoning_effort": "low",
            "max_completion_tokens": 8192,
        },
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"Groq HTTP {response.status_code}: {response.text[:200]}")
    content = response.json()["choices"][0]["message"].get("content") or ""
    if not content.strip() or _looks_like_service_error(content):
        raise RuntimeError(f"Groq returned no usable reply: {content[:120]}")
    return content


def hosted_chat(prompt: str) -> str:
    """
    Try the hosted text providers in TEXT_PROVIDERS order (default
    "groq,pollinations,cloudflare"); raises if all of them fail.
    """
    errors = []
    order = os.getenv("TEXT_PROVIDERS", "groq,pollinations,cloudflare")
    for provider in [p.strip() for p in order.split(",") if p.strip()]:
        try:
            if provider == "groq":
                return _normalize_text(groq_chat(prompt))
            if provider == "pollinations":
                return _normalize_text(pollinations_chat(prompt))
            if provider == "cloudflare":
                return _normalize_text(cloudflare_chat(prompt))
        except Exception as err:
            errors.append(f"{provider}: {str(err)[:120]}")
    raise RuntimeError("; ".join(errors) or "no hosted text provider configured")


# Typographic characters some models emit; they break the Windows console and
# can confuse text-to-speech. Em/en dashes are kept (they mark dramatic pauses).
_CHAR_FIXES = {
    " ": " ", " ": " ", " ": " ", "​": "",
    "‐": "-", "‑": "-", "‒": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
}


def _normalize_text(text: str) -> str:
    for bad, good in _CHAR_FIXES.items():
        text = text.replace(bad, good)
    return text


def _looks_like_service_error(text: str) -> bool:
    """True for provider error/billing messages returned as normal text."""
    lowered = (text or "").lower()
    # Real error replies are short; a long script that happens to say "top up"
    # or "quota" in its story is not one
    if len((text or "").split()) > 120:
        return False
    markers = (
        "api key", "top up", "top-up", "enough credits", "insufficient balance",
        "pollinations", "rate limit", "quota", "complete a quest",
    )
    return any(marker in lowered for marker in markers)


def generate_image_prompts(
    video_subject: str, scenes: List[str], ai_model: str
) -> List[str]:
    """
    Write one literal image description per narration scene, so each picture
    shows exactly what is being said at that moment. Uses Pollinations when a
    key is set, otherwise the local Ollama model. Falls back to the scene text
    itself for any prompt the model doesn't return.

    Args:
        video_subject (str): The subject of the video.
        scenes (List[str]): Narration text of each scene, in order.
        ai_model (str): The Ollama model to use as fallback.

    Returns:
        List[str]: One image prompt per scene.
    """
    numbered = "\n".join(f"{i + 1}. \"{scene}\"" for i, scene in enumerate(scenes))
    prompt = f"""
You are picking the picture shown on screen while each line of a voiceover is spoken.
Video topic: {video_subject}

Rules:
- The picture must show EXACTLY what the line says, literally, so viewers instantly connect
  the words to the image. First pick the single most important concept/word in the line
  (e.g. "revenue", "customers", "debt", "coffee sizes", "interest"), then describe a realistic
  photo where that concept is the obvious main subject.
  Example: "Most of your revenue comes from a few loyal customers" -> keyword "revenue" ->
  "a cash register drawer overflowing with banknotes as a few regular customers pay at the counter".
- Abstract ideas must become concrete money/business visuals (growth -> a stack of coins
  getting taller; risk -> a man on a tightrope over a city; time -> an hourglass on a desk with cash).
- Anything named must appear exactly: a place named -> that place (Italy -> an Italian piazza
  with Italian architecture; Wall Street -> Wall Street); "streets" -> people walking on a busy street;
  a person or job named -> that person/job; an object named -> that object.
- {{image_kind}}. One clear subject. Max 30 words.
- No readable text, words, numbers, signs, logos or charts in the picture.
{{character_rule}}
Lines:
{numbered}

Return ONLY a JSON array with exactly {len(scenes)} objects in the same order:
[{{"keyword": "...", "image": "..."}}, ...]
"""

    character = os.getenv("STORY_CHARACTER", "").strip()
    look = os.getenv("STORY_CHARACTER_LOOK", "").strip()
    character_rule = ""
    if character and look:
        character_rule = (
            f"- {character} is the recurring main character. Put him in most pictures, written as the "
            f"token CHARACTER, actively doing what the line describes or pointing at / holding the key "
            f"thing (e.g. 'CHARACTER pointing at three coffee cups on a cafe counter'). Leave him out only "
            f"when the line is clearly about other people or a place on its own."
        )
    prompt = prompt.replace("{character_rule}", character_rule)
    prompt = prompt.replace(
        "{image_kind}", os.getenv("IMAGE_PROMPT_KIND", "Realistic, cinematic photo")
    )

    items: List = []
    for source in ("hosted", "ollama"):
        try:
            if source == "hosted":
                response = hosted_chat(prompt)
            else:
                response = generate_response(prompt, ai_model)
            items = _parse_prompt_items(response)
            if len(items) >= len(scenes):
                break
            log(f"[-] {source} returned {len(items)}/{len(scenes)} image prompts", "warning")
        except Exception as err:
            log(f"[-] Could not generate image prompts with {source}: {err}", "warning")

    prompts: List[str] = []
    for i, scene in enumerate(scenes):
        item = items[i] if i < len(items) else None
        if isinstance(item, dict) and item.get("image"):
            keyword = str(item.get("keyword", "")).strip()
            image = str(item["image"]).strip()
            if character_rule:
                # Same detailed description every time keeps the character recognizable
                image = re.sub(r"\bCHARACTER\b", f"{character} ({look})", image)
            prompts.append(f"{image} (main subject: {keyword})" if keyword else image)
        elif isinstance(item, str) and item.strip():
            prompts.append(item.strip())
        else:
            # Keep the channel's look even when the model's output was unusable
            kind = os.getenv("IMAGE_PROMPT_KIND", "Realistic, cinematic photo")
            subject = f"{character} ({look}) " if character_rule else ""
            prompts.append(f"{kind} of {subject}illustrating: {scene}")
    return prompts


def generate_stock_queries(video_subject: str, scenes: List[str]) -> List[str]:
    """
    Write one short stock-footage search phrase per narration scene, naming
    something that can actually be filmed (e.g. "crowded shopping mall",
    "phone banking app", "man counting cash"), so each clip matches its line.

    Args:
        video_subject (str): The subject of the video.
        scenes (List[str]): Narration text of each scene, in order.

    Returns:
        List[str]: One search phrase per scene.
    """
    numbered = "\n".join(f'{i + 1}. "{scene}"' for i, scene in enumerate(scenes))
    prompt = f"""
You are picking stock video clips (from a free stock footage site) to play while each line
of a voiceover is spoken. Video topic: {video_subject}

For EACH numbered line, write ONE search phrase of 2 to 4 words describing real footage
that literally shows what the line talks about. Use concrete, filmable things: people,
places, objects, actions (e.g. "crowded shopping mall", "woman paying with card",
"stack of dollar bills", "man stressed bills", "city skyline night").
Never use abstract words like "psychology", "strategy", "concept" or brand names.

Lines:
{numbered}

Return ONLY a JSON array with exactly {len(scenes)} strings, in the same order.
"""
    queries: List[str] = []
    try:
        response = hosted_chat(prompt)
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if match:
            queries = [str(q).strip() for q in json.loads(match.group(0)) if str(q).strip()]
    except Exception as err:
        log(f"[-] Could not generate stock queries: {err}", "warning")

    queries = queries[: len(scenes)]
    fallback = " ".join(video_subject.split()[:3])
    while len(queries) < len(scenes):
        queries.append(fallback)
    return queries


def _parse_prompt_items(response: str) -> List:
    """Parse the JSON array of image prompts, salvaging items from broken JSON."""
    match = re.search(r"\[.*\]", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    # Salvage each {"keyword": ..., "image": ...} object that parses on its own
    items = []
    for chunk in re.findall(r"\{[^{}]*\}", response, re.DOTALL):
        try:
            items.append(json.loads(chunk))
        except json.JSONDecodeError:
            image = re.search(r'"image"\s*:\s*"([^"]+)', chunk)
            if image:
                items.append({"image": image.group(1)})
    return items


def _clean_metadata_text(text: str) -> str:
    """
    Strip chatty preambles, markdown and wrapping quotes from model output.

    Args:
        text (str): Raw model output.

    Returns:
        str: The cleaned text.
    """
    lines = [line.strip() for line in text.strip().splitlines()]

    # Drop preamble lines such as "Here's a catchy title for your video:"
    while lines and (
        not lines[0]
        or lines[0].endswith(":")
        or re.match(r"^(here('s| is| are)|sure|certainly)\b", lines[0], re.IGNORECASE)
    ):
        lines.pop(0)

    cleaned = "\n".join(lines).replace("**", "").strip()
    cleaned = re.sub(r"^(title|description)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
    return cleaned.strip().strip('"').strip()


def generate_metadata(
    video_subject: str, script: str, ai_model: str
) -> Tuple[str, str, List[str]]:
    """
    Generate metadata for a YouTube video, including the title, description, and keywords.

    Args:
        video_subject (str): The subject of the video.
        script (str): The script of the video.
        ai_model (str): The AI model to use for generation.

    Returns:
        Tuple[str, str, List[str]]: The title, description, and keywords for the video.
    """

    # Build prompt for title
    title_prompt = f"""
    Generate a catchy and SEO-friendly title for a YouTube shorts video about {video_subject}.
    Return ONLY the title itself on a single line, under 90 characters.
    No options, no explanation, no quotes, no markdown.
    """

    # Generate title (YouTube rejects titles over 100 characters)
    title = _clean_metadata_text(generate_response(title_prompt, ai_model))
    title = title.splitlines()[0][:100] if title else video_subject[:100]

    # Build prompt for description
    description_prompt = f"""
    Write a brief and engaging description for a YouTube shorts video about {video_subject}.
    The video is based on the following script:
    {script}

    Return ONLY the description text (2-3 sentences, then 3-5 hashtags).
    No introduction like "Here is", no options, no markdown.
    """

    # Generate description
    description = _clean_metadata_text(generate_response(description_prompt, ai_model))

    # Generate keywords
    keywords = get_search_terms(video_subject, 6, script, ai_model)

    return title, description, keywords


def thumbnail_word(title: str) -> str:
    """1-2 punchy words for a thumbnail (e.g. "TRAP", "STOP", "BROKE")."""
    try:
        reply = hosted_chat(
            f'Video title: "{title}". Give ONE or TWO punchy words (max 12 characters total) '
            "for a YouTube thumbnail that create curiosity or emotion, like TRAP, STOP, "
            "BROKE, RICH, THE TRICK. Reply with the words only."
        )
        word = re.sub(r"[^A-Za-z0-9 !?$]", "", reply).strip().upper()
        if 0 < len(word) <= 14:
            return word
    except Exception:
        pass
    words = [w for w in re.findall(r"[A-Za-z]+", title) if len(w) > 3]
    return (words[0] if words else "WATCH").upper()
