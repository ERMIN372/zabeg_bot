"""Бот-поддержка: вопросы пересылаются в админ-группу, ответы — обратно reply-ем."""
import sqlalchemy as sa
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.helpers import esc, safe_delete
from bot.keyboards import kb, menu_btn_row
from bot.states import SupportChat
from config import Config
from db.models import SupportMessage, User
from services.content import KEY_SUPPORT_GREETING, get_content

router = Router(name="support")


async def _greet(message: Message, session, state: FSMContext) -> None:
    text, _ = await get_content(session, KEY_SUPPORT_GREETING)
    await state.set_state(SupportChat.chatting)
    await message.answer(esc(text), reply_markup=kb(menu_btn_row()))


@router.message(Command("support"), F.chat.type == "private")
async def cmd_support(message: Message, session, state: FSMContext):
    await _greet(message, session, state)


@router.callback_query(F.data == "menu:support")
async def cb_support(cb: CallbackQuery, session, state: FSMContext):
    await safe_delete(cb.message)
    await _greet(cb.message, session, state)
    await cb.answer()


# --- Ответ администратора в группе поддержки --------------------------------

@router.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message)
async def support_admin_reply(message: Message, session, config: Config):
    if config.support_chat_id == 0 or message.chat.id != config.support_chat_id:
        return
    replied = message.reply_to_message
    if replied.from_user is None or replied.from_user.id != message.bot.id:
        return
    mapping = (
        await session.execute(
            sa.select(SupportMessage).where(
                SupportMessage.group_message_id == replied.message_id
            )
        )
    ).scalars().first()
    if mapping is None:
        return
    user = await session.get(User, mapping.user_id)
    if user is None:
        return
    try:
        if message.text:
            await message.bot.send_message(
                user.telegram_id,
                f"💬 <b>Ответ поддержки:</b>\n\n{esc(message.text)}",
            )
        else:
            await message.bot.send_message(user.telegram_id, "💬 <b>Ответ поддержки:</b>")
            await message.copy_to(user.telegram_id)
        await message.reply("✅ Доставлено")
    except Exception:
        await message.reply("⚠️ Не удалось доставить ответ (пользователь заблокировал бота?)")


# --- Сообщения пользователя в режиме поддержки ------------------------------

@router.message(SupportChat.chatting, F.chat.type == "private")
async def support_relay(message: Message, session, db_user: User, config: Config):
    if message.text and message.text.startswith("/"):
        return  # команды не пересылаем
    if config.support_chat_id == 0:
        await message.answer(
            "Поддержка временно недоступна. Попробуйте позже, пожалуйста."
        )
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    header = (
        f"📩 Вопрос от <b>{esc(db_user.full_name)}</b> "
        f"({username}, id <code>{db_user.telegram_id}</code>)"
    )
    try:
        if message.text:
            sent = await message.bot.send_message(
                config.support_chat_id, f"{header}\n\n{esc(message.text)}"
            )
            group_message_id = sent.message_id
        else:
            await message.bot.send_message(config.support_chat_id, header)
            copied = await message.copy_to(config.support_chat_id)
            group_message_id = copied.message_id
    except Exception:
        await message.answer(
            "Не получилось передать вопрос — попробуйте позже, пожалуйста."
        )
        return

    session.add(SupportMessage(user_id=db_user.id, group_message_id=group_message_id))
    await session.commit()
    await message.answer(
        "✅ Передали ваш вопрос команде — ответим прямо в этом чате.",
        reply_markup=kb(menu_btn_row()),
    )
