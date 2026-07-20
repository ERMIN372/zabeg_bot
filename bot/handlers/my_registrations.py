"""Сценарий 7: «Мои записи» и отмена регистрации."""
import sqlalchemy as sa
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.helpers import esc, safe_delete, reg_details_text
from bot.keyboards import btn, kb, menu_btn_row
from config import Config
from db.models import Event, EventKind, EventStatus, RegStatus, Registration, User
from services import registration as reg_service
from services.notify import send_notes
from services.timeutil import format_date_ru, utcnow

router = Router(name="my_registrations")


async def _active_regs(session, db_user: User) -> list[Registration]:
    return (
        await session.execute(
            sa.select(Registration)
            .join(Event, Event.id == Registration.event_id)
            .where(
                Registration.user_id == db_user.id,
                Registration.status != RegStatus.CANCELLED,
                Event.status == EventStatus.ACTIVE,
                Event.starts_at > utcnow(),
            )
            .order_by(Event.starts_at)
        )
    ).scalars().all()


async def _show_list(message: Message, session, db_user: User) -> None:
    regs = await _active_regs(session, db_user)
    if not regs:
        await message.answer(
            "У вас пока нет активных записей.\n"
            "Загляните в «Ближайшие мероприятия»! 🏃",
            reply_markup=kb(
                [btn("🏃 Ближайшие мероприятия", "menu:events")],
                menu_btn_row(),
            ),
        )
        return
    rows = []
    for reg in regs:
        mark = "✅" if reg.status == RegStatus.CONFIRMED else "⏳"
        date = format_date_ru(reg.event.starts_at, reg.location.timezone)
        rows.append([btn(f"{mark} {reg.event.title} — {date}", f"reg:view:{reg.id}")])
    rows.append(menu_btn_row())
    await message.answer("Ваши записи:", reply_markup=kb(*rows))


@router.message(Command("registrations"))
async def cmd_registrations(message: Message, session, db_user: User, state: FSMContext):
    await state.clear()
    await _show_list(message, session, db_user)


@router.callback_query(F.data == "menu:regs")
async def cb_registrations(cb: CallbackQuery, session, db_user: User, state: FSMContext):
    await state.clear()
    await safe_delete(cb.message)
    await _show_list(cb.message, session, db_user)
    await cb.answer()


async def _load_own_reg(session, db_user: User, reg_id: int) -> Registration | None:
    reg = await session.get(Registration, reg_id)
    if reg is None or reg.user_id != db_user.id:
        return None
    return reg


@router.callback_query(F.data.startswith("reg:view:"))
async def cb_reg_view(cb: CallbackQuery, session, db_user: User):
    reg = await _load_own_reg(session, db_user, int(cb.data.split(":")[2]))
    if reg is None or reg.status == RegStatus.CANCELLED:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    if reg.status == RegStatus.WAITLIST:
        pos = await reg_service.waitlist_position(session, reg)
        header = f"⏳ Вы в листе ожидания (позиция {pos})"
    else:
        header = "✅ Вы записаны"
    rows = [[btn("❌ Отменить участие", f"reg:cancel:{reg.id}")]]
    if reg.status == RegStatus.CONFIRMED and reg.event.kind == EventKind.RUN:
        rows.insert(0, [btn("🔄 Сменить локацию", f"reg:chloc:{reg.id}")])
    rows.append([btn("⬅️ Назад", "menu:regs")])
    await safe_delete(cb.message)
    await cb.message.answer(reg_details_text(reg, header=header), reply_markup=kb(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("reg:cancel2:"))
async def cb_reg_cancel_confirmed(cb: CallbackQuery, session, db_user: User, config: Config):
    reg = await _load_own_reg(session, db_user, int(cb.data.split(":")[2]))
    if reg is None:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    if reg.status == RegStatus.CANCELLED:
        await cb.answer("Запись уже отменена")
        return
    notes = await reg_service.cancel_registration(
        session, reg, config.waitlist_confirm_hours
    )
    await send_notes(cb.bot, notes)
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Запись на «{esc(reg.event.title)}» отменена. "
        "Будем рады видеть вас в другой раз! 🏃",
        reply_markup=kb(
            [btn("🏃 Ближайшие мероприятия", "menu:events")],
            menu_btn_row(),
        ),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("reg:cancel:"))
async def cb_reg_cancel_ask(cb: CallbackQuery, session, db_user: User):
    reg = await _load_own_reg(session, db_user, int(cb.data.split(":")[2]))
    if reg is None or reg.status == RegStatus.CANCELLED:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    await safe_delete(cb.message)
    await cb.message.answer(
        "Вы точно не сможете прийти? После отмены место может быть "
        "передано другому участнику.",
        reply_markup=kb(
            [btn("Да, отменить", f"reg:cancel2:{reg.id}")],
            [btn("Нет, оставить запись", f"reg:view:{reg.id}")],
        ),
    )
    await cb.answer()
