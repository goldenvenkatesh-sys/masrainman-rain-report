import os
import asyncio
from telegram import Bot
from dotenv import load_dotenv

# Load environment variables (works locally with .env, and on GitHub via secrets)
load_dotenv()
# Same secrets as the rainfall reports: either token name works
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
# One or more chat ids, comma separated (e.g. your id and the group id -100…)
CHAT_IDS = [c.strip() for c in (os.getenv("RAIN_REPORT_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or "").split(",") if c.strip()]


async def send_weather_update():
    if not TOKEN or not CHAT_IDS:
        raise SystemExit("Telegram not configured: need TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN) "
                         "and RAIN_REPORT_CHAT_IDS secrets.")
    bot = Bot(token=TOKEN)

    # Put your weather message text here
    message = "🌧️ *MasRainman Automated Weather Update*: Routine 2-hour check complete. Conditions normal."

    failed = 0
    for chat in CHAT_IDS:
        try:
            await bot.send_message(chat_id=chat, text=message, parse_mode="Markdown")
            print(f"Sent to chat {chat[:4]}…")
        except Exception as e:  # one bad chat id shouldn't stop the others
            failed += 1
            print(f"Could not send to chat {chat[:4]}…: {e}")
        await asyncio.sleep(1)  # stay well under Telegram's flood limit
    if failed == len(CHAT_IDS):
        raise SystemExit("Message was not sent to any chat.")


def main():
    asyncio.run(send_weather_update())


if __name__ == "__main__":
    main()
