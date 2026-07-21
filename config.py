"""Конфигурация приложения из переменных окружения."""
import os
from dataclasses import dataclass

PHOTO_MODE_SINGLE = "single_link"
PHOTO_MODE_PER_EVENT = "per_event"


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    bot_token: str
    telegram_proxy: str
    database_url: str
    admin_ids: tuple[int, ...]
    support_chat_id: int
    privacy_policy_url: str
    consent_version: str
    photo_mode: str  # single_link | per_event
    photos_archive_url: str
    waitlist_confirm_hours: int
    scheduler_tick_seconds: int
    default_timezone: str


def load_config() -> Config:
    admin_ids = tuple(
        int(x)
        for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
        if x
    )
    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip(),
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./zabeg.db"),
        admin_ids=admin_ids,
        support_chat_id=_get_int("SUPPORT_CHAT_ID", 0),
        privacy_policy_url=os.getenv("PRIVACY_POLICY_URL", "https://example.com/privacy"),
        consent_version=os.getenv("CONSENT_VERSION", "1"),
        photo_mode=os.getenv("PHOTO_MODE", PHOTO_MODE_SINGLE),
        photos_archive_url=os.getenv("PHOTOS_ARCHIVE_URL", ""),
        waitlist_confirm_hours=_get_int("WAITLIST_CONFIRM_HOURS", 2),
        scheduler_tick_seconds=_get_int("SCHEDULER_TICK_SECONDS", 30),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow"),
    )
