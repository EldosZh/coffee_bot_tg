from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

NAVIGATION = ['☕ Меню', '🛒 Корзина', '📦 Мои заказы', '👤 Профиль',
              '🎁 Бонусы', '⭐ Отзывы', 'ℹ️ О кофейне']


def main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t) for t in NAVIGATION[i:i + 2]] for i in range(0, len(NAVIGATION), 2)
    ], resize_keyboard=True)


def phone_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='📱 Отправить номер телефона', request_contact=True)],
        [KeyboardButton(text='⬅️ Назад')], [KeyboardButton(text='❌ Отмена')]
    ], resize_keyboard=True, one_time_keyboard=True)
