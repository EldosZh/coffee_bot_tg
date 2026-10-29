"""Run with python bot.py; --check validates storage without contacting Telegram."""
import argparse
import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand, CallbackQuery, Message

from config import settings
from database.db import Database
from handlers import start, catalog, cart, checkout, orders, profile, reviews, admin
from handlers.common import GuardMiddleware
from services.notifications import notification_worker


def create_dispatcher(db):
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation(), db=db)
    dp.message.outer_middleware(GuardMiddleware())
    dp.callback_query.outer_middleware(GuardMiddleware())
    # Navigation handlers take priority over FSM text collection.
    navigation = Router()
    for module in (start, catalog, cart, orders, profile, reviews, admin):
        # GuardMiddleware clears active FSM input on main navigation.
        navigation.include_router(module.router)
    dp.include_router(navigation)
    dp.include_router(checkout.router)
    fallback = Router()

    @fallback.callback_query()
    async def stale(callback: CallbackQuery):
        if callback.data != 'noop':
            await callback.message.answer('Эта кнопка устарела. Откройте нужный раздел через /start.')

    @fallback.message()
    async def unknown(message: Message):
        await message.answer('Выберите раздел в меню. Для отмены: /cancel, для начала: /start.')

    dp.include_router(fallback)
    return dp


async def main(check=False):
    db = Database(settings.db_path)
    await db.init()
    if check:
        create_dispatcher(db)
        print('Database, seed and routers OK. Telegram was not contacted.')
        return
    if not settings.token or settings.token == 'PASTE_YOUR_BOT_TOKEN_HERE':
        raise RuntimeError('Добавьте BOT_TOKEN в .env')
    bot = Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = create_dispatcher(db)
    worker = None
    try:
        await bot.set_my_commands([BotCommand(command='start', description='Главное меню'),
                                   BotCommand(command='cancel', description='Отменить ввод'),
                                   BotCommand(command='admin', description='Админ-панель')])
        worker = asyncio.create_task(notification_worker(bot, db))
        print('Coffee Time Almaty started')
        await dp.start_polling(bot, close_bot_session=False)
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await bot.session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Check database and imports offline')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')
    try:
        asyncio.run(main(args.check))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Credentials can occur in HTTP exceptions. Do not echo them to the console.
        print(f'Не удалось запустить бота ({type(exc).__name__}). Проверьте .env, базу и подключение к Telegram.')
        raise SystemExit(1) from None
