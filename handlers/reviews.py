from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from database.db import now
from handlers.common import show
from keyboards.inline import back

router = Router()


class Review(StatesGroup):
    rating = State()
    comment = State()


@router.message(F.text.in_({'⭐ Отзывы', '⭐ Отзыв'}))
async def reviews(message: Message, state: FSMContext, db):
    await state.clear()
    await state.set_state(Review.rating)
    summary = (await db.query('SELECT COUNT(*) AS n,COALESCE(AVG(rating),0) AS avg FROM reviews'))[0]
    await show(message, f"⭐ Отзывы: {summary['n']} · средняя оценка {summary['avg']:.1f}/5\n\nКак вам наша кофейня?",
               [[('⭐' * n, f'rating:{n}')] for n in range(1, 6)] + [back()])


@router.callback_query(Review.rating, F.data.startswith('rating:'))
async def rating(callback: CallbackQuery, state: FSMContext):
    value = int(callback.data.split(':')[1])
    if not 1 <= value <= 5:
        raise ValueError('Оценка должна быть от 1 до 5.')
    await state.update_data(rating=value)
    await state.set_state(Review.comment)
    await show(callback, 'Напишите комментарий к оценке:', [back('reviews:back')])


@router.callback_query(Review.comment, F.data == 'reviews:back')
async def back_rating(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Review.rating)
    await show(callback, 'Выберите оценку:', [[('⭐' * n, f'rating:{n}')] for n in range(1, 6)] + [back()])


@router.message(Review.comment)
async def comment(message: Message, state: FSMContext, db):
    value = (message.text or '').strip()
    if not 1 <= len(value) <= 2000:
        raise ValueError('Напишите отзыв текстом, до 2000 символов.')
    data = await state.get_data()
    await db.execute('INSERT INTO reviews(user_id,rating,comment,created) VALUES(?,?,?,?)',
                     (message.from_user.id, data['rating'], value, now()))
    await state.clear()
    await show(message, 'Спасибо! Ваш отзыв сохранён ❤️', [back()])
