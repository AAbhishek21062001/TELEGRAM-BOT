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

# 1. Setup Logging (Helps track events and errors in Render logs)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. Fetch API Keys from Environment Variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Validate credentials
if not TELEGRAM_BOT_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Missing environment variables: TELEGRAM_BOT_TOKEN or GEMINI_API_KEY")

# 3. Initialize the Google GenAI Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)


# 4. Command Handler for /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message when the user sends /start."""
    welcome_text = (
        "👋 Welcome!\n\n"
        "Send me any image (notes, textbook page, diagram, or photo), "
        "and I will automatically read it and generate a Multiple Choice Quiz (MCQ) for you."
    )
    await update.message.reply_text(welcome_text)


# 5. Message Handler for Photos
async def handle_incoming_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the uploaded photo, extracts text, and creates a Telegram Quiz poll."""
    status_message = await update.message.reply_text("📥 Image received. Analyzing content and creating quiz...")

    try:
        # Step A: Download the highest resolution photo sent by the user
        photo_file = await update.message.photo[-1].get_file()
        image_stream = BytesIO()
        await photo_file.download_to_memory(image_stream)
        image_stream.seek(0)
        image = Image.open(image_stream)

        # Step B: Define the AI instructions
        prompt_instruction = """
        Analyze the provided image carefully and generate 1 high-quality Multiple Choice Question (MCQ) based on its content.
        You must respond STRICTLY in the following JSON format:
        {
          "question": "Clear and concise question text (max 250 characters)",
          "options": ["Option A", "Option B", "Option C", "Option D"],
          "correct_option_id": 0,
          "explanation": "Brief explanation of why this answer is correct (max 180 characters)"
        }
        Note:
        - "correct_option_id" must be an integer index (0 for Option A, 1 for Option B, 2 for Option C, 3 for Option D).
        - Provide exactly 4 options.
        """

        # Step C: Call the Gemini Vision Model
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[image, prompt_instruction],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        # Step D: Parse the AI JSON response
        quiz_data = json.loads(response.text)
        question_text = str(quiz_data.get("question", "")).strip()
        options_list = [str(opt).strip() for opt in quiz_data.get("options", [])][:4]
        correct_id = int(quiz_data.get("correct_option_id", 0))
        explanation_text = str(quiz_data.get("explanation", "")).strip()

        # Step E: Send native Telegram Quiz Poll
        await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question_text[:300],
            options=options_list,
            type="quiz",
            correct_option_id=correct_id,
            explanation=explanation_text[:200],
            is_anonymous=False
        )

        # Remove the temporary status message
        await status_message.delete()

    except Exception as error:
        logger.error(f"Error while processing image: {error}")
        await status_message.edit_text(
            "⚠️ Failed to generate quiz from this image. Please try again with a clearer picture."
        )


# 6. Main Application Runner
def main():
    """Builds and starts the Telegram bot."""
    bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register Handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_incoming_photo))

    logger.info("Bot is running...")
    bot_app.run_polling()


if __name__ == "__main__":
    main()