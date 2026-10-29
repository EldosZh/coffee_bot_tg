from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from config import settings
from handlers.common import show
from keyboards.reply import main_keyboard

router = Router()
WELCOME = '☕ <b>Coffee Time Almaty</b>\n\nДобро пожаловать!\nЗакажите любимый кофе прямо в Telegram.'


@router.message(CommandStart())
@router.message(Command('cancel'))
@router.message(F.text == '❌ Отмена')
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_keyboard())


@router.callback_query(F.data == 'home')
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(callback, WELCOME)
    await callback.message.answer('Выберите раздел:', reply_markup=main_keyboard())


@router.message(F.text == 'ℹ️ О кофейне')
async def about(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f'☕ <b>Coffee Time Almaty</b>\n\n{settings.address}\n'
                         '🕘 08:00–22:00\nАдрес и часы демонстрационные.\n'
                         'Доставка: стоимость и возможность уточняются кофейней; '
                         'в боте плата за доставку не начисляется.', reply_markup=main_keyboard())
