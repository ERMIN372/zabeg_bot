"""Лист ожидания: очередь, таймер 2 часа, автопереход к следующему."""
from datetime import timedelta

import sqlalchemy as sa

from db.models import RegStatus, Registration, User
from services.registration import (
    ConfirmResult,
    cancel_registration,
    confirm_from_waitlist,
    confirm_registration,
    expire_offers,
    join_waitlist,
)
from services.timeutil import utcnow

CONFIRM_HOURS = 2


async def _get_reg(sessionmaker, reg_id: int) -> Registration:
    async with sessionmaker() as session:
        return await session.get(Registration, reg_id)


async def test_waitlist_offer_and_expiry_chain(sessionmaker):
    from tests.conftest import make_event, make_user

    event_id, (loc_id,) = await make_event(sessionmaker, 1)
    ua = await make_user(sessionmaker, 401)
    ub = await make_user(sessionmaker, 402)
    uc = await make_user(sessionmaker, 403)

    # A занимает единственное место, B и C — в лист ожидания
    async with sessionmaker() as session:
        u = await session.get(User, ua.id)
        result, reg_a = await confirm_registration(session, u, event_id, loc_id)
        assert result == ConfirmResult.CONFIRMED
        reg_a_id = reg_a.id
    async with sessionmaker() as session:
        u = await session.get(User, ub.id)
        result, reg_b = await join_waitlist(session, u, event_id, loc_id)
        assert result == ConfirmResult.WAITLISTED
        reg_b_id = reg_b.id
    async with sessionmaker() as session:
        u = await session.get(User, uc.id)
        result, reg_c = await join_waitlist(session, u, event_id, loc_id)
        assert result == ConfirmResult.WAITLISTED
        reg_c_id = reg_c.id

    # A отменяет запись — предложение уходит первому в очереди (B)
    async with sessionmaker() as session:
        reg_a = await session.get(Registration, reg_a_id)
        notes = await cancel_registration(session, reg_a, CONFIRM_HOURS)
    assert [n.chat_id for n in notes] == [ub.telegram_id]

    reg_b = await _get_reg(sessionmaker, reg_b_id)
    assert reg_b.offer_expires_at is not None
    reg_c = await _get_reg(sessionmaker, reg_c_id)
    assert reg_c.offer_expires_at is None  # одно место — одно предложение

    # B не подтвердил за 2 часа — предложение переходит C, B уходит в конец
    async with sessionmaker() as session:
        reg_b = await session.get(Registration, reg_b_id)
        reg_b.offer_expires_at = utcnow() - timedelta(minutes=1)
        await session.commit()
    async with sessionmaker() as session:
        notes = await expire_offers(session, CONFIRM_HOURS)
    chat_ids = [n.chat_id for n in notes]
    assert ub.telegram_id in chat_ids  # уведомление об истечении
    assert uc.telegram_id in chat_ids  # новое предложение

    reg_b = await _get_reg(sessionmaker, reg_b_id)
    reg_c = await _get_reg(sessionmaker, reg_c_id)
    assert reg_b.offer_expires_at is None
    assert reg_c.offer_expires_at is not None
    assert reg_b.queue_pos > reg_c.queue_pos

    # C подтверждает и получает место
    async with sessionmaker() as session:
        reg_c = await session.get(Registration, reg_c_id)
        result = await confirm_from_waitlist(session, reg_c)
    assert result == ConfirmResult.CONFIRMED

    # свободных мест не осталось — просроченный B предложения не получает
    async with sessionmaker() as session:
        notes = await expire_offers(session, CONFIRM_HOURS)
    assert notes == []


async def test_waitlist_confirm_races_lost_seat(sessionmaker):
    """Если место успели занять до подтверждения, пользователь остаётся в очереди."""
    from tests.conftest import make_event, make_user

    event_id, (loc_id,) = await make_event(sessionmaker, 1)
    ua = await make_user(sessionmaker, 501)
    ub = await make_user(sessionmaker, 502)

    async with sessionmaker() as session:
        u = await session.get(User, ua.id)
        _, reg_a = await confirm_registration(session, u, event_id, loc_id)
        reg_a_id = reg_a.id
    async with sessionmaker() as session:
        u = await session.get(User, ub.id)
        _, reg_b = await join_waitlist(session, u, event_id, loc_id)
        reg_b_id = reg_b.id

    # место освободилось, B получил предложение
    async with sessionmaker() as session:
        reg_a = await session.get(Registration, reg_a_id)
        await cancel_registration(session, reg_a, CONFIRM_HOURS)

    # ...но место внезапно снова занято (например, админ уменьшил лимит)
    async with sessionmaker() as session:
        from db.models import Location
        loc = await session.get(Location, loc_id)
        loc.capacity = 0
        await session.commit()

    async with sessionmaker() as session:
        reg_b = await session.get(Registration, reg_b_id)
        result = await confirm_from_waitlist(session, reg_b)
    assert result == ConfirmResult.FULL

    reg_b = await _get_reg(sessionmaker, reg_b_id)
    assert reg_b.status == RegStatus.WAITLIST  # остался в очереди
    assert reg_b.offer_expires_at is None


async def test_join_waitlist_takes_freed_seat_directly(sessionmaker):
    """Если место освободилось к моменту вступления в лист — сразу запись."""
    from tests.conftest import make_event, make_user

    event_id, (loc_id,) = await make_event(sessionmaker, 1)
    user = await make_user(sessionmaker, 601)

    async with sessionmaker() as session:
        u = await session.get(User, user.id)
        result, reg = await join_waitlist(session, u, event_id, loc_id)
    assert result == ConfirmResult.CONFIRMED
    assert reg.status == RegStatus.CONFIRMED
