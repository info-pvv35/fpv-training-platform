# bot/bot.py — Полная версия FPV Training Bot
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, PreCheckoutQueryHandler
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
import pytz
import secrets
import qrcode
from io import BytesIO
from telegram import InputFile
import openai

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))  # Только для первого суперадмина
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TIMEZONE = pytz.timezone('Europe/Moscow')

# Глобальные константы
TRACK_TYPES = {
    "race": "🏁 Гоночная",
    "freestyle": "🪂 Фристайл",
    "low": "⬇️ Low-Level",
    "tech": "⚙️ Техническая",
    "cinematic": "🎥 Кинематографичная",
    "training": "🎓 Тренировочная",
    "other": "❓ Другое"
}

VTX_BANDS = {
    "R": [5658, 5732, 5800, 5866, 5934, 6000, 6066, 6132],
    "F": [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
    "E": [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945]
}

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Подключение к БД
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "fpv_bot"),
        user=os.getenv("DB_USER", "fpv_user"),
        password=os.getenv("DB_PASSWORD", ""),
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# Инициализация БД (вызывается при старте)
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trainings (
            id SERIAL PRIMARY KEY,
            city TEXT NOT NULL DEFAULT 'Не указан',
            location TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            track_type TEXT DEFAULT 'other',
            max_pilots INTEGER DEFAULT 10,
            current_pilots INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS registrations (
            id SERIAL PRIMARY KEY,
            training_id INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            vtx_band TEXT,
            vtx_channel INTEGER,
            paid INTEGER DEFAULT 0,
            payment_id TEXT,
            payment_date TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_consent (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            nickname TEXT,
            consent_given INTEGER DEFAULT 0,
            consent_date TIMESTAMPTZ
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY,
            role TEXT NOT NULL CHECK (role IN ('super_admin', 'location_admin')),
            managed_locations JSONB DEFAULT '[]',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id SERIAL PRIMARY KEY,
            admin_user_id BIGINT NOT NULL,
            action TEXT NOT NULL,
            target_id BIGINT,
            details JSONB,
            ip_address TEXT,
            user_agent TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_2fa_sessions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            secret_code TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()

# Логирование действий админов
def log_admin_action(admin_user_id, action, target_id=None, details=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO admin_audit_log (admin_user_id, action, target_id, details)
        VALUES (%s, %s, %s, %s)
    ''', (
        admin_user_id,
        action,
        target_id,
        psycopg2.extras.Json(details) if details else None
    ))
    conn.commit()
    cursor.close()
    conn.close()

# Получение админа
def get_admin(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, role, managed_locations FROM admins WHERE user_id = %s', (user_id,))
    admin = cursor.fetchone()
    cursor.close()
    conn.close()
    return admin

# Проверка прав на управление площадкой
def can_manage_training(user_id, city, location):
    admin = get_admin(user_id)
    if not admin:
        return False
    if admin['role'] == 'super_admin':
        return True
    if admin['role'] == 'location_admin':
        for loc in admin['managed_locations']:
            if loc.get('city') == city and loc.get('location') == location:
                return True
    return False

# Получение всех тренировок
def get_all_trainings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
        FROM trainings
        ORDER BY date, time
    ''')
    trainings = cursor.fetchall()
    cursor.close()
    conn.close()
    return trainings

# Получение занятых каналов
def get_used_channels(training_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT vtx_band, vtx_channel
        FROM registrations
        WHERE training_id = %s
    ''', (training_id,))
    used = [(row['vtx_band'], row['vtx_channel']) for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return used

# Предложить свободный канал
def suggest_free_channel(training_id):
    used = get_used_channels(training_id)
    used_set = set(used)
    for band_name, channels in VTX_BANDS.items():
        for i, freq in enumerate(channels, start=1):
            if (band_name, i) not in used_set:
                return band_name, i, freq
    return None, None, None

# Регистрация пилота с каналом
def register_pilot_with_channel(training_id, user_id, username, full_name, preferred_band=None, preferred_channel=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM registrations WHERE training_id = %s AND user_id = %s
    ''', (training_id, user_id))
    if cursor.fetchone():
        conn.close()
        return False, "Вы уже записаны на эту тренировку."
    cursor.execute('''
        SELECT current_pilots, max_pilots FROM trainings WHERE id = %s
    ''', (training_id,))
    result = cursor.fetchone()
    if not result:
        conn.close()
        return False, "Тренировка не найдена."
    current, max_p = result['current_pilots'], result['max_pilots']
    if current >= max_p:
        conn.close()
        return False, "Нет свободных мест."
    band, channel, freq = None, None, None
    if preferred_band and preferred_channel:
        if preferred_band in VTX_BANDS and 1 <= preferred_channel <= 8:
            if (preferred_band, preferred_channel) not in get_used_channels(training_id):
                band, channel = preferred_band, preferred_channel
                freq = VTX_BANDS[preferred_band][channel - 1]
            else:
                conn.close()
                return False, f"Канал {preferred_band}{preferred_channel} уже занят. Выберите другой."
        else:
            conn.close()
            return False, "Неверный формат канала. Используйте: R3, F5, E1."
    if not band:
        band, channel, freq = suggest_free_channel(training_id)
        if not band:
            conn.close()
            return False, "Нет свободных каналов для записи."
    cursor.execute('''
        INSERT INTO registrations (training_id, user_id, vtx_band, vtx_channel)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    ''', (training_id, user_id, band, channel))
    reg_id = cursor.fetchone()['id']
    cursor.execute('''
        UPDATE trainings SET current_pilots = current_pilots + 1 WHERE id = %s
    ''', (training_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return True, f"Вы успешно записаны! Ваш канал: {band}{channel} ({freq} MHz)", reg_id

# Отмена записи
def unregister_pilot(training_id, user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM registrations WHERE training_id = %s AND user_id = %s
    ''', (training_id, user_id))
    if cursor.rowcount == 0:
        conn.close()
        return False, "Вы не были записаны на эту тренировку."
    cursor.execute('''
        UPDATE trainings SET current_pilots = current_pilots - 1 WHERE id = %s
    ''', (training_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return True, "Ваша запись отменена."

# Получение пилотов тренировки
def get_pilots_for_training(training_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            COALESCE(uc.nickname, 'Аноним') as display_name,
            r.vtx_band,
            r.vtx_channel,
            r.paid
        FROM registrations r
        LEFT JOIN user_consent uc ON r.user_id = uc.user_id
        WHERE r.training_id = %s
        ORDER BY r.vtx_band, r.vtx_channel
    ''', (training_id,))
    pilots = cursor.fetchall()
    cursor.close()
    conn.close()
    return pilots

# Удаление тренировки
def delete_training(training_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM trainings WHERE id = %s', (training_id,))
    conn.commit()
    cursor.close()
    conn.close()

# Добавление тренировки
def add_training(city, location, date, time, track_type="other", max_pilots=10):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trainings (city, location, date, time, track_type, max_pilots)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (city, location, date, time, track_type, max_pilots))
    training_id = cursor.fetchone()['id']
    conn.commit()
    cursor.close()
    conn.close()
    return training_id

# Добавление админа
def add_admin(user_id, role, managed_locations=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if managed_locations is None:
        managed_locations = []
    cursor.execute('''
        INSERT INTO admins (user_id, role, managed_locations)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            role = EXCLUDED.role,
            managed_locations = EXCLUDED.managed_locations
    ''', (user_id, role, psycopg2.extras.Json(managed_locations)))
    conn.commit()
    cursor.close()
    conn.close()

# Удаление админа
def remove_admin(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admins WHERE user_id = %s', (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

# Создание 2FA сессии
def create_2fa_session(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admin_2fa_sessions WHERE expires_at < NOW()')
    code = str(secrets.randbelow(900000) + 100000)
    expires_at = datetime.now(pytz.UTC) + timedelta(minutes=10)
    cursor.execute('''
        INSERT INTO admin_2fa_sessions (user_id, secret_code, expires_at)
        VALUES (%s, %s, %s)
        RETURNING id
    ''', (user_id, code, expires_at))
    session_id = cursor.fetchone()['id']
    conn.commit()
    cursor.close()
    conn.close()
    return code

# Проверка 2FA кода
def verify_2fa_code(code):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id FROM admin_2fa_sessions
        WHERE secret_code = %s AND expires_at > NOW()
    ''', (code,))
    result = cursor.fetchone()
    if result:
        cursor.execute('DELETE FROM admin_2fa_sessions WHERE secret_code = %s', (code,))
        conn.commit()
        user_id = result['user_id']
        cursor.close()
        conn.close()
        return user_id
    cursor.close()
    conn.close()
    return None

# Отправка уведомления новому админу
async def notify_new_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int, role: str, locations=None):
    try:
        message = "🎉 *Поздравляем!*\n\n"
        message += "Вам назначены права администратора FPV-платформы.\n\n"
        if role == 'super_admin':
            message += "👑 *Роль:* Суперадминистратор\n"
            message += "У вас есть полный доступ ко всем площадкам и функциям."
        else:
            message += "📍 *Роль:* Администратор площадок\n"
            if locations:
                loc_str = "\n".join([f" - {loc['city']} → {loc['location']}" for loc in locations])
                message += f"Ваши площадки:\n{loc_str}"
            else:
                message += "Ваши площадки будут указаны отдельно."
        message += "\n\nИспользуйте /admin для входа в веб-админку."
        await context.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление админу {user_id}: {e}")

# Планирование напоминаний
def schedule_reminders(application, training_id, date_str, time_str):
    dt_str = f"{date_str} {time_str}"
    try:
        training_dt = TIMEZONE.localize(datetime.strptime(dt_str, "%Y-%m-%d %H:%M"))
        if training_dt > datetime.now(TIMEZONE):
            # За 24 часа
            reminder_tomorrow = training_dt - timedelta(hours=24)
            application.job_queue.run_once(
                send_reminder,
                when=reminder_tomorrow,
                data={'training_id': training_id, 'when': 'tomorrow'},
                name=f"reminder_tomorrow_{training_id}"
            )
            # За 1 час
            reminder_hour = training_dt - timedelta(hours=1)
            application.job_queue.run_once(
                send_reminder,
                when=reminder_hour,
                data={'training_id': training_id, 'when': 'hour'},
                name=f"reminder_hour_{training_id}"
            )
    except Exception as e:
        logger.error(f"Ошибка при планировании напоминаний для {training_id}: {e}")

# Отправка напоминания
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    training_id = job.data['training_id']
    when = job.data['when']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT date, time, location FROM trainings WHERE id = %s', (training_id,))
    training = cursor.fetchone()
    if not training:
        conn.close()
        return
    date_str, time_str, location = training['date'], training['time'], training['location']
    dt_str = f"{date_str} {time_str}"
    try:
        training_dt = TIMEZONE.localize(datetime.strptime(dt_str, "%Y-%m-%d %H:%M"))
    except:
        conn.close()
        return
    message = ""
    if when == "tomorrow":
        message = f"🔔 *Напоминание за 24 часа!*\n\nТренировка: {location}\n📅 {date_str} 🕒 {time_str}\n\nНе забудьте подготовить дрон и очки!\n\nВаш канал: {{channel}}\n\nПриятных полётов! 🚁"
    elif when == "hour":
        message = f"🚨 *Старт через 1 час!*\n\n📍 {location} | 📅 {date_str} | 🕒 {time_str}\n\nСобирайтесь, настраивайте VTX! Ваш канал: {{channel}}\n\nУвидимся на площадке! 🏁"
    pilots = get_pilots_for_training(training_id)
    for pilot in pilots:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.vtx_band, r.vtx_channel, r.user_id
            FROM registrations r
            WHERE r.training_id = %s AND r.user_id = (
                SELECT user_id FROM registrations WHERE id = (
                    SELECT id FROM registrations WHERE training_id = %s AND vtx_band = %s AND vtx_channel = %s LIMIT 1
                )
            )
        ''', (training_id, training_id, pilot['vtx_band'], pilot['vtx_channel']))
        result = cursor.fetchone()
        conn.close()
        if result:
            user_id = result['user_id']
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message.format(channel=f"{result['vtx_band']}{result['vtx_channel']}"),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить напоминание пользователю {user_id}: {e}")

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT consent_given FROM user_consent WHERE user_id = %s', (user_id,))
    result = cursor.fetchone()
    conn.close()
    if not result or not result['consent_given']:
        await ask_consent(update, context)
        return
    keyboard = [
        [InlineKeyboardButton("📅 Расписание", callback_data='view_trainings')],
        [InlineKeyboardButton("✏️ Записаться", callback_data='view_trainings')],
        [InlineKeyboardButton("❌ Отменить запись", callback_data='cancel_registration')],
        [InlineKeyboardButton("🌐 Веб-расписание", callback_data='web_schedule')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Я бот для записи на FPV-тренировки. Выберите действие:", reply_markup=reply_markup)

async def ask_consent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
📜 *Политика конфиденциальности*

Мы собираем минимально необходимые данные:
- Telegram ID (для уведомлений)
- Никнейм (по вашему желанию)
- Выбранный канал VTX

Мы НЕ храним:
- Ваше ФИО
- Телефон, email, адрес

Вы можете:
- Изменить никнейм: /set_nickname
- Удалить данные: /delete_me

Согласны продолжить?
    """
    keyboard = [[InlineKeyboardButton("✅ Да, согласен", callback_data='consent_given')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def consent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'consent_given':
        user_id = query.from_user.id
        username = query.from_user.username or f"user_{user_id}"
        full_name = query.from_user.full_name
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO user_consent (user_id, username, nickname, consent_given, consent_date)
            VALUES (%s, %s, %s, 1, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                consent_given = 1,
                consent_date = NOW()
        ''', (user_id, username, full_name))
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ Спасибо за согласие! Теперь вы можете пользоваться ботом.\n\nРекомендуем установить никнейм: /set_nickname")
        await start(update, context)

async def set_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT consent_given FROM user_consent WHERE user_id = %s', (user_id,))
    result = cursor.fetchone()
    if not result or not result['consent_given']:
        await update.message.reply_text("Сначала дайте согласие: /start")
        conn.close()
        return
    if not context.args:
        await update.message.reply_text("Укажите никнейм: /set_nickname ВашНик")
        conn.close()
        return
    nickname = " ".join(context.args)[:50]
    cursor.execute('UPDATE user_consent SET nickname = %s WHERE user_id = %s', (nickname, user_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Ваш никнейм: {nickname}")

async def delete_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM registrations WHERE user_id = %s', (user_id,))
    cursor.execute('DELETE FROM user_consent WHERE user_id = %s', (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑️ Все ваши данные удалены. Чтобы вернуться — напишите /start.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or f"user_{user_id}"
    full_name = query.from_user.full_name

    if query.data == 'view_trainings':
        trainings = get_all_trainings()
        if not trainings:
            await query.edit_message_text("Нет тренировок.")
            return
        text = "📅 *Доступные тренировки:*\n\n"
        keyboard = []
        for t in trainings:
            track_label = TRACK_TYPES.get(t['track_type'], "❓")
            text += f"🏙️ {t['city']} | 📍 {t['location']} | 📅 {t['date']} | 🕒 {t['time']} | 🎯 {track_label} | ({t['current_pilots']}/{t['max_pilots']})\n"
            keyboard.append([InlineKeyboardButton(f"Записаться: {t['date']} {t['time']}", callback_data=f'register_{t["id"]}')])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='main_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    elif query.data.startswith('register_'):
        training_id = int(query.data.split('_')[1])
        keyboard = [
            [InlineKeyboardButton("🎲 Автоматически", callback_data=f'reg_auto_{training_id}')],
            [InlineKeyboardButton("🎛️ Вручную", callback_data=f'reg_manual_{training_id}')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='view_trainings')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Как выбрать канал?", reply_markup=reply_markup)

    elif query.data.startswith('reg_auto_'):
        training_id = int(query.data.split('_')[2])
        success, message, reg_id = register_pilot_with_channel(training_id, user_id, username, full_name)
        await query.edit_message_text(message)

    elif query.data.startswith('reg_manual_'):
        training_id = int(query.data.split('_')[2])
        bands = list(VTX_BANDS.keys())
        keyboard = [[InlineKeyboardButton(f"Band {band}", callback_data=f'choose_band_{training_id}_{band}')] for band in bands]
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f'register_{training_id}')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите Band:", reply_markup=reply_markup)

    elif query.data.startswith('choose_band_'):
        parts = query.data.split('_')
        training_id = int(parts[2])
        band = parts[3]
        keyboard = [[InlineKeyboardButton(f"{band}{ch}", callback_data=f'set_channel_{training_id}_{band}_{ch}')] for ch in range(1, 9)]
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f'reg_manual_{training_id}')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Выберите канал в Band {band}:", reply_markup=reply_markup)

    elif query.data.startswith('set_channel_'):
        parts = query.data.split('_')
        training_id = int(parts[2])
        band = parts[3]
        channel = int(parts[4])
        success, message, reg_id = register_pilot_with_channel(training_id, user_id, username, full_name, band, channel)
        await query.edit_message_text(message)

    elif query.data == 'cancel_registration':
        trainings = get_all_trainings()
        if not trainings:
            await query.edit_message_text("Нет тренировок.")
            return
        keyboard = []
        for t in trainings:
            keyboard.append([InlineKeyboardButton(f"Отменить: {t['date']} {t['time']}", callback_data=f'unregister_{t["id"]}')])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data='main_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите тренировку:", reply_markup=reply_markup)

    elif query.data.startswith('unregister_'):
        training_id = int(query.data.split('_')[1])
        success, message = unregister_pilot(training_id, user_id)
        await query.edit_message_text(message)

    elif query.data == 'web_schedule':
        SCHEDULE_URL = "https://your-domain.com/schedule"  # Замени на свой!
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(SCHEDULE_URL)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        bio = BytesIO()
        bio.name = 'schedule_qr.png'
        img.save(bio, 'PNG')
        bio.seek(0)
        await query.message.reply_photo(photo=InputFile(bio), caption=f"🌐 Онлайн-расписание:\n{SCHEDULE_URL}")
        await query.edit_message_text("QR-код отправлен выше.")

    elif query.data == 'main_menu':
        await start(update, context)

    # Админ панель
    elif query.data == 'admin_panel':
        admin = get_admin(user_id)
        if not admin:
            await query.edit_message_text("⛔ Доступ запрещён.")
            return
        keyboard = [
            [InlineKeyboardButton("➕ Добавить тренировку", callback_data='add_training_info')],
            [InlineKeyboardButton("🗑️ Удалить тренировку", callback_data='delete_training')],
            [InlineKeyboardButton("📋 Пилоты", callback_data='list_pilots')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🔐 Админ-панель:", reply_markup=reply_markup)

# Команды администраторов
async def add_super_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    admin = get_admin(user_id)
    if not admin or admin['role'] != 'super_admin':
        await update.message.reply_text("⛔ Только суперадмин может назначать админов.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Использование: /add_super_admin USER_ID")
        return
    try:
        new_admin_id = int(context.args[0])
        add_admin(new_admin_id, 'super_admin')
        await notify_new_admin(context, new_admin_id, 'super_admin')
        await update.message.reply_text(f"✅ Пользователь {new_admin_id} — суперадмин.")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка.")

async def add_location_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    admin = get_admin(user_id)
    if not admin or admin['role'] != 'super_admin':
        await update.message.reply_text("⛔ Только суперадмин.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("Использование: /add_admin USER_ID город локация [...]")
        return
    try:
        new_admin_id = int(context.args[0])
        locations = []
        args = context.args[1:]
        for i in range(0, len(args), 2):
            if i+1 < len(args):
                locations.append({"city": args[i], "location": args[i+1]})
        if not locations:
            await update.message.reply_text("❌ Укажите пары город/локация.")
            return
        add_admin(new_admin_id, 'location_admin', locations)
        await notify_new_admin(context, new_admin_id, 'location_admin', locations)
        loc_str = "; ".join([f"{loc['city']} - {loc['location']}" for loc in locations])
        await update.message.reply_text(f"✅ Админ площадок:\n{loc_str}")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка.")

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    admin = get_admin(user_id)
    if not admin or admin['role'] != 'super_admin':
        await update.message.reply_text("⛔ Только суперадмин.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Использование: /remove_admin USER_ID")
        return
    try:
        admin_id = int(context.args[0])
        remove_admin(admin_id)
        await update.message.reply_text(f"✅ Админ {admin_id} удалён.")
    except Exception as e:
        await update.message.reply_text("❌ Ошибка.")

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    admin = get_admin(user_id)
    if not admin:
        await update.message.reply_text("⛔ Не админ.")
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, role, managed_locations FROM admins ORDER BY role, user_id')
    admins = cursor.fetchall()
    conn.close()
    if not admins:
        await update.message.reply_text("Нет админов.")
        return
    text = "📋 *Администраторы:*\n\n"
    for a in admins:
        text += f"🆔 {a['user_id']} | {a['role']}\n"
        if a['role'] == 'location_admin' and a['managed_locations']:
            for loc in a['managed_locations']:
                text += f"   → {loc['city']} - {loc['location']}\n"
        text += "\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# Оплата
async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    user_id = update.message.from_user.id
    username = update.message.from_user.username or f"user_{user_id}"
    full_name = update.message.from_user.full_name

    try:
        parts = payload.split('_')
        training_id = int(parts[1])
        payer_id = int(parts[2])
        if payer_id != user_id:
            raise ValueError("User mismatch")
    except:
        await update.message.reply_text("Ошибка платежа.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT consent_given FROM user_consent WHERE user_id = %s', (user_id,))
    consent = cursor.fetchone()
    if not consent or not consent['consent_given']:
        await update.message.reply_text("Сначала дайте согласие: /start")
        conn.close()
        return

    cursor.execute('SELECT nickname FROM user_consent WHERE user_id = %s', (user_id,))
    nick_result = cursor.fetchone()
    nickname = nick_result['nickname'] if nick_result and nick_result['nickname'] else full_name

    success, message, reg_id = register_pilot_with_channel(training_id, user_id, username, nickname)
    if not success:
        await update.message.reply_text(f"Ошибка записи: {message}")
        conn.close()
        return

    cursor.execute('''
        UPDATE registrations
        SET paid = 1, payment_id = %s, payment_date = NOW()
        WHERE id = %s
    ''', (payment.telegram_payment_charge_id, reg_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Оплата прошла!\n\n{message}\n\nСпасибо! 🚁")

# Голосовой ассистент
async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    admin = get_admin(user_id)
    if not admin:
        return
    if not update.message.voice:
        return
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        voice_file = f"voice_{user_id}.oga"
        await file.download_to_drive(voice_file)
        with open(voice_file, "rb") as audio_file:
            transcript = openai.Audio.transcribe("whisper-1", audio_file, language="ru")
        text = transcript["text"]
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": "Ты — голосовой ассистент FPV-платформы. Преобразуй запрос админа в команду для бота. Отвечай ТОЛЬКО командой или 'Не понял'."},
                {"role": "user", "content": f"Голосовой запрос: {text}"}
            ]
        )
        command = response.choices[0].message.content.strip()
        if command.startswith("/"):
            fake_update = Update(update_id=update.update_id, message=update.message)
            fake_update.message.text = command
            context.args = command.split()[1:] if len(command.split()) > 1 else []
            if command.startswith("/add_training"):
                await add_training_cmd(fake_update, context)
            else:
                await update.message.reply_text(f"Выполняю: {command}")
        else:
            await update.message.reply_text(f"Не понял: {text}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# Добавление тренировки через команду
async def add_training_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    admin = get_admin(user_id)
    if not admin:
        await update.message.reply_text("⛔ Не админ.")
        return
    if len(context.args) < 5:
        await update.message.reply_text("Использование: /add_training город локация дата время [тип] [мест]")
        return
    try:
        city = context.args[0]
        location = context.args[1]
        date = context.args[2]
        time = context.args[3]
        if not can_manage_training(user_id, city, location):
            await update.message.reply_text("⛔ Нет прав на эту площадку.")
            return
        track_type = "other"
        max_pilots = 10
        if len(context.args) >= 6 and context.args[-1].isdigit():
            max_pilots = int(context.args[-1])
            other_args = context.args[4:-1]
        else:
            other_args = context.args[4:]
        if other_args and other_args[0] in TRACK_TYPES:
            track_type = other_args[0]
        training_id = add_training(city, location, date, time, track_type, max_pilots)
        schedule_reminders(context.application, training_id, date, time)
        log_admin_action(user_id, 'add_training', training_id, {
            'city': city, 'location': location, 'date': date, 'time': time, 'track_type': track_type, 'max_pilots': max_pilots
        })
        await update.message.reply_text(f"✅ Тренировка добавлена: {city} - {location} на {date} в {time}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# Главная функция
if __name__ == '__main__':
    init_db()
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set_nickname", set_nickname))
    application.add_handler(CommandHandler("delete_me", delete_me))
    application.add_handler(CommandHandler("add_super_admin", add_super_admin))
    application.add_handler(CommandHandler("add_admin", add_location_admin))
    application.add_handler(CommandHandler("remove_admin", remove_admin_cmd))
    application.add_handler(CommandHandler("list_admins", list_admins))
    application.add_handler(CommandHandler("add_training", add_training_cmd))
    application.add_handler(CallbackQueryHandler(consent_handler, pattern='consent_given'))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))

    logger.info("🚀 FPV Training Bot запущен!")
    application.run_polling()