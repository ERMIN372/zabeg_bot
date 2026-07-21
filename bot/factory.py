"""Создание экземпляра Bot с единой конфигурацией.

HTTP-сессия принудительно работает по IPv4 (`family=AF_INET`): в docker-сети
IPv6 недоступен, а DNS отдаёт AAAA-запись api.telegram.org первой, из-за чего
без этого получаем "Network is unreachable" / "Cannot assign requested address".
Фикс на уровне приложения не зависит от резолвера ОС и пересборки образа.
"""
import socket

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode


def make_bot(token: str) -> Bot:
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET  # только IPv4
    return Bot(
        token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
