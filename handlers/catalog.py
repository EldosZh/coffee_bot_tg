import json

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from handlers.common import show
from keyboards.inline import back
from services.pricing import OPTIONS, LABELS, normalize, price, money, describe
from html import escape

router = Router()
STEPS = list(OPTIONS)


class Custom(StatesGroup):
    choosing = State()


@router.message(F.text == '☕ Меню')
@router.callback_query(F.data == 'catalog')
async def catalog(event: Message | CallbackQuery, state: FSMContext, db):
    await state.clear()
    categories = await db.query('SELECT * FROM categories ORDER BY id')
    await show(event, '☕ <b>Наше меню</b>\nВыберите категорию:',
               [[(c['name'], f"cat:{c['id']}")] for c in categories] + [back()])


@router.callback_query(F.data.startswith('cat:'))
async def category(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    cid = int(callback.data.split(':')[1])
    products = await db.query('SELECT * FROM products WHERE category_id=? AND active=1 AND in_stock=1', (cid,))
    await show(callback, 'Выберите товар:' if products else 'В этой категории пока нет доступных товаров.',
               [[(f"{p['name']} · от {money(p['price'])}", f"product:{p['id']}")] for p in products]
               + [back('catalog')])


async def render(callback, state, db):
    data = await state.get_data()
    product = await db.product(data['pid'])
    step = data['step']
    text = f"<b>{escape(product['name'])}</b>\n{escape(product['description'])}\nОт {money(product['price'])}"
    if product['drink'] and step < len(STEPS):
        key = STEPS[step]
        text += '\n\n' + LABELS[key] + ':'
        rows = [[(label, f'choose:{step}:{value}')] for value, (label, _) in OPTIONS[key].items()]
    else:
        raw = normalize(data['options'], bool(product['drink']))
        text += '\n\n' + describe(raw)
        text += '\nЦена за шт.: ' + money(price(product['price'], data['options']))
        text += '\nСумма: ' + money(price(product['price'], data['options']) * data['qty'])
        rows = [[('➖', 'custom:minus'), (str(data['qty']), 'noop'), ('➕', 'custom:plus')],
                [('🛒 Добавить в корзину', 'custom:add')]]
    rows += [back('custom:back')]
    await show(callback, text, rows)


@router.callback_query(F.data.startswith('product:'))
async def product(callback: CallbackQuery, state: FSMContext, db):
    p = await db.product(int(callback.data.split(':')[1]))
    if not p['active'] or not p['in_stock']:
        raise ValueError('Товар сейчас в стоп-листе.')
    await state.clear()
    await state.set_state(Custom.choosing)
    await state.update_data(pid=p['id'], category=p['category_id'], step=0, options={}, qty=1)
    await render(callback, state, db)


@router.callback_query(Custom.choosing, F.data.startswith('choose:'))
async def choose(callback: CallbackQuery, state: FSMContext, db):
    _, number, value = callback.data.split(':')
    data = await state.get_data()
    step = data['step']
    if int(number) != step or not 0 <= step < len(STEPS) or value not in OPTIONS[STEPS[step]]:
        raise ValueError('Эта кнопка устарела. Откройте товар снова.')
    data['options'][STEPS[step]] = value
    await state.update_data(options=data['options'], step=step + 1)
    await render(callback, state, db)


@router.callback_query(Custom.choosing, F.data.startswith('custom:'))
async def custom(callback: CallbackQuery, state: FSMContext, db):
    action = callback.data.split(':')[1]
    data = await state.get_data()
    p = await db.product(data['pid'])
    if action == 'back':
        if data['step'] == 0 or not p['drink']:
            await category(callback.model_copy(update={'data': f"cat:{data['category']}"}), state, db)
            return
        step = data['step'] - 1
        options = {k: v for k, v in data['options'].items() if k in STEPS[:step]}
        await state.update_data(step=step, options=options)
    elif action in ('minus', 'plus'):
        await state.update_data(qty=max(1, min(99, data['qty'] + (1 if action == 'plus' else -1))))
    elif action == 'add':
        await db.add(callback.from_user.id, data['pid'], data['options'], data['qty'])
        await state.clear()
        await show(callback, '✅ Добавлено в корзину.', [[('🛒 Корзина', 'cart')], back('catalog')])
        return
    else:
        raise ValueError('Неизвестное действие.')
    await render(callback, state, db)
