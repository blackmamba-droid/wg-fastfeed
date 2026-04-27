from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
import json
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

SEEN_IDS_FILE = os.path.join(os.path.dirname(__file__), "seen_ids.json")
MAX_SEEN_IDS = 2000

def _load_seen_ids() -> set:
    try:
        with open(SEEN_IDS_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def _save_seen_ids(ids: set):
    trimmed = list(ids)[-MAX_SEEN_IDS:]
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(trimmed, f)

processed_ids: set = _load_seen_ids()
last_text = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")           # WatcherGuru → normaler Channel
DISCORD_WEBHOOK_URL_VIP = os.getenv("DISCORD_WEBHOOK_URL_VIP")  # Deltaone/Bloomberg → VIP Channel

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4.1-nano"

# Twitter-Username (lowercase) → (Anzeigename, webhook_key)
SOURCES = {
    "watcherguru": ("WatcherGuru", "default"),
    "deltaone":    ("Walter Bloomberg", "vip"),
}


# --------------------------------------------------
# Autor-Erkennung
# --------------------------------------------------

def detect_source(t: dict, payload: dict) -> tuple[str, str]:
    """Gibt (anzeigename, webhook_key) zurück."""

    # 1. Matching Rule Tag aus dem Payload prüfen (zuverlässigste Methode)
    rules = (
        payload.get("matching_rules")
        or payload.get("matchingRules")
        or (payload.get("data") or {}).get("matching_rules", [])
        or []
    )
    for rule in rules:
        tag = (rule.get("tag") or "").lower()
        if "vip" in tag or "bloomberg" in tag or "deltaone" in tag:
            return "Walter Bloomberg", "vip"
        if "watcherguru" in tag or "watcher" in tag:
            return "WatcherGuru", "default"

    # 2. Tweet-Autor-Felder prüfen
    author_candidates = [
        t.get("authorUsername", ""),
        t.get("author_username", ""),
        (t.get("author") or {}).get("username", ""),
        (t.get("author") or {}).get("screen_name", ""),
        (t.get("user") or {}).get("screen_name", ""),
        (t.get("user") or {}).get("username", ""),
    ]
    for candidate in author_candidates:
        key = candidate.lower().lstrip("@")
        if key in SOURCES:
            return SOURCES[key]

    # 3. Fallback: Autor aus der Tweet-URL lesen
    url = t.get("url") or t.get("twitterUrl") or ""
    for username_lower, (display, webhook_key) in SOURCES.items():
        if f"/{username_lower}/" in url.lower():
            return display, webhook_key

    return "WatcherGuru", "default"


# --------------------------------------------------
# GPT Filter & Übersetzung
# --------------------------------------------------

async def is_whale_buy_sell(text: str) -> bool:
    if not OPENAI_API_KEY:
        return False

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    prompt = (
        "Beurteile folgende Crypto News.\n\n"
        "Antwort NUR mit YES oder NO.\n\n"
        "YES = Diese Nachricht handelt davon, dass eine Person, Firma, Institution "
        "oder ein Whale Bitcoin, Ethereum oder andere Kryptowährungen kauft oder verkauft.\n\n"
        "NO = Alles andere.\n\n"
        "Beispiele YES:\n"
        "- Michael Saylor buys $1B Bitcoin\n"
        "- Company buys ETH\n"
        "- Fund sells BTC\n\n"
        "Beispiele NO:\n"
        "- ETF news\n"
        "- regulation\n"
        "- hacks\n"
        "- market analysis\n"
        "- announcements\n"
    )

    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": 3,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(OPENAI_API_URL, headers=headers, json=body)
        decision = resp.json()["choices"][0]["message"]["content"].strip()
        print("Whale Buy/Sell Bewertung:", decision)
        return decision == "YES"
    except Exception as e:
        print("Filter Fehler:", e)
        return False


async def translate_tweet(text: str) -> tuple[str, str]:
    if not OPENAI_API_KEY:
        return text[:120], "Automatische Zusammenfassung nicht verfügbar."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "Du bist ein deutschsprachiger Finanz- und Markt-News-Redakteur.\n"
        "Übersetze die Überschrift des Tweets möglichst nah ins Deutsche "
        "und fasse den Inhalt in 1-3 Sätzen neutral zusammen.\n"
        "Antworte als JSON: {\"title\": \"...\", \"summary\": \"...\"}"
    )

    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.2,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(OPENAI_API_URL, headers=headers, json=body)
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        obj = json.loads(content)
        return obj.get("title", text[:120]), obj.get("summary", "Zusammenfassung nicht verfügbar.")
    except Exception as e:
        print("GPT Fehler:", e)
        return text[:120], "Automatische Übersetzung aktuell nicht verfügbar."


# --------------------------------------------------
# Discord Versand
# --------------------------------------------------

async def send_to_discord(url: str, title: str, summary: str, source_name: str, webhook_key: str):
    webhook_url = DISCORD_WEBHOOK_URL_VIP if webhook_key == "vip" else DISCORD_WEBHOOK_URL

    if not webhook_url:
        print(f"Discord Webhook fehlt für: {webhook_key}")
        return

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r1 = await client.post(webhook_url, json={"content": url})
            print(f"Discord embed [{webhook_key}]:", r1.status_code)
        except Exception as e:
            print("Discord Embed Fehler:", e)

        try:
            if webhook_key == "vip":
                text = f"**DE:** {title}\n\n{summary}"
            else:
                text = f"**DE:** {title}\n\n{summary}\n\nQuelle: {source_name} • Übersetzt per KI"
            r2 = await client.post(webhook_url, json={"content": text[:2000]})
            print(f"Discord text [{webhook_key}]:", r2.status_code)
        except Exception as e:
            print("Discord Text Fehler:", e)


# --------------------------------------------------
# Tweet Verarbeitung
# --------------------------------------------------

async def process_tweets(payload):
    global last_text, processed_ids

    print("Webhook Payload:", payload)

    tweets = []
    if isinstance(payload.get("data"), dict):
        tweets = payload["data"].get("tweets", [])
    elif isinstance(payload.get("data"), list):
        tweets = payload["data"]
    else:
        tweets = payload.get("tweets", [])

    print("Tweets erkannt:", len(tweets))

    for t in tweets:
        tweet_id = t.get("id")
        text = t.get("text", "")
        url = t.get("url") or t.get("twitterUrl")

        if not text or not url:
            continue

        if tweet_id and tweet_id in processed_ids:
            print("Duplicate ID übersprungen")
            continue

        if text == last_text:
            print("Duplicate Text übersprungen")
            continue

        if tweet_id:
            processed_ids.add(tweet_id)
            _save_seen_ids(processed_ids)

        last_text = text

        source_name, webhook_key = detect_source(t, payload)
        print(f"Neuer Tweet von {source_name} [{webhook_key}]:", text)

        # Whale-Filter nur für WatcherGuru
        if webhook_key == "default":
            if await is_whale_buy_sell(text):
                print("Whale Buy/Sell ignoriert:", text)
                continue

        title, summary = await translate_tweet(text)
        await send_to_discord(url, title, summary, source_name, webhook_key)


# --------------------------------------------------
# Webhook Endpoint
# --------------------------------------------------

@app.post("/wg-stream")
async def wg_stream(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    background_tasks.add_task(process_tweets, payload)
    return JSONResponse({"status": "received"})
