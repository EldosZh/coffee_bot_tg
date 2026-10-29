import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from database.models import SCHEMA, STATUSES, TRANSITIONS
from database.seed import seed
from services.bonuses import available, earned
from services.pricing import normalize, price

LOCAL = timezone(timedelta(hours=5))


def now():
    return datetime.now(LOCAL).isoformat(timespec='seconds')


class Database:
    def __init__(self, path):
        self.path = Path(path)

    @asynccontextmanager
    async def connect(self, write=False):
        async with aiosqlite.connect(self.path, timeout=20) as db:
            db.row_factory = aiosqlite.Row
            await db.execute('PRAGMA foreign_keys=ON')
            if write:
                await db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                if write:
                    await db.commit()
            except BaseException:
                if write:
                    await db.rollback()
                raise

    @staticmethod
    async def one(db, sql, args=()):
        async with db.execute(sql, args) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    async def all(db, sql, args=()):
        async with db.execute(sql, args) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            columns = [r[1] for r in await (await db.execute('PRAGMA table_info(users)')).fetchall()]
            if columns and 'bonus_balance' not in columns:
                backup = self.path.with_name(self.path.name + '.pre-mvp.bak')
                if not backup.exists():
                    async with aiosqlite.connect(backup) as target:
                        await db.backup(target)
                # All DDL and data transfer belong to one transaction. A failed
                # migration rolls back to the original, usable schema.
                await db.execute('BEGIN IMMEDIATE')
                try:
                    for table in ('users', 'orders', 'cart'):
                        exists = await (await db.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))).fetchone()
                        if exists:
                            await db.execute(f'ALTER TABLE {table} RENAME TO legacy_{table}')
                    for statement in SCHEMA.split(';'):
                        if statement.strip():
                            await db.execute(statement)
                    await seed(db)
                    await self._migrate(db)
                    await db.execute('PRAGMA user_version=2')
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
            else:
                await db.executescript(SCHEMA)
                await seed(db)
                await db.execute('PRAGMA user_version=2')
                await db.commit()
            await db.execute('PRAGMA foreign_keys=ON')
            if await (await db.execute('PRAGMA foreign_key_check')).fetchone():
                raise RuntimeError('Нарушена целостность базы данных.')

    async def _migrate(self, db):
        db.row_factory = aiosqlite.Row
        await db.execute('''INSERT INTO users(user_id,name,phone,bonus_balance,bonus_earned,created)
            SELECT user_id,COALESCE(name,''),phone,MAX(COALESCE(bonuses,0),0),
            MAX(COALESCE(bonuses,0),0),? FROM legacy_users''', (now(),))
        tables = {r['name'] for r in await self.all(db, "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'legacy_orders' in tables:
            status_map = {'Новый': 'new', 'Готовится': 'preparing', 'Готов': 'ready',
                          'Выдан': 'completed', 'Завершён': 'completed', 'Отменён': 'cancelled'}
            for row in await self.all(db, 'SELECT * FROM legacy_orders'):
                await self._ensure_legacy_user(db, row['user_id'])
                try:
                    created = datetime.strptime(row['created'], '%d.%m.%Y %H:%M').replace(tzinfo=LOCAL).isoformat()
                except (TypeError, ValueError):
                    created = now()
                await db.execute('''INSERT INTO orders(id,user_id,subtotal,total,method,address,phone,payment,status,created,legacy)
                    VALUES(?,?,?,?,?,?,'','cash',?,?,1)''',
                    (row['id'], row['user_id'], row['total'], row['total'],
                     'pickup' if row['method'] == 'Самовывоз' else 'delivery', row['address'] or '',
                     status_map.get(row['status'], 'new'), created))
        if 'legacy_cart' in tables:
            for row in await self.all(db, 'SELECT * FROM legacy_cart'):
                await self._ensure_legacy_user(db, row['user_id'])
                product = await self.one(db, 'SELECT * FROM products WHERE name=?', (row['item'],))
                if not product:
                    cur = await db.execute('''INSERT INTO products(name,description,category_id,price)
                        VALUES(?,'Перенесено из старого меню',4,?)''', (row['item'], max(1, row['price'])))
                    product = {'id': cur.lastrowid, 'category_id': 4}
                options = {} if product['category_id'] in (4, 5) else dict(
                    size='S', temperature='hot', milk='regular', syrup='none', extra='no')
                raw = json.dumps(options, sort_keys=True)
                await db.execute('INSERT OR IGNORE INTO carts(user_id) VALUES(?)', (row['user_id'],))
                await db.execute('''INSERT OR IGNORE INTO cart_items(user_id,product_id,options,qty,unit_price)
                    VALUES(?,?,?,?,?)''', (row['user_id'], product['id'], raw,
                                          min(99, max(1, row['qty'])), max(1, row['price'])))

    async def _ensure_legacy_user(self, db, uid):
        await db.execute("INSERT OR IGNORE INTO users(user_id,name,created) VALUES(?,'Клиент',?)", (uid, now()))

    async def ensure_user(self, user):
        async with self.connect(True) as db:
            await db.execute('''INSERT INTO users(user_id,name,username,created) VALUES(?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,username=excluded.username''',
                (user.id, user.full_name, user.username, now()))
            await db.execute('INSERT OR IGNORE INTO carts(user_id) VALUES(?)', (user.id,))

    async def query(self, sql, args=()):
        async with self.connect() as db:
            return await self.all(db, sql, args)

    async def execute(self, sql, args=()):
        async with self.connect(True) as db:
            await db.execute(sql, args)

    async def product(self, pid, db=None):
        sql = '''SELECT p.*,c.drink FROM products p JOIN categories c ON c.id=p.category_id WHERE p.id=?'''
        if db:
            return await self.one(db, sql, (pid,))
        rows = await self.query(sql, (pid,))
        if not rows:
            raise ValueError('Товар не найден.')
        return rows[0]

    async def _add(self, db, uid, pid, options, qty):
        product = await self.product(pid, db)
        if not product or not product['active'] or not product['in_stock']:
            raise ValueError('Товар сейчас в стоп-листе.')
        if not 1 <= qty <= 99:
            raise ValueError('Допустимо от 1 до 99 штук.')
        raw = normalize(options, bool(product['drink']))
        existing = await self.one(db, '''SELECT qty FROM cart_items
            WHERE user_id=? AND product_id=? AND options=?''', (uid, pid, raw))
        if existing and existing['qty'] + qty > 99:
            raise ValueError('В корзине может быть не более 99 штук одной позиции.')
        count = await self.one(db, 'SELECT COUNT(*) AS n FROM cart_items WHERE user_id=?', (uid,))
        if not existing and count['n'] >= 20:
            raise ValueError('В корзине максимум 20 разных позиций. Оформите текущий заказ или удалите позицию.')
        await db.execute('INSERT OR IGNORE INTO carts(user_id) VALUES(?)', (uid,))
        await db.execute('''INSERT INTO cart_items(user_id,product_id,options,qty,unit_price) VALUES(?,?,?,?,?)
            ON CONFLICT(user_id,product_id,options) DO UPDATE SET qty=qty+excluded.qty,unit_price=excluded.unit_price''',
            (uid, pid, raw, qty, price(product['price'], options)))

    async def add(self, uid, pid, options, qty):
        async with self.connect(True) as db:
            await self._add(db, uid, pid, options, qty)

    async def change_item(self, uid, iid, delta):
        async with self.connect(True) as db:
            row = await self.one(db, 'SELECT qty FROM cart_items WHERE id=? AND user_id=?', (iid, uid))
            if not row:
                raise ValueError('Позиция уже удалена.')
            qty = row['qty'] + delta if delta else 0
            if qty > 99:
                raise ValueError('Максимум 99 штук.')
            if qty <= 0:
                await db.execute('DELETE FROM cart_items WHERE id=? AND user_id=?', (iid, uid))
            else:
                await db.execute('UPDATE cart_items SET qty=? WHERE id=? AND user_id=?', (qty, iid, uid))

    async def clear(self, uid):
        async with self.connect(True) as db:
            await db.execute('DELETE FROM cart_items WHERE user_id=?', (uid,))
            await db.execute('UPDATE carts SET promo_code=NULL WHERE user_id=?', (uid,))

    async def _promo(self, db, uid, code):
        promo = await self.one(db, 'SELECT * FROM promo_codes WHERE code=?', (code,))
        if not promo or not promo['active']:
            raise ValueError('Промокод не найден или отключён.')
        if promo['expires'] and promo['expires'] < now():
            raise ValueError('Срок действия промокода истёк.')
        counts = await self.one(db, '''SELECT COUNT(*) AS total,
            COALESCE(SUM(user_id=?),0) AS personal FROM promo_usages WHERE code=?''', (uid, code))
        if promo['max_uses'] is not None and counts['total'] >= promo['max_uses']:
            raise ValueError('Лимит промокода исчерпан.')
        if counts['personal'] >= promo['per_user']:
            raise ValueError('Вы уже использовали этот промокод.')
        return promo

    async def set_promo(self, uid, code):
        async with self.connect(True) as db:
            await self._promo(db, uid, code)
            await db.execute('UPDATE carts SET promo_code=? WHERE user_id=?', (code, uid))

    async def _quote(self, db, uid, use_bonus=False):
        rows = await self.all(db, '''SELECT ci.*,p.name,p.price,p.active,p.in_stock FROM cart_items ci
            JOIN products p ON p.id=ci.product_id WHERE ci.user_id=? ORDER BY ci.id''', (uid,))
        subtotal = sum(price(r['price'], json.loads(r['options'])) * r['qty'] for r in rows)
        for row in rows:
            row['unit_price'] = price(row['price'], json.loads(row['options']))
        cart = await self.one(db, 'SELECT promo_code FROM carts WHERE user_id=?', (uid,))
        code = cart['promo_code'] if cart else None
        discount, warning = 0, ''
        if code:
            try:
                promo = await self._promo(db, uid, code)
                discount = subtotal * promo['percent'] // 100
            except ValueError as exc:
                warning = str(exc)
        user = await self.one(db, 'SELECT * FROM users WHERE user_id=?', (uid,))
        limit = available(user['bonus_balance'], subtotal - discount) if user else 0
        used = limit if use_bonus else 0
        quote = dict(items=rows, subtotal=subtotal, discount=discount, bonus_used=used,
                     bonus_limit=limit, total=subtotal - discount - used, promo=code, warning=warning)
        # A confirmation may only purchase the exact cart and prices shown.
        quote['fingerprint'] = hashlib.sha256(json.dumps(quote, sort_keys=True).encode()).hexdigest()
        return quote

    async def quote(self, uid, use_bonus=False):
        async with self.connect() as db:
            return await self._quote(db, uid, use_bonus)

    async def place_order(self, uid, data, admin_ids):
        async with self.connect(True) as db:
            previous = await self.one(db, 'SELECT id FROM orders WHERE checkout_key=? AND user_id=?', (data['key'], uid))
            if previous:
                return previous['id']
            quote = await self._quote(db, uid, data['use_bonus'])
            if not quote['items']:
                raise ValueError('Корзина пуста.')
            if quote['fingerprint'] != data['fingerprint']:
                raise ValueError('Корзина, цена или бонусы изменились. Откройте корзину и подтвердите заказ заново.')
            if quote['warning']:
                raise ValueError(quote['warning'])
            if any(not r['active'] or not r['in_stock'] for r in quote['items']):
                raise ValueError('Один из товаров в стоп-листе. Удалите его из корзины.')
            award = earned(quote['total'])
            cur = await db.execute('''INSERT INTO orders(user_id,subtotal,discount,bonus_used,bonus_awarded,total,
                method,address,phone,comment,payment,created,checkout_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (uid, quote['subtotal'], quote['discount'], quote['bonus_used'], award, quote['total'],
                 data['method'], data['address'], data['phone'], data['comment'], data['payment'], now(), data['key']))
            oid = cur.lastrowid
            await db.executemany('''INSERT INTO order_items(order_id,product_id,name,options,qty,unit_price)
                VALUES(?,?,?,?,?,?)''', [(oid, r['product_id'], r['name'], r['options'], r['qty'], r['unit_price'])
                                        for r in quote['items']])
            if quote['promo']:
                await db.execute('INSERT INTO promo_usages(code,user_id,order_id) VALUES(?,?,?)', (quote['promo'], uid, oid))
            await db.execute('''UPDATE users SET bonus_balance=bonus_balance-?+?,bonus_earned=bonus_earned+?,phone=?
                WHERE user_id=?''', (quote['bonus_used'], award, award, data['phone'], uid))
            await db.execute('DELETE FROM cart_items WHERE user_id=?', (uid,))
            await db.execute('UPDATE carts SET promo_code=NULL WHERE user_id=?', (uid,))
            for aid in admin_ids:
                await db.execute('INSERT INTO notifications(user_id,text) VALUES(?,?)',
                                 (aid, f'🆕 Новый заказ #{oid}. Откройте /admin → Новые заказы.'))
            return oid

    async def order(self, oid, uid=None):
        args = (oid,) if uid is None else (oid, uid)
        rows = await self.query('SELECT o.*,u.name,u.username FROM orders o JOIN users u USING(user_id) '
                                'WHERE o.id=?' + ('' if uid is None else ' AND o.user_id=?'), args)
        if not rows:
            raise ValueError('Заказ не найден.')
        row = rows[0]
        row['items'] = await self.query('SELECT * FROM order_items WHERE order_id=?', (oid,))
        return row

    async def repeat(self, uid, oid):
        async with self.connect(True) as db:
            order = await self.one(db, 'SELECT id FROM orders WHERE id=? AND user_id=?', (oid, uid))
            if not order:
                raise ValueError('Заказ не найден.')
            rows = await self.all(db, 'SELECT * FROM order_items WHERE order_id=?', (oid,))
            if not rows:
                raise ValueError('В старом заказе состав не сохранялся. Выберите товары в меню.')
            for row in rows:
                await self._add(db, uid, row['product_id'], json.loads(row['options']), row['qty'])

    async def status(self, oid, target):
        async with self.connect(True) as db:
            row = await self.one(db, 'SELECT * FROM orders WHERE id=?', (oid,))
            if not row or target not in TRANSITIONS.get(row['status'], ()):
                raise ValueError('Этот переход статуса недоступен. Откройте заказ заново.')
            if target == 'delivery' and row['method'] != 'delivery':
                raise ValueError('Самовывоз не может быть в пути.')
            if target == 'cancelled' and not row['legacy']:
                user = await self.one(db, 'SELECT bonus_balance FROM users WHERE user_id=?', (row['user_id'],))
                if user['bonus_balance'] < row['bonus_awarded']:
                    raise ValueError('Бонусы заказа уже потрачены. Требуется ручная сверка перед отменой.')
                await db.execute('''UPDATE users SET bonus_balance=bonus_balance-?+?,bonus_earned=bonus_earned-?
                    WHERE user_id=?''', (row['bonus_awarded'], row['bonus_used'], row['bonus_awarded'], row['user_id']))
            await db.execute('UPDATE orders SET status=? WHERE id=?', (target, oid))
            text = f'Заказ #{oid}: {STATUSES[target]}'
            if target == 'ready' and row['method'] == 'pickup':
                text += '\nМожете забрать его в кофейне.'
            await db.execute('INSERT INTO notifications(user_id,text) VALUES(?,?)', (row['user_id'], text))

    async def statistics(self):
        async with self.connect() as db:
            result = await self.one(db, '''SELECT COUNT(*) AS count,
                COALESCE(SUM(CASE WHEN status!='cancelled' THEN total ELSE 0 END),0) AS revenue,
                COALESCE(AVG(CASE WHEN status!='cancelled' THEN total END),0) AS average,
                COALESCE(SUM(substr(created,1,10)=?),0) AS today_count,
                COALESCE(SUM(CASE WHEN substr(created,1,10)=? AND status!='cancelled' THEN total ELSE 0 END),0)
                AS today_revenue FROM orders''', (now()[:10], now()[:10]))
            result['users'] = (await self.one(db, 'SELECT COUNT(*) AS n FROM users'))['n']
            popular = await self.one(db, '''SELECT oi.name,SUM(oi.qty) AS n FROM order_items oi
                JOIN orders o ON o.id=oi.order_id WHERE o.status!='cancelled'
                GROUP BY oi.product_id ORDER BY n DESC LIMIT 1''')
            result['popular'] = popular['name'] if popular else '—'
            return result

