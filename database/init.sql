-- database/init.sql — Инициализация БД для FPV Training Platform

-- Включаем UTF8
SET client_encoding = 'UTF8';

-- Создаём ENUM для типов трасс (опционально, но полезно)
-- В текущей реализации используем TEXT, но можно расширить
-- CREATE TYPE track_type_enum AS ENUM ('race', 'freestyle', 'low', 'tech', 'cinematic', 'training', 'other');

-- Таблица: Тренировки
CREATE TABLE IF NOT EXISTS trainings (
    id SERIAL PRIMARY KEY,
    city TEXT NOT NULL DEFAULT 'Не указан',
    location TEXT NOT NULL,
    date TEXT NOT NULL,          -- формат YYYY-MM-DD
    time TEXT NOT NULL,          -- формат HH:MM
    track_type TEXT NOT NULL DEFAULT 'other'
        CHECK (track_type IN ('race', 'freestyle', 'low', 'tech', 'cinematic', 'training', 'other')),
    max_pilots INTEGER NOT NULL DEFAULT 10
        CHECK (max_pilots > 0 AND max_pilots <= 50),
    current_pilots INTEGER NOT NULL DEFAULT 0
        CHECK (current_pilots >= 0 AND current_pilots <= max_pilots),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для ускорения поиска
CREATE INDEX IF NOT EXISTS idx_trainings_datetime ON trainings (date, time);
CREATE INDEX IF NOT EXISTS idx_trainings_city_location ON trainings (city, location);

-- Таблица: Записи пилотов
CREATE TABLE IF NOT EXISTS registrations (
    id SERIAL PRIMARY KEY,
    training_id INTEGER NOT NULL REFERENCES trainings(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,     -- Telegram user_id
    vtx_band TEXT NOT NULL
        CHECK (vtx_band IN ('R', 'F', 'E')),
    vtx_channel INTEGER NOT NULL
        CHECK (vtx_channel >= 1 AND vtx_channel <= 8),
    paid INTEGER NOT NULL DEFAULT 0
        CHECK (paid IN (0, 1)),  -- 0 = не оплачено, 1 = оплачено
    payment_id TEXT,             -- ID платежа от Telegram
    payment_date TIMESTAMPTZ,    -- дата оплаты
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (training_id, user_id)  -- один пилот — одна запись на тренировку
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_registrations_training ON registrations (training_id);
CREATE INDEX IF NOT EXISTS idx_registrations_user ON registrations (user_id);
CREATE INDEX IF NOT EXISTS idx_registrations_paid ON registrations (paid);

-- Таблица: Согласие пользователей (152-ФЗ)
CREATE TABLE IF NOT EXISTS user_consent (
    user_id BIGINT PRIMARY KEY,  -- Telegram user_id
    username TEXT,               -- Telegram username (может меняться)
    nickname TEXT,               -- Псевдоним, выбранный пользователем (для отображения)
    consent_given INTEGER NOT NULL DEFAULT 0
        CHECK (consent_given IN (0, 1)),
    consent_date TIMESTAMPTZ,    -- дата согласия
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Таблица: Администраторы
CREATE TABLE IF NOT EXISTS admins (
    user_id BIGINT PRIMARY KEY,  -- Telegram user_id
    role TEXT NOT NULL
        CHECK (role IN ('super_admin', 'location_admin')),
    managed_locations JSONB NOT NULL DEFAULT '[]',  -- [{"city": "Москва", "location": "Парк Победы"}, ...]
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Таблица: Аудит действий администраторов
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id SERIAL PRIMARY KEY,
    admin_user_id BIGINT NOT NULL,  -- кто совершил действие
    action TEXT NOT NULL,           -- 'add_training', 'delete_training', 'edit_channel', 'add_admin', ...
    target_id BIGINT,               -- ID тренировки / пилота / админа (если применимо)
    details JSONB,                  -- Доп. данные: {"city": "...", "location": "...", "old_channel": "...", ...}
    ip_address TEXT,                -- IP-адрес (для веб-действий)
    user_agent TEXT,                -- User-Agent браузера
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы для аудита
CREATE INDEX IF NOT EXISTS idx_audit_admin ON admin_audit_log (admin_user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON admin_audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON admin_audit_log (created_at DESC);

-- Таблица: 2FA сессии для админки
CREATE TABLE IF NOT EXISTS admin_2fa_sessions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,        -- Telegram user_id
    secret_code TEXT NOT NULL,      -- 6-значный код
    expires_at TIMESTAMPTZ NOT NULL, -- срок действия
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (LENGTH(secret_code) = 6)
);

-- Индекс для очистки истёкших сессий
CREATE INDEX IF NOT EXISTS idx_2fa_expires ON admin_2fa_sessions (expires_at);

-- Триггер для обновления updated_at (опционально)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Триггеры для таблиц с updated_at
DROP TRIGGER IF EXISTS update_trainings_updated_at ON trainings;
CREATE TRIGGER update_trainings_updated_at
    BEFORE UPDATE ON trainings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_user_consent_updated_at ON user_consent;
CREATE TRIGGER update_user_consent_updated_at
    BEFORE UPDATE ON user_consent
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_admins_updated_at ON admins;
CREATE TRIGGER update_admins_updated_at
    BEFORE UPDATE ON admins
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Первоначальные данные (опционально)
-- INSERT INTO admins (user_id, role) VALUES (123456789, 'super_admin'); -- Замени на свой Telegram ID

-- Комментарии для документации
COMMENT ON TABLE trainings IS 'Тренировки FPV';
COMMENT ON TABLE registrations IS 'Записи пилотов на тренировки';
COMMENT ON TABLE user_consent IS 'Согласие пользователей на обработку ПДн (152-ФЗ)';
COMMENT ON TABLE admins IS 'Администраторы системы';
COMMENT ON TABLE admin_audit_log IS 'Лог аудита действий администраторов';
COMMENT ON TABLE admin_2fa_sessions IS 'Сессии двухфакторной аутентификации';

-- Гранты (если нужно ограничить права)
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO fpv_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO fpv_user;

-- Уведомление об успешной инициализации
DO $$
BEGIN
    RAISE NOTICE '✅ База данных FPV Training Platform успешно инициализирована!';
END $$;