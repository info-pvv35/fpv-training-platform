import asyncio
import hashlib
import json
import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton,
    InlineKeyboardMarkup, WebAppInfo, CallbackQuery
)
from aiogram.filters import Command
#from aiogram.filters import ContentTypesFilter
from aiogram.enums import ContentType
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiogram_i18n import I18nContext
from ..database.db import *
from ..config import (
    PROVIDER_TOKEN, YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY,
    STRIPE_SECRET_KEY, WEBHOOK_URL, SCHEDULE_URL
)
import uuid
from datetime import datetime
import pytz
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
import os
from io import BytesIO
import qrcode
from PIL import Image

# Настройка логгера
logger = logging.getLogger(__name__)

# Инициализация платежных систем
yoo_client = None
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    from yookassa import Configuration, Payment as YooPayment
    Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    yoo_client = YooPayment

if STRIPE_SECRET_KEY:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

router = Router()

# Регистрация шрифта для PDF
FONT_PATH = os.path.join(os.path.dirname(__file__), "..", "DejaVuSans.ttf")
if os.path.exists(FONT_PATH):
    pdfmetrics.registerFont(TTFont('DejaVu', FONT_PATH))


def generate_qr_code(data: str) -> BytesIO:
    """Генерация QR-кода для PDF"""
    qr = qrcode.QRCode(version=1, box_size=4, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


async def generate_receipt_pdf(
    reg_id: int,
    user_name: str,
    amount: float,
    channel: str,
    date_str: str,
    location: str,
    payment_id: str,
    is_refund: bool = False
) -> BytesIO:
    """Генерация PDF-чека с QR-кодом"""
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Шрифт
    font_name = "DejaVu" if os.path.exists(FONT_PATH) else "Helvetica"
    p.setFont(font_name, 12)

    # Заголовок
    p.setFillColor(colors.blue)
    p.setFont("Helvetica-Bold", 16)
    title = "FPV TRAINING — КВИТАНЦИЯ" if not is_refund else "FPV TRAINING — ЧЕК ВОЗВРАТА"
    p.drawCentredString(width / 2, height - 50, title)
    p.setFillColor(colors.black)
    p.setFont(font_name, 12)

    # Данные
    y = height - 100
    lines = [
        f"ID регистрации: {reg_id}",
        f"Пилот: {user_name}",
        f"Сумма: {amount} руб.",
        f"Канал VTX: {channel}",
        f"Тренировка: {location}",
        f"Дата: {date_str}",
        f"ID платежа: {payment_id[:8]}...",
        f"Время выдачи: {datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    if is_refund:
        lines.append("Статус: ВОЗВРАТ СРЕДСТВ")

    lines.append("")
    lines.append("Спасибо за участие! Приятных полётов 🚁")

    for line in lines:
        p.drawString(50, y, line)
        y -= 20

        # QR-код для верификации
    qr_data = f"fpv_verify:{reg_id}:{payment_id}:{int(amount * 100)}:{int(is_refund)}"
    qr_buffer = generate_qr_code(qr_data)
    p.drawImage(Image.open(qr_buffer), width - 150, 100, 100, 100)

    p.setFont("Helvetica", 10)
    p.drawString(width - 150, 85, "Сканируйте для проверки")

    # Добавляем гиперссылку "Записаться на тренировку"
    p.setFillColor(colors.blue)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, 70, "Записаться на тренировку")
    p.linkURL(SCHEDULE_URL, (50, 70, 250, 90), relative=0)  # Координаты: x1, y1, x2, y2
    p.setFillColor(colors.black)

    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer


@router.message(Command("my_payments"))
async def my_payments(message: Message, i18n: I18nContext):
    """История платежей пользователя"""
    user_id = message.from_user.id

    payments = await fetch('''
        SELECT 
            r.id as reg_id,
            r.payment_id,
            r.paid,
            r.payment_date,
            t.location,
            t.date,
            t.time,
            r.vtx_band,
            r.vtx_channel,
            r.paid as is_paid
        FROM registrations r
        JOIN trainings t ON r.training_id = t.id
        WHERE r.user_id = $1
        ORDER BY r.payment_date DESC NULLS LAST, r.created_at DESC
    ''', user_id)

    if not payments:
        await message.answer("У вас пока нет платежей.")
        return

    text = "📋 *История платежей:*\n\n"
    for p in payments:
        status = "✅ Оплачено" if p['is_paid'] else "⏳ Ожидает оплаты"
        if p['payment_date']:
            date_str = p['payment_date'].strftime('%Y-%m-%d %H:%M')
        else:
            date_str = "Не оплачено"
        text += (
            f"🆔 {p['reg_id']} | {p['location']} ({p['date']} {p['time']})\n"
            f"📡 {p['vtx_band']}{p['vtx_channel']} | {status} | {date_str}\n"
            f"💳 {p['payment_id'][:8] if p['payment_id'] else 'N/A'}\n\n"
        )

    await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "/refund")
async def refund_menu(message: Message, i18n: I18nContext):
    """Меню возврата средств"""
    user_id = message.from_user.id

    registrations = await fetch('''
        SELECT r.id, r.payment_id, r.paid, t.location, t.date, t.time, r.vtx_band, r.vtx_channel
        FROM registrations r
        JOIN trainings t ON r.training_id = t.id
        WHERE r.user_id = $1 AND r.paid = 1
        ORDER BY t.date DESC, t.time DESC
    ''', user_id)

    if not registrations:
        await message.answer("У вас нет оплаченных регистраций для возврата.")
        return

    keyboard = []
    for reg in registrations:
        btn_text = f"Вернуть: {reg['location']} ({reg['date']} {reg['time']}) — {reg['vtx_band']}{reg['vtx_channel']}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"refund_{reg['id']}")])

    keyboard.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="cancel_refund")])
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer("Выберите регистрацию для возврата средств:", reply_markup=markup)


@router.callback_query(F.data.startswith("refund_"))
async def process_refund(callback: CallbackQuery, bot: Bot, i18n: I18nContext):
    """Обработка запроса на возврат"""
    reg_id = int(callback.data.split('_')[1])
    user_id = callback.from_user.id

    reg = await fetchrow('''
        SELECT r.id, r.payment_id, r.paid, t.location, t.date, t.time, r.vtx_band, r.vtx_channel, r.user_id, r.training_id
        FROM registrations r
        JOIN trainings t ON r.training_id = t.id
        WHERE r.id = $1
    ''', reg_id)

    if not reg or reg['user_id'] != user_id or not reg['paid']:
        await callback.answer("Неверная регистрация.", show_alert=True)
        return

    # Отменяем регистрацию
    await execute('DELETE FROM registrations WHERE id = $1', reg_id)
    await execute('UPDATE trainings SET current_pilots = current_pilots - 1 WHERE id = $1', reg['training_id'])

    # Генерируем PDF чек возврата
    pdf_buffer = await generate_receipt_pdf(
        reg_id,
        callback.from_user.full_name,
        0.0,
        f"{reg['vtx_band']}{reg['vtx_channel']}",
        f"{reg['date']} {reg['time']}",
        reg['location'],
        reg['payment_id'],
        is_refund=True
    )

    await bot.send_document(
        chat_id=user_id,
        document=("refund_receipt.pdf", pdf_buffer),
        caption="✅ Регистрация отменена и возврат инициирован.\n\nЧек возврата:"
    )

    await callback.answer("Возврат выполнен. Чек отправлен.", show_alert=True)
    await callback.message.delete()


@router.message(F.text.startswith("/pay_custom"))
async def pay_custom_amount(message: Message, i18n: I18nContext):
    """Оплата с выбором суммы"""
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Использование: /pay_custom TRAINING_ID СУММА_В_РУБЛЯХ")
        return

    try:
        training_id = int(args[1])
        amount_rub = float(args[2])
        amount_kopek = int(amount_rub * 100)

        if amount_kopek < 100:
            await message.answer("Минимальная сумма — 1 рубль.")
            return

        training = await fetchrow('SELECT location, date, time FROM trainings WHERE id = $1', training_id)
        if not training:
            await message.answer("Тренировка не найдена.")
            return

        prices = [LabeledPrice(label=f"Оплата участия: {training['location']}", amount=amount_kopek)]

        await message.bot.send_invoice(
            chat_id=message.chat.id,
            title="FPV Тренировка — Произвольная сумма",
            description=f"Оплата участия в тренировке {training['location']} ({training['date']} {training['time']})",
            payload=f"fpv_{training_id}_{message.from_user.id}",
            provider_token=PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
            start_parameter=f"fpv_{training_id}",
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False
        )

    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@router.message(F.text.startswith("/pay_yoo"))
async def pay_with_yookassa(message: Message, i18n: I18nContext):
    """Оплата через ЮKassa"""
    if not yoo_client:
        await message.answer("ЮKassa не настроен.")
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("Использование: /pay_yoo TRAINING_ID")
        return

    try:
        training_id = int(args[1])
        training = await fetchrow('SELECT location, date, time FROM trainings WHERE id = $1', training_id)
        if not training:
            await message.answer("Тренировка не найдена.")
            return

        amount = 500.0
        user_id = message.from_user.id

        # Создаем платеж
        payment = yoo_client.create({
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{(await message.bot.get_me()).username}"
            },
            "capture": True,
            "description": f"Оплата FPV тренировки #{training_id}",
            "metadata": {
                "training_id": str(training_id),
                "user_id": str(user_id),
                "source": "telegram"
            }
        })

        # Сохраняем как незавершенную регистрацию
        reg_id = await fetchrow('''
            INSERT INTO registrations (training_id, user_id, vtx_band, vtx_channel, paid, payment_id)
            VALUES ($1, $2, $3, $4, 0, $5)
            RETURNING id
        ''', training_id, user_id, "R", 1, payment.id)

        await message.answer(
            f"🔷 Оплата через ЮKassa\n\n"
            f"Тренировка: {training['location']}\n"
            f"Дата: {training['date']} {training['time']}\n"
            f"Сумма: {amount} руб.\n\n"
            f"👉 [Оплатить]({payment.confirmation.confirmation_url})",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"ЮKassa ошибка: {e}")
        await message.answer(f"Ошибка ЮKassa: {e}")


# ========================
# WEBHOOKS
# ========================

async def yookassa_webhook_handler(request):
    """Обработчик вебхука от ЮKassa"""
    try:
        # Проверка подписи (если настроена)
        body = await request.json()
        event = body.get('event')
        payment_data = body.get('object')

        if event != 'payment.succeeded':
            return web.Response(status=200)

        payment_id = payment_data['id']
        metadata = payment_data.get('metadata', {})
        training_id = int(metadata.get('training_id', 0))
        user_id = int(metadata.get('user_id', 0))

        if not training_id or not user_id:
            return web.Response(status=400)

        # Получаем регистрацию
        reg = await fetchrow('SELECT id, vtx_band, vtx_channel FROM registrations WHERE payment_id = $1', payment_id)
        if not reg:
            # Создаем новую регистрацию
            success, message, reg_id = await register_pilot_with_channel(
                training_id, user_id, f"user_{user_id}", f"User {user_id}"
            )
            if not success:
                logger.error(f"Не удалось зарегистрировать после оплаты: {message}")
                return web.Response(status=500)

            # Обновляем payment_id
            await execute('UPDATE registrations SET payment_id = $1, paid = 1, payment_date = NOW() WHERE id = $2', payment_id, reg_id)
            reg = await fetchrow('SELECT id, vtx_band, vtx_channel FROM registrations WHERE id = $1', reg_id)

        # Отправляем уведомление пользователю
        bot = Bot(token=PROVIDER_TOKEN.split(":")[0])  # HACK: берем токен из PROVIDER_TOKEN
        try:
            training = await fetchrow('SELECT location, date, time FROM trainings WHERE id = $1', training_id)
            if training:
                channel_str = f"{reg['vtx_band']}{reg['vtx_channel']}"
                pdf_buffer = await generate_receipt_pdf(
                    reg['id'],
                    f"User {user_id}",
                    float(payment_data['amount']['value']),
                    channel_str,
                    f"{training['date']} {training['time']}",
                    training['location'],
                    payment_id
                )

                await bot.send_document(
                    chat_id=user_id,
                    document=("receipt.pdf", pdf_buffer),
                    caption=f"✅ Оплата прошла!\nВаш канал: {channel_str}\n\nЧек прикреплен."
                )
        except Exception as e:
            logger.error(f"Не удалось отправить чек: {e}")

        return web.Response(status=200)

    except Exception as e:
        logger.error(f"Ошибка вебхука ЮKassa: {e}")
        return web.Response(status=500)


async def stripe_webhook_handler(request):
    """Обработчик вебхука от Stripe (опционально)"""
    if not STRIPE_SECRET_KEY:
        return web.Response(status=400)

    payload = await request.text()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, os.getenv("STRIPE_WEBHOOK_SECRET")
        )
    except ValueError:
        return web.Response(status=400)
    except stripe.error.SignatureVerificationError:
        return web.Response(status=400)

    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        metadata = payment_intent.get('metadata', {})
        training_id = int(metadata.get('training_id', 0))
        user_id = int(metadata.get('user_id', 0))

        if training_id and user_id:
            # Аналогично ЮKassa — регистрируем пилота
            success, message, reg_id = await register_pilot_with_channel(
                training_id, user_id, f"user_{user_id}", f"User {user_id}"
            )
            if success:
                await execute('UPDATE registrations SET paid = 1, payment_id = $1 WHERE id = $2', payment_intent['id'], reg_id)

    return web.Response(status=200)


# ========================
# SETUP WEBHOOKS
# ========================

def setup_payment_webhooks(app, bot: Bot):
    """Настройка вебхуков для aiohttp приложения"""
    if YOOKASSA_SHOP_ID:
        app.router.add_post('/webhook/yookassa', yookassa_webhook_handler)

    if STRIPE_SECRET_KEY:
        app.router.add_post('/webhook/stripe', stripe_webhook_handler)


@router.pre_checkout_query()
async def precheckout_handler(query: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(query.id, ok=True)


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: Message, bot: Bot, i18n: I18nContext):
    payment = message.successful_payment
    payload = payment.invoice_payload
    user_id = message.from_user.id

    try:
        parts = payload.split('_')
        if len(parts) < 3 or parts[0] != "fpv":
            raise ValueError("Invalid payload")

        training_id = int(parts[1])
        payer_id = int(parts[2])
        if payer_id != user_id:
            raise ValueError("User mismatch")

    except Exception as e:
        await message.answer("❌ Ошибка при обработке платежа.")
        return

    consent = await get_user_consent(user_id)
    if not consent or not consent.get('consent_given'):
        await message.answer("Сначала дайте согласие: /start")
        return

    nickname = consent.get('nickname') if consent else None
    if not nickname:
        nickname = message.from_user.full_name

    success, reg_message, reg_id = await register_pilot_with_channel(
        training_id,
        user_id,
        message.from_user.username or f"user_{user_id}",
        nickname
    )

    if not success:
        await message.answer(f"❌ Ошибка записи: {reg_message}")
        return

    await execute('''
        UPDATE registrations
        SET paid = 1, payment_id = $1, payment_date = NOW()
        WHERE id = $2
    ''', payment.telegram_payment_charge_id, reg_id)

    # Генерация PDF-чека
    training = await fetchrow('SELECT location, date, time FROM trainings WHERE id = $1', training_id)
    if training:
        channel_str = reg_message.split("Ваш канал: ")[-1].split(" ")[0] if "Ваш канал: " in reg_message else "N/A"
        pdf_buffer = await generate_receipt_pdf(
            reg_id,
            nickname,
            payment.total_amount / 100,
            channel_str,
            f"{training['date']} {training['time']}",
            training['location'],
            payment.telegram_payment_charge_id
        )

        await bot.send_document(
            chat_id=user_id,
            document=("receipt.pdf", pdf_buffer),
            caption="✅ Оплата прошла! Ваш чек:"
        )

    await message.answer(f"✅ {reg_message}\n\nСпасибо за участие! 🚁", parse_mode="Markdown")