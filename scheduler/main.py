"""Планировщик — отдельный процесс, не зависящий от апдейтов Telegram.

Каждый тик читает из БД, что пора сделать:
  * просроченные предложения листа ожидания (2 часа на подтверждение);
  * раздача освободившихся мест листу ожидания;
  * напоминания за 24 и за 3 часа до старта.

Всё состояние — в БД, поэтому рестарт процесса ничего не теряет.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import load_config
from db.base import make_engine, make_sessionmaker
from services.notify import send_notes
from services.registration import due_reminders, expire_offers, reconcile_all

log = logging.getLogger("scheduler")


async def run_tick(sessionmaker, bot: Bot, confirm_hours: int) -> int:
    notes = []
    async with sessionmaker() as session:
        notes += await expire_offers(session, confirm_hours)
        notes += await reconcile_all(session, confirm_hours)
        notes += await due_reminders(session)
    return await send_notes(bot, notes)


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

    log.info("Планировщик запущен, тик каждые %s сек.", config.scheduler_tick_seconds)
    try:
        while True:
            try:
                sent = await run_tick(sessionmaker, bot, config.waitlist_confirm_hours)
                if sent:
                    log.info("Отправлено уведомлений: %s", sent)
            except Exception:
                log.exception("Ошибка тика планировщика")
            await asyncio.sleep(config.scheduler_tick_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
