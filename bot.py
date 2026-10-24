import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# 1. Load hidden environment variables from the .env file
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN not found in environment variables. Check your .env file!")

# 2. Set up logging to track what your bot is doing
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 3. Define a simple command handler for /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(f"Hello {user_name}! Your new secure bot is up and running smoothly.")

# 4. Main function to initialize and run the bot
def main():
    # Build the application using your secure token and explicit timezone
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Register command handlers
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)

    logger.info("Starting bot polling...")
    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()