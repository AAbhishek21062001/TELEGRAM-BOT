import os
import json
import logging
from io import BytesIO
from PIL import Image
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# 1. Setup logging to monitor incoming requests and errors
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. Retrieve API tokens from environment variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing environment variables: TELEGRAM_BOT_TOKEN or GEMINI_API_KEY")

# 3. Initialize Google GenAI client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# 4. Strict prompt template to enforce structured JSON output
QUIZ_PROMPT = """
Read the provided content carefully and create 1 high-quality Multiple Choice Question (MCQ).
Return the result STRICTLY as a raw JSON object with the following structure:
{
  "question": "Question text (maximum 250 characters)",
  "options": ["Option 1", "Option 2", "Option 3", "Option 4"],
  "correct_option_id": 0,
  "explanation": "Brief explanation of the answer (maximum 150 characters)"
}

Rules:
1. "options" must contain exactly 4 distinct choices.
2. Each option must be under 90 characters.
3. "correct_option_id" must be an integer (0, 1, 2, or 3) matching the correct option.
4. Keep the question language consistent with the input language (e.g., Hindi for Hindi inputs).
"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message when /start is used."""
    welcome_message = (
        "👋 **Welcome to Quiz Maker Bot!**\n\n"
        "Send me any **photo** (textbook, notes, GK) or type any **text message**, "
        "and I will instantly convert it into an interactive Telegram quiz."
    )
    await update.message.reply_text(welcome_message, parse_mode="Markdown")


async def generate_and_send_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, content_payload, status_msg):
    """Calls Gemini API, parses the JSON output, and sends the quiz poll."""
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=content_payload,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        quiz_data = json.loads(response.text)

        question = str(quiz_data.get("question", "")).strip()[:290]
        options = [str(opt).strip()[:95] for opt in quiz_data.get("options", [])][:4]
        correct_id = int(quiz_data.get("correct_option_id", 0))
        explanation = str(quiz_data.get("explanation", "")).strip()[:190]

        if len(options) < 2:
            raise ValueError("Insufficient options generated.")

        # Ensure correct_id is within valid bounds
        if correct_id < 0 or correct_id >= len(options):
            correct_id = 0

        # Send interactive Telegram quiz poll
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question,
            options=options,
            type="quiz",
            correct_option_id=correct_id,
            explanation=explanation if explanation else None,
            is_anonymous=False
        )

        # Delete processing message
        await status_msg.delete()

    except Exception as error:
        logger.error(f"Error generating quiz: {error}")
        await status_msg.edit_text(
            f"⚠️ Could not generate quiz from this content.\nError: `{str(error)[:100]}`",
            parse_mode="Markdown"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles image uploads."""
    status_msg = await update.message.reply_text("📥 Reading image and generating quiz...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        image_stream = BytesIO()
        await photo_file.download_to_memory(image_stream)
        image_stream.seek(0)
        image = Image.open(image_stream)

        await generate_and_send_quiz(update, context, [image, QUIZ_PROMPT], status_msg)
    except Exception as err:
        logger.error(f"Image processing error: {err}")
        await status_msg.edit_text("⚠️ Failed to load image. Please upload a clear picture.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text/notes messages."""
    if update.message.text.startswith("/"):
        return
    status_msg = await update.message.reply_text("📝 Reading text and generating quiz...")
    user_text = update.message.text
    await generate_and_send_quiz(update, context, [user_text, QUIZ_PROMPT], status_msg)


def main():
    """Starts the bot application."""
    bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started successfully. Waiting for updates...")
    bot_app.run_polling()


if __name__ == "__main__":
    main()
