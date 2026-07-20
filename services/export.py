"""Экспорт регистраций в CSV (UTF-8 с BOM — корректно открывается в Excel)."""
from __future__ import annotations

import csv
import io

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RegStatus, Registration
from services.timeutil import format_date_ru, format_time_ru

STATUS_RU = {
    RegStatus.CONFIRMED: "Записан",
    RegStatus.WAITLIST: "Лист ожидания",
    RegStatus.CANCELLED: "Отменена",
}


async def registrations_csv(session: AsyncSession, event_id: int | None = None) -> bytes:
    query = sa.select(Registration).order_by(
        Registration.event_id, Registration.created_at
    )
    if event_id is not None:
        query = query.where(Registration.event_id == event_id)
    regs = (await session.execute(query)).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "Мероприятие", "Дата", "Время", "Локация", "Статус",
            "Позиция в очереди", "Имя", "Фамилия", "Телефон", "Email",
            "Telegram ID", "Дата записи (UTC)",
        ]
    )
    for reg in regs:
        event, loc, user = reg.event, reg.location, reg.user
        writer.writerow(
            [
                event.title,
                format_date_ru(event.starts_at, loc.timezone),
                format_time_ru(event.starts_at, loc.timezone),
                loc.name,
                STATUS_RU.get(reg.status, reg.status),
                reg.queue_pos or "",
                user.name or "",
                user.surname or "",
                user.phone or "",
                user.email or "",
                user.telegram_id,
                reg.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )
    return buf.getvalue().encode("utf-8-sig")
