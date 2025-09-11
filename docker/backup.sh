#!/bin/bash
# docker/backup/backup.sh

set -e

# Конфигурация
DB_HOST=${DB_HOST:-db}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-fpv_bot}
DB_USER=${DB_USER:-fpv_user}
DB_PASSWORD=${DB_PASSWORD:-b4H78Q9z_}
BACKUP_DIR="/backups"
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
RETENTION_DAYS=${RETENTION_DAYS:-7}

# Создаём папку, если не существует
mkdir -p $BACKUP_DIR

# Имя файла
DATE=$(date +%Y%m%d_%H%M%S)
SQL_FILE="$BACKUP_DIR/${DB_NAME}_$DATE.sql"
DUMP_FILE="$BACKUP_DIR/${DB_NAME}_$DATE.dump"
ZIP_FILE="$BACKUP_DIR/${DB_NAME}_$DATE.sql.gz"

# Создаём бэкап в формате SQL
echo "🔄 Создаём SQL-бэкап..."
PGPASSWORD=$DB_PASSWORD pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f $SQL_FILE

# Создаём бинарный дамп (альтернативный формат)
echo "🔄 Создаём бинарный дамп..."
PGPASSWORD=$DB_PASSWORD pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -Fc -f $DUMP_FILE

# Сжимаем SQL-файл
gzip $SQL_FILE
echo "✅ Бэкап создан: $ZIP_FILE и $DUMP_FILE"

# Отправляем в Telegram (если настроено)
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    echo "📤 Отправляем уведомление в Telegram..."
    curl -F chat_id=$TELEGRAM_CHAT_ID \
         -F document=@"$ZIP_FILE" \
         -F caption="✅ Бэкап БД $DB_NAME создан: $(date)" \
         "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendDocument"

    curl -F chat_id=$TELEGRAM_CHAT_ID \
         -F document=@"$DUMP_FILE" \
         -F caption="📦 Бинарный дамп БД $DB_NAME" \
         "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendDocument"
fi

# Удаляем старые бэкапы
echo "🧹 Удаляем бэкапы старше $RETENTION_DAYS дней..."
find $BACKUP_DIR -name "*.gz" -mtime +$RETENTION_DAYS -delete
find $BACKUP_DIR -name "*.dump" -mtime +$RETENTION_DAYS -delete

echo "🎉 Бэкап завершён успешно!"