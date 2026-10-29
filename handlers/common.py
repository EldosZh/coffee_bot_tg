import logging
import re
from html import escape

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from keyboards.inline import keyboard, back
from keyboards.reply import NAVIGATION, main_keyboard
from services.pricing import describe, money
from database.models import STATUSES

log = logging.getLogger(__name__)


async def show(event, text, rows=None):
    markup = keyboard(rows) if rows else None
    if len(text) > 3900:
        chunks, current = [], ''
        for line in text.splitlines(keepends=True):
            if len(current) + len(line) > 3500:
                chunks.append(current)
                current = ''
            current += line
        if current:
            chunks.append(current)
        # Every generated HTML tag is closed on its own line.
        await show(event, chunks[0])
        target = event.message if isinstance(event, CallbackQuery) else event
        for index, chunk in enumerate(chunks[1:], 1):
            await target.answer(chunk, reply_markup=markup if index == len(chunks)-1 else None)
        return
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc):
                raise
    else:
        await event.answer(text, reply_markup=markup)


def phone_value(message):
    if message.contact:
        if message.contact.user_id != message.from_user.id:
            raise ValueError('Отправьте свой контакт или введите номер вручную.')
        value = message.contact.phone_number
    else:
        value = message.text or ''
    if not re.fullmatch(r'[+\d\s()\-]+', value):
        raise ValueError('Введите телефон: например +7 700 123 45 67.')
    digits = re.sub(r'\D', '', value)
    if not 10 <= len(digits) <= 15:
        raise ValueError('Телефон должен содержать от 10 до 15 цифр.')
    return '+' + digits


def item_text(row):
    options = describe(row['options'])
    return (f"<b>{escape(row['name'])}</b> × {row['qty']}\n"
            + (options + '\n' if options else '')
            + f"{money(row['unit_price'])} / шт. · {money(row['unit_price'] * row['qty'])}")


def quote_text(quote):
    text = '\n\n'.join(item_text(row) for row in quote['items']) or 'Корзина пуста.'
    text += (f"\n\nТовары: {money(quote['subtotal'])}\nСкидка: {money(quote['discount'])}"
             f"\nБонусы: −{money(quote['bonus_used'])}\n<b>Итого: {money(quote['total'])}</b>")
    if quote['promo']:
        text += '\nПромокод: ' + escape(quote['promo'])
    if quote['warning']:
        text += '\n⚠️ ' + escape(quote['warning']) + '\nУдалите промокод или введите другой.'
    return text


def order_text(order, admin=False):
    text = f"<b>Заказ #{order['id']}</b> · {escape(order['created'][:16].replace('T', ' '))}\n"
    text += STATUSES.get(order['status'], escape(order['status'])) + '\n\n'
    text += '\n\n'.join(item_text(r) for r in order['items']) or 'Состав старого заказа не сохранялся.'
    text += (f"\n\nТовары: {money(order['subtotal'])}\nСкидка: {money(order['discount'])}"
             f"\nСписано бонусов: {order['bonus_used']}\nИтого: <b>{money(order['total'])}</b>"
             f"\nНачислено бонусов: {order['bonus_awarded']}"
             f"\nПолучение: {'Самовывоз' if order['method'] == 'pickup' else 'Доставка'}"
             f"\nАдрес: {escape(order['address'])}\nТелефон: {escape(order['phone'])}"
             f"\nОплата: {'При получении' if order['payment'] == 'cash' else 'Карта — демо, деньги не списаны'}"
             f"\nКомментарий: {escape(order['comment']) or '—'}")
    if admin:
        text += f"\nКлиент: {escape(order['name'])} · ID {order['user_id']}"
        if order['username']:
            text += ' @' + escape(order['username'])
    return text


class GuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Answer before processing so every callback, including stale ones, stops spinning.
        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
            except TelegramBadRequest:
                pass
        try:
            if isinstance(event, CallbackQuery) and await data['state'].get_state() in ('Checkout:phone', 'Profile:phone'):
                await event.message.answer('Навигация:', reply_markup=main_keyboard())
            if isinstance(event, Message) and (
                event.text in NAVIGATION + ['👑 Админ-панель', '⭐ Отзыв', '❌ Отмена']
                or (event.text or '').split('@')[0].split(' ')[0] in ('/start', '/cancel', '/admin')
            ):
                if await data['state'].get_state() in ('Checkout:phone', 'Profile:phone'):
                    await event.answer('Главное меню:', reply_markup=main_keyboard())
                await data['state'].clear()
                data['raw_state'] = None
            if event.from_user:
                await data['db'].ensure_user(event.from_user)
            return await handler(event, data)
        except ValueError as exc:
            target = event.message if isinstance(event, CallbackQuery) else event
            text = str(exc)
            if text.startswith(('invalid literal', 'not enough values', 'too many values')):
                text = 'Кнопка недействительна. Откройте раздел заново через /start.'
            await target.answer(escape(text), reply_markup=keyboard([back('cart')]))
        except Exception as exc:
            # Do not log request URLs, bot objects or exception messages containing secrets.
            log.error('Handler failed: %s', type(exc).__name__)
            target = event.message if isinstance(event, CallbackQuery) else event
            if isinstance(target, Message):
                try:
                    await target.answer('Не удалось выполнить действие. Попробуйте снова или /start.')
                except Exception:
                    log.error('Could not deliver error message')
