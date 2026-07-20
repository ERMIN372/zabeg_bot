"""Админ-панель: создание мероприятий, редактирование полей и локаций."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.panel import AdminFilter, show_admin_event_card
from bot.helpers import esc, safe_delete
from bot.keyboards import btn, kb
from bot.states import AdminCreateEvent, AdminEditField
from config import Config
from db.models import Event, EventKind, EventStatus, Location
from services import registration as reg_service
from services.notify import send_notes
from services.timeutil import (
    format_time_ru,
    is_valid_timezone,
    parse_local,
    to_local,
)

router = Router(name="admin_edit")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


# --- Создание мероприятия ----------------------------------------------------

@router.callback_query(F.data == "adm:new")
async def cb_new_event(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AdminCreateEvent.title)
    await cb.message.answer("Создание мероприятия.\n\n<b>Название?</b>")
    await cb.answer()


@router.message(AdminCreateEvent.title, F.text)
async def new_event_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AdminCreateEvent.description)
    await message.answer("<b>Описание?</b> (или «-», чтобы оставить пустым)")


@router.message(AdminCreateEvent.description, F.text)
async def new_event_description(message: Message, state: FSMContext):
    text = message.text.strip()
    await state.update_data(description="" if text == "-" else text)
    await state.set_state(AdminCreateEvent.date)
    await message.answer("<b>Дата?</b> (ДД.ММ.ГГГГ)")


@router.message(AdminCreateEvent.date, F.text)
async def new_event_date(message: Message, state: FSMContext):
    try:
        parse_local(message.text.strip(), "12:00", "UTC")
    except ValueError:
        await message.answer("Формат: ДД.ММ.ГГГГ, например 18.07.2026")
        return
    await state.update_data(date=message.text.strip())
    await state.set_state(AdminCreateEvent.time)
    await message.answer("<b>Время начала?</b> (ЧЧ:ММ, местное)")


@router.message(AdminCreateEvent.time, F.text)
async def new_event_time(message: Message, state: FSMContext, config: Config):
    try:
        parse_local("01.01.2030", message.text.strip(), "UTC")
    except ValueError:
        await message.answer("Формат: ЧЧ:ММ, например 09:00")
        return
    await state.update_data(time=message.text.strip())
    await state.set_state(AdminCreateEvent.tz)
    await message.answer(
        f"<b>Часовой пояс?</b> (например Europe/Moscow)\n"
        f"Отправьте «-», чтобы использовать {config.default_timezone}."
    )


@router.message(AdminCreateEvent.tz, F.text)
async def new_event_tz(message: Message, state: FSMContext, config: Config):
    tz = message.text.strip()
    if tz == "-":
        tz = config.default_timezone
    if not is_valid_timezone(tz):
        await message.answer("Не удалось распознать часовой пояс. Пример: Europe/Moscow")
        return
    await state.update_data(tz=tz)
    await state.set_state(AdminCreateEvent.image)
    await message.answer("<b>Картинка мероприятия?</b> Пришлите фото или «-», чтобы пропустить.")


@router.message(AdminCreateEvent.image, F.photo)
async def new_event_image(message: Message, state: FSMContext):
    await state.update_data(image_file_id=message.photo[-1].file_id)
    await _ask_kind(message, state)


@router.message(AdminCreateEvent.image, F.text == "-")
async def new_event_no_image(message: Message, state: FSMContext):
    await state.update_data(image_file_id=None)
    await _ask_kind(message, state)


async def _ask_kind(message: Message, state: FSMContext):
    await state.set_state(AdminCreateEvent.kind)
    await message.answer(
        "<b>Тип мероприятия?</b>",
        reply_markup=kb(
            [btn("🏃 Пробежка (несколько локаций)", "adm:kind:run")],
            [btn("🎟 Закрытое мероприятие (одна локация)", "adm:kind:simple")],
        ),
    )


@router.callback_query(AdminCreateEvent.kind, F.data.startswith("adm:kind:"))
async def new_event_kind(cb: CallbackQuery, state: FSMContext, session):
    kind = EventKind.RUN if cb.data.endswith(":run") else EventKind.SIMPLE
    data = await state.get_data()
    event = Event(
        title=data["title"],
        description=data["description"],
        starts_at=parse_local(data["date"], data["time"], data["tz"]),
        timezone=data["tz"],
        image_file_id=data.get("image_file_id"),
        kind=kind,
        status=EventStatus.ACTIVE,
    )
    session.add(event)
    await session.commit()
    await state.update_data(event_id=event.id, kind=kind, creating=True)
    await state.set_state(AdminCreateEvent.loc_name)
    await cb.message.answer(
        "Мероприятие создано. Теперь добавим "
        + ("локации." if kind == EventKind.RUN else "место проведения.")
        + "\n\n<b>Название локации?</b>"
    )
    await cb.answer()


# --- Добавление локации (в мастере создания и отдельно) ----------------------

@router.callback_query(F.data.startswith("adm:addloc:"))
async def cb_add_location(cb: CallbackQuery, state: FSMContext, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.clear()
    await state.update_data(event_id=event.id, kind=event.kind, creating=False)
    await state.set_state(AdminCreateEvent.loc_name)
    await cb.message.answer("<b>Название локации?</b>")
    await cb.answer()


@router.message(AdminCreateEvent.loc_name, F.text)
async def loc_name(message: Message, state: FSMContext):
    await state.update_data(loc_name=message.text.strip())
    await state.set_state(AdminCreateEvent.loc_address)
    await message.answer("<b>Адрес локации?</b>")


@router.message(AdminCreateEvent.loc_address, F.text)
async def loc_address(message: Message, state: FSMContext):
    await state.update_data(loc_address=message.text.strip())
    await state.set_state(AdminCreateEvent.loc_capacity)
    await message.answer("<b>Лимит участников?</b> (число)")


@router.message(AdminCreateEvent.loc_capacity, F.text)
async def loc_capacity(message: Message, state: FSMContext, session, config: Config):
    try:
        capacity = int(message.text.strip())
        if capacity < 0:
            raise ValueError
    except ValueError:
        await message.answer("Отправьте целое число, например 50.")
        return
    data = await state.get_data()
    event = await session.get(Event, data["event_id"])
    if event is None:
        await state.clear()
        await message.answer("Мероприятие не найдено.")
        return
    session.add(
        Location(
            event_id=event.id,
            name=data["loc_name"],
            address=data["loc_address"],
            capacity=capacity,
            timezone=event.timezone,
        )
    )
    await session.commit()

    if data.get("creating") and data.get("kind") == EventKind.RUN:
        await message.answer(
            f"📍 Локация «{esc(data['loc_name'])}» добавлена.",
            reply_markup=kb(
                [btn("➕ Добавить ещё локацию", "adm:new:moreloc")],
                [btn("✅ Готово", "adm:new:done")],
            ),
        )
    else:
        await state.clear()
        await message.answer(
            f"✅ Локация «{esc(data['loc_name'])}» добавлена.",
            reply_markup=kb([btn("⬅️ К мероприятию", f"adm:e:{event.id}")]),
        )


@router.callback_query(F.data == "adm:new:moreloc")
async def cb_more_loc(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("event_id"):
        await cb.answer("Мастер создания уже завершён", show_alert=True)
        return
    await state.set_state(AdminCreateEvent.loc_name)
    await cb.message.answer("<b>Название следующей локации?</b>")
    await cb.answer()


@router.callback_query(F.data == "adm:new:done")
async def cb_new_done(cb: CallbackQuery, state: FSMContext, session):
    data = await state.get_data()
    event_id = data.get("event_id")
    await state.clear()
    event = await session.get(Event, event_id) if event_id else None
    await safe_delete(cb.message)
    if event is not None:
        await cb.message.answer("✅ Мероприятие опубликовано!")
        await show_admin_event_card(cb.message, session, event)
    await cb.answer()


# --- Редактирование полей мероприятия ---------------------------------------

EVENT_FIELDS = {
    "title": "Название",
    "description": "Описание",
    "date": "Дата",
    "time": "Время",
    "timezone": "Часовой пояс",
    "image": "Картинка",
    "album_url": "Ссылка на альбом",
}
LOCATION_FIELDS = {
    "name": "Название",
    "address": "Адрес",
    "capacity": "Лимит мест",
    "timezone": "Часовой пояс",
}

FIELD_PROMPTS = {
    "date": "Отправьте новую дату (ДД.ММ.ГГГГ).",
    "time": "Отправьте новое время (ЧЧ:ММ, местное).",
    "timezone": "Отправьте часовой пояс, например Europe/Moscow.",
    "image": "Пришлите новое фото (или «-», чтобы убрать картинку).",
    "album_url": "Отправьте ссылку на альбом (или «-», чтобы убрать).",
    "capacity": "Отправьте новый лимит участников (число).",
}


@router.callback_query(F.data.startswith("adm:edit:"))
async def cb_edit_event(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    rows = [
        [btn(label, f"adm:set:{event.id}:{field}")]
        for field, label in EVENT_FIELDS.items()
    ]
    rows.append([btn("⬅️ Назад", f"adm:e:{event.id}")])
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Редактирование «{esc(event.title)}». Что изменить?", reply_markup=kb(*rows)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:set:"))
async def cb_set_event_field(cb: CallbackQuery, session, state: FSMContext):
    _, _, event_id, field = cb.data.split(":")
    if field not in EVENT_FIELDS:
        await cb.answer("Неизвестное поле", show_alert=True)
        return
    await state.set_state(AdminEditField.value)
    await state.update_data(target="event", target_id=int(event_id), field=field)
    await cb.message.answer(
        FIELD_PROMPTS.get(field, f"Отправьте новое значение поля «{EVENT_FIELDS[field]}».")
    )
    await cb.answer()


# --- Локации: карточка и редактирование -------------------------------------

@router.callback_query(F.data.startswith("adm:locs:"))
async def cb_admin_locations(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    rows = [
        [btn(f"{loc.name} — {loc.taken}/{loc.capacity}", f"adm:l:{loc.id}")]
        for loc in event.locations
    ]
    rows.append([btn("➕ Добавить локацию", f"adm:addloc:{event.id}")])
    rows.append([btn("⬅️ Назад", f"adm:e:{event.id}")])
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Локации «{esc(event.title)}»:", reply_markup=kb(*rows)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:l:"))
async def cb_admin_location(cb: CallbackQuery, session):
    loc = await session.get(Location, int(cb.data.split(":")[2]))
    if loc is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    rows = [
        [btn(label, f"adm:lset:{loc.id}:{field}")]
        for field, label in LOCATION_FIELDS.items()
    ]
    rows.append([btn("⬅️ Назад", f"adm:locs:{loc.event_id}")])
    await safe_delete(cb.message)
    await cb.message.answer(
        f"📍 <b>{esc(loc.name)}</b>\n"
        f"Адрес: {esc(loc.address)}\n"
        f"Занято: {loc.taken}/{loc.capacity}\n"
        f"Часовой пояс: {loc.timezone}\n\n"
        "Что изменить?",
        reply_markup=kb(*rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:lset:"))
async def cb_set_location_field(cb: CallbackQuery, state: FSMContext):
    _, _, loc_id, field = cb.data.split(":")
    if field not in LOCATION_FIELDS:
        await cb.answer("Неизвестное поле", show_alert=True)
        return
    await state.set_state(AdminEditField.value)
    await state.update_data(target="location", target_id=int(loc_id), field=field)
    await cb.message.answer(
        FIELD_PROMPTS.get(field, f"Отправьте новое значение поля «{LOCATION_FIELDS[field]}».")
    )
    await cb.answer()


# --- Приём нового значения поля ---------------------------------------------

@router.message(AdminEditField.value, F.photo)
async def edit_field_photo(message: Message, state: FSMContext, session):
    data = await state.get_data()
    if data.get("target") != "event" or data.get("field") != "image":
        await message.answer("Ожидался текст. Попробуйте ещё раз.")
        return
    event = await session.get(Event, data["target_id"])
    if event is None:
        await state.clear()
        await message.answer("Мероприятие не найдено.")
        return
    event.image_file_id = message.photo[-1].file_id
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ Картинка обновлена", reply_markup=kb([btn("⬅️ К мероприятию", f"adm:e:{event.id}")])
    )


@router.message(AdminEditField.value, F.text)
async def edit_field_text(
    message: Message, state: FSMContext, session, config: Config
):
    data = await state.get_data()
    value = message.text.strip()
    target, field = data.get("target"), data.get("field")

    if target == "event":
        event = await session.get(Event, data["target_id"])
        if event is None:
            await state.clear()
            await message.answer("Мероприятие не найдено.")
            return
        try:
            if field == "title":
                event.title = value
            elif field == "description":
                event.description = "" if value == "-" else value
            elif field == "album_url":
                event.album_url = None if value == "-" else value
            elif field == "image":
                if value == "-":
                    event.image_file_id = None
                else:
                    await message.answer("Пришлите фото или «-», чтобы убрать картинку.")
                    return
            elif field == "timezone":
                if not is_valid_timezone(value):
                    await message.answer("Не удалось распознать часовой пояс. Пример: Europe/Moscow")
                    return
                # сохраняем тот же локальный момент времени в новом поясе
                local = to_local(event.starts_at, event.timezone)
                event.timezone = value
                event.starts_at = parse_local(
                    local.strftime("%d.%m.%Y"), local.strftime("%H:%M"), value
                )
            elif field == "date":
                old_time = format_time_ru(event.starts_at, event.timezone)
                event.starts_at = parse_local(value, old_time, event.timezone)
            elif field == "time":
                local = to_local(event.starts_at, event.timezone)
                event.starts_at = parse_local(local.strftime("%d.%m.%Y"), value, event.timezone)
        except ValueError:
            await message.answer("Неверный формат, попробуйте ещё раз.")
            return
        await session.commit()
        await state.clear()
        await message.answer(
            "✅ Сохранено", reply_markup=kb([btn("⬅️ К мероприятию", f"adm:e:{event.id}")])
        )
        return

    if target == "location":
        loc = await session.get(Location, data["target_id"])
        if loc is None:
            await state.clear()
            await message.answer("Локация не найдена.")
            return
        if field == "name":
            loc.name = value
        elif field == "address":
            loc.address = value
        elif field == "timezone":
            if not is_valid_timezone(value):
                await message.answer("Не удалось распознать часовой пояс. Пример: Europe/Moscow")
                return
            loc.timezone = value
        elif field == "capacity":
            try:
                capacity = int(value)
                if capacity < 0:
                    raise ValueError
            except ValueError:
                await message.answer("Отправьте целое число, например 50.")
                return
            loc.capacity = capacity
        await session.commit()
        await state.clear()
        # лимит могли увеличить — раздаём места листу ожидания
        if field == "capacity":
            notes = await reg_service.reconcile_location(
                session, loc.id, config.waitlist_confirm_hours
            )
            await send_notes(message.bot, notes)
        await message.answer(
            "✅ Сохранено", reply_markup=kb([btn("⬅️ К локации", f"adm:l:{loc.id}")])
        )
        return

    await state.clear()
    await message.answer("Сессия редактирования сброшена, начните заново.")
