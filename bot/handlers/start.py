from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.helpers import ask_consent, esc, safe_delete, send_card
from bot.keyboards import back_kb, main_menu_kb
from config import Config
from db.models import ConsentKind, ConsentLog, User
from services.content import KEY_ABOUT, KEY_WELCOME, get_content
from services.timeutil import utcnow

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message, session, db_user: User, config: Config, state: FSMContext
):
    await state.clear()
    text, image = await get_content(session, KEY_WELCOME)
    await send_card(message, esc(text), image, main_menu_kb())
    if not db_user.has_pdn_consent:
        await ask_consent(message, session, config)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_delete(cb.message)
    await cb.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "consent:accept")
async def cb_consent_accept(cb: CallbackQuery, session, db_user: User, config: Config):
    if db_user.has_pdn_consent:
        await cb.answer("Согласие уже получено ✅")
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
    try:
        await cb.message.edit_text(
            "✅ Спасибо! Согласие на обработку персональных данных зафиксировано.\n\n"
            "Теперь вы можете записываться на мероприятия."
        )
    except Exception:
        await cb.message.answer("✅ Спасибо! Согласие зафиксировано.")
    await cb.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "menu:about")
async def cb_about(cb: CallbackQuery, session):
    text, image = await get_content(session, KEY_ABOUT)
    await safe_delete(cb.message)
    await send_card(cb.message, esc(text), image, back_kb("menu"))
    await cb.answer()
