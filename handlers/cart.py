from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from handlers.common import quote_text, show
from keyboards.inline import back

router = Router()


class Promo(StatesGroup):
    code = State()


async def render_cart(event, db):
    quote = await db.quote(event.from_user.id)
    rows = []
    for n, item in enumerate(quote['items'], 1):
        iid = item['id']
        rows.append([(f'➖ {n}', f'cartitem:{iid}:-1'), (f'➕ {n}', f'cartitem:{iid}:1'),
                     (f'🗑 {n}', f'cartitem:{iid}:0')])
    if quote['items']:
        rows += [[('🎟 Промокод', 'promo')], [('✅ Оформить заказ', 'checkout')],
                 [('🗑 Очистить корзину', 'cart:clear')]]
        if quote['promo']:
            rows += [[('Удалить промокод', 'promo:remove')]]
    await show(event, '🛒 <b>Ваша корзина</b>\n\n' + quote_text(quote), rows + [back('catalog')])


@router.message(F.text == '🛒 Корзина')
@router.callback_query(F.data == 'cart')
async def cart(event: Message | CallbackQuery, state: FSMContext, db):
    await state.clear()
    await render_cart(event, db)


@router.callback_query(F.data.startswith('cartitem:'))
async def change(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    _, iid, delta = callback.data.split(':')
    if delta not in ('-1', '1', '0'):
        raise ValueError('Некорректное действие.')
    await db.change_item(callback.from_user.id, int(iid), int(delta))
    await render_cart(callback, db)


@router.callback_query(F.data == 'cart:clear')
async def clear(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    await db.clear(callback.from_user.id)
    await render_cart(callback, db)


@router.callback_query(F.data == 'promo')
async def promo(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Promo.code)
    await show(callback, 'Введите промокод:', [back('cart')])


@router.callback_query(F.data == 'promo:remove')
async def remove(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    await db.execute('UPDATE carts SET promo_code=NULL WHERE user_id=?', (callback.from_user.id,))
    await render_cart(callback, db)


@router.message(Promo.code)
async def code(message: Message, state: FSMContext, db):
    value = (message.text or '').strip().upper()
    if not 1 <= len(value) <= 40:
        raise ValueError('Введите промокод текстом, до 40 символов.')
    await db.set_promo(message.from_user.id, value)
    await state.clear()
    await render_cart(message, db)
