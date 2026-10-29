import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from database.db import Database
from services.pricing import price

DRINK = dict(size='L', temperature='hot', milk='oat', syrup='caramel', extra='yes')


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'test.db')
        await self.db.init()
        for uid in (1, 2):
            await self.db.ensure_user(SimpleNamespace(id=uid, full_name='Клиент <test>', username='client'))
        self.latte = (await self.db.query("SELECT id FROM products WHERE name='Латте'"))[0]['id']
        self.food = (await self.db.query("SELECT id FROM products WHERE name='Круассан'"))[0]['id']

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def data(self, uid=1, bonus=False):
        q = await self.db.quote(uid, bonus)
        return dict(key=uuid4().hex, use_bonus=bonus, fingerprint=q['fingerprint'], method='pickup',
                    address='Демо', phone='+77001234567', comment='<без сахара>', payment='cash')

    async def test_seed_idempotent_and_pricing(self):
        await self.db.execute('UPDATE products SET price=1600,active=0 WHERE id=?', (self.latte,))
        await self.db.init()
        self.assertEqual(len(await self.db.query('SELECT * FROM categories')), 5)
        self.assertEqual(len(await self.db.query('SELECT * FROM products')), 17)
        self.assertEqual((await self.db.product(self.latte))['price'], 1600)
        self.assertEqual((await self.db.product(self.latte))['active'], 0)
        # 1200 + 400 + 300 + 150 + 300 = 2350.
        self.assertEqual(price(1200, DRINK), 2350)
        self.assertEqual(await self.db.query('PRAGMA foreign_key_check'), [])

    async def test_order_transaction_and_idempotency(self):
        await self.db.add(1, self.latte, DRINK, 2)
        await self.db.execute('UPDATE users SET bonus_balance=2000 WHERE user_id=1')
        await self.db.set_promo(1, 'WELCOME10')
        data = await self.data(bonus=True)
        oid = await self.db.place_order(1, data, [2])
        self.assertEqual(await self.db.place_order(1, data, [2]), oid)
        order = await self.db.order(oid, 1)
        self.assertEqual((order['subtotal'], order['discount'], order['bonus_used'], order['total']),
                         (4700, 470, 1269, 2961))
        self.assertEqual(order['bonus_awarded'], 148)
        self.assertEqual(len(order['items']), 1)
        self.assertEqual(order['items'][0]['qty'], 2)
        self.assertEqual((await self.db.quote(1))['items'], [])
        self.assertEqual(len(await self.db.query('SELECT * FROM promo_usages')), 1)
        self.assertEqual(len(await self.db.query('SELECT * FROM notifications')), 1)
        self.assertEqual((await self.db.query('SELECT bonus_balance FROM users WHERE user_id=1'))[0]['bonus_balance'], 879)
        with self.assertRaises(ValueError):
            await self.db.order(oid, 2)
        with self.assertRaises(ValueError):
            await self.db.set_promo(1, 'WELCOME10')
        await self.db.status(oid, 'cancelled')
        self.assertEqual((await self.db.query('SELECT bonus_balance FROM users WHERE user_id=1'))[0]['bonus_balance'], 2000)
        with self.assertRaises(ValueError):
            await self.db.status(oid, 'preparing')

    async def test_persistent_cart_and_ownership(self):
        await self.db.add(1, self.food, {}, 2)
        again = Database(self.db.path)
        item = (await again.quote(1))['items'][0]
        with self.assertRaises(ValueError):
            await again.change_item(2, item['id'], 1)
        await again.change_item(1, item['id'], -1)
        self.assertEqual((await again.quote(1))['items'][0]['qty'], 1)
        await again.change_item(1, item['id'], 0)
        self.assertEqual((await again.quote(1))['items'], [])

    async def test_changed_price_and_stoplist_block_checkout(self):
        await self.db.add(1, self.food, {}, 1)
        data = await self.data()
        await self.db.execute('UPDATE products SET price=1000 WHERE id=?', (self.food,))
        with self.assertRaises(ValueError):
            await self.db.place_order(1, data, [])
        self.assertEqual(await self.db.query('SELECT * FROM orders'), [])
        await self.db.execute('UPDATE products SET active=0 WHERE id=?', (self.food,))
        with self.assertRaises(ValueError):
            await self.db.add(1, self.food, {}, 1)
        with self.assertRaises(ValueError):
            await self.db.place_order(1, await self.data(), [])

    async def test_global_promo_limit_atomic(self):
        await self.db.execute('UPDATE promo_codes SET max_uses=1 WHERE code=?', ('COFFEE15',))
        for uid in (1, 2):
            await self.db.add(uid, self.food, {}, 1)
            await self.db.set_promo(uid, 'COFFEE15')
        first, second = await self.data(1), await self.data(2)
        result = await asyncio.gather(self.db.place_order(1, first, []),
                                      self.db.place_order(2, second, []), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, int) for value in result), 1)
        self.assertEqual(len(await self.db.query('SELECT * FROM promo_usages')), 1)
        self.assertEqual(len(await self.db.query('SELECT * FROM orders')), 1)

    async def test_expired_and_inactive_promo(self):
        await self.db.execute("UPDATE promo_codes SET expires='2000-01-01T23:59:59+05:00' WHERE code='WELCOME10'")
        with self.assertRaises(ValueError):
            await self.db.set_promo(1, 'WELCOME10')
        await self.db.execute("UPDATE promo_codes SET active=0 WHERE code='COFFEE15'")
        with self.assertRaises(ValueError):
            await self.db.set_promo(1, 'COFFEE15')

    async def test_repeat_rollback_unavailable_item(self):
        await self.db.add(1, self.latte, DRINK, 1)
        await self.db.add(1, self.food, {}, 1)
        oid = await self.db.place_order(1, await self.data(), [])
        await self.db.execute('UPDATE products SET active=0 WHERE id=?', (self.food,))
        with self.assertRaises(ValueError):
            await self.db.repeat(1, oid)
        self.assertEqual((await self.db.quote(1))['items'], [])
        await self.db.execute('UPDATE products SET active=1,price=1000 WHERE id=?', (self.food,))
        await self.db.repeat(1, oid)
        self.assertEqual((await self.db.quote(1))['subtotal'], 3350)

    async def test_invalid_options_and_quantities(self):
        for options, qty in (({}, 1), (DRINK, 100), (dict(DRINK, milk='invalid'), 1)):
            with self.assertRaises(ValueError):
                await self.db.add(1, self.latte, options, qty)
        with self.assertRaises(ValueError):
            await self.db.add(1, self.food, DRINK, 1)

    async def test_legacy_migration(self):
        path = Path(self.temp.name) / 'legacy.db'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('''
            CREATE TABLE users(user_id INTEGER PRIMARY KEY,name TEXT,phone TEXT,bonuses INTEGER DEFAULT 100);
            CREATE TABLE cart(user_id INTEGER,item TEXT,price INTEGER,qty INTEGER,UNIQUE(user_id,item));
            CREATE TABLE orders(id INTEGER PRIMARY KEY,user_id INTEGER,total INTEGER,method TEXT,address TEXT,status TEXT,created TEXT);
            INSERT INTO users VALUES(9,'Старый клиент','+77001234567',123);
            INSERT INTO cart VALUES(9,'Латте',1300,2);
            INSERT INTO cart VALUES(9,'Печенье',700,1);
            INSERT INTO orders VALUES(42,9,5000,'Самовывоз','Адрес','Выдан','29.10.2026 20:00');
            ''')
            db.commit()
        migrated = Database(path)
        await migrated.init()
        await migrated.init()
        self.assertTrue(path.with_name(path.name + '.pre-mvp.bak').exists())
        user = (await migrated.query('SELECT * FROM users'))[0]
        self.assertEqual(user['bonus_balance'], 123)
        self.assertEqual(len((await migrated.quote(9))['items']), 2)
        order = await migrated.order(42, 9)
        self.assertEqual(order['status'], 'completed')
        self.assertEqual(order['total'], 5000)
        self.assertEqual(await migrated.query('PRAGMA foreign_key_check'), [])
        with self.assertRaises(ValueError):
            await migrated.repeat(9, 42)


if __name__ == '__main__':
    unittest.main()
