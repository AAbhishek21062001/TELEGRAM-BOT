import os
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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


# =========================================================
# 1. LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# =========================================================
# 2. ENVIRONMENT VARIABLES
# =========================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN environment variable is missing!"
    )

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY environment variable is missing!"
    )


# =========================================================
# 3. GEMINI CLIENT
# =========================================================

ai_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# 4. RENDER HEALTH SERVER
# =========================================================

class SimpleHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

        self.wfile.write(
            b"Telegram Quiz Bot is healthy and running!"
        )

    def log_message(self, format, *args):
        # Disable unnecessary HTTP logs
        return


def run_health_server():

    port = int(
        os.environ.get("PORT", 8080)
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        SimpleHandler
    )

    logger.info(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# =========================================================
# 5. AI PROMPT
# =========================================================

PROMPT_TEXT = """
Read the provided text or image carefully and create exactly ONE
Multiple Choice Question (MCQ).

Return ONLY a valid JSON object.

The JSON format must be exactly:

{
  "question": "Question text",
  "options": [
    "Option 1",
    "Option 2",
    "Option 3",
    "Option 4"
  ],
  "correct_option_id": 0,
  "explanation": "Short explanation"
}

RULES:

1. Create exactly ONE MCQ.

2. Create exactly FOUR options.

3. Only ONE option must be correct.

4. correct_option_id must be:
   0, 1, 2, or 3.

5. The question must be based ONLY on the provided
   text/image.

6. Do not invent information that is not present
   in the provided content.

7. Keep the question under 300 characters.

8. Keep every option under 100 characters.

9. Keep the explanation under 200 characters.

10. Use the same language as the provided content.

11. If the image contains Hindi text, generate the
    question and options in Hindi.

12. If the image contains English text, generate the
    question and options in English.

13. Do not return Markdown.

14. Do not return ```json.

15. Return valid JSON only.
"""


# =========================================================
# 6. START COMMAND
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👋 नमस्ते!\n\n"
        "मुझे कोई भी फोटो या टेक्स्ट भेजें।\n"
        "मैं उससे तुरंत एक Quiz Poll बना दूंगा।\n\n"
        "📸 Photo → MCQ Quiz\n"
        "📝 Text → MCQ Quiz"
    )


# =========================================================
# 7. GEMINI QUIZ PROCESSOR
# =========================================================

async def process_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    contents,
    status_msg
):

    try:

        logger.info(
            "Sending request to Gemini..."
        )

        # -------------------------------------------------
        # CURRENT GEMINI MODEL
        # -------------------------------------------------

        response = ai_client.models.generate_content(

            model="gemini-3.6-flash",

            contents=contents,

            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )


        # -------------------------------------------------
        # GET RESPONSE
        # -------------------------------------------------

        if not response.text:
            raise ValueError(
                "Gemini returned an empty response."
            )


        raw_text = response.text.strip()


        logger.info(
            f"Gemini response received: {raw_text[:300]}"
        )


        # -------------------------------------------------
        # REMOVE MARKDOWN IF AI ADDS IT
        # -------------------------------------------------

        if raw_text.startswith("```"):

            raw_text = (
                raw_text
                .split("\n", 1)[-1]
                .rsplit("```", 1)[0]
                .strip()
            )


        # -------------------------------------------------
        # PARSE JSON
        # -------------------------------------------------

        quiz_data = json.loads(
            raw_text
        )


        # -------------------------------------------------
        # QUESTION
        # -------------------------------------------------

        question = str(
            quiz_data.get(
                "question",
                ""
            )
        ).strip()


        # Telegram poll question limit
        question = question[:300]


        if not question:
            raise ValueError(
                "AI did not generate a question."
            )


        # -------------------------------------------------
        # OPTIONS
        # -------------------------------------------------

        raw_options = quiz_data.get(
            "options",
            []
        )


        if not isinstance(
            raw_options,
            list
        ):
            raise ValueError(
                "Invalid options format."
            )


        options = [
            str(option).strip()[:100]
            for option in raw_options
        ]


        # Exactly 4 options required
        if len(options) != 4:

            raise ValueError(
                f"AI generated {len(options)} options instead of 4."
            )


        # Check empty options
        if any(
            not option
            for option in options
        ):

            raise ValueError(
                "One or more options are empty."
            )


        # -------------------------------------------------
        # CORRECT ANSWER
        # -------------------------------------------------

        try:

            correct_id = int(
                quiz_data.get(
                    "correct_option_id",
                    0
                )
            )

        except (TypeError, ValueError):

            correct_id = 0


        if correct_id not in [0, 1, 2, 3]:

            correct_id = 0


        # -------------------------------------------------
        # EXPLANATION
        # -------------------------------------------------

        explanation = str(
            quiz_data.get(
                "explanation",
                ""
            )
        ).strip()


        explanation = explanation[:200]


        # -------------------------------------------------
        # SEND TELEGRAM QUIZ POLL
        # -------------------------------------------------

        await context.bot.send_poll(

            chat_id=update.effective_chat.id,

            question=question,

            options=options,

            type="quiz",

            correct_option_id=correct_id,

            explanation=(
                explanation
                if explanation
                else None
            ),

            is_anonymous=False
        )


        # -------------------------------------------------
        # DELETE PROCESSING MESSAGE
        # -------------------------------------------------

        try:

            await status_msg.delete()

        except Exception:

            pass


        logger.info(
            "Quiz successfully sent."
        )


    except json.JSONDecodeError as error:

        logger.error(
            f"JSON parsing error: {error}"
        )

        await status_msg.edit_text(
            "⚠️ AI ने सही JSON response नहीं दिया।\n"
            "कृपया दोबारा कोशिश करें।"
        )


    except Exception as error:

        logger.exception(
            "Quiz processing error"
        )

        error_message = str(error)

        await status_msg.edit_text(
            "⚠️ Quiz बनाने में error आया:\n\n"
            f"{error_message[:500]}"
        )


# =========================================================
# 8. PHOTO HANDLER
# =========================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    status_msg = await update.message.reply_text(
        "📥 फोटो प्रोसेस हो रही है...\n"
        "⏳ कृपया थोड़ा इंतज़ार करें।"
    )


    try:

        # -------------------------------------------------
        # GET TELEGRAM PHOTO
        # -------------------------------------------------

        photo = update.message.photo[-1]

        photo_file = await photo.get_file()


        # -------------------------------------------------
        # DOWNLOAD PHOTO INTO MEMORY
        # -------------------------------------------------

        image_bytes = BytesIO()

        await photo_file.download_to_memory(
            image_bytes
        )

        image_bytes.seek(0)


        # -------------------------------------------------
        # OPEN IMAGE
        # -------------------------------------------------

        img = Image.open(
            image_bytes
        )


        # Force load image
        img.load()


        logger.info(
            f"Image received: {img.size}"
        )


        # -------------------------------------------------
        # SEND IMAGE + PROMPT TO GEMINI
        # -------------------------------------------------

        contents = [
            img,
            PROMPT_TEXT
        ]


        await process_quiz(
            update,
            context,
            contents,
            status_msg
        )


    except Exception as error:

        logger.exception(
            "Photo processing error"
        )

        await status_msg.edit_text(
            "⚠️ Photo process करने में error आया:\n\n"
            f"{str(error)[:500]}"
        )


# =========================================================
# 9. TEXT HANDLER
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text


    # Ignore commands
    if text.startswith("/"):
        return


    # Empty message protection
    if not text.strip():

        await update.message.reply_text(
            "📝 कृपया कुछ text भेजें।"
        )

        return


    status_msg = await update.message.reply_text(
        "📝 टेक्स्ट प्रोसेस हो रहा है...\n"
        "⏳ कृपया थोड़ा इंतज़ार करें।"
    )


    try:

        contents = [
            text,
            PROMPT_TEXT
        ]


        await process_quiz(
            update,
            context,
            contents,
            status_msg
        )


    except Exception as error:

        logger.exception(
            "Text processing error"
        )

        await status_msg.edit_text(
            "⚠️ Text process करने में error आया:\n\n"
            f"{str(error)[:500]}"
        )


# =========================================================
# 10. ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram bot error",
        exc_info=context.error
    )


# =========================================================
# 11. MAIN FUNCTION
# =========================================================

def main():

    logger.info(
        "Starting Telegram Quiz Bot..."
    )


    # -----------------------------------------------------
    # START RENDER HEALTH SERVER
    # -----------------------------------------------------

    server_thread = threading.Thread(

        target=run_health_server,

        daemon=True
    )

    server_thread.start()


    # -----------------------------------------------------
    # CREATE TELEGRAM APPLICATION
    # -----------------------------------------------------

    bot_app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )


    # -----------------------------------------------------
    # HANDLERS
    # -----------------------------------------------------

    bot_app.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )


    bot_app.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )


    bot_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )


    # -----------------------------------------------------
    # ERROR HANDLER
    # -----------------------------------------------------

    bot_app.add_error_handler(
        error_handler
    )


    logger.info(
        "Telegram Quiz Bot is running..."
    )


    # -----------------------------------------------------
    # START BOT
    # -----------------------------------------------------

    bot_app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# 12. RUN
# =========================================================

if __name__ == "__main__":
    main()
