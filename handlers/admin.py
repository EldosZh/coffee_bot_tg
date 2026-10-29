import re
from datetime import datetime
from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from config import settings
from database.db import LOCAL
from database.models import STATUSES, TRANSITIONS
from handlers.common import show, order_text
from keyboards.inline import back
from services.pricing import money

router = Router()


class Admin(StatesGroup):
    price = State()
    promo = State()


def authorize(event):
    if event.from_user.id not in settings.admin_ids:
        raise ValueError('Доступ только администраторам.')


@router.message(Command('admin'))
@router.message(F.text == '👑 Админ-панель')
@router.callback_query(F.data == 'admin')
async def admin(event: Message | CallbackQuery, state: FSMContext):
    authorize(event)
    await state.clear()
    await show(event, '👑 <b>Админ-панель</b>', [
        [('📦 Новые заказы', 'adm:orders:new:0')], [('📋 Все заказы', 'adm:orders:all:0')],
        [('☕ Товары', 'adm:products')], [('🎟 Промокоды', 'adm:promos')],
        [('📊 Статистика', 'adm:stats')], back()])


@router.callback_query(F.data.startswith('adm:orders:'))
async def orders(callback: CallbackQuery, state: FSMContext, db):
    authorize(callback)
    await state.clear()
    _, _, kind, offset = callback.data.split(':')
    offset = int(offset)
    if kind not in ('new', 'all') or offset < 0:
        raise ValueError('Некорректная страница.')
    rows = await db.query('SELECT * FROM orders ' + ("WHERE status='new' " if kind == 'new' else '')
                          + 'ORDER BY id DESC LIMIT 11 OFFSET ?', (offset,))
    buttons = [[(f"#{r['id']} · {money(r['total'])} · {STATUSES[r['status']]}", f"adm:order:{r['id']}")]
               for r in rows[:10]]
    if offset:
        buttons += [[('← Предыдущие', f'adm:orders:{kind}:{max(0, offset-10)}')]]
    if len(rows) > 10:
        buttons += [[('Следующие →', f'adm:orders:{kind}:{offset+10}')]]
    await show(callback, '📦 Заказы' if rows else 'Заказов нет.', buttons + [back('admin')])


async def render_order(callback, db, oid):
    row = await db.order(oid)
    buttons = [[(STATUSES[status], f'adm:status:{oid}:{status}')]
               for status in TRANSITIONS[row['status']]
               if status != 'delivery' or row['method'] == 'delivery']
    await show(callback, order_text(row, admin=True), buttons + [back('adm:orders:all:0')])


@router.callback_query(F.data.startswith('adm:order:'))
async def order(callback: CallbackQuery, state: FSMContext, db):
    authorize(callback)
    await state.clear()
    await render_order(callback, db, int(callback.data.split(':')[2]))


@router.callback_query(F.data.startswith('adm:status:'))
async def status(callback: CallbackQuery, db):
    authorize(callback)
    _, _, oid, value = callback.data.split(':')
    await db.status(int(oid), value)
    await render_order(callback, db, int(oid))


@router.callback_query(F.data == 'adm:products')
async def products(callback: CallbackQuery, state: FSMContext, db):
    authorize(callback)
    await state.clear()
    rows = await db.query('SELECT * FROM products ORDER BY category_id,id')
    await show(callback, '☕ Товары · выберите для редактирования:',
               [[(f"{'✅' if p['active'] and p['in_stock'] else '⛔'} {p['name']} · {money(p['price'])}",
                  f"adm:product:{p['id']}")] for p in rows] + [back('admin')])


async def render_product(callback, db, pid):
    product = await db.product(pid)
    await show(callback, f"<b>{escape(product['name'])}</b>\n{escape(product['description'])}"
               f"\nЦена: {money(product['price'])}\nДоступен: {'да' if product['active'] and product['in_stock'] else 'нет'}",
               [[('⛔ В стоп-лист' if product['active'] and product['in_stock'] else '✅ Включить', f'adm:toggle:{pid}')],
                [('✏️ Изменить цену', f'adm:price:{pid}')], back('adm:products')])


@router.callback_query(F.data.startswith('adm:product:'))
async def product(callback: CallbackQuery, state: FSMContext, db):
    authorize(callback)
    await state.clear()
    await render_product(callback, db, int(callback.data.split(':')[2]))


@router.callback_query(F.data.startswith('adm:toggle:'))
async def toggle(callback: CallbackQuery, db):
    authorize(callback)
    pid = int(callback.data.split(':')[2])
    await db.execute('UPDATE products SET active=CASE WHEN active=1 AND in_stock=1 THEN 0 ELSE 1 END,in_stock=1 WHERE id=?', (pid,))
    await render_product(callback, db, pid)


@router.callback_query(F.data.startswith('adm:price:'))
async def price(callback: CallbackQuery, state: FSMContext):
    authorize(callback)
    pid = int(callback.data.split(':')[2])
    await state.clear()
    await state.set_state(Admin.price)
    await state.update_data(pid=pid)
    await show(callback, 'Введите новую цену в тенге, целое число от 1 до 1 000 000:', [back(f'adm:product:{pid}')])


@router.message(Admin.price)
async def new_price(message: Message, state: FSMContext, db):
    authorize(message)
    value = (message.text or '').strip().replace(' ', '')
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 1000000:
        raise ValueError('Цена должна быть целым числом от 1 до 1 000 000.')
    data = await state.get_data()
    await db.execute('UPDATE products SET price=? WHERE id=?', (int(value), data['pid']))
    await state.clear()
    await show(message, '✅ Цена обновлена. Корзины будут рассчитаны по новой цене.', [back('adm:products')])


@router.callback_query(F.data == 'adm:promos')
async def promos(callback: CallbackQuery, state: FSMContext, db):
    authorize(callback)
    await state.clear()
    rows = await db.query('''SELECT p.*,COUNT(u.id) AS used FROM promo_codes p LEFT JOIN promo_usages u
        ON p.code=u.code GROUP BY p.code ORDER BY p.code''')
    text = '🎟 <b>Промокоды</b>\n\n' + '\n'.join(
        f"{escape(p['code'])}: {p['percent']}%, {'включён' if p['active'] else 'отключён'}; "
        f"использований {p['used']}/{p['max_uses'] if p['max_uses'] is not None else '∞'}; "
        f"до {escape(p['expires'] or 'бессрочно')}; на клиента: {p['per_user']}" for p in rows)
    await show(callback, text, [[('➕ Создать / изменить', 'adm:promo:edit')], back('admin')])


@router.callback_query(F.data == 'adm:promo:edit')
async def promo_edit(callback: CallbackQuery, state: FSMContext):
    authorize(callback)
    await state.clear()
    await state.set_state(Admin.promo)
    await show(callback, 'Введите через пробел:\n<code>КОД ПРОЦЕНТ АКТИВЕН ДАТА ЛИМИТ НА_КЛИЕНТА</code>'
               '\nПример: <code>SUMMER10 10 1 2027-06-30 100 1</code>'
               '\nДата или лимит «-» — без ограничения. АКТИВЕН: 1 или 0.'
               '\nРедактирование сохраняет историю использований.', [back('adm:promos')])


@router.message(Admin.promo)
async def promo_save(message: Message, state: FSMContext, db):
    authorize(message)
    parts = (message.text or '').upper().split()
    if len(parts) != 6:
        raise ValueError('Нужно 6 значений. Пример: SUMMER10 10 1 2027-06-30 100 1')
    code, percent, active, expires, limit, personal = parts
    if not re.fullmatch('[A-Z0-9_]{1,40}', code):
        raise ValueError('Код: 1–40 латинских букв, цифр или подчёркиваний.')
    if not percent.isdigit() or not 1 <= int(percent) <= 100 or active not in ('0', '1'):
        raise ValueError('Процент: от 1 до 100; активен: 0 или 1.')
    if (limit != '-' and (not limit.isdigit() or not 1 <= int(limit) <= 1000000)
            or not personal.isdigit() or not 1 <= int(personal) <= 1000000):
        raise ValueError('Лимиты должны быть от 1 до 1 000 000.')
    expiration = None
    if expires != '-':
        try:
            expiration = datetime.strptime(expires, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, tzinfo=LOCAL).isoformat()
        except ValueError:
            raise ValueError('Дата должна быть в формате ГГГГ-ММ-ДД.') from None
    await db.execute('''INSERT INTO promo_codes(code,percent,active,expires,max_uses,per_user) VALUES(?,?,?,?,?,?)
        ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,active=excluded.active,
        expires=excluded.expires,max_uses=excluded.max_uses,per_user=excluded.per_user''',
        (code, int(percent), int(active), expiration, None if limit == '-' else int(limit), int(personal)))
    await state.clear()
    await show(message, '✅ Промокод сохранён.', [back('adm:promos')])


@router.callback_query(F.data == 'adm:stats')
async def stats(callback: CallbackQuery, state: FSMContext, db):
    authorize(callback)
    await state.clear()
    s = await db.statistics()
    await show(callback, f"📊 <b>Статистика</b> (Алматы, UTC+5)\n\nЗаказов сегодня: {s['today_count']}"
               f"\nВыручка сегодня: {money(s['today_revenue'])}\nЗаказов всего: {s['count']}"
               f"\nВыручка всего: {money(s['revenue'])}\nСредний чек: {money(round(s['average']))}"
               f"\nПользователей: {s['users']}\nПопулярный товар: {escape(s['popular'])}"
               '\n\nВыручка — сумма оформленных заказов без отменённых, включая демо. '
               'Это не отчёт о фактических банковских платежах.', [back('admin')])
