import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram_i18n import I18nMiddleware
from aiogram_i18n.cores import YamlCore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web
from .config import (
    BOT_TOKEN, SENTRY_DSN, WEBHOOK_URL,
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
)
from .database.db import init_db_pool, close_db_pool
from .middlewares.i18n import ACLMiddleware
from .handlers.user import router as user_router
from .handlers.admin import router as admin_router
from .handlers.payments import router as payments_router, setup_payment_webhooks
from .handlers.voice import router as voice_router
import sentry_sdk

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация Sentry
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        environment="production",
    )
    logger.info("✅ Sentry initialized")

# Глобальные переменные для graceful shutdown
runner = None
site = None

async def on_startup(bot: Bot):
    """Действия при старте бота"""
    logger.info("🚀 Bot starting...")
    await init_db_pool()
    logger.info("✅ Database pool initialized")

    if WEBHOOK_URL:
        webhook_info = await bot.get_webhook_info()
        if webhook_info.url != f"{WEBHOOK_URL}/webhook":
            await bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
            logger.info(f"🔗 Webhook set to {WEBHOOK_URL}/webhook")
        else:
            logger.info("🔗 Webhook already set")

async def on_shutdown(bot: Bot):
    """Действия при остановке бота"""
    logger.info("🛑 Bot shutting down...")

    # Остановка веб-сервера
    global runner, site
    if site:
        await site.stop()
        logger.info("✅ Web server stopped")
    if runner:
        await runner.cleanup()

    # Остановка шедулера
    if hasattr(bot, 'scheduler') and bot.scheduler:
        bot.scheduler.shutdown()
        logger.info("✅ Scheduler shutdown")

    # Закрытие пула БД
    await close_db_pool()
    logger.info("✅ Database pool closed")

    # Закрытие сессии бота
    await bot.session.close()
    logger.info("✅ Bot session closed")


async def main():
    """Главная функция запуска бота"""
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Инициализация шедулера
    scheduler = AsyncIOScheduler()
    scheduler.start()
    bot.scheduler = scheduler  # Привязываем к боту для доступа в хендлерах
    logger.info("✅ Scheduler started")

    # Настройка интернационализации
    i18n_middleware = I18nMiddleware(
        core=YamlCore(path="bot/utils/localization/{locale}.yaml"),
        default_locale="ru",
    )
    i18n_middleware.setup(dp)
    dp.message.middleware(ACLMiddleware())
    dp.callback_query.middleware(ACLMiddleware())
    logger.info("✅ i18n middleware configured")

    # Подключение роутеров
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(payments_router)
    dp.include_router(voice_router)
    logger.info("✅ All routers registered")

    # Настройка startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    try:
        if WEBHOOK_URL:
            # Создаем aiohttp приложение
            app = web.Application()

            # Регистрируем обработчики вебхуков платежей
            setup_payment_webhooks(app, bot)

            # Регистрируем основной вебхук бота
            SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")

            # Настраиваем приложение
            setup_application(app, dp, bot=bot)

            # Запускаем веб-сервер
            global runner, site
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', 8080)
            await site.start()
            logger.info(f"🌐 Webhook server started on http://0.0.0.0:8080")
            logger.info(f"🔗 Webhook URL: {WEBHOOK_URL}/webhook")

            # Бот будет работать до прерывания
            while True:
                await asyncio.sleep(3600)  # Спим 1 час, пока работает вебхук

        else:
            # Запуск в режиме long polling
            logger.info("📡 Starting bot in polling mode...")
            await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise
    finally:
        await on_shutdown(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"💥 Fatal error: {e}")