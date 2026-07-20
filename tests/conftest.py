from datetime import timedelta

import pytest_asyncio

from db.base import Base, make_engine, make_sessionmaker
from db.models import Event, EventKind, Location, User
from services.timeutil import utcnow


@pytest_asyncio.fixture
async def sessionmaker(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def make_user(sessionmaker, telegram_id: int) -> User:
    async with sessionmaker() as session:
        user = User(
            telegram_id=telegram_id,
            name="Тест",
            surname=f"Юзер{telegram_id}",
            phone="+70000000000",
            consent_pdn_at=utcnow(),
            consent_pdn_version="1",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def make_event(sessionmaker, *capacities: int) -> tuple[int, list[int]]:
    """Создаёт мероприятие через сутки с локациями заданной вместимости."""
    async with sessionmaker() as session:
        event = Event(
            title="Тестовая пробежка",
            starts_at=utcnow() + timedelta(days=1),
            timezone="Europe/Moscow",
            kind=EventKind.RUN,
        )
        session.add(event)
        await session.flush()
        loc_ids = []
        for i, cap in enumerate(capacities):
            loc = Location(
                event_id=event.id,
                name=f"Локация {i + 1}",
                address=f"Адрес {i + 1}",
                capacity=cap,
                timezone="Europe/Moscow",
            )
            session.add(loc)
            await session.flush()
            loc_ids.append(loc.id)
        await session.commit()
        return event.id, loc_ids
