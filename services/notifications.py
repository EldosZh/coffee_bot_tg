"""Durable notifications: transient Telegram failures do not roll back orders."""
import asyncio
import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter

log = logging.getLogger(__name__)


async def deliver_pending(bot, db):
    rows = await db.query('SELECT * FROM notifications WHERE sent=0 ORDER BY id LIMIT 20')
    for row in rows:
        try:
            await bot.send_message(row['user_id'], row['text'])
        except (TelegramForbiddenError, TelegramBadRequest):
            await db.execute('UPDATE notifications SET sent=-1,attempts=attempts+1 WHERE id=?', (row['id'],))
            log.warning('Notification %s undeliverable', row['id'])
        except TelegramRetryAfter as exc:
            await asyncio.sleep(min(exc.retry_after, 30))
            return
        except Exception:
            await db.execute('UPDATE notifications SET attempts=attempts+1 WHERE id=?', (row['id'],))
            return
        else:
            await db.execute('UPDATE notifications SET sent=1,attempts=attempts+1 WHERE id=?', (row['id'],))


async def notification_worker(bot, db):
    while True:
        try:
            await deliver_pending(bot, db)
        except Exception as exc:
            log.error('Notification worker: %s', type(exc).__name__)
        await asyncio.sleep(3)
