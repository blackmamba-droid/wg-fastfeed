from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import json
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# In-Memory-Cache: verarbeitete Tweet-IDs und letzter Text
processed_ids = set()
last_text = None  # Text des zuletzt verarbeiteten Tweets

# Umgebungsvariablen lesen
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4.1-nano"


async def translate_tweet(text: str) -> tuple[str, str]:
    """
    Ruft OpenAI auf und gibt (title_de, summary_de) zurück.
    Bei Fehlern wird ein Fallback verwendet.
    """

    if not OPENAI_API_KEY:
        # Fallback, wenn lokal kein Key gesetzt ist
        return text[:120], "Automatischer Hinweis: Kein OPENAI_API_KEY gesetzt."

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    system_prompt = (
    "Du bist ein deutschsprachiger News-Redakteur.\n"
    "Übersetze die Überschrift des folgenden Tweets möglichst nah am englischen Original ins Deutsche "
    "und fasse den Inhalt in 1–3 deutschen Sätzen neutral zusammen.\n"
    "Antworte im JSON-Format: {\"title\": \"<kurze deutsche Überschrift>\", \"summary\": \"<deutsche Zusammenfassung>\"}."
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
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        obj = json.loads(content)
        title_de = obj.get("title", text[:120])
        summary_de = obj.get(
            "summary",
            "Automatische Zusammenfassung nicht verfügbar.",
        )
        return title_de, summary_de

    except Exception as e:
        print("Fehler bei GPT-Übersetzung:", e)
        fallback_title = text[:120]
        fallback_summary = "Automatischer Hinweis: GPT-Übersetzung aktuell nicht verfügbar."
        return fallback_title, fallback_summary


async def send_to_discord(url: str, title_de: str, summary_de: str) -> None:
    """
    Schickt zwei Nachrichten:
    1) nur den Tweet-Link -> Discord rendert X-Embed mit Bild/Video
    2) deutsche Übersetzung als normaler Text darunter
    """

    if not DISCORD_WEBHOOK_URL:
        print("WARNUNG: DISCORD_WEBHOOK_URL ist nicht gesetzt.")
        return

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. Nachricht: nur Link
        try:
            r1 = await client.post(
                DISCORD_WEBHOOK_URL,
                json={"content": url},
            )
            r1.raise_for_status()
        except Exception as e:
            print("Fehler beim Senden der Link-Nachricht an Discord:", e)

        # 2. Nachricht: Übersetzung
        text = f"**DE:** {title_de}\n\n{summary_de}\n\nQuelle: WatcherGuru • Übersetzt per KI"
        try:
            r2 = await client.post(
                DISCORD_WEBHOOK_URL,
                json={"content": text[:2000]},  # Safety gegen Discord-Limit
            )
            r2.raise_for_status()
        except Exception as e:
            print("Fehler beim Senden der Übersetzungs-Nachricht an Discord:", e)


@app.post("/wg-stream")
async def wg_stream(request: Request):
    global last_text, processed_ids

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    try:
        tweets = payload.get("data", {}).get("tweets", [])
        if not tweets:
            return JSONResponse({"status": "no_tweets"})

        for t in tweets:
            tweet_id = t.get("id")
            text = t.get("text", "")
            url = t.get("url") or t.get("twitterUrl")

            if not url or not text:
                continue

            # Duplikate vermeiden
            if tweet_id in processed_ids or text == last_text:
                continue

            processed_ids.add(tweet_id)
            last_text = text

            # GPT-Übersetzung holen
            title_de, summary_de = await translate_tweet(text)

            # An Discord schicken (zwei Nachrichten)
            await send_to_discord(url, title_de, summary_de)

        return JSONResponse({"status": "ok"})

    except Exception as e:
        print("Fehler im /wg-stream Handler:", e)
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)
