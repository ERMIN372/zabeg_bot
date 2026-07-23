"""Точка входа веб-сервера mini app (отдельный процесс, как планировщик).

Слушает WEBAPP_HOST:WEBAPP_PORT (по умолчанию 0.0.0.0:8080). HTTPS и домен
обеспечивает reverse-proxy снаружи (Caddy в docker-compose) — сам сервис
работает по http внутри сети.
"""
import asyncio
import logging

from aiohttp import web

from bot.factory import make_bot
from config import load_config
from db.base import make_engine, make_sessionmaker
from webapp.server import build_app

log = logging.getLogger("webapp")


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
    bot = make_bot(config.bot_token, config.telegram_proxy or None)

    app = build_app(config, sessionmaker, bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.webapp_host, config.webapp_port)
    await site.start()
    log.info("Mini app сервер на %s:%s", config.webapp_host, config.webapp_port)
    try:
        await asyncio.Event().wait()  # работаем до остановки контейнера
    finally:
        await runner.cleanup()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
