import asyncpg
import pytz
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
from ..config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

# Глобальные константы (можно вынести в config, если нужно)
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

_pool = None


async def init_db_pool():
    """Инициализация пула соединений к PostgreSQL"""
    global _pool
    _pool = await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=5,
        max_size=20,
        command_timeout=60
    )


async def close_db_pool():
    """Закрытие пула соединений"""
    global _pool
    if _pool:
        await _pool.close()


# Вспомогательные функции для выполнения запросов
async def fetch(query: str, *args) -> List[Dict[str, Any]]:
    async with _pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args) -> Optional[Dict[str, Any]]:
    async with _pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def execute(query: str, *args) -> str:
    async with _pool.acquire() as conn:
        return await conn.execute(query, *args)


# Основные функции логики бота

async def get_all_trainings() -> List[Dict[str, Any]]:
    """Получить все тренировки"""
    return await fetch('''
        SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
        FROM trainings
        ORDER BY date, time
    ''')


async def get_used_channels(training_id: int) -> List[Tuple[str, int]]:
    """Получить занятые каналы на тренировке"""
    rows = await fetch('''
        SELECT vtx_band, vtx_channel
        FROM registrations
        WHERE training_id = $1
    ''', training_id)
    return [(row['vtx_band'], row['vtx_channel']) for row in rows]


def suggest_free_channel(used: List[Tuple[str, int]]) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """Предложить свободный канал (синхронная логика)"""
    used_set = set(used)
    for band_name, channels in VTX_BANDS.items():
        for i, freq in enumerate(channels, start=1):
            if (band_name, i) not in used_set:
                return band_name, i, freq
    return None, None, None


async def register_pilot_with_channel(
    training_id: int,
    user_id: int,
    username: str,
    full_name: str,
    preferred_band: str = None,
    preferred_channel: int = None
) -> Tuple[bool, str, Optional[int]]:
    """
    Регистрация пилота с автоматическим или ручным выбором канала.
    Возвращает: (успех, сообщение, reg_id)
    """
    # Проверка: уже записан?
    existing = await fetchrow(
        'SELECT id FROM registrations WHERE training_id = $1 AND user_id = $2',
        training_id, user_id
    )
    if existing:
        return False, "Вы уже записаны на эту тренировку.", None

    # Проверка: есть ли тренировка и свободные места?
    training = await fetchrow(
        'SELECT current_pilots, max_pilots FROM trainings WHERE id = $1',
        training_id
    )
    if not training:
        return False, "Тренировка не найдена.", None
    if training['current_pilots'] >= training['max_pilots']:
        return False, "Нет свободных мест.", None

    band, channel, freq = None, None, None

    # Если указаны предпочтения
    if preferred_band and preferred_channel:
        if preferred_band in VTX_BANDS and 1 <= preferred_channel <= 8:
            used = await get_used_channels(training_id)
            if (preferred_band, preferred_channel) not in used:
                band, channel = preferred_band, preferred_channel
                freq = VTX_BANDS[preferred_band][channel - 1]
            else:
                return False, f"Канал {preferred_band}{preferred_channel} уже занят. Выберите другой.", None
        else:
            return False, "Неверный формат канала. Используйте: R3, F5, E1.", None

    # Если не указаны — подбираем автоматически
    if not band:
        used = await get_used_channels(training_id)
        band, channel, freq = suggest_free_channel(used)
        if not band:
            return False, "Нет свободных каналов для записи.", None

    # Вставляем регистрацию
    row = await fetchrow('''
        INSERT INTO registrations (training_id, user_id, vtx_band, vtx_channel)
        VALUES ($1, $2, $3, $4)
        RETURNING id
    ''', training_id, user_id, band, channel)

    # Обновляем счётчик пилотов
    await execute(
        'UPDATE trainings SET current_pilots = current_pilots + 1 WHERE id = $1',
        training_id
    )

    return True, f"Вы успешно записаны! Ваш канал: {band}{channel} ({freq} MHz)", row['id']


async def unregister_pilot(training_id: int, user_id: int) -> Tuple[bool, str]:
    """Отмена записи пилота"""
    result = await execute(
        'DELETE FROM registrations WHERE training_id = $1 AND user_id = $2',
        training_id, user_id
    )
    if "DELETE 0" in result:
        return False, "Вы не были записаны на эту тренировку."

    await execute(
        'UPDATE trainings SET current_pilots = current_pilots - 1 WHERE id = $1',
        training_id
    )
    return True, "Ваша запись отменена."


async def get_pilots_for_training(training_id: int) -> List[Dict[str, Any]]:
    """Получить список пилотов тренировки с никнеймами и каналами"""
    return await fetch('''
        SELECT 
            COALESCE(uc.nickname, 'Аноним') as display_name,
            r.vtx_band,
            r.vtx_channel,
            r.paid,
            r.user_id
        FROM registrations r
        LEFT JOIN user_consent uc ON r.user_id = uc.user_id
        WHERE r.training_id = $1
        ORDER BY r.vtx_band, r.vtx_channel
    ''', training_id)


async def delete_training(training_id: int):
    """Удалить тренировку (каскадно удалит регистрации)"""
    await execute('DELETE FROM trainings WHERE id = $1', training_id)


async def add_training(
    city: str,
    location: str,
    date: str,
    time: str,
    track_type: str = "other",
    max_pilots: int = 10
) -> int:
    """Добавить новую тренировку, возвращает её ID"""
    row = await fetchrow('''
        INSERT INTO trainings (city, location, date, time, track_type, max_pilots)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
    ''', city, location, date, time, track_type, max_pilots)
    return row['id']


# Админ-функции

async def get_admin(user_id: int) -> Optional[Dict[str, Any]]:
    """Получить данные админа по user_id"""
    return await fetchrow('SELECT user_id, role, managed_locations FROM admins WHERE user_id = $1', user_id)


async def can_manage_training(user_id: int, city: str, location: str) -> bool:
    """Проверить, может ли пользователь управлять этой площадкой"""
    admin = await get_admin(user_id)
    if not admin:
        return False
    if admin['role'] == 'super_admin':
        return True
    if admin['role'] == 'location_admin':
        for loc in admin['managed_locations']:
            if loc.get('city') == city and loc.get('location') == location:
                return True
    return False


async def add_admin(user_id: int, role: str, managed_locations: list = None):
    """Добавить или обновить админа"""
    if managed_locations is None:
        managed_locations = []
    await execute('''
        INSERT INTO admins (user_id, role, managed_locations)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO UPDATE SET
            role = EXCLUDED.role,
            managed_locations = EXCLUDED.managed_locations
    ''', user_id, role, managed_locations)


async def remove_admin(user_id: int):
    """Удалить админа"""
    await execute('DELETE FROM admins WHERE user_id = $1', user_id)


# 2FA

async def create_2fa_session(user_id: int) -> str:
    """Создать сессию 2FA, вернуть код"""
    import secrets
    code = str(secrets.randbelow(900000) + 100000)
    expires_at = datetime.now(pytz.UTC) + timedelta(minutes=10)

    await execute('DELETE FROM admin_2fa_sessions WHERE expires_at < NOW()')

    await execute('''
        INSERT INTO admin_2fa_sessions (user_id, secret_code, expires_at)
        VALUES ($1, $2, $3)
    ''', user_id, code, expires_at)

    return code


async def verify_2fa_code(code: str) -> Optional[int]:
    """Проверить код 2FA, вернуть user_id или None"""
    row = await fetchrow('''
        SELECT user_id FROM admin_2fa_sessions
        WHERE secret_code = $1 AND expires_at > NOW()
    ''', code)
    if row:
        await execute('DELETE FROM admin_2fa_sessions WHERE secret_code = $1', code)
        return row['user_id']
    return None


# Логирование действий админов

async def log_admin_action(admin_user_id: int, action: str, target_id: int = None, details: dict = None):
    """Записать действие админа в лог"""
    await execute('''
        INSERT INTO admin_audit_log (admin_user_id, action, target_id, details)
        VALUES ($1, $2, $3, $4)
    ''', admin_user_id, action, target_id, details)


# Работа с согласием пользователя

async def get_user_consent(user_id: int) -> Optional[Dict[str, Any]]:
    """Получить статус согласия пользователя"""
    return await fetchrow('SELECT consent_given, lang FROM user_consent WHERE user_id = $1', user_id)


async def set_user_consent(user_id: int, username: str, full_name: str, lang: str = 'ru'):
    """Записать/обновить согласие пользователя"""
    await execute('''
        INSERT INTO user_consent (user_id, username, nickname, consent_given, consent_date, lang)
        VALUES ($1, $2, $3, 1, NOW(), $4)
        ON CONFLICT (user_id) DO UPDATE SET
            consent_given = 1,
            consent_date = NOW(),
            lang = EXCLUDED.lang
    ''', user_id, username, full_name, lang)


async def set_user_nickname(user_id: int, nickname: str):
    """Обновить никнейм пользователя"""
    await execute('UPDATE user_consent SET nickname = $1 WHERE user_id = $2', nickname, user_id)


async def delete_user_data(user_id: int):
    """Полностью удалить данные пользователя"""
    await execute('DELETE FROM registrations WHERE user_id = $1', user_id)
    await execute('DELETE FROM user_consent WHERE user_id = $1', user_id)