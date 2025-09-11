# web/web.py — Полная версия веб-приложения FPV Training Platform
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv
import time
import json
from datetime import datetime, timedelta
import pytz
import secrets
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO

# Загрузка переменных окружения
load_dotenv()

# Инициализация Flask
app = Flask(__name__)
app.secret_key = os.getenv("API_KEY", secrets.token_hex(16))

# Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'

# Константы
TRACK_TYPES = {
    "race": "🏁 Гоночная",
    "freestyle": "🪂 Фристайл",
    "low": "⬇️ Low-Level",
    "tech": "⚙️ Техническая",
    "cinematic": "🎥 Кинематографичная",
    "training": "🎓 Тренировочная",
    "other": "❓ Другое"
}

TIMEZONE = pytz.timezone('Europe/Moscow')

# Модель пользователя для Flask-Login
class AdminUser(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return AdminUser(user_id)

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

# ========================
# Публичные маршруты
# ========================

@app.route('/')
def index():
    return redirect(url_for('schedule'))

@app.route('/schedule')
def schedule():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
        FROM trainings
        WHERE TO_DATE(date || ' ' || time, 'YYYY-MM-DD HH24:MI') > NOW()
        ORDER BY date, time
    ''')
    trainings = cursor.fetchall()
    conn.close()

    # Группировка по городам
    from collections import defaultdict
    city_groups = defaultdict(list)
    for t in trainings:
        try:
            dt = datetime.strptime(f"{t['date']} {t['time']}", "%Y-%m-%d %H:%M")
            dt = TIMEZONE.localize(dt)
            is_past = dt < datetime.now(TIMEZONE)
        except:
            is_past = False
        t['is_past'] = is_past
        city_groups[t['city']].append(t)

    schedule_data = sorted(city_groups.items(), key=lambda x: x[0])

    return render_template('schedule.html',
                       schedule=schedule_data,
                       TRACK_TYPES=TRACK_TYPES,
                       year=datetime.now().year)

# Маршрут для HTMX-обновления
@app.route('/schedule-partial')
def schedule_partial():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
        FROM trainings
        WHERE TO_DATE(date || ' ' || time, 'YYYY-MM-DD HH24:MI') > NOW()
        ORDER BY date, time
    ''')
    trainings = cursor.fetchall()
    conn.close()

    from collections import defaultdict
    city_groups = defaultdict(list)
    for t in trainings:
        try:
            dt = datetime.strptime(f"{t['date']} {t['time']}", "%Y-%m-%d %H:%M")
            dt = TIMEZONE.localize(dt)
            is_past = dt < datetime.now(TIMEZONE)
        except:
            is_past = False
        t['is_past'] = is_past
        city_groups[t['city']].append(t)

    schedule_data = sorted(city_groups.items(), key=lambda x: x[0])

    return render_template('partials/schedule_table.html', schedule=schedule_data, TRACK_TYPES=TRACK_TYPES)

# SSE для автообновления
@app.route('/updates')
def sse_updates():
    def event_stream():
        while True:
            time.sleep(30)
            yield "event: refresh\ndata: {}\n\n".format(json.dumps({"time": time.time()}))
    return Response(event_stream(), mimetype="text/event-stream")

# Страница политики конфиденциальности
@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

# ========================
# Админ-панель
# ========================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form['password']
        code = request.form.get('2fa_code', '').strip()
        
        # Проверка основного пароля (можно заменить на более сложную логику)
        if password != "admin123":  # ← ЗАМЕНИ НА СВОЙ ИЛИ ВЫНЕСИ В .env
            flash('Неверный пароль', 'error')
            return render_template('admin/login.html')

        # Проверка 2FA кода
        if not code:
            flash('Введите 6-значный код из Telegram (команда /get_2fa_code)', 'warning')
            return render_template('admin/login.html')

        # Верификация кода
        from database import verify_2fa_code  # Предполагается, что verify_2fa_code в database.py
        user_id = verify_2fa_code(code)
        if not user_id:
            flash('Неверный или устаревший 2FA код', 'error')
            return render_template('admin/login.html')

        # Проверка, является ли пользователь админом
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM admins WHERE user_id = %s', (user_id,))
        admin = cursor.fetchone()
        conn.close()

        if not admin:
            flash('Пользователь не является администратором', 'error')
            return render_template('admin/login.html')

        login_user(AdminUser(str(user_id)))
        flash('Добро пожаловать в админ-панель!', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('admin/login.html')

@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin_dashboard():
    user_id = int(current_user.id)
    conn = get_db_connection()
    cursor = conn.cursor()

    # Получаем роль админа
    cursor.execute('SELECT role, managed_locations FROM admins WHERE user_id = %s', (user_id,))
    admin = cursor.fetchone()
    if not admin:
        flash('Доступ запрещён', 'error')
        return redirect(url_for('index'))

    # Получаем тренировки в зависимости от прав
    if admin['role'] == 'super_admin':
        cursor.execute('''
            SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
            FROM trainings
            ORDER BY date, time
        ''')
    else:
        conditions = []
        params = []
        for loc in admin['managed_locations']:
            conditions.append("(city = %s AND location = %s)")
            params.extend([loc['city'], loc['location']])
        if not conditions:
            trainings = []
        else:
            where_clause = " OR ".join(conditions)
            cursor.execute(f'''
                SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
                FROM trainings
                WHERE {where_clause}
                ORDER BY date, time
            ''', params)
    trainings = cursor.fetchall()
    conn.close()

    return render_template('admin/dashboard.html', trainings=trainings, TRACK_TYPES=TRACK_TYPES, admin_role=admin['role'])

# Добавление тренировки
@app.route('/admin/add', methods=['POST'])
@login_required
def add_training():
    user_id = int(current_user.id)
    city = request.form['city']
    location = request.form['location']
    date = request.form['date']
    time = request.form['time']
    track_type = request.form.get('track_type', 'other')
    max_pilots = int(request.form['max_pilots'])

    # Проверка прав
    if not can_manage_training(user_id, city, location):
        flash('⛔ У вас нет прав на эту площадку.', 'error')
        return redirect(url_for('admin_dashboard'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trainings (city, location, date, time, track_type, max_pilots, current_pilots)
        VALUES (%s, %s, %s, %s, %s, %s, 0)
        RETURNING id
    ''', (city, location, date, time, track_type, max_pilots))
    training_id = cursor.fetchone()['id']
    conn.commit()
    conn.close()

    # Логирование
    log_admin_action(user_id, 'add_training', training_id, {
        'city': city, 'location': location, 'date': date, 'time': time, 'track_type': track_type, 'max_pilots': max_pilots
    })

    flash('Тренировка добавлена', 'success')
    return redirect(url_for('admin_dashboard'))

# Удаление тренировки
@app.route('/admin/delete/<int:training_id>', methods=['POST'])
@login_required
def delete_training(training_id):
    user_id = int(current_user.id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT city, location FROM trainings WHERE id = %s', (training_id,))
    training = cursor.fetchone()
    if not training:
        flash('Тренировка не найдена.', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    # Проверка прав
    if not can_manage_training(user_id, training['city'], training['location']):
        flash('⛔ У вас нет прав на удаление этой тренировки.', 'error')
        conn.close()
        return redirect(url_for('admin_dashboard'))

    # Удаляем записи пилотов и саму тренировку
    cursor.execute('DELETE FROM registrations WHERE training_id = %s', (training_id,))
    cursor.execute('DELETE FROM trainings WHERE id = %s', (training_id,))
    conn.commit()
    conn.close()

    # Логирование
    log_admin_action(user_id, 'delete_training', training_id, {
        'city': training['city'], 'location': training['location']
    })

    flash('Тренировка удалена', 'success')
    return redirect(url_for('admin_dashboard'))

# Редактирование канала пилота
@app.route('/admin/edit-channel', methods=['POST'])
@login_required
def edit_pilot_channel():
    user_id = int(current_user.id)
    reg_id = request.form['reg_id']
    band = request.form['band']
    channel = int(request.form['channel'])

    conn = get_db_connection()
    cursor = conn.cursor()

    # Получаем training_id и user_id для проверки прав
    cursor.execute('SELECT training_id, user_id FROM registrations WHERE id = %s', (reg_id,))
    reg = cursor.fetchone()
    if not reg:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Запись не найдена'})

    # Получаем город и локацию тренировки
    cursor.execute('SELECT city, location FROM trainings WHERE id = %s', (reg['training_id'],))
    training = cursor.fetchone()
    if not training:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Тренировка не найдена'})

    # Проверка прав
    if not can_manage_training(user_id, training['city'], training['location']):
        conn.close()
        return jsonify({'status': 'error', 'message': 'Нет прав на эту площадку'})

    # Обновляем канал
    cursor.execute('''
        UPDATE registrations
        SET vtx_band = %s, vtx_channel = %s
        WHERE id = %s
    ''', (band, channel, reg_id))
    conn.commit()
    conn.close()

    # Логирование
    log_admin_action(user_id, 'edit_pilot_channel', reg_id, {
        'training_id': reg['training_id'], 'user_id': reg['user_id'], 'new_band': band, 'new_channel': channel
    })

    return jsonify({'status': 'success'})

# Получение списка пилотов для тренировки
@app.route('/admin/pilots/<int:training_id>')
@login_required
def get_pilots_for_admin(training_id):
    user_id = int(current_user.id)

    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверка прав
    cursor.execute('SELECT city, location FROM trainings WHERE id = %s', (training_id,))
    training = cursor.fetchone()
    if not training:
        conn.close()
        return "Тренировка не найдена", 404

    if not can_manage_training(user_id, training['city'], training['location']):
        conn.close()
        return "Доступ запрещён", 403

    # Получаем пилотов
    cursor.execute('''
        SELECT 
            r.id,
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

    cursor.execute('SELECT date, time, location FROM trainings WHERE id = %s', (training_id,))
    training_info = cursor.fetchone()
    conn.close()

    return render_template('admin/pilots_modal.html', training=training_info, pilots=pilots)

# Аудит действий
@app.route('/admin/audit')
@login_required
def admin_audit():
    user_id = int(current_user.id)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT role FROM admins WHERE user_id = %s', (user_id,))
    admin = cursor.fetchone()
    if not admin:
        conn.close()
        return "Доступ запрещён", 403

    if admin['role'] == 'super_admin':
        cursor.execute('''
            SELECT a.*, u.nickname as admin_name
            FROM admin_audit_log a
            LEFT JOIN user_consent u ON a.admin_user_id = u.user_id
            ORDER BY a.created_at DESC
            LIMIT 100
        ''')
    else:
        cursor.execute('''
            SELECT a.*, u.nickname as admin_name
            FROM admin_audit_log a
            LEFT JOIN user_consent u ON a.admin_user_id = u.user_id
            WHERE a.admin_user_id = %s
            ORDER BY a.created_at DESC
            LIMIT 100
        ''', (user_id,))

    logs = cursor.fetchall()
    conn.close()

    return render_template('admin/audit.html', logs=logs)

# Профиль администратора
@app.route('/admin/profile')
@login_required
def admin_profile():
    user_id = int(current_user.id)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT role, managed_locations FROM admins WHERE user_id = %s', (user_id,))
    admin = cursor.fetchone()
    if not admin:
        conn.close()
        return "Доступ запрещён", 403

    cursor.execute('SELECT username, nickname, consent_date FROM user_consent WHERE user_id = %s', (user_id,))
    user_data = cursor.fetchone()
    conn.close()

    return render_template('admin/profile.html', admin=admin, user_data=user_data)

# ========================
# Вспомогательные функции
# ========================

def can_manage_training(user_id, city, location):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT role, managed_locations FROM admins WHERE user_id = %s', (user_id,))
    admin = cursor.fetchone()
    conn.close()
    if not admin:
        return False
    if admin['role'] == 'super_admin':
        return True
    if admin['role'] == 'location_admin':
        for loc in admin['managed_locations']:
            if loc.get('city') == city and loc.get('location') == location:
                return True
    return False

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

# ========================
# API для интеграций
# ========================

@app.route('/api/trainings')
def api_trainings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, city, location, date, time, track_type, current_pilots, max_pilots
        FROM trainings
        WHERE TO_DATE(date || ' ' || time, 'YYYY-MM-DD HH24:MI') > NOW()
        ORDER BY date, time
    ''')
    trainings = cursor.fetchall()
    conn.close()

    result = []
    for t in trainings:
        result.append({
            "id": t["id"],
            "city": t["city"],
            "location": t["location"],
            "datetime": f"{t['date']}T{t['time']}:00",
            "track_type": t["track_type"],
            "spots": {
                "current": t["current_pilots"],
                "max": t["max_pilots"]
            }
        })

    return jsonify({
        "status": "success",
        "data": result,
        "count": len(result)
    })

@app.route('/api/alert', methods=['POST'])
def handle_alert():
    data = request.get_json()
    alerts = data.get('alerts', [])
    TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    for alert in alerts:
        status = alert['status']
        name = alert['labels'].get('alertname', 'Unknown')
        instance = alert['labels'].get('instance', 'Unknown')
        description = alert['annotations'].get('description', '')

        message = f"🚨 *{status.upper()}*: {name}\n"
        message += f"📍 Instance: {instance}\n"
        message += f"📝 {description}"

        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "Markdown"
                }
            )
        except Exception as e:
            print(f"Failed to send alert: {e}")

    return jsonify({"status": "ok"})

# ========================
# Экспорт в PDF (опционально)
# ========================

@app.route('/admin/export/pdf/<int:training_id>')
@login_required
def export_training_pdf(training_id):
    user_id = int(current_user.id)

    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверка прав
    cursor.execute('SELECT city, location, date, time FROM trainings WHERE id = %s', (training_id,))
    training = cursor.fetchone()
    if not training:
        conn.close()
        return "Тренировка не найдена", 404

    if not can_manage_training(user_id, training['city'], training['location']):
        conn.close()
        return "Доступ запрещён", 403

    # Получаем пилотов
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
    conn.close()

    # Создаем PDF
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Заголовок
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, f"FPV Тренировка: {training['location']}")
    p.setFont("Helvetica", 12)
    p.drawString(50, height - 70, f"Город: {training['city']}")
    p.drawString(50, height - 90, f"Дата: {training['date']} Время: {training['time']}")

    # Таблица пилотов
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, height - 130, "Список пилотов:")
    p.setFont("Helvetica", 10)

    y = height - 150
    for i, pilot in enumerate(pilots):
        if y < 50:
            p.showPage()
            y = height - 50
        p.drawString(50, y, f"{i+1}. {pilot['display_name']} - Канал: {pilot['vtx_band']}{pilot['vtx_channel']} {'✅' if pilot['paid'] else ''}")
        y -= 20

    p.showPage()
    p.save()

    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"training_{training_id}.pdf", mimetype='application/pdf')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)