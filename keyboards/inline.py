from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows
    ])


def back(data='home'):
    return [('⬅️ Назад', data)]
