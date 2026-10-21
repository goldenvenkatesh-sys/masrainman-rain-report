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
    print("Telegram not configured: secret TELEGRAM_BOT_TOKEN is "
          + ("set" if token else "MISSING") + ", secret TELEGRAM_CHAT_ID is "
          + (f"set ({len(chats)} chat ids)" if chats else "MISSING") + ".")
    sys.exit(1)
if not sys.argv[1:] or not any(os.path.exists(a) for a in sys.argv[1:]):
    print("No report video was made, so nothing to send.")
    sys.exit(1)

api = f"https://api.telegram.org/bot{token}"
me = requests.get(f"{api}/getMe", timeout=30).json()
if not me.get("ok"):
    print("Bot token rejected by Telegram:", me.get("description"))
    sys.exit(1)
print("Bot:", "@" + me["result"]["username"])
failed = 0
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
        failed += 0 if ok else 1
if failed:
    print(f"{failed} send(s) failed.")
    sys.exit(1)
