"""Создание экземпляра Bot с единой конфигурацией.

Сеть до Telegram:
* Если задан TELEGRAM_PROXY — весь трафик к api.telegram.org идёт через
  прокси (SOCKS5/SOCKS4/HTTP). Нужно, когда прямой доступ к Telegram с хоста
  заблокирован/троттлится (DPI): TCP то проходит, то отваливается, TLS к
  api.telegram.org не завершается.
* Если прокси нет — HTTP-сессия принудительно работает по IPv4
  (`family=AF_INET`): в docker-сети IPv6 недоступен, а DNS отдаёт AAAA первой,
  из-за чего иначе получаем "Network is unreachable" / "Cannot assign
  requested address".
"""
import socket

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode


def make_bot(token: str, proxy: str | None = None) -> Bot:
    if proxy:
        # aiogram сам поднимает ProxyConnector (через aiohttp-socks);
        # до Telegram ходит прокси, поэтому форс IPv4 здесь не нужен.
        session = AiohttpSession(proxy=proxy)
    else:
        session = AiohttpSession()
        session._connector_init["family"] = socket.AF_INET  # только IPv4
    return Bot(
        token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
