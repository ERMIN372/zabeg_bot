"""Смена локации: при неудаче текущая запись сохраняется."""
from db.models import Location, RegStatus, Registration, User
from services.registration import (
    ConfirmResult,
    change_location,
    confirm_registration,
)
from tests.conftest import make_event, make_user


async def test_change_location_full_keeps_old_registration(sessionmaker):
    event_id, (loc1, loc2) = await make_event(sessionmaker, 5, 1)
    ua = await make_user(sessionmaker, 701)
    ub = await make_user(sessionmaker, 702)

    # B занимает единственное место во второй локации
    async with sessionmaker() as session:
        u = await session.get(User, ub.id)
        result, _ = await confirm_registration(session, u, event_id, loc2)
        assert result == ConfirmResult.CONFIRMED

    # A записан в первую, пытается перейти во вторую (мест нет)
    async with sessionmaker() as session:
        u = await session.get(User, ua.id)
        _, reg_a = await confirm_registration(session, u, event_id, loc1)
        reg_a_id = reg_a.id

    async with sessionmaker() as session:
        reg_a = await session.get(Registration, reg_a_id)
        ok, _ = await change_location(session, reg_a, loc2, 2)
    assert ok is False

    async with sessionmaker() as session:
        reg_a = await session.get(Registration, reg_a_id)
        assert reg_a.status == RegStatus.CONFIRMED
        assert reg_a.location_id == loc1  # запись не тронута
        l1 = await session.get(Location, loc1)
        l2 = await session.get(Location, loc2)
        assert l1.taken == 1
        assert l2.taken == 1


async def test_change_location_success_moves_seat(sessionmaker):
    event_id, (loc1, loc2) = await make_event(sessionmaker, 5, 5)
    user = await make_user(sessionmaker, 801)

    async with sessionmaker() as session:
        u = await session.get(User, user.id)
        _, reg = await confirm_registration(session, u, event_id, loc1)
        reg_id = reg.id

    async with sessionmaker() as session:
        reg = await session.get(Registration, reg_id)
        ok, _ = await change_location(session, reg, loc2, 2)
    assert ok is True

    async with sessionmaker() as session:
        reg = await session.get(Registration, reg_id)
        assert reg.location_id == loc2
        l1 = await session.get(Location, loc1)
        l2 = await session.get(Location, loc2)
        assert l1.taken == 0
        assert l2.taken == 1
