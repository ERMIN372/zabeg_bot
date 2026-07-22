"""Сценарии 3–6: просмотр мероприятий, выбор локации, запись, лист ожидания."""
import sqlalchemy as sa
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.profile import start_profile
from bot.helpers import (
    esc,
    plural_ru,
    safe_delete,
    seats_phrase,
    send_card,
    reg_details_text,
)
from bot.keyboards import btn, kb, menu_btn_row
from config import Config
from db.models import Event, EventKind, EventStatus, Location, RegStatus, Registration, User
from services import registration as reg_service
from services.notify import Note, send_notes
from services.registration import ConfirmResult
from services.timeutil import format_date_ru, format_time_ru, utcnow

router = Router(name="events")


async def _upcoming_events(session) -> list[Event]:
    return (
        await session.execute(
            sa.select(Event)
            .where(Event.status == EventStatus.ACTIVE, Event.starts_at > utcnow())
            .order_by(Event.starts_at)
        )
    ).scalars().all()


def _event_card_text(event: Event) -> str:
    parts = [f"<b>{esc(event.title)}</b>"]
    if event.description:
        parts += ["", esc(event.description)]
    parts += [
        "",
        f"🗓 {format_date_ru(event.starts_at, event.timezone)}",
        f"🕒 {format_time_ru(event.starts_at, event.timezone)}",
    ]
    return "\n".join(parts)


async def _show_event_card(message: Message, session, idx: int) -> None:
    events = await _upcoming_events(session)
    if not events:
        await message.answer(
            "Пока нет запланированных мероприятий. Загляните позже! 🏃",
            reply_markup=kb(menu_btn_row()),
        )
        return
    idx = max(0, min(idx, len(events) - 1))
    event = events[idx]

    rows = [[btn("✅ Записаться", f"ev:reg:{event.id}")]]
    nav = []
    if idx > 0:
        nav.append(btn("◀️ Предыдущее", f"ev:page:{idx - 1}"))
    if idx < len(events) - 1:
        nav.append(btn("Следующее ▶️", f"ev:page:{idx + 1}"))
    if nav:
        rows.append(nav)
    rows.append(menu_btn_row())

    text = _event_card_text(event)
    if len(events) > 1:
        text += f"\n\nМероприятие {idx + 1} из {len(events)}"
    await send_card(message, text, event.image_file_id, kb(*rows))


@router.message(Command("events"))
async def cmd_events(message: Message, session, state: FSMContext):
    await state.clear()
    await _show_event_card(message, session, 0)


@router.callback_query(F.data == "menu:events")
async def cb_events(cb: CallbackQuery, session, state: FSMContext):
    await state.clear()
    await safe_delete(cb.message)
    await _show_event_card(cb.message, session, 0)
    await cb.answer()


@router.callback_query(F.data.startswith("ev:page:"))
async def cb_events_page(cb: CallbackQuery, session):
    idx = int(cb.data.split(":")[2])
    await safe_delete(cb.message)
    await _show_event_card(cb.message, session, idx)
    await cb.answer()


# --- Запись: точка входа -----------------------------------------------------

async def _get_active_event(session, event_id: int) -> Event | None:
    event = await session.get(Event, event_id)
    if event is None or event.status != EventStatus.ACTIVE or event.starts_at <= utcnow():
        return None
    return event


async def _ensure_ready(
    cb: CallbackQuery, session, db_user: User, config: Config,
    state: FSMContext, after: str,
) -> bool:
    """Заполненная карточка обязательна для записи. Согласие на ПДн фиксируется
    внутри анкеты (в момент «Поделиться контактом»), поэтому заполненный
    профиль => согласие уже получено."""
    if not db_user.profile_complete:
        await start_profile(cb.message, state, after, session, config)
        await cb.answer()
        return False
    return True


def _already_text(reg: Registration) -> str:
    if reg.status == RegStatus.WAITLIST:
        return (
            "Вы уже в листе ожидания на эту тренировку.\n"
            f"Локация: {esc(reg.location.name)}."
        )
    return (
        "Вы уже записаны на эту тренировку.\n"
        f"Ваша локация: {esc(reg.location.name)}."
    )


def _already_kb(reg: Registration):
    rows = [[btn("👀 Посмотреть запись", f"reg:view:{reg.id}")]]
    if reg.status == RegStatus.CONFIRMED and reg.event.kind == EventKind.RUN:
        rows.append([btn("🔄 Сменить локацию", f"reg:chloc:{reg.id}")])
    rows.append(menu_btn_row())
    return kb(*rows)


async def _show_location_choice(message: Message, session, event: Event) -> None:
    rows = []
    for loc in event.locations:
        free = await reg_service.free_seats(session, loc.id)
        if free > 0:
            rows.append(
                [btn(f"{loc.name} — {seats_phrase(free)}", f"loc:sel:{event.id}:{loc.id}")]
            )
        else:
            rows.append(
                [btn(f"{loc.name} — мест нет", f"wl:full:{event.id}:{loc.id}")]
            )
    rows.append([btn("⬅️ Назад", "menu:events")])
    await message.answer(
        f"<b>{esc(event.title)}</b>\n\n"
        "Выберите локацию, в которой хотите принять участие в тренировке:",
        reply_markup=kb(*rows),
    )


async def _show_location_summary(
    message: Message, session, event: Event, loc: Location
) -> None:
    free = await reg_service.free_seats(session, loc.id)
    if free <= 0:
        await _show_waitlist_prompt(message, event, loc)
        return
    other_btn = (
        btn("🔄 Выбрать другую локацию", f"ev:reg:{event.id}")
        if event.kind == EventKind.RUN
        else btn("🔄 Выбрать другое мероприятие", "menu:events")
    )
    await message.answer(
        "Вы выбрали:\n\n"
        f"Мероприятие: {esc(event.title)}\n"
        f"📍 Локация: {esc(loc.name)}\n"
        f"Адрес: {esc(loc.address)}\n"
        f"🗓 Дата: {format_date_ru(event.starts_at, loc.timezone)}\n"
        f"🕒 Время: {format_time_ru(event.starts_at, loc.timezone)}\n"
        f"Свободных мест: {free}\n\n"
        "Подтвердите запись на тренировку.",
        reply_markup=kb(
            [btn("✅ Подтвердить запись", f"reg:go:{event.id}:{loc.id}")],
            [other_btn],
            [btn("❌ Отменить", "menu")],
        ),
    )


async def _show_waitlist_prompt(message: Message, event: Event, loc: Location) -> None:
    other_btn = (
        btn("🔄 Выбрать другую локацию", f"ev:reg:{event.id}")
        if event.kind == EventKind.RUN
        else btn("🔄 Выбрать другое мероприятие", "menu:events")
    )
    await message.answer(
        f"На «{esc(event.title)}» ({esc(loc.name)}) все места заняты. "
        "Можем добавить вас в лист ожидания — если место освободится, "
        "пришлём уведомление.",
        reply_markup=kb(
            [btn("📝 Встать в лист ожидания", f"wl:join:{event.id}:{loc.id}")],
            [other_btn],
            menu_btn_row(),
        ),
    )


@router.callback_query(F.data.startswith("ev:reg:"))
async def cb_register(
    cb: CallbackQuery, session, db_user: User, config: Config, state: FSMContext
):
    event_id = int(cb.data.split(":")[2])
    event = await _get_active_event(session, event_id)
    if event is None:
        await cb.answer("Мероприятие недоступно", show_alert=True)
        return
    if not await _ensure_ready(cb, session, db_user, config, state, cb.data):
        return

    existing = await reg_service.get_active_registration(session, db_user.id, event.id)
    if existing:
        await safe_delete(cb.message)
        await cb.message.answer(_already_text(existing), reply_markup=_already_kb(existing))
        await cb.answer()
        return

    await safe_delete(cb.message)
    if event.kind == EventKind.SIMPLE and event.locations:
        await _show_location_summary(cb.message, session, event, event.locations[0])
    elif not event.locations:
        await cb.message.answer(
            "Для этого мероприятия ещё не открыта запись.",
            reply_markup=kb(menu_btn_row()),
        )
    else:
        await _show_location_choice(cb.message, session, event)
    await cb.answer()


@router.callback_query(F.data.startswith("loc:sel:"))
async def cb_location_selected(cb: CallbackQuery, session, db_user: User, config, state):
    _, _, event_id, loc_id = cb.data.split(":")
    event = await _get_active_event(session, int(event_id))
    loc = await session.get(Location, int(loc_id))
    if event is None or loc is None or loc.event_id != event.id:
        await cb.answer("Локация недоступна", show_alert=True)
        return
    if not await _ensure_ready(cb, session, db_user, config, state, f"ev:reg:{event.id}"):
        return
    await safe_delete(cb.message)
    await _show_location_summary(cb.message, session, event, loc)
    await cb.answer()


@router.callback_query(F.data.startswith("wl:full:"))
async def cb_location_full(cb: CallbackQuery, session, db_user: User, config, state):
    _, _, event_id, loc_id = cb.data.split(":")
    event = await _get_active_event(session, int(event_id))
    loc = await session.get(Location, int(loc_id))
    if event is None or loc is None:
        await cb.answer("Локация недоступна", show_alert=True)
        return
    if not await _ensure_ready(cb, session, db_user, config, state, f"ev:reg:{event.id}"):
        return
    # вдруг место уже появилось — тогда сразу к подтверждению
    await safe_delete(cb.message)
    if await reg_service.free_seats(session, loc.id) > 0:
        await _show_location_summary(cb.message, session, event, loc)
    else:
        await _show_waitlist_prompt(cb.message, event, loc)
    await cb.answer()


# --- Подтверждение записи (сценарий 4/5) ------------------------------------

@router.callback_query(F.data.startswith("reg:go:"))
async def cb_confirm_registration(
    cb: CallbackQuery, session, db_user: User, config: Config, state: FSMContext
):
    _, _, event_id, loc_id = cb.data.split(":")
    event = await _get_active_event(session, int(event_id))
    loc = await session.get(Location, int(loc_id))
    if event is None or loc is None:
        await cb.answer("Мероприятие недоступно", show_alert=True)
        return
    if not await _ensure_ready(cb, session, db_user, config, state, f"ev:reg:{event.id}"):
        return

    result, reg = await reg_service.confirm_registration(
        session, db_user, event.id, loc.id
    )
    await safe_delete(cb.message)
    if result == ConfirmResult.CONFIRMED:
        await _notify_support_new_reg(cb.bot, config, reg)
        await cb.message.answer(
            reg_details_text(reg),
            reply_markup=kb(
                [btn("❌ Отменить участие", f"reg:cancel:{reg.id}")],
                menu_btn_row(),
            ),
        )
    elif result == ConfirmResult.ALREADY:
        await cb.message.answer(_already_text(reg), reply_markup=_already_kb(reg))
    else:  # FULL — гонка за последнее место проиграна
        other_btn = (
            btn("🔄 Выбрать другую локацию", f"ev:reg:{event.id}")
            if event.kind == EventKind.RUN
            else btn("🔄 Выбрать другое мероприятие", "menu:events")
        )
        await cb.message.answer(
            "К сожалению, пока вы подтверждали запись, свободные места "
            "в этой локации закончились.",
            reply_markup=kb(
                [btn("📝 Встать в лист ожидания", f"wl:join:{event.id}:{loc.id}")],
                [other_btn],
            ),
        )
    await cb.answer()


# --- Лист ожидания (сценарий 6) ---------------------------------------------

@router.callback_query(F.data.startswith("wl:join:"))
async def cb_waitlist_join(
    cb: CallbackQuery, session, db_user: User, config: Config, state: FSMContext
):
    _, _, event_id, loc_id = cb.data.split(":")
    event = await _get_active_event(session, int(event_id))
    loc = await session.get(Location, int(loc_id))
    if event is None or loc is None:
        await cb.answer("Мероприятие недоступно", show_alert=True)
        return
    if not await _ensure_ready(cb, session, db_user, config, state, f"ev:reg:{event.id}"):
        return

    result, reg = await reg_service.join_waitlist(session, db_user, event.id, loc.id)
    await safe_delete(cb.message)
    if result == ConfirmResult.CONFIRMED:
        await _notify_support_new_reg(cb.bot, config, reg)
        await cb.message.answer(
            reg_details_text(reg, header="Повезло — место как раз освободилось. Вы записаны!"),
            reply_markup=kb(
                [btn("❌ Отменить участие", f"reg:cancel:{reg.id}")],
                menu_btn_row(),
            ),
        )
    elif result == ConfirmResult.WAITLISTED:
        await _notify_support_new_reg(cb.bot, config, reg, waitlist=True)
        pos = await reg_service.waitlist_position(session, reg)
        await cb.message.answer(
            f"📝 Вы в листе ожидания «{esc(event.title)}» ({esc(loc.name)}), "
            f"позиция: {pos}.\n\n"
            f"Если место освободится, пришлём уведомление — на подтверждение "
            f"будет {config.waitlist_confirm_hours} "
            f"{plural_ru(config.waitlist_confirm_hours, 'час', 'часа', 'часов')}.",
            reply_markup=kb(
                [btn("❌ Покинуть лист ожидания", f"reg:cancel:{reg.id}")],
                menu_btn_row(),
            ),
        )
    else:  # ALREADY
        await cb.message.answer(_already_text(reg), reply_markup=_already_kb(reg))
    await cb.answer()


async def _load_own_reg(session, db_user: User, reg_id: int) -> Registration | None:
    reg = await session.get(Registration, reg_id)
    if reg is None or reg.user_id != db_user.id:
        return None
    return reg


async def _notify_support_new_reg(
    bot, config: Config, reg: Registration, *, waitlist: bool = False
) -> None:
    """Постит в группу поддержки уведомление о новой записи на забег."""
    if not config.support_chat_id:
        return
    user, event, loc = reg.user, reg.event, reg.location
    head = "📝 Новая запись в лист ожидания" if waitlist else "🆕 Новая запись на забег"
    lines = [
        head,
        "",
        f"<b>{esc(event.title)}</b>",
        f"📍 {esc(loc.name)}",
        f"🗓 {format_date_ru(event.starts_at, loc.timezone)}, "
        f"{format_time_ru(event.starts_at, loc.timezone)}",
        "",
        f"👤 {esc(user.full_name)}",
    ]
    if user.phone:
        lines.append(f"📞 {esc(user.phone)}")
    if user.email:
        lines.append(f"✉️ {esc(user.email)}")
    await send_notes(bot, [Note(chat_id=config.support_chat_id, text="\n".join(lines))])


@router.callback_query(F.data.startswith("wl:accept:"))
async def cb_waitlist_accept(cb: CallbackQuery, session, db_user: User, config: Config):
    reg = await _load_own_reg(session, db_user, int(cb.data.split(":")[2]))
    if reg is None:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    if reg.status == RegStatus.CANCELLED:
        await cb.answer("Эта запись уже не активна", show_alert=True)
        return
    result = await reg_service.confirm_from_waitlist(session, reg)
    await safe_delete(cb.message)
    if result in (ConfirmResult.CONFIRMED, ConfirmResult.ALREADY):
        if result == ConfirmResult.CONFIRMED:
            await _notify_support_new_reg(cb.bot, config, reg)
        await cb.message.answer(
            reg_details_text(reg),
            reply_markup=kb(
                [btn("❌ Отменить участие", f"reg:cancel:{reg.id}")],
                menu_btn_row(),
            ),
        )
    else:
        await cb.message.answer(
            "Увы, место снова успели занять. 😔 Вы остаётесь первым в очереди — "
            "сообщим, как только место освободится.",
            reply_markup=kb(menu_btn_row()),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("wl:decline:"))
async def cb_waitlist_decline(cb: CallbackQuery, session, db_user: User, config: Config):
    reg = await _load_own_reg(session, db_user, int(cb.data.split(":")[2]))
    if reg is None:
        await cb.answer("Запись не найдена", show_alert=True)
        return
    if reg.status != RegStatus.WAITLIST:
        await cb.answer("Эта запись уже не активна", show_alert=True)
        return
    notes = await reg_service.decline_waitlist(session, reg, config.waitlist_confirm_hours)
    await send_notes(cb.bot, notes)
    await safe_delete(cb.message)
    await cb.message.answer(
        "Хорошо, мы убрали вас из листа ожидания. Будем рады видеть в другой раз!",
        reply_markup=kb(menu_btn_row()),
    )
    await cb.answer()


# --- Смена локации ----------------------------------------------------------

@router.callback_query(F.data.startswith("reg:chloc:"))
async def cb_change_location(cb: CallbackQuery, session, db_user: User):
    reg = await _load_own_reg(session, db_user, int(cb.data.split(":")[2]))
    if reg is None or reg.status != RegStatus.CONFIRMED:
        await cb.answer("Смена локации доступна только для подтверждённой записи",
                        show_alert=True)
        return
    event = reg.event
    rows = []
    for loc in event.locations:
        if loc.id == reg.location_id:
            continue
        free = await reg_service.free_seats(session, loc.id)
        label = f"{loc.name} — {seats_phrase(free)}"
        rows.append([btn(label, f"loc:chg:{reg.id}:{loc.id}")])
    if not rows:
        await cb.answer("Других локаций у этого мероприятия нет", show_alert=True)
        return
    rows.append([btn("⬅️ Назад", f"reg:view:{reg.id}")])
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Текущая запись: {esc(event.title)}, локация «{esc(reg.location.name)}».\n\n"
        "Выберите новую локацию:",
        reply_markup=kb(*rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("loc:chg:"))
async def cb_change_location_pick(cb: CallbackQuery, session, db_user: User):
    _, _, reg_id, loc_id = cb.data.split(":")
    reg = await _load_own_reg(session, db_user, int(reg_id))
    new_loc = await session.get(Location, int(loc_id))
    if reg is None or new_loc is None or new_loc.event_id != reg.event_id:
        await cb.answer("Локация недоступна", show_alert=True)
        return
    free = await reg_service.free_seats(session, new_loc.id)
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Сменить локацию на «{esc(new_loc.name)}» ({esc(new_loc.address)})?\n"
        f"Свободных мест: {free}\n\n"
        f"Текущая локация: «{esc(reg.location.name)}».",
        reply_markup=kb(
            [btn("✅ Подтвердить смену", f"chg:go:{reg.id}:{new_loc.id}")],
            [btn("⬅️ Назад", f"reg:chloc:{reg.id}")],
        ),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("chg:go:"))
async def cb_change_location_go(cb: CallbackQuery, session, db_user: User, config: Config):
    _, _, reg_id, loc_id = cb.data.split(":")
    reg = await _load_own_reg(session, db_user, int(reg_id))
    new_loc = await session.get(Location, int(loc_id))
    if reg is None or reg.status != RegStatus.CONFIRMED or new_loc is None:
        await cb.answer("Запись недоступна", show_alert=True)
        return
    ok, notes = await reg_service.change_location(
        session, reg, new_loc.id, config.waitlist_confirm_hours
    )
    await send_notes(cb.bot, notes)
    await safe_delete(cb.message)
    if ok:
        await cb.message.answer(
            reg_details_text(reg, header="Локация изменена ✅"),
            reply_markup=kb(
                [btn("❌ Отменить участие", f"reg:cancel:{reg.id}")],
                menu_btn_row(),
            ),
        )
    else:
        await cb.message.answer(
            f"В локации «{esc(new_loc.name)}» мест уже нет. 😔\n"
            f"Ваша текущая запись сохранена: «{esc(reg.location.name)}».",
            reply_markup=kb(
                [btn("🔄 Выбрать другую локацию", f"reg:chloc:{reg.id}")],
                menu_btn_row(),
            ),
        )
    await cb.answer()
