from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram_i18n import I18nContext
from ..database.db import *
from ..config import SCHEDULE_URL
import qrcode
from io import BytesIO
from PIL import Image
from datetime import datetime

router = Router()

# Константы
VTX_BANDS = ["R", "F", "E"]
ITEMS_PER_PAGE = 5


def get_main_menu_keyboard(i18n: I18nContext) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.buttons.schedule(), callback_data="view_trainings_1")],
        [InlineKeyboardButton(text=i18n.buttons.search(), callback_data="search_menu")],
        [InlineKeyboardButton(text=i18n.buttons.register(), callback_data="view_trainings_1")],
        [InlineKeyboardButton(text=i18n.buttons.cancel(), callback_data="cancel_registration")],
        [InlineKeyboardButton(text=i18n.buttons.stats(), callback_data="show_stats")],
        [InlineKeyboardButton(text=i18n.buttons.web(), callback_data="web_schedule")],
    ])


def get_consent_keyboard(i18n: I18nContext) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=i18n.consent.agree(), callback_data="consent_given")]
    ])


def get_pagination_keyboard(current_page: int, total_pages: int, prefix: str = "view_trainings") -> InlineKeyboardMarkup:
    """Генерация клавиатуры пагинации"""
    buttons = []

    # Кнопки пагинации
    if total_pages > 1:
        row = []
        if current_page > 1:
            row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_{current_page - 1}"))
        row.append(InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop"))
        if current_page < total_pages:
            row.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_{current_page + 1}"))
        buttons.append(row)

    # Кнопка назад
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("start"))
async def start(message: Message, i18n: I18nContext):
    user_id = message.from_user.id
    consent = await get_user_consent(user_id)

    if not consent or not consent.get('consent_given'):
        await ask_consent(message, i18n)
        return

    await message.answer(
        i18n.start.hello(),
        reply_markup=get_main_menu_keyboard(i18n)
    )


async def ask_consent(message: Message, i18n: I18nContext):
    text = f"{i18n.consent.title()}\n\n{i18n.consent.body()}"
    await message.answer(
        text,
        reply_markup=get_consent_keyboard(i18n),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "consent_given")
async def consent_handler(callback: CallbackQuery, i18n: I18nContext):
    user_id = callback.from_user.id
    username = callback.from_user.username or f"user_{user_id}"
    full_name = callback.from_user.full_name

    await set_user_consent(user_id, username, full_name, "ru")
    await callback.message.edit_text(i18n.consent.thanks())
    await callback.answer()
    await start(callback.message, i18n)


@router.message(Command("set_nickname"))
async def set_nickname(message: Message, i18n: I18nContext):
    user_id = message.from_user.id
    consent = await get_user_consent(user_id)

    if not consent or not consent.get('consent_given'):
        await message.answer("Сначала дайте согласие: /start")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажите никнейм: /set_nickname ВашНик")
        return

    nickname = args[1][:50]
    await set_user_nickname(user_id, nickname)
    await message.answer(f"✅ Ваш никнейм: {nickname}")


@router.message(Command("delete_me"))
async def delete_me(message: Message, i18n: I18nContext):
    user_id = message.from_user.id
    await delete_user_data(user_id)
    await message.answer("🗑️ Все ваши данные удалены. Чтобы вернуться — напишите /start.")


@router.message(Command("my_registrations"))
async def my_registrations(message: Message, i18n: I18nContext):
    user_id = message.from_user.id
    registrations = await fetch('''
        SELECT t.date, t.time, t.location, r.vtx_band, r.vtx_channel, r.paid
        FROM registrations r
        JOIN trainings t ON r.training_id = t.id
        WHERE r.user_id = $1
        ORDER BY t.date, t.time
    ''', user_id)

    if not registrations:
        await message.answer("У вас нет активных записей.")
        return

    text = "📋 *Ваши записи:*\n\n"
    for reg in registrations:
        status = "✅" if reg['paid'] else "⏳"
        text += f"{status} {reg['location']} | 📅 {reg['date']} 🕒 {reg['time']} | 📡 {reg['vtx_band']}{reg['vtx_channel']}\n"

    await message.answer(text, parse_mode="Markdown")


@router.message(Command("search"))
async def search_trainings_cmd(message: Message, i18n: I18nContext):
    """Поиск тренировок: /search [город] [дата]"""
    args = message.text.split()[1:]  # Пропускаем команду

    city = None
    date = None

    if len(args) >= 1:
        city = args[0]
    if len(args) >= 2:
        date = args[1]

    await show_trainings_paginated(message, i18n, page=1, city=city, date=date, is_search=True)


@router.callback_query(F.data == "search_menu")
async def search_menu(callback: CallbackQuery, i18n: I18nContext):
    """Меню поиска"""
    text = (
        "🔍 *Поиск тренировок*\n\n"
        "Используйте команду:\n"
        "`/search город` — поиск по городу\n"
        "`/search город дата` — поиск по городу и дате\n"
        "Пример: `/search Москва 2025-06-01`\n\n"
        "Дата в формате ГГГГ-ММ-ДД"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(i18n))


async def show_trainings_paginated(
    obj,  # Message или CallbackQuery
    i18n: I18nContext,
    page: int = 1,
    city: str = None,
    date: str = None,
    is_search: bool = False
):
    """Показать тренировки с пагинацией"""
    # Формируем запрос
    query = '''
        SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
        FROM trainings
        WHERE 1=1
    '''
    params = []
    param_index = 1

    if city:
        query += f" AND city ILIKE ${param_index}"
        params.append(f"%{city}%")
        param_index += 1

    if date:
        query += f" AND date = ${param_index}"
        params.append(date)
        param_index += 1

    query += " ORDER BY date, time"

    all_trainings = await fetch(query, *params)

    if not all_trainings:
        text = "❌ Тренировки не найдены." if is_search else "Нет запланированных тренировок."
        if isinstance(obj, CallbackQuery):
            await obj.message.edit_text(text)
        else:
            await obj.answer(text)
        return

    # Пагинация
    total_pages = (len(all_trainings) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    trainings = all_trainings[start_idx:end_idx]

    # Формируем текст
    text = "📅 *Доступные тренировки:*\n\n" if not is_search else "🔍 *Результаты поиска:*\n\n"
    keyboard = []

    for t in trainings:
        track_label = TRACK_TYPES.get(t['track_type'], "❓")
        spots = f"({t['current_pilots']}/{t['max_pilots']})"
        text += f"🏙️ {t['city']} | 📍 {t['location']} | 📅 {t['date']} | 🕒 {t['time']} | 🎯 {track_label} | {spots}\n"

        btn_text = f"Записаться: {t['date']} {t['time']}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"register_{t['id']}")])

    # Клавиатура пагинации
    prefix = "search_results" if is_search else "view_trainings"
    pagination_kb = get_pagination_keyboard(page, total_pages, prefix)
    for row in pagination_kb.inline_keyboard:
        keyboard.append(row)

    # Отправляем/редактируем
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    if isinstance(obj, CallbackQuery):
        await obj.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await obj.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("view_trainings_"))
async def view_trainings(callback: CallbackQuery, i18n: I18nContext):
    page = int(callback.data.split('_')[2])
    await show_trainings_paginated(callback, i18n, page=page)


@router.callback_query(F.data.startswith("search_results_"))
async def view_search_results(callback: CallbackQuery, i18n: I18nContext):
    page = int(callback.data.split('_')[2])
    # В реальном проекте можно сохранить параметры поиска в FSM или передавать в callback_data
    await show_trainings_paginated(callback, i18n, page=page, is_search=True)


@router.callback_query(F.data.startswith("register_"))
async def register_step1(callback: CallbackQuery, i18n: I18nContext):
    training_id = int(callback.data.split('_')[1])

    keyboard = [
        [InlineKeyboardButton(text="🎲 Автоматически", callback_data=f"reg_auto_{training_id}")],
        [InlineKeyboardButton(text="🎛️ Вручную", callback_data=f"reg_manual_{training_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="view_trainings_1")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text("Как выбрать канал?", reply_markup=reply_markup)


@router.callback_query(F.data.startswith("reg_auto_"))
async def register_auto(callback: CallbackQuery, i18n: I18nContext):
    training_id = int(callback.data.split('_')[2])
    user_id = callback.from_user.id
    username = callback.from_user.username or f"user_{user_id}"
    full_name = callback.from_user.full_name

    consent = await get_user_consent(user_id)
    if not consent or not consent.get('consent_given'):
        await callback.message.edit_text("Сначала дайте согласие: /start")
        return

    nickname = consent.get('nickname') if consent else full_name

    success, message, reg_id = await register_pilot_with_channel(
        training_id, user_id, username, nickname
    )

    await callback.message.edit_text(message)


@router.callback_query(F.data.startswith("reg_manual_"))
async def register_manual_band(callback: CallbackQuery, i18n: I18nContext):
    training_id = int(callback.data.split('_')[2])

    keyboard = [
        [InlineKeyboardButton(text=f"Band {band}", callback_data=f"choose_band_{training_id}_{band}")]
        for band in VTX_BANDS
    ]
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"register_{training_id}")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text("Выберите Band:", reply_markup=reply_markup)


@router.callback_query(F.data.startswith("choose_band_"))
async def register_manual_channel(callback: CallbackQuery, i18n: I18nContext):
    parts = callback.data.split('_')
    training_id = int(parts[2])
    band = parts[3]

    keyboard = [
        [InlineKeyboardButton(text=f"{band}{ch}", callback_data=f"set_channel_{training_id}_{band}_{ch}")]
        for ch in range(1, 9)
    ]
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"reg_manual_{training_id}")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(f"Выберите канал в Band {band}:", reply_markup=reply_markup)


@router.callback_query(F.data.startswith("set_channel_"))
async def register_set_channel(callback: CallbackQuery, i18n: I18nContext):
    parts = callback.data.split('_')
    training_id = int(parts[2])
    band = parts[3]
    channel = int(parts[4])

    user_id = callback.from_user.id
    username = callback.from_user.username or f"user_{user_id}"
    full_name = callback.from_user.full_name

    consent = await get_user_consent(user_id)
    if not consent or not consent.get('consent_given'):
        await callback.message.edit_text("Сначала дайте согласие: /start")
        return

    nickname = consent.get('nickname') if consent else full_name

    success, message, reg_id = await register_pilot_with_channel(
        training_id, user_id, username, nickname, band, channel
    )

    await callback.message.edit_text(message)


@router.callback_query(F.data == "cancel_registration")
async def cancel_registration(callback: CallbackQuery, i18n: I18nContext):
    user_id = callback.from_user.id

    registrations = await fetch('''
        SELECT t.id, t.location, t.date, t.time
        FROM registrations r
        JOIN trainings t ON r.training_id = t.id
        WHERE r.user_id = $1
        ORDER BY t.date, t.time
    ''', user_id)

    if not registrations:
        await callback.message.edit_text("Вы не записаны ни на одну тренировку.")
        return

    keyboard = []
    for reg in registrations:
        btn_text = f"Отменить: {reg['date']} {reg['time']} - {reg['location']}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"unregister_{reg['id']}")])

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text("Выберите тренировку для отмены:", reply_markup=reply_markup)


@router.callback_query(F.data.startswith("unregister_"))
async def unregister_confirm(callback: CallbackQuery, i18n: I18nContext):
    training_id = int(callback.data.split('_')[1])
    user_id = callback.from_user.id

    success, message = await unregister_pilot(training_id, user_id)
    await callback.message.edit_text(message)


@router.callback_query(F.data == "web_schedule")
async def web_schedule(callback: CallbackQuery, i18n: I18nContext):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(SCHEDULE_URL)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    bio = BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)

    await callback.message.answer_photo(
        photo=bio,
        caption=f"🌐 Онлайн-расписание:\n{SCHEDULE_URL}"
    )

    await callback.message.edit_text("QR-код отправлен выше.")


@router.message(Command("stats"))
@router.callback_query(F.data == "show_stats")
async def show_stats(obj, i18n: I18nContext):  # obj = Message или CallbackQuery
    """Показать личную статистику пользователя"""
    user_id = obj.from_user.id if isinstance(obj, Message) else obj.from_user.id

    # Статистика
    total_registrations = await fetchrow('''
        SELECT COUNT(*) as total FROM registrations WHERE user_id = $1
    ''', user_id)

    paid_registrations = await fetchrow('''
        SELECT COUNT(*) as paid FROM registrations WHERE user_id = $1 AND paid = 1
    ''', user_id)

    favorite_band = await fetchrow('''
        SELECT vtx_band, COUNT(*) as count
        FROM registrations
        WHERE user_id = $1
        GROUP BY vtx_band
        ORDER BY count DESC
        LIMIT 1
    ''', user_id)

    next_training = await fetchrow('''
        SELECT t.date, t.time, t.location
        FROM registrations r
        JOIN trainings t ON r.training_id = t.id
        WHERE r.user_id = $1 AND t.date >= $2
        ORDER BY t.date, t.time
        LIMIT 1
    ''', user_id, datetime.now().strftime("%Y-%m-%d"))

    # Формируем текст
    text = "📊 *Ваша статистика:*\n\n"
    text += f"📝 Всего записей: {total_registrations['total']}\n"
    text += f"💰 Оплачено: {paid_registrations['paid']}\n"

    if favorite_band and favorite_band['vtx_band']:
        text += f"📡 Любимый Band: {favorite_band['vtx_band']} ({favorite_band['count']} раз)\n"

    if next_training:
        text += f"🚀 Ближайшая тренировка: {next_training['location']} ({next_training['date']} в {next_training['time']})\n"
    else:
        text += "🚀 Ближайших тренировок не запланировано\n"

    # Кнопка назад
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])

    if isinstance(obj, CallbackQuery):
        await obj.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await obj.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery, i18n: I18nContext):
    await callback.message.edit_text(
        i18n.start.hello(),
        reply_markup=get_main_menu_keyboard(i18n)
    )


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    """Пустой обработчик для кнопок пагинации"""
    await callback.answer()