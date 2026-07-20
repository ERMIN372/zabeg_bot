"""Атомарность записи: гонка за последнее место и идемпотентность."""
import asyncio

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from db.models import Location, RegStatus, Registration, User
from services.registration import ConfirmResult, confirm_registration
from tests.conftest import make_event, make_user


async def test_race_for_last_seat(sessionmaker):
    """Из двух одновременных записей на последнее место проходит ровно одна."""
    event_id, (loc_id,) = await make_event(sessionmaker, 1)
    u1 = await make_user(sessionmaker, 101)
    u2 = await make_user(sessionmaker, 102)

    async def attempt(user_id: int):
        async with sessionmaker() as session:
            user = await session.get(User, user_id)
            return await confirm_registration(session, user, event_id, loc_id)

    (r1, _), (r2, _) = await asyncio.gather(attempt(u1.id), attempt(u2.id))
    assert {r1, r2} == {ConfirmResult.CONFIRMED, ConfirmResult.FULL}

    async with sessionmaker() as session:
        loc = await session.get(Location, loc_id)
        assert loc.taken == 1
        count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Registration).where(
                    Registration.status == RegStatus.CONFIRMED
                )
            )
        ).scalar_one()
        assert count == 1


async def test_double_confirm_is_idempotent(sessionmaker):
    """Повторное подтверждение не создаёт дубликат и не съедает второе место."""
    event_id, (loc_id,) = await make_event(sessionmaker, 10)
    user = await make_user(sessionmaker, 201)

    async with sessionmaker() as session:
        u = await session.get(User, user.id)
        r1, reg1 = await confirm_registration(session, u, event_id, loc_id)
    async with sessionmaker() as session:
        u = await session.get(User, user.id)
        r2, reg2 = await confirm_registration(session, u, event_id, loc_id)

    assert r1 == ConfirmResult.CONFIRMED
    assert r2 == ConfirmResult.ALREADY
    assert reg1.id == reg2.id

    async with sessionmaker() as session:
        loc = await session.get(Location, loc_id)
        assert loc.taken == 1


async def test_unique_index_blocks_duplicate_active_registration(sessionmaker):
    """Частичный уникальный индекс не даёт создать две активные записи."""
    event_id, (loc_id,) = await make_event(sessionmaker, 10)
    user = await make_user(sessionmaker, 301)

    async with sessionmaker() as session:
        session.add(
            Registration(
                user_id=user.id, event_id=event_id, location_id=loc_id,
                status=RegStatus.CONFIRMED,
            )
        )
        await session.commit()

    async with sessionmaker() as session:
        session.add(
            Registration(
                user_id=user.id, event_id=event_id, location_id=loc_id,
                status=RegStatus.WAITLIST,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    # отменённая запись не мешает создать новую
    async with sessionmaker() as session:
        session.add(
            Registration(
                user_id=user.id, event_id=event_id, location_id=loc_id,
                status=RegStatus.CANCELLED,
            )
        )
        await session.commit()
