import asyncio
import io
import logging
import os

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AVALAI_API_KEY = os.getenv("AVALAI_API_KEY")

AVALAI_BASE_URL = os.getenv(
    "AVALAI_BASE_URL",
    "https://api.avalai.ir/v1",
).rstrip("/")

MODEL = os.getenv("AVALAI_TRANSCRIPTION_MODEL", "whisper-1")
LANGUAGE = os.getenv("TRANSCRIPTION_LANGUAGE", "fa").strip()

MAX_FILE_SIZE = 20 * 1024 * 1024

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.WARNING,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def initialize(application: Application):
    application.bot_data["client"] = httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=20.0)
    )

    application.bot_data["semaphore"] = asyncio.Semaphore(3)

    print("Bot is running ...", flush=True)


async def shutdown(application: Application):
    client = application.bot_data.get("client")

    if client is not None:
        await client.aclose()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        "سلام! خوش اومدی 👋😊\n\n"
        "یه ویس برام بفرست؛ منتظرم تا برات به متن تبدیلش کنم 🎤📝"
    )


async def voice_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message is None or message.voice is None:
        return

    voice = message.voice

    if voice.file_size and voice.file_size > MAX_FILE_SIZE:
        await message.reply_text(
            "این ویس یه‌کم حجیمه 😅\n"
            "لطفاً یه ویس کمتر از ۲۰ مگابایت بفرست."
        )
        return

    status_message = await message.reply_text(
        "⌛ دارم ویست رو به متن تبدیل می‌کنم؛ یه کوچولو صبر کن 😊"
    )

    try:
        semaphore = context.application.bot_data["semaphore"]
        client = context.application.bot_data["client"]

        async with semaphore:
            telegram_file = await context.bot.get_file(voice.file_id)

            with io.BytesIO() as audio:
                await telegram_file.download_to_memory(out=audio)

                if audio.tell() > MAX_FILE_SIZE:
                    await status_message.edit_text(
                        "این ویس یه‌کم حجیمه 😅\n"
                        "لطفاً یه ویس کمتر از ۲۰ مگابایت بفرست."
                    )
                    return

                audio.seek(0)

                data = {
                    "model": MODEL,
                    "response_format": "json",
                }

                if LANGUAGE:
                    data["language"] = LANGUAGE

                response = await client.post(
                    f"{AVALAI_BASE_URL}/audio/transcriptions",
                    headers={
                        "Authorization": f"Bearer {AVALAI_API_KEY}"
                    },
                    data=data,
                    files={
                        "file": ("voice.ogg", audio, "audio/ogg")
                    },
                )

            if response.is_error:
                logger.warning(
                    "AvalAI returned HTTP %s",
                    response.status_code,
                )

                await status_message.edit_text(
                    "متأسفانه الان نتونستم ویست رو تبدیل کنم 😕\n"
                    "لطفاً کمی بعد دوباره امتحان کن."
                )
                return

            result = response.json()

            text = (
                result.get("text")
                if isinstance(result, dict)
                else None
            )

            if not isinstance(text, str) or not text.strip():
                await status_message.edit_text(
                    "نتونستم گفتار واضحی توی این ویس پیدا کنم 🥲\n"
                    "میشه یه ویس واضح‌تر بفرستی؟ 🎤"
                )
                return

            text = text.strip()

        # قطعه‌های کوتاه برای رعایت محدودیت طول پیام تلگرام.
        chunks = [
            text[index:index + 2000]
            for index in range(0, len(text), 2000)
        ]

        # جایگزینی پیام انتظار با نتیجه.
        await status_message.edit_text(
            f"📝 متن ویس :\n\n{chunks[0]}",
            parse_mode=None,
        )

        # ارسال ادامه متن، فقط در صورت طولانی بودن.
        for chunk in chunks[1:]:
            await message.reply_text(
                chunk,
                parse_mode=None,
            )

    except httpx.TimeoutException:
        await status_message.edit_text(
            "تبدیل ویست بیشتر از حد معمول طول کشید ⏳\n"
            "لطفاً دوباره امتحان کن 😊"
        )

    except httpx.RequestError:
        logger.warning("Could not connect to AvalAI")

        await status_message.edit_text(
            "الان ارتباطم با سرویس تبدیل متن قطع شده 😕\n"
            "لطفاً کمی بعد دوباره امتحان کن."
        )

    except Exception as exc:
        logger.warning(
            "Processing failed: %s",
            type(exc).__name__,
        )

        await status_message.edit_text(
            "یه مشکلی موقع تبدیل ویست پیش اومد 🥲\n"
            "لطفاً دوباره بفرستش."
        )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.warning(
        "Bot error: %s",
        type(context.error).__name__,
    )


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN در فایل .env تنظیم نشده است."
        )

    if not AVALAI_API_KEY:
        raise RuntimeError(
            "AVALAI_API_KEY در فایل .env تنظیم نشده است."
        )

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(8)
        .post_init(initialize)
        .post_shutdown(shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_error_handler(error_handler)

    app.run_polling()


if __name__ == "__main__":
    main()