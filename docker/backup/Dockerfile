FROM postgres:15-alpine

# Установка curl для отправки в Telegram
RUN apk add --no-cache curl

# Копируем скрипт и crontab
COPY backup.sh /usr/local/bin/backup.sh
COPY crontab /etc/crontabs/root

# Даём права
RUN chmod +x /usr/local/bin/backup.sh

# Запускаем cron в foreground
CMD ["crond", "-f", "-d", "8"]