import os
import re
import json
import logging
import threading
from io import BytesIO
from http.server import HTTPServer, BaseHTTPRequestHandler

from PIL import Image
from pypdf import PdfReader, PdfWriter

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


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_NAME = "gemini-3.6-flash"

MAX_QUESTIONS = 30
MAX_OPTIONS = 10
TELEGRAM_MESSAGE_LIMIT = 3900


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN environment variable is missing."
    )

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY environment variable is missing."
    )


# ============================================================
# GEMINI CLIENT
# ============================================================

ai_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# USER CONFIG STORAGE
# ============================================================

# Stores configuration temporarily for each Telegram user.
# Example:
# user_configs[user_id] = {
#     "topic": "...",
#     "pages": "...",
#     "questions": 5,
#     "options": 4,
#     "difficulty": "Hard",
#     "language": "English",
#     "question_types": "Mixed"
# }

user_configs = {}


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Telegram Quiz Generator is running!"
        )

    def log_message(self, format, *args):
        return


def run_health_server():

    port = int(
        os.getenv("PORT", "8080")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    logger.info(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# START COMMAND
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = """
👋 नमस्ते!

मैं आपका AI Quiz Generator Bot हूँ।

मैं इन चीज़ों से Quiz बना सकता हूँ:

📚 Topic
📄 PDF
🖼 Image
📝 Text

आप अपनी configuration इस format में भेजें:

Topic / Input Source: Attached PDF/Image
Page Number(s) / Range: 5, 12-15, All
Number of Questions: 5
Number of Options per Question: 4
Level of Difficulty: Hard
Language(s): English
Question Type(s): Mixed

अगर Topic दिया है तो PDF/Image की जरूरत नहीं है।

अगर "Attached PDF/Image" दिया है,
तो configuration भेजने के बाद PDF या Image भेजें।
"""

    await update.message.reply_text(
        message
    )


# ============================================================
# HELP COMMAND
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "Use /start and send your USER CONFIGURATION.\n\n"
        "Then send your PDF/Image if required."
    )


# ============================================================
# CLEAN CONFIG VALUE
# ============================================================

def clean_value(value):

    value = value.strip()

    value = value.replace(
        "**",
        ""
    )

    # Remove placeholder brackets
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()

    return value.strip()


# ============================================================
# FIND CONFIG VALUE
# ============================================================

def extract_config_value(
    text,
    patterns
):

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE | re.MULTILINE
        )

        if match:

            value = clean_value(
                match.group(1)
            )

            if value:
                return value

    return None


# ============================================================
# PARSE USER CONFIGURATION
# ============================================================

def parse_configuration(text):

    config = {}

    # --------------------------------------------------------
    # Topic / Input Source
    # --------------------------------------------------------

    config["topic"] = extract_config_value(
        text,
        [
            r"Topic\s*/\s*Input\s*Source\s*:\s*(.+)",
            r"Topic\s*/\s*Input\s*Source\s*:\s*\[(.+)\]",
        ]
    )


    # --------------------------------------------------------
    # Page Number(s) / Range
    # --------------------------------------------------------

    config["pages"] = extract_config_value(
        text,
        [
            r"Page\s*Number\(s\)\s*/\s*Range\s*:\s*(.+)",
            r"Page\s*Number.*?Range\s*:\s*(.+)",
        ]
    )


    # --------------------------------------------------------
    # Number of Questions
    # --------------------------------------------------------

    questions_value = extract_config_value(
        text,
        [
            r"Number\s*of\s*Questions\s*:\s*(.+)",
        ]
    )

    try:

        config["questions"] = int(
            questions_value
        )

    except Exception:

        config["questions"] = None


    # --------------------------------------------------------
    # Number of Options
    # --------------------------------------------------------

    options_value = extract_config_value(
        text,
        [
            r"Number\s*of\s*Options\s*per\s*Question\s*:\s*(.+)",
        ]
    )

    try:

        config["options"] = int(
            options_value
        )

    except Exception:

        config["options"] = None


    # --------------------------------------------------------
    # Difficulty
    # --------------------------------------------------------

    config["difficulty"] = extract_config_value(
        text,
        [
            r"Level\s*of\s*Difficulty\s*:\s*(.+)",
            r"Difficulty\s*:\s*(.+)",
        ]
    )


    # --------------------------------------------------------
    # Language
    # --------------------------------------------------------

    config["language"] = extract_config_value(
        text,
        [
            r"Language\(s\)\s*:\s*(.+)",
            r"Language\s*:\s*(.+)",
        ]
    )


    # --------------------------------------------------------
    # Question Types
    # --------------------------------------------------------

    config["question_types"] = extract_config_value(
        text,
        [
            r"Question\s*Type\(s\)\s*:\s*(.+)",
            r"Question\s*Types?\s*:\s*(.+)",
        ]
    )


    return config


# ============================================================
# VALIDATE CONFIGURATION
# ============================================================

def validate_configuration(config):

    errors = []


    # Topic
    if not config.get("topic"):

        errors.append(
            "Topic / Input Source"
        )


    # Questions
    questions = config.get(
        "questions"
    )

    if not questions:

        errors.append(
            "Number of Questions"
        )

    elif questions < 1:

        errors.append(
            "Number of Questions must be at least 1"
        )

    elif questions > MAX_QUESTIONS:

        errors.append(
            f"Maximum {MAX_QUESTIONS} questions are allowed"
        )


    # Options
    options = config.get(
        "options"
    )

    if not options:

        errors.append(
            "Number of Options per Question"
        )

    elif options < 2:

        errors.append(
            "At least 2 options are required"
        )

    elif options > MAX_OPTIONS:

        errors.append(
            f"Maximum {MAX_OPTIONS} options are allowed"
        )


    # Difficulty
    if not config.get("difficulty"):

        errors.append(
            "Level of Difficulty"
        )


    # Language
    if not config.get("language"):

        errors.append(
            "Language(s)"
        )


    # Question Type
    if not config.get("question_types"):

        errors.append(
            "Question Type(s)"
        )


    return errors


# ============================================================
# CHECK IF SOURCE IS ATTACHED
# ============================================================

def source_is_attached(config):

    topic = (
        config.get("topic")
        or ""
    ).lower()

    return (
        "attached pdf" in topic
        or
        "attached image" in topic
        or
        "pdf/image" in topic
        or
        "pdf" == topic.strip()
        or
        "image" == topic.strip()
    )


# ============================================================
# PAGE RANGE PARSER
# ============================================================

def parse_page_range(
    page_text,
    total_pages
):

    if not page_text:

        return list(
            range(total_pages)
        )


    page_text = (
        page_text
        .strip()
        .lower()
    )


    if page_text in [
        "all",
        "all pages",
        "*"
    ]:

        return list(
            range(total_pages)
        )


    selected = set()


    # Supports:
    # 5
    # 12-15
    # 1,3,5
    # 1, 5-8, 10

    parts = page_text.split(",")


    for part in parts:

        part = part.strip()

        if not part:
            continue


        # Range
        if "-" in part:

            try:

                start, end = part.split(
                    "-",
                    1
                )

                start = int(
                    start.strip()
                )

                end = int(
                    end.strip()
                )


                if start > end:
                    start, end = end, start


                if start < 1:
                    start = 1


                if end > total_pages:
                    end = total_pages


                for page in range(
                    start,
                    end + 1
                ):

                    selected.add(
                        page - 1
                    )


            except ValueError:

                raise ValueError(
                    f"Invalid page range: {part}"
                )


        else:

            try:

                page = int(
                    part
                )

            except ValueError:

                raise ValueError(
                    f"Invalid page number: {part}"
                )


            if page < 1 or page > total_pages:

                raise ValueError(
                    f"Page {page} does not exist. "
                    f"PDF has {total_pages} pages."
                )


            selected.add(
                page - 1
            )


    return sorted(
        selected
    )


# ============================================================
# EXTRACT SELECTED PDF PAGES
# ============================================================

def extract_pdf_pages(
    pdf_bytes,
    page_range
):

    reader = PdfReader(
        BytesIO(pdf_bytes)
    )

    total_pages = len(
        reader.pages
    )

    page_indexes = parse_page_range(
        page_range,
        total_pages
    )


    writer = PdfWriter()


    for index in page_indexes:

        writer.add_page(
            reader.pages[index]
        )


    output = BytesIO()

    writer.write(
        output
    )

    output.seek(0)


    return output.read()


# ============================================================
# CREATE AI PROMPT
# ============================================================

def build_ai_prompt(config):

    topic = config.get(
        "topic",
        "Attached PDF/Image"
    )

    pages = config.get(
        "pages",
        "All"
    )

    questions = config.get(
        "questions"
    )

    options = config.get(
        "options"
    )

    difficulty = config.get(
        "difficulty"
    )

    language = config.get(
        "language"
    )

    question_types = config.get(
        "question_types"
    )


    prompt = f"""
You are an expert educational content extractor,
competitive-exam question setter, and quiz generator.

USER CONFIGURATION
==================

Topic / Input Source:
{topic}

Page Number(s) / Range:
{pages}

Number of Questions:
{questions}

Number of Options per Question:
{options}

Level of Difficulty:
{difficulty}

Language(s):
{language}

Question Type(s):
{question_types}


============================================================
1. INTELLIGENT EXTRACTION VS GENERATION
============================================================

If the attached document already contains MCQs,
quiz questions, or objective questions:

- Extract existing questions.
- Do NOT create new questions.
- Preserve the original meaning.
- Reformat them according to the required output structure.

If the attached document contains theory, notes,
textbook content, diagrams, tables, or informational material:

- Analyze the provided material.
- Generate high-quality conceptual MCQs strictly from
  the provided content.
- Do not introduce unrelated information.
- Match the requested difficulty.
- Use competitive-exam/PYQ-style framing where appropriate.

If only a Topic is provided:

- Generate questions from reliable subject knowledge.
- Questions must be directly relevant to the topic.
- Match the requested difficulty.
- Follow the requested question types.


============================================================
2. PAGE SCOPE
============================================================

If specific pages are provided:

USE ONLY THOSE PAGES.

Ignore all other pages.

If "All" is specified:

The complete provided document may be used.


============================================================
3. LANGUAGE
============================================================

If one language is requested:

Everything must be in that language.

If bilingual language is requested:

Every question, statement, option, table item,
and explanation MUST contain both languages.

Separate the languages with:

" / "

Example:

What is normalization? / नॉर्मलाइज़ेशन क्या है?


============================================================
4. NUMBER OF QUESTIONS
============================================================

Generate EXACTLY:

{questions}

questions.


============================================================
5. NUMBER OF OPTIONS
============================================================

Every question MUST contain EXACTLY:

{options}

options.

For example:

2 → A and B
4 → A, B, C, D
5 → A, B, C, D, E


============================================================
6. QUESTION TYPES
============================================================

Follow:

{question_types}

Question types may include:

- Direct
- Conceptual
- Statement
- Assertion-Reason
- Match the Following
- Numerical
- Application Based
- Mixed


============================================================
7. DIFFICULTY
============================================================

Difficulty:

{difficulty}

Easy:
Basic concepts and direct questions.

Medium:
Moderate conceptual understanding.

Hard:
Multi-step reasoning, conceptual traps,
and close distractors.

Ultra Hard:
Advanced reasoning, multi-concept questions,
subtle distractors, and competitive-exam level difficulty.


============================================================
8. MATCH THE FOLLOWING
============================================================

If the question type is Match the Following:

Create matching pairs.

Return them in JSON using:

"match_left": [],
"match_right": []

The final formatter will convert them into
a Markdown table.


============================================================
9. ASSERTION-REASON
============================================================

For Assertion-Reason questions:

Use:

Assertion: ...
Reason: ...

Store both inside the "statements" array.


============================================================
10. STATEMENT QUESTIONS
============================================================

For statement-based questions:

Use:

1. Statement 1
2. Statement 2
3. Statement 3

Store statements in the "statements" array.


============================================================
11. MATHEMATICS
============================================================

For mathematical expressions use:

$$ ... $$

Do not use invalid mathematical notation.


============================================================
12. QUALITY RULES
============================================================

- Only ONE option must be correct.
- Options must be plausible.
- Do not create duplicate options.
- Do not make the correct option obvious from its length.
- Avoid ambiguous wording.
- Avoid unnecessary "All of the above".
- Avoid unnecessary "None of the above".
- Do not invent information from the source.
- Follow the requested language.
- Follow the requested difficulty.
- Follow the requested question type.


============================================================
13. OUTPUT FORMAT
============================================================

Return ONLY valid JSON.

DO NOT return Markdown.

DO NOT return a code block.

DO NOT add any text outside JSON.

Use exactly this JSON structure:

{{
  "questions": [
    {{
      "question": "Question text",
      "statements": [],
      "match_left": [],
      "match_right": [],
      "options": [
        "Option A",
        "Option B",
        "Option C",
        "Option D"
      ],
      "correct_option_id": 0,
      "explanation": "Ex: Explanation..."
    }}
  ]
}}

IMPORTANT:

correct_option_id starts from 0.

A = 0
B = 1
C = 2
D = 3
E = 4

The number of options MUST be exactly:

{options}

The explanation MUST start with:

Ex:
"""

    return prompt


# ============================================================
# CLEAN JSON RESPONSE
# ============================================================

def clean_json_response(
    text
):

    text = (
        text
        .strip()
    )


    if text.startswith(
        "```"
    ):

        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )


    return text.strip()


# ============================================================
# VALIDATE AI QUESTIONS
# ============================================================

def validate_questions(
    data,
    config
):

    if not isinstance(
        data,
        dict
    ):

        raise ValueError(
            "AI response is not a JSON object."
        )


    questions = data.get(
        "questions"
    )


    if not isinstance(
        questions,
        list
    ):

        raise ValueError(
            "AI response does not contain questions."
        )


    requested_questions = config[
        "questions"
    ]

    requested_options = config[
        "options"
    ]


    if len(questions) != requested_questions:

        logger.warning(
            "AI returned %s questions, expected %s.",
            len(questions),
            requested_questions
        )


    for question in questions:

        if not isinstance(
            question,
            dict
        ):

            raise ValueError(
                "Invalid question object."
            )


        if not question.get(
            "question"
        ):

            raise ValueError(
                "Question text is missing."
            )


        options = question.get(
            "options",
            []
        )


        if not isinstance(
            options,
            list
        ):

            raise ValueError(
                "Options must be a list."
            )


        if len(options) != requested_options:

            raise ValueError(
                "AI generated "
                f"{len(options)} options instead of "
                f"{requested_options}."
            )


        correct_id = question.get(
            "correct_option_id"
        )


        try:

            correct_id = int(
                correct_id
            )

        except Exception:

            raise ValueError(
                "Invalid correct_option_id."
            )


        if not (
            0 <= correct_id < requested_options
        ):

            raise ValueError(
                "correct_option_id is outside option range."
            )


        explanation = str(
            question.get(
                "explanation",
                ""
            )
        )


        if not explanation.startswith(
            "Ex:"
        ):

            question["explanation"] = (
                "Ex: "
                + explanation
            )


    return questions


# ============================================================
# OPTION LABEL
# ============================================================

def option_label(
    index
):

    return (
        chr(
            ord("A") + index
        )
    )


# ============================================================
# FORMAT ONE QUESTION
# ============================================================

def format_question(
    question,
    config
):

    lines = []


    # --------------------------------------------------------
    # QUESTION
    # --------------------------------------------------------

    question_text = str(
        question.get(
            "question",
            ""
        )
    ).strip()


    lines.append(
        question_text
    )


    # --------------------------------------------------------
    # MATCH THE FOLLOWING TABLE
    # --------------------------------------------------------

    match_left = question.get(
        "match_left",
        []
    )

    match_right = question.get(
        "match_right",
        []
    )


    question_type = (
        config.get(
            "question_types",
            ""
        )
        .lower()
    )


    if (
        "match" in question_type
        or match_left
        or match_right
    ):

        if match_left and match_right:

            lines.append(
                "| Column I | Column II |"
            )

            lines.append(
                "|---|---|"
            )


            max_length = max(
                len(match_left),
                len(match_right)
            )


            for i in range(
                max_length
            ):

                left = (
                    str(match_left[i])
                    if i < len(match_left)
                    else ""
                )

                right = (
                    str(match_right[i])
                    if i < len(match_right)
                    else ""
                )


                lines.append(
                    f"| {left} | {right} |"
                )


    # --------------------------------------------------------
    # STATEMENTS
    # --------------------------------------------------------

    statements = question.get(
        "statements",
        []
    )


    if statements:

        for i, statement in enumerate(
            statements,
            start=1
        ):

            lines.append(
                f"{i}. {str(statement).strip()}"
            )


    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

    options = question.get(
        "options",
        []
    )


    correct_id = int(
        question.get(
            "correct_option_id",
            0
        )
    )


    for i, option in enumerate(
        options
    ):

        label = option_label(
            i
        )


        option_text = str(
            option
        ).strip()


        suffix = (
            " ✅"
            if i == correct_id
            else ""
        )


        lines.append(
            f"{label}) {option_text}{suffix}"
        )


    # --------------------------------------------------------
    # EXPLANATION
    # --------------------------------------------------------

    explanation = str(
        question.get(
            "explanation",
            ""
        )
    ).strip()


    if not explanation.startswith(
        "Ex:"
    ):

        explanation = (
            "Ex: "
            + explanation
        )


    lines.append(
        explanation
    )


    return "\n".join(
        lines
    )


# ============================================================
# FORMAT ALL QUESTIONS
# ============================================================

def format_all_questions(
    questions,
    config
):

    formatted = []


    for question in questions:

        formatted.append(
            format_question(
                question,
                config
            )
        )


    # Double line break between questions
    return "\n\n".join(
        formatted
    )


# ============================================================
# SPLIT LONG TELEGRAM MESSAGE
# ============================================================

def split_message(
    text,
    limit=TELEGRAM_MESSAGE_LIMIT
):

    if len(text) <= limit:

        return [text]


    chunks = []

    current = ""


    for block in text.split(
        "\n\n"
    ):

        candidate = (
            current
            + ("\n\n" if current else "")
            + block
        )


        if len(candidate) <= limit:

            current = candidate

        else:

            if current:

                chunks.append(
                    current
                )


            # If a single question itself is too long
            if len(block) > limit:

                for i in range(
                    0,
                    len(block),
                    limit
                ):

                    chunks.append(
                        block[
                            i:i + limit
                        ]
                    )

                current = ""

            else:

                current = block


    if current:

        chunks.append(
            current
        )


    return chunks


# ============================================================
# SEND RAW CODE BLOCK
# ============================================================

async def send_formatted_result(
    update,
    text
):

    chunks = split_message(
        text
    )


    total = len(
        chunks
    )


    for i, chunk in enumerate(
        chunks,
        start=1
    ):

        # Telegram Markdown code block.
        # The content itself is kept raw.
        message = (
            "```\n"
            + chunk
            + "\n```"
        )


        if total > 1:

            message = (
                f"Part {i}/{total}\n"
                + message
            )


        await update.message.reply_text(
            message
        )


# ============================================================
# GENERATE QUIZ
# ============================================================

async def generate_quiz(
    update,
    config,
    source_contents,
    status_message
):

    try:

        logger.info(
            "Generating quiz using %s",
            MODEL_NAME
        )


        prompt = build_ai_prompt(
            config
        )


        contents = []


        # Source first
        if source_contents:

            contents.extend(
                source_contents
            )


        # Prompt
        contents.append(
            prompt
        )


        response = ai_client.models.generate_content(

            model=MODEL_NAME,

            contents=contents,

            config=types.GenerateContentConfig(

                response_mime_type="application/json"

            )
        )


        if not response.text:

            raise ValueError(
                "Gemini returned an empty response."
            )


        raw = clean_json_response(
            response.text
        )


        logger.info(
            "AI response received."
        )


        data = json.loads(
            raw
        )


        questions = validate_questions(
            data,
            config
        )


        formatted = format_all_questions(
            questions,
            config
        )


        try:

            await status_message.delete()

        except Exception:

            pass


        await send_formatted_result(
            update,
            formatted
        )


    except json.JSONDecodeError as error:

        logger.error(
            f"JSON parsing error: {error}"
        )


        await status_message.edit_text(
            "⚠️ AI response को JSON में convert नहीं किया जा सका.\n"
            "कृपया दोबारा कोशिश करें."
        )


    except Exception as error:

        logger.exception(
            "Quiz generation failed."
        )


        await status_message.edit_text(
            "⚠️ Quiz generate करते समय error आया:\n\n"
            f"{str(error)[:1000]}"
        )


# ============================================================
# CONFIGURATION MESSAGE HANDLER
# ============================================================

async def handle_configuration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        update.message.text
        or ""
    ).strip()


    config = parse_configuration(
        text
    )


    errors = validate_configuration(
        config
    )


    if errors:

        await update.message.reply_text(
            "⚠️ Configuration incomplete है.\n\n"
            "Missing / invalid:\n"
            + "\n".join(
                f"• {error}"
                for error in errors
            )
            + "\n\n"
            "Example:\n\n"
            "Topic / Input Source: Attached PDF/Image\n"
            "Page Number(s) / Range: All\n"
            "Number of Questions: 5\n"
            "Number of Options per Question: 4\n"
            "Level of Difficulty: Hard\n"
            "Language(s): English\n"
            "Question Type(s): Mixed"
        )

        return


    user_id = update.effective_user.id


    user_configs[
        user_id
    ] = config


    # --------------------------------------------------------
    # Attached source
    # --------------------------------------------------------

    if source_is_attached(
        config
    ):

        await update.message.reply_text(
            "✅ Configuration saved.\n\n"
            "अब अपना PDF या Image भेजें.\n\n"
            f"📄 Pages: {config['pages']}\n"
            f"❓ Questions: {config['questions']}\n"
            f"🔢 Options: {config['options']}\n"
            f"🎯 Difficulty: {config['difficulty']}\n"
            f"🌐 Language: {config['language']}\n"
            f"📝 Type: {config['question_types']}"
        )

        return


    # --------------------------------------------------------
    # Topic-only generation
    # --------------------------------------------------------

    status = await update.message.reply_text(
        "🤖 Configuration received.\n"
        "🧠 Topic से questions तैयार किए जा रहे हैं..."
    )


    topic_text = config[
        "topic"
    ]


    await generate_quiz(
        update,
        config,
        [
            types.Part.from_text(
                text=topic_text
            )
        ],
        status
    )


# ============================================================
# TEXT HANDLER
# ============================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        update.message.text
        or ""
    ).strip()


    if not text:

        return


    if text.startswith(
        "/"
    ):

        return


    # If it looks like configuration
    configuration_keywords = [
        "Topic / Input Source",
        "Number of Questions",
        "Number of Options",
        "Level of Difficulty",
        "Language(s)",
        "Question Type(s)"
    ]


    is_configuration = any(
        keyword.lower()
        in text.lower()
        for keyword in configuration_keywords
    )


    if is_configuration:

        await handle_configuration(
            update,
            context
        )

        return


    # --------------------------------------------------------
    # If user already configured attached source,
    # plain text can be treated as source material.
    # --------------------------------------------------------

    user_id = update.effective_user.id


    config = user_configs.get(
        user_id
    )


    if config and source_is_attached(
        config
    ):

        status = await update.message.reply_text(
            "📝 Text source process हो रहा है..."
        )


        await generate_quiz(
            update,
            config,
            [
                types.Part.from_text(
                    text=text
                )
            ],
            status
        )

        return


    await update.message.reply_text(
        "⚠️ पहले अपनी USER CONFIGURATION भेजें.\n\n"
        "Use /start for the format."
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id


    config = user_configs.get(
        user_id
    )


    if not config:

        await update.message.reply_text(
            "⚠️ पहले USER CONFIGURATION भेजें."
        )

        return


    if not source_is_attached(
        config
    ):

        await update.message.reply_text(
            "⚠️ Current configuration Topic-based है.\n"
            "अगर Image use करनी है तो configuration में:\n\n"
            "Topic / Input Source: Attached PDF/Image\n\n"
            "करके फिर Image भेजें."
        )

        return


    status = await update.message.reply_text(
        "🖼 Image received.\n"
        "🤖 Questions तैयार किए जा रहे हैं..."
    )


    try:

        photo = (
            update.message.photo[-1]
        )


        telegram_file = await photo.get_file()


        image_bytes = BytesIO()


        await telegram_file.download_to_memory(
            image_bytes
        )


        image_bytes.seek(0)


        # Validate image
        image = Image.open(
            image_bytes
        )


        image.load()


        # Convert to standard JPEG
        converted = BytesIO()


        image.convert(
            "RGB"
        ).save(
            converted,
            format="JPEG",
            quality=95
        )


        converted.seek(0)


        image_part = types.Part.from_bytes(
            data=converted.read(),
            mime_type="image/jpeg"
        )


        await generate_quiz(
            update,
            config,
            [
                image_part
            ],
            status
        )


    except Exception as error:

        logger.exception(
            "Image processing failed."
        )


        await status.edit_text(
            "⚠️ Image process error:\n"
            f"{str(error)[:800]}"
        )


# ============================================================
# PDF HANDLER
# ============================================================

async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id


    config = user_configs.get(
        user_id
    )


    if not config:

        await update.message.reply_text(
            "⚠️ पहले USER CONFIGURATION भेजें."
        )

        return


    if not source_is_attached(
        config
    ):

        await update.message.reply_text(
            "⚠️ Current configuration Topic-based है.\n"
            "PDF use करने के लिए पहले "
            "Attached PDF/Image configuration भेजें."
        )

        return


    document = (
        update.message.document
    )


    file_name = (
        document.file_name
        or ""
    )


    mime_type = (
        document.mime_type
        or ""
    ).lower()


    if not (
        mime_type == "application/pdf"
        or file_name.lower().endswith(".pdf")
    ):

        await update.message.reply_text(
            "⚠️ अभी केवल PDF documents supported हैं."
        )

        return


    status = await update.message.reply_text(
        "📄 PDF received.\n"
        "🔍 Selected pages process की जा रही हैं..."
    )


    try:

        telegram_file = await document.get_file()


        pdf_buffer = BytesIO()


        await telegram_file.download_to_memory(
            pdf_buffer
        )


        pdf_buffer.seek(0)


        original_pdf = pdf_buffer.read()


        # ----------------------------------------------------
        # Restrict pages
        # ----------------------------------------------------

        selected_pdf = extract_pdf_pages(
            original_pdf,
            config.get(
                "pages",
                "All"
            )
        )


        pdf_part = types.Part.from_bytes(
            data=selected_pdf,
            mime_type="application/pdf"
        )


        await generate_quiz(
            update,
            config,
            [
                pdf_part
            ],
            status
        )


    except Exception as error:

        logger.exception(
            "PDF processing failed."
        )


        await status.edit_text(
            "⚠️ PDF process error:\n"
            f"{str(error)[:1000]}"
        )


# ============================================================
# CLEAR CONFIG
# ============================================================

async def clear_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id


    user_configs.pop(
        user_id,
        None
    )


    await update.message.reply_text(
        "🗑️ आपकी configuration clear कर दी गई है.\n\n"
        "अब /start से नई configuration भेज सकते हैं."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram error:",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "Starting AI Quiz Generator..."
    )


    # Render health server
    health_thread = threading.Thread(
        target=run_health_server,
        daemon=True
    )

    health_thread.start()


    # Telegram application
    application = (
        ApplicationBuilder()
        .token(
            TELEGRAM_BOT_TOKEN
        )
        .build()
    )


    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )


    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )


    application.add_handler(
        CommandHandler(
            "clear",
            clear_command
        )
    )


    # PDF handler
    application.add_handler(
        MessageHandler(
            filters.Document.PDF,
            handle_document
        )
    )


    # Image handler
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )


    # Text handler
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )


    application.add_error_handler(
        error_handler
    )


    logger.info(
        "Bot is running..."
    )


    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":
    main()
