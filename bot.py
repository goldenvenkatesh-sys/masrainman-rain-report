import os
import asyncio
from telegram import Bot
from dotenv import load_dotenv

# Load environment variables (works locally with .env, and on GitHub via secrets)
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
# If you have a specific chat or channel ID stored in secrets:
CHAT_ID = os.getenv("RAIN_REPORT_CHAT_IDS") 

async def send_weather_update():
    bot = Bot(token=TOKEN)
    
    # Put your weather message text here
    message = "🌧️ **MasRainman Automated Weather Update**: Routine 2-hour check complete. Conditions normal."
    
    # Send the message to your channel/chat
    await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
    print("Weather update sent successfully!")

def main():
    # Run the async function once and exit cleanly
    asyncio.run(send_weather_update())

if __name__ == "__main__":
    main()
