from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
import json
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Cache gegen doppelte Tweets
processed_ids = set()
last_text = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4.1-nano"


# --------------------------------------------------
# GPT Übersetzung
# --------------------------------------------------

async def translate_tweet(text: str):

    if not OPENAI_API_KEY:
        return text[:120], "Automatische Zusammenfassung nicht verfügbar."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "Du bist ein deutschsprachiger News-Redakteur.\n"
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
            resp = await client.post(
                OPENAI_API_URL,
                headers=headers,
                json=body,
            )

            resp.raise_for_status()

        data = resp.json()

        content = data["choices"][0]["message"]["content"]

        obj = json.loads(content)

        title = obj.get("title", text[:120])
        summary = obj.get("summary", "Zusammenfassung nicht verfügbar.")

        return title, summary

    except Exception as e:

        print("GPT Fehler:", e)

        return text[:120], "Automatische Übersetzung aktuell nicht verfügbar."


# --------------------------------------------------
# Discord Versand
# --------------------------------------------------

async def send_to_discord(url: str, title: str, summary: str):

    if not DISCORD_WEBHOOK_URL:
        print("Discord Webhook fehlt.")
        return

    async with httpx.AsyncClient(timeout=10) as client:

        try:

            # Tweet Embed
            r1 = await client.post(
                DISCORD_WEBHOOK_URL,
                json={"content": url},
            )

            print("Discord embed status:", r1.status_code)

        except Exception as e:

            print("Discord Embed Fehler:", e)

        try:

            text = f"**DE:** {title}\n\n{summary}\n\nQuelle: WatcherGuru • Übersetzt per KI"

            r2 = await client.post(
                DISCORD_WEBHOOK_URL,
                json={"content": text[:2000]},
            )

            print("Discord text status:", r2.status_code)

        except Exception as e:

            print("Discord Text Fehler:", e)


# --------------------------------------------------
# Tweet Verarbeitung
# --------------------------------------------------

async def process_tweets(payload):

    global last_text
    global processed_ids

    print("Webhook Payload:", payload)

    tweets = []

    # verschiedene mögliche Payload Strukturen
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

        # Duplicate Schutz
        if tweet_id and tweet_id in processed_ids:
            print("Duplicate ID übersprungen")
            continue

        if text == last_text:
            print("Duplicate Text übersprungen")
            continue

        if tweet_id:
            processed_ids.add(tweet_id)

        last_text = text

        print("Neuer Tweet:", text)

        # Übersetzen
        title, summary = await translate_tweet(text)

        # Discord senden
        await send_to_discord(url, title, summary)


# --------------------------------------------------
# Webhook Endpoint
# --------------------------------------------------

@app.post("/wg-stream")
async def wg_stream(request: Request, background_tasks: BackgroundTasks):

    try:

        payload = await request.json()

    except Exception:

        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Verarbeitung im Hintergrund starten
    background_tasks.add_task(process_tweets, payload)

    # sofort antworten (wichtig für Webhooks)
    return JSONResponse({"status": "received"})