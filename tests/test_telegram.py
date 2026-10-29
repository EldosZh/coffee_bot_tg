import tempfile
import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.methods import SendMessage, EditMessageText, AnswerCallbackQuery
from aiogram.types import Update, Message, User, Chat, CallbackQuery

from bot import create_dispatcher
from database.db import Database
from handlers import admin
from handlers.checkout import Checkout
from handlers.profile import Profile


class TelegramScenario(unittest.IsolatedAsyncioTestCase):
    async def test_customer_and_admin_flows_offline(self):
        asyncio.get_running_loop().slow_callback_duration = 2
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / 'telegram.db')
            await db.init()
            dp = create_dispatcher(db)
            bot = Bot('123456789:' + 'a' * 35)
            sent = []
            counter = 0
            user = User(id=101, is_bot=False, first_name='<Анна>', username='anna')

            async def fake_request(bot, method, timeout=None):
                sent.append(method)
                if isinstance(method, (SendMessage, EditMessageText)):
                    return Message(message_id=1, date=datetime.now(timezone.utc),
                                   chat=Chat(id=int(method.chat_id), type='private'), text=method.text)
                return True

            bot.session.make_request = AsyncMock(side_effect=fake_request)

            async def feed(text=None, callback=None, actor=user):
                nonlocal counter
                counter += 1
                message = Message(message_id=counter, date=datetime.now(timezone.utc),
                                  chat=Chat(id=actor.id, type='private'), from_user=actor, text=text or 'Экран')
                update = Update(update_id=counter, **(
                    {'callback_query': CallbackQuery(id=str(counter), from_user=actor, chat_instance='test',
                                                     data=callback, message=message)}
                    if callback else {'message': message}))
                await dp.feed_update(bot, update)

            try:
                await feed('/start')
                self.assertIn('Coffee Time Almaty', sent[-1].text)
                await feed('☕ Меню')
                await feed(callback='cat:1')
                latte = (await db.query("SELECT id FROM products WHERE name='Латте'"))[0]['id']
                await feed(callback=f'product:{latte}')
                await feed(callback='custom:back')
                self.assertIn('Выберите товар', sent[-1].text)
                await feed(callback=f'product:{latte}')
                for step, value in enumerate(('L', 'hot', 'oat', 'caramel', 'yes')):
                    await feed(callback=f'choose:{step}:{value}')
                self.assertIn('2 350 ₸', sent[-1].text)
                await feed(callback='custom:add')
                self.assertEqual((await db.quote(user.id))['subtotal'], 2350)
                await feed('🛒 Корзина')
                await feed(callback='promo')
                await feed('WELCOME10')
                self.assertEqual((await db.quote(user.id))['discount'], 235)
                await feed(callback='checkout')
                await feed(callback='receive:delivery')
                await feed('Абая, 10, квартира 5')
                await feed('не телефон')
                context = dp.fsm.get_context(bot=bot, chat_id=user.id, user_id=user.id)
                self.assertEqual(await context.get_state(), Checkout.phone.state)
                await feed('+7 700 123 45 67')
                await feed(callback='checkout:skip')
                await feed(callback='pay:card')
                await feed(callback='usebonus:no')
                self.assertIn('Онлайн-оплата находится в демо-режиме', sent[-1].text)
                confirm_button = sent[-1].reply_markup.inline_keyboard[0][0].callback_data
                await feed(callback=confirm_button)
                order = (await db.query('SELECT * FROM orders'))[0]
                self.assertEqual(order['total'], 2115)
                self.assertEqual(order['payment'], 'card')
                await feed(callback=confirm_button)  # stale double click
                self.assertEqual(len(await db.query('SELECT * FROM orders')), 1)
                await feed('📦 Мои заказы')
                await feed(callback=f"order:{order['id']}")
                self.assertIn('Карта — демо', sent[-1].text)
                await feed('👤 Профиль')
                self.assertIn('&lt;Анна&gt;', sent[-1].text)
                await feed(callback='profile:phone')
                self.assertEqual(await context.get_state(), Profile.phone.state)
                await feed(callback='cart')
                self.assertIsNone(await context.get_state())
                await feed(callback='profile:phone')
                # Main navigation must interrupt all FSM handlers.
                await feed('⭐ Отзывы')
                await feed(callback='rating:5')
                await feed('Отличный кофе <3')
                self.assertEqual((await db.query('SELECT * FROM reviews'))[0]['rating'], 5)
                await feed(callback='adm:products')
                self.assertIn('Доступ только', sent[-1].text)
                administrator = User(id=202, is_bot=False, first_name='Admin')
                with patch.object(admin, 'settings', replace(admin.settings, admin_ids=frozenset({202}))):
                    await feed('/admin', actor=administrator)
                    await feed(callback='adm:products', actor=administrator)
                    await feed(callback=f'adm:toggle:{latte}', actor=administrator)
                    self.assertEqual((await db.product(latte))['active'], 0)
                    await feed(callback=f"adm:status:{order['id']}:preparing", actor=administrator)
                    self.assertEqual((await db.order(order['id']))['status'], 'preparing')
                    await feed(callback='adm:stats', actor=administrator)
                    self.assertIn('2 115 ₸', sent[-1].text)
                await feed(callback='cat:invalid')
                await feed(callback='old:button')
                answers = [m for m in sent if isinstance(m, AnswerCallbackQuery)]
                self.assertGreater(len(answers), 20)
                self.assertFalse(any('Не удалось выполнить' in (getattr(m, 'text', '') or '') for m in sent))
            finally:
                await dp.storage.close()
                await bot.session.close()
