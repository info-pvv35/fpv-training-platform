import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "fpv_bot")
DB_USER = os.getenv("DB_USER", "fpv_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
SCHEDULE_URL = os.getenv("SCHEDULE_URL", "https://example.com/schedule")
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")