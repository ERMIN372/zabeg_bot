"""Сценарий 2: пошаговая регистрация карточки участника."""
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.keyboards import btn, kb, menu_btn_row
from bot.states import ProfileForm
from db.models import ConsentKind, ConsentLog, User
from services.timeutil import utcnow

router = Router(name="profile")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{6,}$")


async def start_profile(message: Message, state: FSMContext, after: str | None) -> None:
    """Запуск анкеты; after — callback_data, куда вернуться после заполнения."""
    await state.clear()
    await state.set_state(ProfileForm.name)
    await state.update_data(after=after)
    await message.answer(
        "Заполним карточку участника — это займёт минуту.\n\n<b>Ваше имя?</b>"
    )


@router.message(ProfileForm.name, F.text)
async def profile_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value or value.startswith("/") or len(value) > 100:
        await message.answer("Пожалуйста, отправьте имя обычным текстом.")
        return
    await state.update_data(name=value)
    await state.set_state(ProfileForm.surname)
    await message.answer("<b>Ваша фамилия?</b>")


@router.message(ProfileForm.surname, F.text)
async def profile_surname(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value or value.startswith("/") or len(value) > 100:
        await message.answer("Пожалуйста, отправьте фамилию обычным текстом.")
        return
    await state.update_data(surname=value)
    await state.set_state(ProfileForm.phone)
    await message.answer(
        "<b>Номер телефона</b> — нажмите кнопку ниже или введите вручную.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )


@router.message(ProfileForm.phone, F.contact)
async def profile_phone_contact(message: Message, state: FSMContext):
    await state.update_data(phone=message.contact.phone_number)
    await _ask_email(message, state)


@router.message(ProfileForm.phone, F.text)
async def profile_phone_text(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not PHONE_RE.match(value):
        await message.answer(
            "Не похоже на номер телефона. Нажмите «📱 Поделиться номером» "
            "или введите номер в формате +7 900 000-00-00."
        )
        return
    await state.update_data(phone=value)
    await _ask_email(message, state)


async def _ask_email(message: Message, state: FSMContext):
    await state.set_state(ProfileForm.email)
    await message.answer(
        "<b>Электронная почта</b> (необязательно):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Если не хотите указывать почту — нажмите «Пропустить».",
        reply_markup=kb([btn("➡️ Пропустить", "profile:skip_email")]),
    )


@router.message(ProfileForm.email, F.text)
async def profile_email(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not EMAIL_RE.match(value):
        await message.answer(
            "Не похоже на адрес почты. Отправьте адрес вида name@example.com "
            "или нажмите «Пропустить».",
            reply_markup=kb([btn("➡️ Пропустить", "profile:skip_email")]),
        )
        return
    await state.update_data(email=value)
    await _ask_marketing(message, state)


@router.callback_query(ProfileForm.email, F.data == "profile:skip_email")
async def profile_skip_email(cb: CallbackQuery, state: FSMContext):
    await state.update_data(email=None)
    await _ask_marketing(cb.message, state)
    await cb.answer()


async def _ask_marketing(message: Message, state: FSMContext):
    await state.set_state(ProfileForm.marketing)
    await message.answer(
        "Хотите получать новости и анонсы сообщества?",
        reply_markup=kb(
            [btn("✅ Да, присылайте", "profile:mkt:yes")],
            [btn("Нет, спасибо", "profile:mkt:no")],
        ),
    )


@router.callback_query(ProfileForm.marketing, F.data.startswith("profile:mkt:"))
async def profile_finish(
    cb: CallbackQuery, state: FSMContext, session, db_user: User, config
):
    data = await state.get_data()
    marketing_yes = cb.data.endswith(":yes")
    db_user.name = data.get("name")
    db_user.surname = data.get("surname")
    db_user.phone = data.get("phone")
    db_user.email = data.get("email")
    if marketing_yes and db_user.consent_marketing_at is None:
        db_user.consent_marketing_at = utcnow()
        session.add(
            ConsentLog(
                user_id=db_user.id,
                kind=ConsentKind.MARKETING,
                version=config.consent_version,
            )
        )
    await session.commit()
    await state.clear()

    after = data.get("after")
    rows = []
    if after:
        rows.append([btn("▶️ Продолжить запись", after)])
    rows.append(menu_btn_row())
    await cb.message.answer(
        "✅ Карточка участника создана!", reply_markup=kb(*rows)
    )
    await cb.answer()
