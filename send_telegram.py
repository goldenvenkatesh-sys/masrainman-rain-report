"""
Send finished report videos to Telegram (optional).

Needs two environment variables (GitHub secrets):
  TELEGRAM_BOT_TOKEN   your bot token
  TELEGRAM_CHAT_ID     one or more chat ids, comma separated
                       (your private chat, the users' group: group ids start with -100…)

Usage: python send_telegram.py out/rain_daily_2026-10-18.mp4 [more .mp4 files]
The caption comes from the matching .txt file written by rain_report.py.
"""
import os
import re
import sys
import time

import requests

token = os.environ.get("TELEGRAM_BOT_TOKEN")
chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
if not token or not chats:
    print("Telegram not configured: secret TELEGRAM_BOT_TOKEN is "
          + ("set" if token else "MISSING") + ", secret TELEGRAM_CHAT_ID is "
          + (f"set ({len(chats)} chat ids)" if chats else "MISSING") + ".")
    sys.exit(1)
if not sys.argv[1:] or not any(os.path.exists(a) for a in sys.argv[1:]):
    print("No report video was made, so nothing to send.")
    sys.exit(1)

api = f"https://api.telegram.org/bot{token}"


def redact(text):
    """The token is part of every request URL; never let it reach the Actions log."""
    return re.sub(r"\d{6,}:[A-Za-z0-9_-]{30,}", "<token>", str(text).replace(token, "<token>"))


def tg(method, data=None, file_path=None, timeout=60):
    """One Bot API call. Waits out a 429 flood limit (up to 60 s) instead of
    retrying straight away, retries 5xx, and never prints the token.
    Returns (ok, result or error text)."""
    for attempt in range(1, 4):
        time.sleep(1.1 if attempt == 1 else 0)   # ~1 message/s per chat, 20/min per group
        try:
            if file_path:
                with open(file_path, "rb") as v:
                    r = requests.post(f"{api}/{method}", data=data, timeout=timeout,
                                      files={"video": (os.path.basename(file_path), v, "video/mp4")})
            else:
                r = requests.post(f"{api}/{method}", data=data, timeout=timeout)
        except requests.RequestException as e:
            return False, "network error: " + redact(e)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if body.get("ok"):
            return True, body["result"]
        if r.status_code == 429:
            wait = int((body.get("parameters") or {}).get("retry_after") or 5)
            if attempt < 3 and wait <= 60:
                print(f"Telegram flood limit, waiting {wait}s as asked")
                time.sleep(wait + 1)
                continue
        elif r.status_code >= 500 and attempt < 3 and not file_path:   # a re-sent video could arrive twice
            time.sleep(5 * attempt)
            continue
        return False, redact(body.get("description") or r.text[:200])
    return False, "gave up after 3 tries"


ok, me = tg("getMe", timeout=30)
if not ok:
    print("Bot token rejected by Telegram (update the TELEGRAM_BOT_TOKEN secret):", me)
    sys.exit(1)
print("Bot:", "@" + me["username"])
failed = 0
for mp4 in sys.argv[1:]:
    base = mp4[:-4]
    cap = ""
    if os.path.exists(base + ".txt"):
        with open(base + ".txt", encoding="utf-8") as f:
            cap = f.read()[:1020]          # Telegram caption limit is 1024
    for chat in chats:
        ok, res = tg("sendVideo", {"chat_id": chat, "caption": cap, "supports_streaming": "true",
                                   "width": 1080, "height": 1620}, file_path=mp4, timeout=180)
        print(f"{os.path.basename(mp4)} -> {chat}: {'sent' if ok else 'FAILED ' + res}")
        failed += 0 if ok else 1
if failed:
    print(f"{failed} send(s) failed.")
    sys.exit(1)
