"""Работа со временем: всё в БД хранится в наивном UTC, отображается в поясе локации."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt_utc: datetime, tz_name: str) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))


def parse_local(date_str: str, time_str: str, tz_name: str) -> datetime:
    """'18.07.2026' + '09:00' в поясе tz_name -> наивный UTC."""
    local = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%d.%m.%Y %H:%M")
    local = local.replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def format_date_ru(dt_utc: datetime, tz_name: str) -> str:
    """Формат «18 июля, суббота» в поясе tz_name."""
    local = to_local(dt_utc, tz_name)
    return f"{local.day} {MONTHS_GEN[local.month - 1]}, {WEEKDAYS[local.weekday()]}"


def format_time_ru(dt_utc: datetime, tz_name: str) -> str:
    return to_local(dt_utc, tz_name).strftime("%H:%M")


def is_valid_timezone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except Exception:
        return False
