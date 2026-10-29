from html import escape
from uuid import uuid4

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from config import settings
from handlers.common import show, quote_text, phone_value
from keyboards.inline import back, keyboard
from keyboards.reply import phone_keyboard, main_keyboard
from services.pricing import money

router = Router()


class Checkout(StatesGroup):
    method = State()
    address = State()
    phone = State()
    comment = State()
    payment = State()
    bonus = State()
    confirm = State()


async def method_prompt(event, state):
    await state.set_state(Checkout.method)
    await show(event, 'Как хотите получить заказ?\nСамовывоз: ' + settings.address,
               [[('🏪 Самовывоз', 'receive:pickup'), ('🚗 Доставка', 'receive:delivery')], back('cart')])


async def phone_prompt(event, state):
    await state.set_state(Checkout.phone)
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('Введите телефон или отправьте свой контакт:', reply_markup=phone_keyboard())


async def comment_prompt(event, state):
    await state.set_state(Checkout.comment)
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer('Телефон сохранён для заказа.', reply_markup=main_keyboard())
    await show(event, 'Комментарий к заказу? Например: «без сахара».',
               [[('⏭ Без комментария', 'checkout:skip')], back('checkout:back')])


async def payment_prompt(event, state):
    await state.set_state(Checkout.payment)
    await show(event, 'Выберите способ оплаты:',
               [[('💵 При получении', 'pay:cash')], [('💳 Картой', 'pay:card')], back('checkout:back')])


async def bonus_prompt(event, state, db):
    await state.set_state(Checkout.bonus)
    quote = await db.quote(event.from_user.id)
    await show(event, f"Можно списать до {quote['bonus_limit']} бонусов (до 30% после промокода).",
               [[('Использовать максимум', 'usebonus:yes')], [('Без бонусов', 'usebonus:no')], back('checkout:back')])


async def confirmation(event, state, db):
    data = await state.get_data()
    quote = await db.quote(event.from_user.id, data['use_bonus'])
    if not quote['items'] or quote['warning']:
        raise ValueError(quote['warning'] or 'Корзина пуста.')
    await state.update_data(fingerprint=quote['fingerprint'])
    await state.set_state(Checkout.confirm)
    text = '🧾 <b>Подтвердите заказ</b>\n\n' + quote_text(quote)
    text += (f"\n\nПолучение: {'Самовывоз' if data['method'] == 'pickup' else 'Доставка'}"
             f"\nАдрес: {escape(data['address'])}\nТелефон: {escape(data['phone'])}"
             f"\nКомментарий: {escape(data['comment']) or '—'}"
             f"\nОплата: {'При получении' if data['payment'] == 'cash' else 'Картой (демо)'}")
    if data['payment'] == 'card':
        text += '\n⚠️ Онлайн-оплата находится в демо-режиме. Деньги не списываются; заказ тестовый.'
    if data['method'] == 'delivery':
        text += '\nСтоимость доставки в итог не включена и уточняется кофейней.'
    await show(event, text, [[('✅ Подтвердить', f"checkout:confirm:{data['key']}")], back('checkout:back'), [('❌ Отмена', 'cart')]])


@router.callback_query(F.data == 'checkout')
async def checkout(callback: CallbackQuery, state: FSMContext, db):
    quote = await db.quote(callback.from_user.id)
    if not quote['items']:
        raise ValueError('Корзина пуста.')
    if quote['warning']:
        raise ValueError(quote['warning'])
    await state.clear()
    await state.update_data(key=uuid4().hex, use_bonus=False)
    await method_prompt(callback, state)


@router.callback_query(Checkout.method, F.data.startswith('receive:'))
async def receive(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split(':')[1]
    if method not in ('pickup', 'delivery'):
        raise ValueError('Выберите самовывоз или доставку.')
    await state.update_data(method=method)
    if method == 'delivery':
        await state.set_state(Checkout.address)
        await show(callback, 'Введите адрес доставки (улица, дом, квартира):', [back('checkout:back')])
    else:
        await state.update_data(address=settings.address)
        await phone_prompt(callback, state)


@router.message(Checkout.address)
async def address(message: Message, state: FSMContext):
    value = (message.text or '').strip()
    if not 5 <= len(value) <= 500:
        raise ValueError('Введите адрес текстом: от 5 до 500 символов.')
    await state.update_data(address=value)
    await phone_prompt(message, state)


@router.message(Checkout.phone, F.text == '⬅️ Назад')
async def phone_back(message: Message, state: FSMContext):
    await message.answer('Выбор получения:', reply_markup=main_keyboard())
    await method_prompt(message, state)


@router.message(Checkout.phone)
async def phone(message: Message, state: FSMContext):
    await state.update_data(phone=phone_value(message))
    await comment_prompt(message, state)


@router.message(Checkout.comment)
async def comment(message: Message, state: FSMContext):
    value = (message.text or '').strip()
    if not 1 <= len(value) <= 1000:
        raise ValueError('Напишите комментарий до 1000 символов или нажмите «Без комментария».')
    await state.update_data(comment=value)
    await payment_prompt(message, state)


@router.callback_query(Checkout.comment, F.data == 'checkout:skip')
async def skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(comment='')
    await payment_prompt(callback, state)


@router.callback_query(Checkout.payment, F.data.startswith('pay:'))
async def payment(callback: CallbackQuery, state: FSMContext, db):
    method = callback.data.split(':')[1]
    if method not in ('cash', 'card'):
        raise ValueError('Неизвестный способ оплаты.')
    await state.update_data(payment=method)
    if method == 'card':
        await callback.message.answer('Онлайн-оплата находится в демо-режиме. Можно оформить тестовый заказ.')
    await bonus_prompt(callback, state, db)


@router.callback_query(Checkout.bonus, F.data.startswith('usebonus:'))
async def bonus(callback: CallbackQuery, state: FSMContext, db):
    value = callback.data.split(':')[1]
    if value not in ('yes', 'no'):
        raise ValueError('Выберите вариант кнопкой.')
    await state.update_data(use_bonus=value == 'yes')
    await confirmation(callback, state, db)


@router.callback_query(Checkout.confirm, F.data.startswith('checkout:confirm:'))
async def confirm(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if callback.data.split(':')[2] != data['key']:
        raise ValueError('Это подтверждение устарело. Используйте последнее сообщение заказа.')
    oid = await db.place_order(callback.from_user.id, data, settings.admin_ids)
    order = await db.order(oid, callback.from_user.id)
    await state.clear()
    await show(callback, f"✅ Заказ #{oid} принят!\nИтого: {money(order['total'])}"
               f"\nНачислено: {order['bonus_awarded']} бонусов."
               + ('\nТестовый заказ: оплата картой не выполнялась.' if order['payment'] == 'card' else ''),
               [[('📦 Открыть заказ', f'order:{oid}')], back('catalog')])


@router.callback_query(F.data == 'checkout:back')
async def go_back(callback: CallbackQuery, state: FSMContext, db):
    current = await state.get_state()
    data = await state.get_data()
    if current == Checkout.confirm.state:
        await bonus_prompt(callback, state, db)
    elif current == Checkout.bonus.state:
        await payment_prompt(callback, state)
    elif current == Checkout.payment.state:
        await comment_prompt(callback, state)
    elif current == Checkout.comment.state:
        await phone_prompt(callback, state)
    elif current == Checkout.phone.state and data.get('method') == 'delivery':
        await state.set_state(Checkout.address)
        await show(callback, 'Введите адрес доставки:', [back('checkout:back')])
    elif current in (Checkout.address.state, Checkout.phone.state):
        await method_prompt(callback, state)
    else:
        raise ValueError('Оформление устарело. Откройте корзину заново.')
