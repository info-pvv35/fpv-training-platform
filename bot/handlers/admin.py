from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram_i18n import I18nContext
from ..database.db import *
from ..config import ADMIN_ID

router = Router()


async def notify_new_admin(bot, user_id: int, role: str, locations=None):
    """Отправить уведомление новому админу"""
    try:
        if role == 'super_admin':
            message = (
                "🎉 *Поздравляем!*\n\n"
                "Вам назначены права администратора FPV-платформы.\n\n"
                "👑 *Роль:* Суперадминистратор\n"
                "У вас есть полный доступ ко всем площадкам и функциям."
            )
        else:
            message = (
                "🎉 *Поздравляем!*\n\n"
                "Вам назначены права администратора FPV-платформы.\n\n"
                "📍 *Роль:* Администратор площадок\n"
            )
            if locations:
                loc_str = "\n".join([f" - {loc['city']} → {loc['location']}" for loc in locations])
                message += f"Ваши площадки:\n{loc_str}"
            else:
                message += "Ваши площадки будут указаны отдельно."

        message += "\n\nИспользуйте /admin для входа в админ-панель."

        await bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
    except Exception as e:
        print(f"Не удалось отправить уведомление админу {user_id}: {e}")


@router.message(Command("add_super_admin"))
async def add_super_admin(message: Message, i18n: I18nContext):
    """Назначить суперадмина (только для суперадмина)"""
    user_id = message.from_user.id
    admin = await get_admin(user_id)

    if not admin or admin['role'] != 'super_admin':
        await message.answer("⛔ Только суперадмин может назначать админов.")
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /add_super_admin USER_ID")
        return

    try:
        new_admin_id = int(args[1])
        await add_admin(new_admin_id, 'super_admin')
        await notify_new_admin(message.bot, new_admin_id, 'super_admin')
        await log_admin_action(user_id, 'add_super_admin', new_admin_id)
        await message.answer(f"✅ Пользователь {new_admin_id} назначен суперадмином.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("add_admin"))
async def add_location_admin(message: Message, i18n: I18nContext):
    """Назначить админа площадок (только для суперадмина)"""
    user_id = message.from_user.id
    admin = await get_admin(user_id)

    if not admin or admin['role'] != 'super_admin':
        await message.answer("⛔ Только суперадмин может назначать админов площадок.")
        return

    args = message.text.split()
    if len(args) < 4:
        await message.answer("Использование: /add_admin USER_ID город локация [город2 локация2 ...]")
        return

    try:
        new_admin_id = int(args[1])
        locations = []
        loc_args = args[2:]

        if len(loc_args) % 2 != 0:
            await message.answer("❌ Укажите пары: город локация город локация ...")
            return

        for i in range(0, len(loc_args), 2):
            city = loc_args[i]
            location = loc_args[i + 1]
            locations.append({"city": city, "location": location})

        await add_admin(new_admin_id, 'location_admin', locations)
        await notify_new_admin(message.bot, new_admin_id, 'location_admin', locations)
        await log_admin_action(user_id, 'add_location_admin', new_admin_id, {"locations": locations})

        loc_str = "; ".join([f"{loc['city']} - {loc['location']}" for loc in locations])
        await message.answer(f"✅ Админ площадок {new_admin_id} назначен.\nПлощадки: {loc_str}")

    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("remove_admin"))
async def remove_admin_cmd(message: Message, i18n: I18nContext):
    """Удалить админа (только для суперадмина)"""
    user_id = message.from_user.id
    admin = await get_admin(user_id)

    if not admin or admin['role'] != 'super_admin':
        await message.answer("⛔ Только суперадмин может удалять админов.")
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /remove_admin USER_ID")
        return

    try:
        admin_id = int(args[1])
        await remove_admin(admin_id)
        await log_admin_action(user_id, 'remove_admin', admin_id)
        await message.answer(f"✅ Админ {admin_id} удалён.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("list_admins"))
async def list_admins(message: Message, i18n: I18nContext):
    """Показать список админов"""
    user_id = message.from_user.id
    admin = await get_admin(user_id)

    if not admin:
        await message.answer("⛔ Вы не админ.")
        return

    rows = await fetch('''
        SELECT user_id, role, managed_locations FROM admins ORDER BY role, user_id
    ''')

    if not rows:
        await message.answer("📋 Нет администраторов.")
        return

    text = "📋 *Администраторы:*\n\n"
    for row in rows:
        text += f"🆔 {row['user_id']} | {row['role']}\n"
        if row['role'] == 'location_admin' and row['managed_locations']:
            for loc in row['managed_locations']:
                text += f"   → {loc.get('city', '?')} - {loc.get('location', '?')}\n"
        text += "\n"

    await message.answer(text, parse_mode="Markdown")


@router.message(Command("add_training"))
async def add_training_cmd(message: Message, i18n: I18nContext):
    """Добавить тренировку (для админов)"""
    user_id = message.from_user.id
    args = message.text.split()

    if len(args) < 5:
        await message.answer(
            "Использование: /add_training город локация дата время [тип] [мест]\n"
            "Пример: /add_training Москва Парк 2025-06-01 18:00 race 12"
        )
        return

    try:
        city = args[1]
        location = args[2]
        date = args[3]
        time = args[4]

        # Проверка прав
        if not await can_manage_training(user_id, city, location):
            await message.answer("⛔ У вас нет прав на управление этой площадкой.")
            return

        # Парсинг необязательных аргументов
        track_type = "other"
        max_pilots = 10

        extra_args = args[5:]
        if extra_args and extra_args[-1].isdigit():
            max_pilots = int(extra_args[-1])
            extra_args = extra_args[:-1]

        if extra_args and extra_args[0] in TRACK_TYPES:
            track_type = extra_args[0]

        # Добавление в БД
        training_id = await add_training(city, location, date, time, track_type, max_pilots)

        # Логирование
        await log_admin_action(user_id, 'add_training', training_id, {
            'city': city,
            'location': location,
            'date': date,
            'time': time,
            'track_type': track_type,
            'max_pilots': max_pilots
        })

        await message.answer(
            f"✅ Тренировка добавлена!\n"
            f"🏙️ {city} | 📍 {location}\n"
            f"📅 {date} 🕒 {time}\n"
            f"🎯 {TRACK_TYPES.get(track_type, 'Другое')} | 👥 {max_pilots} мест"
        )

        # Планирование напоминаний (если вы реализуете scheduler в utils)
        # from ..utils.scheduler import schedule_reminders
        # schedule_reminders(message.bot, message.bot.scheduler, training_id, date, time)

    except Exception as e:
        await message.answer(f"❌ Ошибка при добавлении тренировки: {e}")


@router.message(Command("admin"))
async def admin_panel_cmd(message: Message, i18n: I18nContext):
    """Показать админ-панель (в будущем — кнопки)"""
    user_id = message.from_user.id
    admin = await get_admin(user_id)

    if not admin:
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "🔐 *Админ-панель*\n\n"
        "Доступные команды:\n"
        "/add_training — добавить тренировку\n"
        "/add_admin — назначить админа площадок\n"
        "/add_super_admin — назначить суперадмина\n"
        "/remove_admin — удалить админа\n"
        "/list_admins — список админов",
        parse_mode="Markdown"
    )


# TRACK_TYPES — копия из db.py для удобства (можно вынести в отдельный модуль)
TRACK_TYPES = {
    "race": "🏁 Гоночная",
    "freestyle": "🪂 Фристайл",
    "low": "⬇️ Low-Level",
    "tech": "⚙️ Техническая",
    "cinematic": "🎥 Кинематографичная",
    "training": "🎓 Тренировочная",
    "other": "❓ Другое"
}