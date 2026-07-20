"""Точка входа бота (long polling)."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.handlers import events, my_registrations, photos, profile, start, support
from bot.handlers.admin import admin_router
from bot.middlewares import DbSessionMiddleware, UserMiddleware
from config import load_config
from db.base import make_engine, make_sessionmaker

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Запуск бота"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="events", description="Ближайшие мероприятия"),
    BotCommand(command="registrations", description="Мои записи"),
    BotCommand(command="support", description="Связь с поддержкой"),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    if not config.bot_token:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")

    engine = make_engine(config.database_url)
    sessionmaker = make_sessionmaker(engine)

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp["config"] = config
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(UserMiddleware())

    # Порядок важен: команды и сценарии — раньше catch-all поддержки.
    dp.include_routers(
        admin_router,
        start.router,
        events.router,
        my_registrations.router,
        photos.router,
        profile.router,
        support.router,
    )

    await bot.set_my_commands(COMMANDS)
    log.info("Бот запущен (polling)")
    try:
        await dp.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
