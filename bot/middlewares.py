import sqlalchemy as sa
from aiogram import BaseMiddleware
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import User


class DbSessionMiddleware(BaseMiddleware):
    """Сессия БД на каждый апдейт (data['session'])."""

    def __init__(self, sessionmaker: async_sessionmaker):
        self.sessionmaker = sessionmaker

    async def __call__(self, handler, event, data):
        async with self.sessionmaker() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Гарантирует наличие строки users для автора апдейта (data['db_user'])."""

    async def __call__(self, handler, event, data):
        from_user = data.get("event_from_user")
        if from_user is not None and not from_user.is_bot:
            session = data["session"]
            user = (
                await session.execute(
                    sa.select(User).where(User.telegram_id == from_user.id)
                )
            ).scalars().first()
            if user is None:
                user = User(telegram_id=from_user.id)
                session.add(user)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    user = (
                        await session.execute(
                            sa.select(User).where(User.telegram_id == from_user.id)
                        )
                    ).scalars().one()
            data["db_user"] = user
        return await handler(event, data)
