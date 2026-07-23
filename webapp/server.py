"""HTTP-сервер mini app: REST-API всей админки + отдача фронтенда.

Каждый запрос к /api/* проходит проверку подписи Telegram initData и членства
в ADMIN_IDS (см. webapp/auth.py). Фронтенд (webapp/static) отдаётся статикой.

Уведомления участникам (отмена/перенос/раздача мест из листа ожидания)
отправляются через тот же Bot, что и у поллинга — переиспользуем services.
Выгрузка CSV доставляется админу личным сообщением от бота: это надёжнее,
чем скачивание файла внутри webview Telegram.
"""
from __future__ import annotations

import logging
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
)
from services import registration as reg_service
from services.content import KEY_ABOUT, KEY_LABELS, get_content, set_content
from services.export import registrations_csv
from services.notify import send_notes
from services.timeutil import (
    format_date_ru,
    format_time_ru,
    is_valid_timezone,
    parse_local,
    to_local,
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
    app.router.add_get("/api/texts", api_texts)
    app.router.add_patch("/api/texts/{key}", api_update_text)
    app.router.add_post("/api/export", api_export)
    app.router.add_post("/api/recount", api_recount)

    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC_DIR)
    return app
