"""Сценарий 2: регистрация карточки участника.

Согласие на ПДн (152-ФЗ) фиксируется в момент, когда пользователь делится
контактом: короткий текст + ссылка на политику в том же сообщении, отдельной
кнопки «Согласен» нет (как в референсе). В БД пишем user_id, время и версию.
"""
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot.helpers import TEXT_INPUT, esc
from bot.keyboards import btn, kb, menu_btn_row
from bot.states import ProfileForm
from config import Config
from db.models import ConsentKind, ConsentLog, User
from services.content import KEY_CONSENT, get_content
from services.timeutil import utcnow

router = Router(name="profile")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{6,}$")

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def start_profile(
    message: Message, state: FSMContext, after: str | None, session, config: Config
) -> None:
    """Запуск анкеты; after — callback_data, куда вернуться после заполнения."""
    await state.clear()
    await state.set_state(ProfileForm.phone)
    await state.update_data(after=after)
    intro, _ = await get_content(session, KEY_CONSENT)
    await message.answer(
        f"{esc(intro)}\n\n"
        "Поделившись контактом, вы принимаете "
        f'<a href="{config.privacy_policy_url}">политику конфиденциальности</a>.',
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
        link_preview_options=_NO_PREVIEW,
    )


async def _record_pdn_consent(session, db_user: User, config: Config) -> None:
    if db_user.has_pdn_consent:
        return
    db_user.consent_pdn_at = utcnow()
    db_user.consent_pdn_version = config.consent_version
    session.add(
        ConsentLog(
            user_id=db_user.id,
            kind=ConsentKind.PDN,
            version=config.consent_version,
        )
    )
    await session.commit()


@router.message(ProfileForm.phone, F.contact)
async def profile_phone_contact(
    message: Message, state: FSMContext, session, db_user: User, config: Config
):
    await _record_pdn_consent(session, db_user, config)
    await state.update_data(phone=message.contact.phone_number)
    await _ask_name(message, state)


@router.message(ProfileForm.phone, TEXT_INPUT)
async def profile_phone_text(
    message: Message, state: FSMContext, session, db_user: User, config: Config
):
    value = (message.text or "").strip()
    if not PHONE_RE.match(value):
        await message.answer(
            "Не похоже на номер телефона. Нажмите «📱 Поделиться контактом» "
            "или введите номер в формате +7 900 000-00-00."
        )
        return
    await _record_pdn_consent(session, db_user, config)
    await state.update_data(phone=value)
    await _ask_name(message, state)


async def _ask_name(message: Message, state: FSMContext):
    await state.set_state(ProfileForm.name)
    await message.answer(
        "Спасибо! Контакт привязан. ✅\n\nКак вас зовут? <b>Имя?</b>",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ProfileForm.name, TEXT_INPUT)
async def profile_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value or len(value) > 100:
        await message.answer("Пожалуйста, отправьте имя обычным текстом.")
        return
    await state.update_data(name=value)
    await state.set_state(ProfileForm.surname)
    await message.answer("<b>Ваша фамилия?</b>")


@router.message(ProfileForm.surname, TEXT_INPUT)
async def profile_surname(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value or len(value) > 100:
        await message.answer("Пожалуйста, отправьте фамилию обычным текстом.")
        return
    await state.update_data(surname=value)
    await _ask_email(message, state)


async def _ask_email(message: Message, state: FSMContext):
    await state.set_state(ProfileForm.email)
    await message.answer(
        "<b>Электронная почта</b> (необязательно):\n"
        "Если не хотите указывать — нажмите «Пропустить».",
        reply_markup=kb([btn("➡️ Пропустить", "profile:skip_email")]),
    )


@router.message(ProfileForm.email, TEXT_INPUT)
async def profile_email(message: Message, state: FSMContext, session, db_user: User):
    value = (message.text or "").strip()
    if not EMAIL_RE.match(value):
        await message.answer(
            "Не похоже на адрес почты. Отправьте адрес вида name@example.com "
            "или нажмите «Пропустить».",
            reply_markup=kb([btn("➡️ Пропустить", "profile:skip_email")]),
        )
        return
    await state.update_data(email=value)
    await _finish_profile(message, state, session, db_user)


@router.callback_query(ProfileForm.email, F.data == "profile:skip_email")
async def profile_skip_email(cb: CallbackQuery, state: FSMContext, session, db_user: User):
    await state.update_data(email=None)
    await _finish_profile(cb.message, state, session, db_user)
    await cb.answer()


async def _finish_profile(
    message: Message, state: FSMContext, session, db_user: User
) -> None:
    data = await state.get_data()
    db_user.name = data.get("name")
    db_user.surname = data.get("surname")
    db_user.phone = data.get("phone")
    db_user.email = data.get("email")
    await session.commit()
    await state.clear()

    after = data.get("after")
    rows = []
    if after:
        rows.append([btn("▶️ Продолжить запись", after)])
    rows.append(menu_btn_row())
    await message.answer("✅ Карточка участника создана!", reply_markup=kb(*rows))
