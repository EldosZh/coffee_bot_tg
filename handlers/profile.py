from html import escape

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from handlers.common import show, phone_value
from keyboards.inline import back
from keyboards.reply import main_keyboard, phone_keyboard
from services.pricing import money

router = Router()


class Profile(StatesGroup):
    phone = State()


@router.message(F.text == '👤 Профиль')
@router.callback_query(F.data == 'profile')
async def profile(event: Message | CallbackQuery, state: FSMContext, db):
    await state.clear()
    uid = event.from_user.id
    user = (await db.query('SELECT * FROM users WHERE user_id=?', (uid,)))[0]
    totals = (await db.query("SELECT COUNT(*) AS n,COALESCE(SUM(total),0) AS spent FROM orders WHERE user_id=? AND status!='cancelled'", (uid,)))[0]
    await show(event, f"👤 <b>{escape(user['name'])}</b>\nUsername: {escape(user['username'] or '—')}"
               f"\nТелефон: {escape(user['phone'] or '—')}\nЗаказов (без отменённых): {totals['n']}"
               f"\nПотрачено: {money(totals['spent'])}\nБонусный баланс: {user['bonus_balance']}"
               f"\nРегистрация: {escape(user['created'][:10])}",
               [[('📱 Изменить телефон', 'profile:phone')], [('📦 История заказов', 'orders')], back()])


@router.callback_query(F.data == 'profile:phone')
async def edit_phone(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Profile.phone)
    await callback.message.answer('Введите новый телефон или отправьте контакт:', reply_markup=phone_keyboard())


@router.message(Profile.phone, F.text == '⬅️ Назад')
async def phone_back(message: Message, state: FSMContext, db):
    await message.answer('Профиль:', reply_markup=main_keyboard())
    await profile(message, state, db)


@router.message(Profile.phone)
async def phone(message: Message, state: FSMContext, db):
    value = phone_value(message)
    await db.execute('UPDATE users SET phone=? WHERE user_id=?', (value, message.from_user.id))
    await state.clear()
    await message.answer('✅ Телефон обновлён.', reply_markup=main_keyboard())
    await profile(message, state, db)


@router.message(F.text == '🎁 Бонусы')
async def bonuses(message: Message, state: FSMContext, db):
    await state.clear()
    user = (await db.query('SELECT * FROM users WHERE user_id=?', (message.from_user.id,)))[0]
    await show(message, f"🎁 Баланс: <b>{user['bonus_balance']}</b>\nНачислено всего: {user['bonus_earned']}"
               '\n\n1 бонус = 1 ₸. Начисляем 5% от итоговой суммы заказа с округлением вниз. '
               'Бонусами можно оплатить до 30% суммы после скидки по промокоду. '
               'При отмене начисление возвращается, списанные бонусы восстанавливаются.', [back()])
