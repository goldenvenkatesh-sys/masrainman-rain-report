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
import sys

import requests

token = os.environ.get("TELEGRAM_BOT_TOKEN")
chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
if not token or not chats:
    print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing); skipping.")
    sys.exit(0)

api = f"https://api.telegram.org/bot{token}"
for mp4 in sys.argv[1:]:
    base = mp4[:-4]
    cap = ""
    if os.path.exists(base + ".txt"):
        with open(base + ".txt", encoding="utf-8") as f:
            cap = f.read()[:1020]          # Telegram caption limit is 1024
    for chat in chats:
        with open(mp4, "rb") as v:
            r = requests.post(f"{api}/sendVideo",
                              data={"chat_id": chat, "caption": cap, "supports_streaming": "true",
                                    "width": 1080, "height": 1620},
                              files={"video": (os.path.basename(mp4), v, "video/mp4")}, timeout=180)
        ok = r.ok and r.json().get("ok")
        print(f"{os.path.basename(mp4)} -> {chat}: {'sent' if ok else 'FAILED ' + r.text[:200]}")
