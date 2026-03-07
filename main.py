from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import json

app = FastAPI()

# einfache In-Memory-Liste für verarbeitete Tweet-IDs
processed_ids = set()


@app.post("/wg-stream")
async def wg_stream(request: Request):
    """
    Dies ist der Endpoint, den später twitterapi.io als Webhook aufruft.
    Aktuell: wir parsen nur das JSON und loggen neue Tweets.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    print("=== Webhook payload ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    # Platzhalter: wir suchen nach einer Liste von Tweets im Payload
    tweets = payload.get("tweets") or payload.get("data") or payload.get("results") or []

    if not isinstance(tweets, list):
        return JSONResponse(
            {"status": "ok", "info": "no tweets array found"},
            status_code=200,
        )

    new_tweets = []

    for t in tweets:
        tweet_id = str(t.get("id") or t.get("tweet_id") or "")
        text = t.get("text") or ""
        url = t.get("url") or t.get("tweet_url") or ""

        if not tweet_id:
            continue

        if tweet_id in processed_ids:
            continue

        processed_ids.add(tweet_id)
        new_tweets.append({"id": tweet_id, "text": text, "url": url})

    print("=== Neue Tweets gefunden ===")
    print(new_tweets)

    return JSONResponse(
        {"status": "ok", "new_tweets_count": len(new_tweets)},
        status_code=200,
    )
