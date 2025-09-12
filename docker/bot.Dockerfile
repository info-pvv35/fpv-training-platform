# 🐳 Dockerfile для FPV Training Bot
FROM python:3.11-slim

# 💡 Устанавливаем системные зависимости для:
# - reportlab (PDF с кириллицей)
# - Pillow (QR-коды)
# - Локали (UTF-8)
RUN apt-get update && apt-get install -y \
    gcc \
    libc6-dev \
    libfreetype6-dev \
    libjpeg-dev \
    libpng-dev \
    locales \
    && rm -rf /var/lib/apt/lists/*

# 💡 Настраиваем UTF-8 по умолчанию
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

WORKDIR /app

# 💡 Копируем requirements первым — для кэширования слоёв
COPY bot/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 💡 Копируем всё остальное
COPY . .

# 💡 Создаём папку для временных файлов (голосовые сообщения)
RUN mkdir -p /app/temp

# 💡 Устанавливаем шрифт DejaVu для PDF с кириллицей
COPY DejaVuSans.ttf .
#RUN wget -O /app/DejaVuSans.ttf https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf

# 💡 Открываем порты:
# - 8080 — для вебхуков Telegram и платежей
# - 8000 — для FastAPI веб-админки
EXPOSE 8080 8000

# 💡 Запускаем бота (по умолчанию)
CMD ["python", "-m", "bot.bot"]