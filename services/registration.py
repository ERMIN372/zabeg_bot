"""Бизнес-логика записи на мероприятия, листа ожидания и напоминаний.

Ключевой примитив — условный атомарный UPDATE счётчика мест
(`taken = taken + 1 WHERE taken < capacity`). Он одинаково защищает
от гонок и в SQLite, и в PostgreSQL: из двух одновременных запросов
на последнее место пройдёт ровно один.
"""
from __future__ import annotations

import html
from datetime import timedelta
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Event, EventStatus, Location, RegStatus, Registration, User
from services.notify import Note
from services.timeutil import format_date_ru, format_time_ru, utcnow


class ConfirmResult(Enum):
    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    FULL = "full"
    ALREADY = "already"


def _esc(s: str | None) -> str:
    return html.escape(s or "")


# --- Атомарные операции со счётчиком мест -----------------------------------

async def capture_seat(session: AsyncSession, location_id: int) -> bool:
    """Пытается занять место. True — место закреплено, False — мест нет."""
    res = await session.execute(
        sa.update(Location)
        .where(Location.id == location_id, Location.taken < Location.capacity)
        .values(taken=Location.taken + 1)
        .execution_options(synchronize_session=False)
    )
    return res.rowcount == 1


async def release_seat(session: AsyncSession, location_id: int) -> None:
    await session.execute(
        sa.update(Location)
        .where(Location.id == location_id, Location.taken > 0)
        .values(taken=Location.taken - 1)
        .execution_options(synchronize_session=False)
    )


async def free_seats(session: AsyncSession, location_id: int) -> int:
    """Свежее значение из БД (мимо identity map сессии)."""
    value = (
        await session.execute(
            sa.select(Location.capacity - Location.taken).where(Location.id == location_id)
        )
    ).scalar_one()
    return max(0, value)


# --- Запись -----------------------------------------------------------------

async def get_active_registration(
    session: AsyncSession, user_id: int, event_id: int
) -> Registration | None:
    return (
        await session.execute(
            sa.select(Registration).where(
                Registration.user_id == user_id,
                Registration.event_id == event_id,
                Registration.status != RegStatus.CANCELLED,
            )
        )
    ).scalars().first()


async def confirm_registration(
    session: AsyncSession, user: User, event_id: int, location_id: int
) -> tuple[ConfirmResult, Registration | None]:
    """Подтверждение записи с повторной проверкой мест (атомарно)."""
    existing = await get_active_registration(session, user.id, event_id)
    if existing:
        return ConfirmResult.ALREADY, existing

    if not await capture_seat(session, location_id):
        await session.rollback()
        return ConfirmResult.FULL, None

    reg = Registration(
        user_id=user.id, event_id=event_id, location_id=location_id,
        status=RegStatus.CONFIRMED,
    )
    session.add(reg)
    try:
        # commit фиксирует захват места и запись одной транзакцией
        await session.commit()
    except IntegrityError:
        # двойное нажатие: активная запись уже есть, rollback вернул место
        await session.rollback()
        existing = await get_active_registration(session, user.id, event_id)
        return ConfirmResult.ALREADY, existing
    return ConfirmResult.CONFIRMED, reg


async def join_waitlist(
    session: AsyncSession, user: User, event_id: int, location_id: int
) -> tuple[ConfirmResult, Registration | None]:
    """Встать в лист ожидания. Если место успело освободиться — сразу запись."""
    existing = await get_active_registration(session, user.id, event_id)
    if existing:
        return ConfirmResult.ALREADY, existing

    if await capture_seat(session, location_id):
        reg = Registration(
            user_id=user.id, event_id=event_id, location_id=location_id,
            status=RegStatus.CONFIRMED,
        )
        session.add(reg)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await get_active_registration(session, user.id, event_id)
            return ConfirmResult.ALREADY, existing
        return ConfirmResult.CONFIRMED, reg

    max_pos = (
        await session.execute(
            sa.select(sa.func.max(Registration.queue_pos)).where(
                Registration.location_id == location_id,
                Registration.status == RegStatus.WAITLIST,
            )
        )
    ).scalar() or 0
    reg = Registration(
        user_id=user.id, event_id=event_id, location_id=location_id,
        status=RegStatus.WAITLIST, queue_pos=max_pos + 1,
    )
    session.add(reg)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await get_active_registration(session, user.id, event_id)
        return ConfirmResult.ALREADY, existing
    return ConfirmResult.WAITLISTED, reg


async def waitlist_position(session: AsyncSession, reg: Registration) -> int:
    return (
        await session.execute(
            sa.select(sa.func.count()).select_from(Registration).where(
                Registration.location_id == reg.location_id,
                Registration.status == RegStatus.WAITLIST,
                Registration.queue_pos <= (reg.queue_pos or 0),
            )
        )
    ).scalar_one()


async def cancel_registration(
    session: AsyncSession, reg: Registration, confirm_hours: int
) -> list[Note]:
    """Отмена записи. Возвращает уведомления для листа ожидания."""
    was_confirmed = reg.status == RegStatus.CONFIRMED
    reg.status = RegStatus.CANCELLED
    reg.queue_pos = None
    reg.offered_at = None
    reg.offer_expires_at = None
    if was_confirmed:
        await release_seat(session, reg.location_id)
    await session.commit()
    if was_confirmed:
        return await reconcile_location(session, reg.location_id, confirm_hours)
    return []


async def change_location(
    session: AsyncSession, reg: Registration, new_location_id: int, confirm_hours: int
) -> tuple[bool, list[Note]]:
    """Смена локации: место в новой захватывается ДО освобождения старой.

    Если мест в новой локации нет — текущая запись не меняется.
    """
    old_location_id = reg.location_id
    if new_location_id == old_location_id:
        return True, []
    if not await capture_seat(session, new_location_id):
        await session.rollback()
        return False, []
    await release_seat(session, old_location_id)
    reg.location_id = new_location_id
    await session.commit()
    # relationship могла закэшировать старую локацию — перечитываем
    await session.refresh(reg, attribute_names=["location"])
    notes = await reconcile_location(session, old_location_id, confirm_hours)
    return True, notes


# --- Лист ожидания ----------------------------------------------------------

def _offer_note(reg: Registration, confirm_hours: int) -> Note:
    event, loc = reg.event, reg.location
    return Note(
        chat_id=reg.user.telegram_id,
        text=(
            f"🎉 На «{_esc(event.title)}» освободилось место!\n\n"
            f"📍 Локация: {_esc(loc.name)}\n"
            f"🗓 {format_date_ru(event.starts_at, loc.timezone)}, "
            f"{format_time_ru(event.starts_at, loc.timezone)}\n\n"
            f"Подтвердите участие в течение {confirm_hours} ч., "
            f"иначе место перейдёт следующему в очереди."
        ),
        buttons=[
            [("✅ Подтвердить", f"wl:accept:{reg.id}")],
            [("❌ Отказаться", f"wl:decline:{reg.id}")],
        ],
    )


async def reconcile_location(
    session: AsyncSession, location_id: int, confirm_hours: int
) -> list[Note]:
    """Раздаёт освободившиеся места листу ожидания.

    Активных предложений никогда не больше, чем свободных мест,
    поэтому одно место не будет предложено двум людям сразу.
    """
    free = await free_seats(session, location_id)
    if free <= 0:
        return []
    now = utcnow()
    pending = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Registration).where(
                Registration.location_id == location_id,
                Registration.status == RegStatus.WAITLIST,
                Registration.offer_expires_at.is_not(None),
                Registration.offer_expires_at > now,
            )
        )
    ).scalar_one()
    slots = free - pending
    if slots <= 0:
        return []
    candidates = (
        await session.execute(
            sa.select(Registration)
            .where(
                Registration.location_id == location_id,
                Registration.status == RegStatus.WAITLIST,
                Registration.offer_expires_at.is_(None),
            )
            .order_by(Registration.queue_pos)
            .limit(slots)
        )
    ).scalars().all()
    notes = []
    for reg in candidates:
        reg.offered_at = now
        reg.offer_expires_at = now + timedelta(hours=confirm_hours)
        notes.append(_offer_note(reg, confirm_hours))
    await session.commit()
    return notes


async def confirm_from_waitlist(
    session: AsyncSession, reg: Registration
) -> ConfirmResult:
    """Подтверждение места из листа ожидания (повторная атомарная проверка)."""
    if reg.status == RegStatus.CONFIRMED:
        return ConfirmResult.ALREADY
    if reg.status != RegStatus.WAITLIST:
        return ConfirmResult.FULL
    if not await capture_seat(session, reg.location_id):
        # место снова заняли — пользователь остаётся в начале очереди
        reg.offered_at = None
        reg.offer_expires_at = None
        await session.commit()
        return ConfirmResult.FULL
    reg.status = RegStatus.CONFIRMED
    reg.queue_pos = None
    reg.offered_at = None
    reg.offer_expires_at = None
    await session.commit()
    return ConfirmResult.CONFIRMED


async def decline_waitlist(
    session: AsyncSession, reg: Registration, confirm_hours: int
) -> list[Note]:
    """Отказ от предложенного места — предложение уходит следующему."""
    had_offer = reg.offer_expires_at is not None
    location_id = reg.location_id
    reg.status = RegStatus.CANCELLED
    reg.queue_pos = None
    reg.offered_at = None
    reg.offer_expires_at = None
    await session.commit()
    if had_offer:
        return await reconcile_location(session, location_id, confirm_hours)
    return []


async def expire_offers(session: AsyncSession, confirm_hours: int) -> list[Note]:
    """Просроченные предложения: уведомить, отправить в конец очереди, предложить следующим."""
    now = utcnow()
    expired = (
        await session.execute(
            sa.select(Registration).where(
                Registration.status == RegStatus.WAITLIST,
                Registration.offer_expires_at.is_not(None),
                Registration.offer_expires_at <= now,
            )
        )
    ).scalars().all()
    if not expired:
        return []
    notes = []
    touched: set[int] = set()
    for reg in expired:
        max_pos = (
            await session.execute(
                sa.select(sa.func.max(Registration.queue_pos)).where(
                    Registration.location_id == reg.location_id,
                    Registration.status == RegStatus.WAITLIST,
                )
            )
        ).scalar() or 0
        reg.offered_at = None
        reg.offer_expires_at = None
        reg.queue_pos = max_pos + 1
        touched.add(reg.location_id)
        notes.append(
            Note(
                chat_id=reg.user.telegram_id,
                text=(
                    f"⏰ Время на подтверждение места на «{_esc(reg.event.title)}» истекло — "
                    f"предложение перешло следующему. Вы остаётесь в листе ожидания."
                ),
            )
        )
    await session.commit()
    for location_id in touched:
        notes += await reconcile_location(session, location_id, confirm_hours)
    return notes


async def reconcile_all(session: AsyncSession, confirm_hours: int) -> list[Note]:
    """Проходит по всем локациям будущих мероприятий с листом ожидания."""
    now = utcnow()
    location_ids = (
        await session.execute(
            sa.select(Registration.location_id)
            .join(Event, Event.id == Registration.event_id)
            .where(
                Registration.status == RegStatus.WAITLIST,
                Event.status == EventStatus.ACTIVE,
                Event.starts_at > now,
            )
            .distinct()
        )
    ).scalars().all()
    notes = []
    for location_id in location_ids:
        notes += await reconcile_location(session, location_id, confirm_hours)
    return notes


# --- Напоминания ------------------------------------------------------------

def _reminder_note(reg: Registration, header: str) -> Note:
    event, loc = reg.event, reg.location
    return Note(
        chat_id=reg.user.telegram_id,
        text=(
            f"{header}\n\n"
            f"<b>{_esc(event.title)}</b>\n"
            f"📍 {_esc(loc.name)}, {_esc(loc.address)}\n"
            f"🗓 {format_date_ru(event.starts_at, loc.timezone)}\n"
            f"🕒 {format_time_ru(event.starts_at, loc.timezone)} "
            f"(время местное)"
        ),
        image_file_id=event.image_file_id,
    )


async def due_reminders(session: AsyncSession) -> list[Note]:
    """Напоминания за 24 и за 3 часа до старта (время события — абсолютное, UTC)."""
    now = utcnow()
    notes = []

    base = (
        sa.select(Registration)
        .join(Event, Event.id == Registration.event_id)
        .where(
            Registration.status == RegStatus.CONFIRMED,
            Event.status == EventStatus.ACTIVE,
            Event.starts_at > now,
        )
    )

    for_3h = (
        await session.execute(
            base.where(
                Event.starts_at <= now + timedelta(hours=3),
                Registration.reminded_3h_at.is_(None),
            )
        )
    ).scalars().all()
    for reg in for_3h:
        reg.reminded_3h_at = now
        # чтобы следом не пришло и «за 24 часа»
        if reg.reminded_24h_at is None:
            reg.reminded_24h_at = now
        notes.append(
            _reminder_note(
                reg,
                "⏳ До старта осталось 3 часа. Не забудьте форму, воду и хорошее настроение!",
            )
        )

    for_24h = (
        await session.execute(
            base.where(
                Event.starts_at <= now + timedelta(hours=24),
                Registration.reminded_24h_at.is_(None),
            )
        )
    ).scalars().all()
    for reg in for_24h:
        reg.reminded_24h_at = now
        notes.append(
            _reminder_note(reg, f"🏃 Уже завтра встречаемся на «{_esc(reg.event.title)}»!")
        )

    await session.commit()
    return notes


# --- Рассылки и служебное ---------------------------------------------------

async def event_participant_notes(
    session: AsyncSession, event_id: int, text: str, image_file_id: str | None = None
) -> list[Note]:
    """Уведомление всем активным участникам мероприятия (отмена/перенос)."""
    regs = (
        await session.execute(
            sa.select(Registration).where(
                Registration.event_id == event_id,
                Registration.status != RegStatus.CANCELLED,
            )
        )
    ).scalars().all()
    return [Note(chat_id=r.user.telegram_id, text=text, image_file_id=image_file_id) for r in regs]


async def recount_taken(session: AsyncSession) -> int:
    """Страховочный пересчёт счётчиков мест по фактическим записям."""
    locations = (await session.execute(sa.select(Location))).scalars().all()
    fixed = 0
    for loc in locations:
        actual = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Registration).where(
                    Registration.location_id == loc.id,
                    Registration.status == RegStatus.CONFIRMED,
                )
            )
        ).scalar_one()
        if loc.taken != actual:
            loc.taken = actual
            fixed += 1
    await session.commit()
    return fixed
