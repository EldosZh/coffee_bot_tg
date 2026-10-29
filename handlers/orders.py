from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from database.models import STATUSES
from handlers.common import order_text, show
from keyboards.inline import back
from services.pricing import money

router = Router()


@router.message(F.text == '📦 Мои заказы')
@router.callback_query(F.data == 'orders')
async def orders(event: Message | CallbackQuery, state: FSMContext, db):
    await state.clear()
    rows = await db.query('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10', (event.from_user.id,))
    await show(event, '📦 Последние заказы:' if rows else '📦 Заказов пока нет.',
               [[(f"#{r['id']} · {money(r['total'])} · {STATUSES.get(r['status'], r['status'])}",
                  f"order:{r['id']}")] for r in rows] + [back()])


@router.callback_query(F.data.startswith('order:'))
async def order(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    oid = int(callback.data.split(':')[1])
    row = await db.order(oid, callback.from_user.id)
    await show(callback, order_text(row), [[('🔄 Повторить заказ', f'repeat:{oid}')], back('orders')])


@router.callback_query(F.data.startswith('repeat:'))
async def repeat(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    await db.repeat(callback.from_user.id, int(callback.data.split(':')[1]))
    await show(callback, '✅ Товары добавлены в корзину по текущим ценам.',
               [[('🛒 Открыть корзину', 'cart')], back('orders')])
