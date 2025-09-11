import asyncio
import os
import logging
from aiogram import Router, F
from aiogram.types import Message, Voice
from aiogram_i18n import I18nContext
from ..config import OPENAI_API_KEY
from ..database.db import get_admin
import openai

# Настройка логгера
logger = logging.getLogger(__name__)

# Инициализация OpenAI
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

router = Router()


@router.message(F.voice)
async def handle_voice_message(message: Message, i18n: I18nContext):
    """Обработка голосовых сообщений от админов"""
    user_id = message.from_user.id

    # Проверка: пользователь — админ?
    admin = await get_admin(user_id)
    if not admin:
        # Игнорируем не-админов
        return

    voice: Voice = message.voice

    try:
        # Скачиваем голосовое сообщение
        file_info = await message.bot.get_file(voice.file_id)
        voice_file = f"voice_{user_id}_{voice.file_unique_id}.oga"

        await message.bot.download_file(file_info.file_path, destination=voice_file)
        logger.info(f"🔊 Голосовое сообщение скачано: {voice_file}")

        # Транскрибируем асинхронно (чтобы не блокировать бота)
        transcript = await asyncio.to_thread(
            openai.Audio.transcribe,
            model="whisper-1",
            file=open(voice_file, "rb"),
            language="ru"
        )
        text = transcript["text"]
        logger.info(f"📝 Транскрипция: {text}")

        # Отправляем транскрипцию админу
        await message.answer(f"🎤 Распознано: _{text}_", parse_mode="Markdown")

        # Генерируем команду через GPT
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="gpt-4-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — голосовой ассистент FPV-платформы. Преобразуй запрос админа в команду для Telegram-бота. "
                        "Отвечай ТОЛЬКО в формате команды Telegram (например, /add_training Москва Парк 2025-06-01 18:00 race 10) "
                        "или фразой 'Не понял'. Не добавляй пояснений."
                    )
                },
                {
                    "role": "user",
                    "content": f"Голосовой запрос: {text}"
                }
            ],
            temperature=0.0,
            max_tokens=100
        )

        command = response.choices[0].message.content.strip()
        logger.info(f"🤖 Сгенерированная команда: {command}")

        if command.startswith("/"):
            # Эмулируем выполнение команды
            fake_message = Message(
                message_id=message.message_id,
                from_user=message.from_user,
                chat=message.chat,
                date=message.date,
                text=command
            )

            # Определяем, какую команду вызывать
            if command.startswith("/add_training"):
                from .admin import add_training_cmd
                await add_training_cmd(fake_message, i18n)
            elif command.startswith("/add_admin"):
                from .admin import add_location_admin
                await add_location_admin(fake_message, i18n)
            elif command.startswith("/add_super_admin"):
                from .admin import add_super_admin
                await add_super_admin(fake_message, i18n)
            elif command.startswith("/search"):
                from .user import search_trainings_cmd
                await search_trainings_cmd(fake_message, i18n)
            else:
                await message.answer(f"⚠️ Команда не поддерживается голосом: {command}")

            await message.answer(f"✅ Выполняю команду: `{command}`", parse_mode="Markdown")

        else:
            await message.answer(f"❌ Не понял: {text}")

    except openai.error.AuthenticationError:
        await message.answer("❌ Ошибка: неверный API-ключ OpenAI.")
        logger.error("OpenAI AuthenticationError")
    except openai.error.RateLimitError:
        await message.answer("❌ Ошибка: лимит запросов OpenAI превышен.")
        logger.error("OpenAI RateLimitError")
    except Exception as e:
        await message.answer(f"❌ Ошибка обработки голоса: {e}")
        logger.error(f"Ошибка обработки голоса: {e}", exc_info=True)
    finally:
        # Удаляем временный файл
        if os.path.exists(voice_file):
            try:
                os.remove(voice_file)
                logger.info(f"🗑️ Временный файл удалён: {voice_file}")
            except Exception as e:
                logger.error(f"Не удалось удалить файл {voice_file}: {e}")