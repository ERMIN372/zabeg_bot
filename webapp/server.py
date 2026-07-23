"""HTTP-сервер mini app: REST-API всей админки + отдача фронтенда.

Каждый запрос к /api/* проходит проверку подписи Telegram initData и членства
в ADMIN_IDS (см. webapp/auth.py). Фронтенд (webapp/static) отдаётся статикой.

Уведомления участникам (отмена/перенос/раздача мест из листа ожидания)
отправляются через тот же Bot, что и у поллинга — переиспользуем services.
Выгрузка CSV доставляется админу личным сообщением от бота: это надёжнее,
чем скачивание файла внутри webview Telegram.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa
from aiohttp import web
from aiogram.types import BufferedInputFile

from config import Config
from db.models import (
    Event,
    EventKind,
    EventStatus,
    Location,
    RegStatus,
    Registration,
    User,
)
from services import registration as reg_service
from services.content import KEY_ABOUT, KEY_LABELS, get_content, set_content
from services.export import registrations_csv
from services.notify import Note, send_notes
from services.timeutil import (
    format_date_ru,
    format_time_ru,
    is_valid_timezone,
    parse_local,
    to_local,
    utcnow,
)
from webapp.auth import admin_from_init_data

log = logging.getLogger("webapp")

STATIC_DIR = Path(__file__).parent / "static"

VALID_KINDS = {EventKind.RUN, EventKind.SIMPLE}
STATUS_LABELS = {
    EventStatus.ACTIVE: "Активно",
    EventStatus.CANCELLED: "Отменено",
    EventStatus.POSTPONED: "Перенесено",
}
REG_STATUS_LABELS = {
    RegStatus.CONFIRMED: "Записан",
    RegStatus.WAITLIST: "Лист ожидания",
    RegStatus.CANCELLED: "Отменён",
}


# --- Сериализация -----------------------------------------------------------

async def _location_dict(session, loc: Location) -> dict:
    waitlist = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Registration).where(
                Registration.location_id == loc.id,
                Registration.status == RegStatus.WAITLIST,
            )
        )
    ).scalar_one()
    return {
        "id": loc.id,
        "event_id": loc.event_id,
        "name": loc.name,
        "address": loc.address,
        "capacity": loc.capacity,
        "taken": loc.taken,
        "free": max(0, loc.capacity - loc.taken),
        "waitlist": waitlist,
        "timezone": loc.timezone,
    }


async def _event_dict(session, event: Event, with_locations: bool = True) -> dict:
    data = {
        "id": event.id,
        "title": event.title,
        "description": event.description or "",
        "kind": event.kind,
        "kind_label": "Пробежка" if event.kind == EventKind.RUN else "Закрытое",
        "status": event.status,
        "status_label": STATUS_LABELS.get(event.status, event.status),
        "timezone": event.timezone,
        "date": to_local(event.starts_at, event.timezone).strftime("%d.%m.%Y"),
        "time": format_time_ru(event.starts_at, event.timezone),
        "date_human": format_date_ru(event.starts_at, event.timezone),
        "album_url": event.album_url or "",
        "has_image": bool(event.image_file_id),
    }
    if with_locations:
        data["locations"] = [
            await _location_dict(session, loc) for loc in event.locations
        ]
    return data


# --- Вспомогательное --------------------------------------------------------

def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _read_json(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _sessionmaker(request):
    return request.app["sessionmaker"]


def _config(request) -> Config:
    return request.app["config"]


# --- Middleware авторизации -------------------------------------------------

@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)

    auth = request.headers.get("Authorization", "")
    init_data = auth[4:] if auth.startswith("tma ") else ""
    config = _config(request)
    user = admin_from_init_data(init_data, config.bot_token, config.admin_ids)
    if user is None:
        return _err("Доступ только для администраторов", status=401)
    request["tg_user"] = user
    return await handler(request)


# --- Служебные эндпоинты -----------------------------------------------------

async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def api_me(request: web.Request) -> web.Response:
    config = _config(request)
    user = request["tg_user"]
    return web.json_response(
        {
            "user": {"id": user.get("id"), "name": user.get("first_name", "")},
            "default_timezone": config.default_timezone,
            "kinds": [
                {"value": EventKind.RUN, "label": "Пробежка (несколько локаций)"},
                {"value": EventKind.SIMPLE, "label": "Закрытое мероприятие (одна локация)"},
            ],
        }
    )


async def api_stats(request: web.Request) -> web.Response:
    async with _sessionmaker(request)() as session:
        active = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Event).where(
                    Event.status == EventStatus.ACTIVE
                )
            )
        ).scalar_one()
        confirmed = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Registration).where(
                    Registration.status == RegStatus.CONFIRMED
                )
            )
        ).scalar_one()
        waitlist = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Registration).where(
                    Registration.status == RegStatus.WAITLIST
                )
            )
        ).scalar_one()
    return web.json_response(
        {"active_events": active, "confirmed": confirmed, "waitlist": waitlist}
    )


# --- Мероприятия ------------------------------------------------------------

async def api_events(request: web.Request) -> web.Response:
    include_cancelled = request.query.get("include_cancelled") == "1"
    async with _sessionmaker(request)() as session:
        query = sa.select(Event).order_by(Event.starts_at.desc())
        if not include_cancelled:
            query = query.where(Event.status != EventStatus.CANCELLED)
        events = (await session.execute(query.limit(50))).scalars().all()
        data = [await _event_dict(session, e, with_locations=False) for e in events]
    return web.json_response({"events": data})


async def api_event(request: web.Request) -> web.Response:
    event_id = int(request.match_info["id"])
    async with _sessionmaker(request)() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return _err("Мероприятие не найдено", status=404)
        return web.json_response(await _event_dict(session, event))


async def api_create_event(request: web.Request) -> web.Response:
    body = await _read_json(request)
    config = _config(request)
    title = (body.get("title") or "").strip()
    if not title:
        return _err("Укажите название")
    kind = body.get("kind", EventKind.RUN)
    if kind not in VALID_KINDS:
        return _err("Неизвестный тип мероприятия")
    tz = (body.get("timezone") or config.default_timezone).strip()
    if not is_valid_timezone(tz):
        return _err("Некорректный часовой пояс")
    try:
        starts_at = parse_local(body.get("date", ""), body.get("time", ""), tz)
    except ValueError:
        return _err("Проверьте дату (ДД.ММ.ГГГГ) и время (ЧЧ:ММ)")

    async with _sessionmaker(request)() as session:
        event = Event(
            title=title,
            description=(body.get("description") or "").strip(),
            starts_at=starts_at,
            timezone=tz,
            kind=kind,
            status=EventStatus.ACTIVE,
        )
        session.add(event)
        await session.commit()
        # у нового мероприятия ещё нет локаций — не трогаем relationship
        # (иначе ленивая загрузка вне async-контекста)
        return web.json_response(
            await _event_dict(session, event, with_locations=False)
        )


async def api_update_event(request: web.Request) -> web.Response:
    event_id = int(request.match_info["id"])
    body = await _read_json(request)
    async with _sessionmaker(request)() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return _err("Мероприятие не найдено", status=404)

        if "title" in body:
            title = (body.get("title") or "").strip()
            if not title:
                return _err("Название не может быть пустым")
            event.title = title
        if "description" in body:
            event.description = (body.get("description") or "").strip()
        if "album_url" in body:
            event.album_url = (body.get("album_url") or "").strip() or None
        if body.get("remove_image"):
            event.image_file_id = None

        new_tz = body.get("timezone")
        new_date = body.get("date")
        new_time = body.get("time")
        if new_tz or new_date or new_time:
            tz = (new_tz or event.timezone).strip()
            if not is_valid_timezone(tz):
                return _err("Некорректный часовой пояс")
            local = to_local(event.starts_at, event.timezone)
            date_str = (new_date or local.strftime("%d.%m.%Y")).strip()
            time_str = (new_time or local.strftime("%H:%M")).strip()
            try:
                event.starts_at = parse_local(date_str, time_str, tz)
            except ValueError:
                return _err("Проверьте дату (ДД.ММ.ГГГГ) и время (ЧЧ:ММ)")
            event.timezone = tz

        await session.commit()
        return web.json_response(await _event_dict(session, event))


async def api_cancel_event(request: web.Request) -> web.Response:
    event_id = int(request.match_info["id"])
    config = _config(request)
    async with _sessionmaker(request)() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return _err("Мероприятие не найдено", status=404)
        event.status = EventStatus.CANCELLED
        await session.commit()
        notes = await reg_service.event_participant_notes(
            session,
            event.id,
            f"❌ К сожалению, мероприятие «{event.title}» отменено. "
            "Приносим извинения — будем ждать вас на следующих встречах!",
        )
    sent = await send_notes(request.app["bot"], notes)
    return web.json_response({"ok": True, "notified": sent})


async def api_move_event(request: web.Request) -> web.Response:
    event_id = int(request.match_info["id"])
    body = await _read_json(request)
    async with _sessionmaker(request)() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return _err("Мероприятие не найдено", status=404)
        try:
            new_starts = parse_local(
                body.get("date", ""), body.get("time", ""), event.timezone
            )
        except ValueError:
            return _err("Проверьте дату (ДД.ММ.ГГГГ) и время (ЧЧ:ММ)")
        event.starts_at = new_starts
        event.status = EventStatus.ACTIVE
        await session.execute(
            sa.update(Registration)
            .where(Registration.event_id == event.id)
            .values(reminded_24h_at=None, reminded_3h_at=None)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        notes = await reg_service.event_participant_notes(
            session,
            event.id,
            f"⚠️ Мероприятие «{event.title}» перенесено!\n\n"
            f"Новая дата: {format_date_ru(event.starts_at, event.timezone)}\n"
            f"Время: {format_time_ru(event.starts_at, event.timezone)} (местное)\n\n"
            "Ваша запись сохраняется. Если не сможете прийти — отмените её в «Мои записи».",
            image_file_id=event.image_file_id,
        )
    sent = await send_notes(request.app["bot"], notes)
    return web.json_response({"ok": True, "notified": sent})


async def api_delete_event(request: web.Request) -> web.Response:
    event_id = int(request.match_info["id"])
    async with _sessionmaker(request)() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return _err("Мероприятие не найдено", status=404)
        await session.execute(
            sa.delete(Registration).where(Registration.event_id == event.id)
        )
        await session.execute(
            sa.delete(Location).where(Location.event_id == event.id)
        )
        await session.delete(event)
        await session.commit()
    return web.json_response({"ok": True})


# --- Локации ----------------------------------------------------------------

async def api_add_location(request: web.Request) -> web.Response:
    event_id = int(request.match_info["id"])
    body = await _read_json(request)
    name = (body.get("name") or "").strip()
    if not name:
        return _err("Укажите название локации")
    address = (body.get("address") or "").strip()
    try:
        capacity = int(body.get("capacity"))
        if capacity < 0:
            raise ValueError
    except (TypeError, ValueError):
        return _err("Лимит мест — целое число ≥ 0")
    async with _sessionmaker(request)() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return _err("Мероприятие не найдено", status=404)
        tz = (body.get("timezone") or event.timezone).strip()
        if not is_valid_timezone(tz):
            return _err("Некорректный часовой пояс")
        loc = Location(
            event_id=event.id,
            name=name,
            address=address,
            capacity=capacity,
            timezone=tz,
        )
        session.add(loc)
        await session.commit()
        return web.json_response(await _location_dict(session, loc))


async def api_update_location(request: web.Request) -> web.Response:
    loc_id = int(request.match_info["id"])
    body = await _read_json(request)
    config = _config(request)
    notes = []
    async with _sessionmaker(request)() as session:
        loc = await session.get(Location, loc_id)
        if loc is None:
            return _err("Локация не найдена", status=404)
        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name:
                return _err("Название не может быть пустым")
            loc.name = name
        if "address" in body:
            loc.address = (body.get("address") or "").strip()
        if "timezone" in body:
            tz = (body.get("timezone") or "").strip()
            if not is_valid_timezone(tz):
                return _err("Некорректный часовой пояс")
            loc.timezone = tz
        capacity_changed = False
        if "capacity" in body:
            try:
                capacity = int(body.get("capacity"))
                if capacity < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return _err("Лимит мест — целое число ≥ 0")
            capacity_changed = capacity != loc.capacity
            loc.capacity = capacity
        await session.commit()
        if capacity_changed:
            # лимит увеличили — раздаём места листу ожидания
            notes = await reg_service.reconcile_location(
                session, loc.id, config.waitlist_confirm_hours
            )
        result = await _location_dict(session, loc)
    sent = await send_notes(request.app["bot"], notes) if notes else 0
    result["notified"] = sent
    return web.json_response(result)


# --- Тексты разделов --------------------------------------------------------

async def api_texts(request: web.Request) -> web.Response:
    async with _sessionmaker(request)() as session:
        items = []
        for key, label in KEY_LABELS.items():
            text, image = await get_content(session, key)
            items.append(
                {
                    "key": key,
                    "label": label,
                    "text": text,
                    "has_image": bool(image),
                    "supports_image": key == KEY_ABOUT,
                }
            )
    return web.json_response({"texts": items})


async def api_update_text(request: web.Request) -> web.Response:
    key = request.match_info["key"]
    if key not in KEY_LABELS:
        return _err("Неизвестный раздел", status=404)
    body = await _read_json(request)
    text = body.get("text")
    if text is None or not str(text).strip():
        return _err("Текст не может быть пустым")
    async with _sessionmaker(request)() as session:
        await set_content(session, key, text=str(text))
    return web.json_response({"ok": True})


# --- Экспорт и обслуживание -------------------------------------------------

async def api_export(request: web.Request) -> web.Response:
    body = await _read_json(request)
    event_id = body.get("event_id")
    user = request["tg_user"]
    async with _sessionmaker(request)() as session:
        title = "все мероприятия"
        if event_id:
            event = await session.get(Event, int(event_id))
            if event is None:
                return _err("Мероприятие не найдено", status=404)
            title = event.title
        data = await registrations_csv(
            session, int(event_id) if event_id else None
        )
    filename = f"registrations_{event_id}.csv" if event_id else "registrations_all.csv"
    try:
        await request.app["bot"].send_document(
            user["id"],
            BufferedInputFile(data, filename=filename),
            caption=f"📤 Экспорт регистраций: {title}",
        )
    except Exception:
        log.exception("Не удалось отправить экспорт админу")
        return _err("Не удалось отправить файл в Telegram", status=502)
    return web.json_response({"ok": True})


async def api_recount(request: web.Request) -> web.Response:
    async with _sessionmaker(request)() as session:
        fixed = await reg_service.recount_taken(session)
    return web.json_response({"ok": True, "fixed": fixed})


# --- Пользователи -----------------------------------------------------------

def _reg_dict(reg: Registration) -> dict:
    return {
        "id": reg.id,
        "event_id": reg.event_id,
        "event_title": reg.event.title,
        "location": reg.location.name,
        "status": reg.status,
        "status_label": REG_STATUS_LABELS.get(reg.status, reg.status),
        "queue_pos": reg.queue_pos,
        "date": format_date_ru(reg.event.starts_at, reg.location.timezone),
    }


async def _active_reg_counts(session, user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = await session.execute(
        sa.select(Registration.user_id, sa.func.count())
        .where(
            Registration.user_id.in_(user_ids),
            Registration.status != RegStatus.CANCELLED,
        )
        .group_by(Registration.user_id)
    )
    return {uid: cnt for uid, cnt in rows.all()}


async def api_users(request: web.Request) -> web.Response:
    q = (request.query.get("q") or "").strip().casefold()
    async with _sessionmaker(request)() as session:
        all_users = (
            await session.execute(sa.select(User).order_by(User.created_at.desc()))
        ).scalars().all()
        # фильтр в Python: регистронезависимо и для кириллицы (SQL ILIKE
        # складывает регистр только для ASCII / зависит от локали БД)
        if q:
            def match(u: User) -> bool:
                haystack = " ".join(
                    x for x in (u.name, u.surname, u.phone, u.email) if x
                ).casefold()
                return q in haystack
            matched = [u for u in all_users if match(u)]
        else:
            matched = all_users
        truncated = len(matched) > 100
        users = matched[:100]
        counts = await _active_reg_counts(session, [u.id for u in users])
        data = [
            {
                "id": u.id,
                "telegram_id": u.telegram_id,
                "full_name": u.full_name,
                "phone": u.phone or "",
                "email": u.email or "",
                "created_at": u.created_at.strftime("%d.%m.%Y"),
                "complete": u.profile_complete,
                "reg_count": counts.get(u.id, 0),
            }
            for u in users
        ]
    return web.json_response({"users": data, "truncated": truncated})


async def api_user(request: web.Request) -> web.Response:
    uid = int(request.match_info["id"])
    async with _sessionmaker(request)() as session:
        user = await session.get(User, uid)
        if user is None:
            return _err("Пользователь не найден", status=404)
        regs = (
            await session.execute(
                sa.select(Registration)
                .where(Registration.user_id == uid)
                .order_by(Registration.created_at.desc())
            )
        ).scalars().all()
        return web.json_response(
            {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "full_name": user.full_name,
                "phone": user.phone or "",
                "email": user.email or "",
                "created_at": user.created_at.strftime("%d.%m.%Y"),
                "has_pdn_consent": user.has_pdn_consent,
                "registrations": [_reg_dict(r) for r in regs],
            }
        )


async def api_export_users(request: web.Request) -> web.Response:
    user = request["tg_user"]
    async with _sessionmaker(request)() as session:
        users = (
            await session.execute(sa.select(User).order_by(User.created_at))
        ).scalars().all()
        counts = await _active_reg_counts(session, [u.id for u in users])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Имя", "Фамилия", "Телефон", "Email", "Telegram ID",
                "Активных записей", "Дата регистрации (UTC)"])
    for u in users:
        w.writerow([
            u.name or "", u.surname or "", u.phone or "", u.email or "",
            u.telegram_id, counts.get(u.id, 0),
            u.created_at.strftime("%Y-%m-%d %H:%M"),
        ])
    data = buf.getvalue().encode("utf-8-sig")
    try:
        await request.app["bot"].send_document(
            user["id"],
            BufferedInputFile(data, filename="users.csv"),
            caption="👥 Экспорт пользователей",
        )
    except Exception:
        log.exception("Не удалось отправить экспорт пользователей")
        return _err("Не удалось отправить файл в Telegram", status=502)
    return web.json_response({"ok": True})


# --- Аналитика --------------------------------------------------------------

async def api_analytics(request: web.Request) -> web.Response:
    now = utcnow()
    async with _sessionmaker(request)() as session:
        async def count(stmt) -> int:
            return (await session.execute(stmt)).scalar_one()

        total_users = await count(sa.select(sa.func.count()).select_from(User))
        complete = await count(
            sa.select(sa.func.count()).select_from(User).where(
                User.name.is_not(None),
                User.surname.is_not(None),
                User.phone.is_not(None),
            )
        )
        ever_registered = await count(
            sa.select(sa.func.count(sa.distinct(Registration.user_id))).where(
                Registration.status != RegStatus.CANCELLED
            )
        )
        new_7d = await count(
            sa.select(sa.func.count()).select_from(User).where(
                User.created_at >= now - timedelta(days=7)
            )
        )
        new_30d = await count(
            sa.select(sa.func.count()).select_from(User).where(
                User.created_at >= now - timedelta(days=30)
            )
        )
        confirmed = await count(
            sa.select(sa.func.count()).select_from(Registration).where(
                Registration.status == RegStatus.CONFIRMED
            )
        )
        waitlist = await count(
            sa.select(sa.func.count()).select_from(Registration).where(
                Registration.status == RegStatus.WAITLIST
            )
        )
        cancelled = await count(
            sa.select(sa.func.count()).select_from(Registration).where(
                Registration.status == RegStatus.CANCELLED
            )
        )

        # рост пользователей за 14 дней
        since = now - timedelta(days=13)
        rows = await session.execute(
            sa.select(sa.func.date(User.created_at), sa.func.count())
            .where(User.created_at >= since)
            .group_by(sa.func.date(User.created_at))
        )
        by_day = {str(d): c for d, c in rows.all()}
        growth = []
        for i in range(13, -1, -1):
            day = (now - timedelta(days=i)).date()
            growth.append({"date": day.strftime("%d.%m"), "count": by_day.get(str(day), 0)})

        # топ локаций по подтверждённым записям
        loc_rows = await session.execute(
            sa.select(Location.name, sa.func.count(Registration.id))
            .join(Registration, Registration.location_id == Location.id)
            .where(Registration.status == RegStatus.CONFIRMED)
            .group_by(Location.id)
            .order_by(sa.func.count(Registration.id).desc())
            .limit(5)
        )
        top_locations = [{"name": n, "count": c} for n, c in loc_rows.all()]

        # ближайшие мероприятия с заполняемостью
        upcoming_events = (
            await session.execute(
                sa.select(Event)
                .where(Event.status == EventStatus.ACTIVE, Event.starts_at > now)
                .order_by(Event.starts_at)
                .limit(5)
            )
        ).scalars().all()
        upcoming = []
        for e in upcoming_events:
            cap = sum(l.capacity for l in e.locations)
            taken = sum(l.taken for l in e.locations)
            upcoming.append({
                "id": e.id,
                "title": e.title,
                "date": format_date_ru(e.starts_at, e.timezone),
                "taken": taken,
                "capacity": cap,
                "percent": round(taken / cap * 100) if cap else 0,
            })

    return web.json_response({
        "users": {
            "total": total_users, "complete": complete,
            "ever_registered": ever_registered, "new_7d": new_7d, "new_30d": new_30d,
        },
        "registrations": {"confirmed": confirmed, "waitlist": waitlist, "cancelled": cancelled},
        "growth": growth,
        "top_locations": top_locations,
        "upcoming": upcoming,
    })


# --- Участники мероприятия --------------------------------------------------

async def api_event_registrations(request: web.Request) -> web.Response:
    event_id = int(request.match_info["id"])
    async with _sessionmaker(request)() as session:
        event = await session.get(Event, event_id)
        if event is None:
            return _err("Мероприятие не найдено", status=404)
        regs = (
            await session.execute(
                sa.select(Registration)
                .where(
                    Registration.event_id == event_id,
                    Registration.status != RegStatus.CANCELLED,
                )
                .order_by(Registration.status, Registration.queue_pos, Registration.created_at)
            )
        ).scalars().all()
        items = [
            {
                "id": r.id,
                "full_name": r.user.full_name,
                "phone": r.user.phone or "",
                "location": r.location.name,
                "status": r.status,
                "status_label": REG_STATUS_LABELS.get(r.status, r.status),
                "queue_pos": r.queue_pos,
            }
            for r in regs
        ]
    return web.json_response({"registrations": items})


async def api_cancel_registration(request: web.Request) -> web.Response:
    reg_id = int(request.match_info["id"])
    config = _config(request)
    async with _sessionmaker(request)() as session:
        reg = await session.get(Registration, reg_id)
        if reg is None:
            return _err("Запись не найдена", status=404)
        if reg.status == RegStatus.CANCELLED:
            return _err("Запись уже отменена")
        user_chat = reg.user.telegram_id
        event_title = reg.event.title
        notes = await reg_service.cancel_registration(
            session, reg, config.waitlist_confirm_hours
        )
    notes.append(
        Note(
            chat_id=user_chat,
            text=f"Ваша запись на «{event_title}» отменена администратором. "
            "Если это ошибка — свяжитесь с поддержкой.",
        )
    )
    sent = await send_notes(request.app["bot"], notes)
    return web.json_response({"ok": True, "notified": sent})


# --- Рассылка ---------------------------------------------------------------

async def _broadcast_recipients(session, target: str, event_id) -> list[int]:
    if target == "event":
        if not event_id:
            return []
        rows = await session.execute(
            sa.select(sa.distinct(User.telegram_id))
            .join(Registration, Registration.user_id == User.id)
            .where(
                Registration.event_id == int(event_id),
                Registration.status != RegStatus.CANCELLED,
            )
        )
    else:
        rows = await session.execute(
            sa.select(User.telegram_id).where(User.telegram_id.is_not(None))
        )
    return [r for r in rows.scalars().all() if r]


async def api_broadcast(request: web.Request) -> web.Response:
    body = await _read_json(request)
    target = body.get("target", "all")
    event_id = body.get("event_id")
    async with _sessionmaker(request)() as session:
        recipients = await _broadcast_recipients(session, target, event_id)

    if body.get("preview"):
        return web.json_response({"count": len(recipients)})

    text = (body.get("text") or "").strip()
    if not text:
        return _err("Текст рассылки не может быть пустым")
    notes = [Note(chat_id=cid, text=text) for cid in recipients]
    sent = await send_notes(request.app["bot"], notes)
    return web.json_response({"ok": True, "sent": sent, "total": len(recipients)})


# --- Сборка приложения ------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


def build_app(config: Config, sessionmaker, bot) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app["config"] = config
    app["sessionmaker"] = sessionmaker
    app["bot"] = bot

    app.router.add_get("/health", health)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/events", api_events)
    app.router.add_post("/api/events", api_create_event)
    app.router.add_get("/api/events/{id}", api_event)
    app.router.add_patch("/api/events/{id}", api_update_event)
    app.router.add_delete("/api/events/{id}", api_delete_event)
    app.router.add_post("/api/events/{id}/cancel", api_cancel_event)
    app.router.add_post("/api/events/{id}/move", api_move_event)
    app.router.add_post("/api/events/{id}/locations", api_add_location)
    app.router.add_patch("/api/locations/{id}", api_update_location)
    app.router.add_get("/api/events/{id}/registrations", api_event_registrations)
    app.router.add_get("/api/texts", api_texts)
    app.router.add_patch("/api/texts/{key}", api_update_text)
    app.router.add_post("/api/export", api_export)
    app.router.add_post("/api/recount", api_recount)
    app.router.add_get("/api/users", api_users)
    app.router.add_get("/api/users/{id}", api_user)
    app.router.add_post("/api/users/export", api_export_users)
    app.router.add_get("/api/analytics", api_analytics)
    app.router.add_post("/api/registrations/{id}/cancel", api_cancel_registration)
    app.router.add_post("/api/broadcast", api_broadcast)

    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR)
    return app
