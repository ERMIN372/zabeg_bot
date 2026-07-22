"""Админ-панель: обзор мероприятий, экспорт, отмена/перенос, тексты."""
import sqlalchemy as sa
from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message, TelegramObject

from bot.helpers import esc, safe_delete
from bot.keyboards import btn, kb
from bot.states import AdminEditContent, AdminMoveEvent
from config import Config
from db.models import Event, EventKind, EventStatus, Location, RegStatus, Registration
from services import registration as reg_service
from services.content import KEY_ABOUT, KEY_LABELS, get_content, set_content
from services.export import registrations_csv
from services.notify import send_notes
from services.timeutil import format_date_ru, format_time_ru, parse_local

router = Router(name="admin")


class AdminFilter(BaseFilter):
    async def __call__(self, event: TelegramObject, config: Config) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in config.admin_ids


router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


def admin_menu_kb():
    return kb(
        [btn("📋 Мероприятия", "adm:events")],
        [btn("➕ Создать мероприятие", "adm:new")],
        [btn("📝 Тексты разделов", "adm:texts")],
        [btn("📤 Экспорт всех регистраций", "adm:export")],
        [btn("🔄 Пересчитать места", "adm:recount")],
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔧 <b>Админ-панель</b>", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm:menu")
async def cb_admin_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_delete(cb.message)
    await cb.message.answer("🔧 <b>Админ-панель</b>", reply_markup=admin_menu_kb())
    await cb.answer()


# --- Список и карточка мероприятия ------------------------------------------

STATUS_MARK = {
    EventStatus.ACTIVE: "🟢",
    EventStatus.CANCELLED: "🔴",
    EventStatus.POSTPONED: "🟡",
}


@router.callback_query(F.data == "adm:events")
async def cb_admin_events(cb: CallbackQuery, session):
    events = (
        await session.execute(
            sa.select(Event)
            .where(Event.status != EventStatus.CANCELLED)
            .order_by(Event.starts_at.desc())
            .limit(20)
        )
    ).scalars().all()
    rows = [
        [btn(
            f"{STATUS_MARK.get(e.status, '⚪')} {e.title} — "
            f"{format_date_ru(e.starts_at, e.timezone)}",
            f"adm:e:{e.id}",
        )]
        for e in events
    ]
    rows.append([btn("⬅️ Назад", "adm:menu")])
    await safe_delete(cb.message)
    await cb.message.answer(
        "Мероприятия (последние 20):" if events else "Мероприятий пока нет.",
        reply_markup=kb(*rows),
    )
    await cb.answer()


async def show_admin_event_card(message: Message, session, event: Event) -> None:
    lines = [
        f"{STATUS_MARK.get(event.status, '⚪')} <b>{esc(event.title)}</b>",
        f"Тип: {'пробежка (локации)' if event.kind == EventKind.RUN else 'закрытое мероприятие'}",
        f"🗓 {format_date_ru(event.starts_at, event.timezone)}, "
        f"{format_time_ru(event.starts_at, event.timezone)} ({event.timezone})",
    ]
    if event.description:
        desc = event.description
        lines.append(esc(desc[:200] + ("…" if len(desc) > 200 else "")))
    if event.album_url:
        lines.append(f"Альбом: {esc(event.album_url)}")
    lines.append("")
    for loc in event.locations:
        waitlist_count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Registration).where(
                    Registration.location_id == loc.id,
                    Registration.status == RegStatus.WAITLIST,
                )
            )
        ).scalar_one()
        lines.append(
            f"📍 {esc(loc.name)}: {loc.taken}/{loc.capacity} записаны"
            + (f", {waitlist_count} в листе ожидания" if waitlist_count else "")
        )
    rows = [
        [btn("✏️ Редактировать", f"adm:edit:{event.id}"),
         btn("📍 Локации", f"adm:locs:{event.id}")],
        [btn("📤 Экспорт CSV", f"adm:evexp:{event.id}")],
        [btn("📅 Перенести", f"adm:move:{event.id}"),
         btn("🚫 Отменить", f"adm:cancel:{event.id}")],
        [btn("🗑 Удалить", f"adm:del:{event.id}")],
        [btn("⬅️ Назад", "adm:events")],
    ]
    await message.answer("\n".join(lines), reply_markup=kb(*rows))


@router.callback_query(F.data.startswith("adm:e:"))
async def cb_admin_event(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    await safe_delete(cb.message)
    await show_admin_event_card(cb.message, session, event)
    await cb.answer()


# --- Экспорт ----------------------------------------------------------------

@router.callback_query(F.data == "adm:export")
async def cb_admin_export_all(cb: CallbackQuery, session):
    data = await registrations_csv(session)
    await cb.message.answer_document(
        BufferedInputFile(data, filename="registrations_all.csv"),
        caption="Все регистрации",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:evexp:"))
async def cb_admin_export_event(cb: CallbackQuery, session):
    event_id = int(cb.data.split(":")[2])
    event = await session.get(Event, event_id)
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    data = await registrations_csv(session, event_id)
    await cb.message.answer_document(
        BufferedInputFile(data, filename=f"registrations_event_{event_id}.csv"),
        caption=f"Регистрации: {event.title}",
    )
    await cb.answer()


@router.callback_query(F.data == "adm:recount")
async def cb_admin_recount(cb: CallbackQuery, session):
    fixed = await reg_service.recount_taken(session)
    await cb.answer(f"Готово. Исправлено счётчиков: {fixed}", show_alert=True)


# --- Отмена мероприятия -----------------------------------------------------

@router.callback_query(F.data.startswith("adm:cancel2:"))
async def cb_admin_cancel_go(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    event.status = EventStatus.CANCELLED
    await session.commit()
    notes = await reg_service.event_participant_notes(
        session, event.id,
        f"❌ К сожалению, мероприятие «{esc(event.title)}» отменено. "
        "Приносим извинения — будем ждать вас на следующих встречах!",
    )
    sent = await send_notes(cb.bot, notes)
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Мероприятие отменено. Уведомлений отправлено: {sent}.",
        reply_markup=kb([btn("⬅️ К мероприятиям", "adm:events")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:cancel:"))
async def cb_admin_cancel_ask(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Отменить «{esc(event.title)}»? Все участники получат уведомление.",
        reply_markup=kb(
            [btn("🚫 Да, отменить мероприятие", f"adm:cancel2:{event.id}")],
            [btn("⬅️ Назад", f"adm:e:{event.id}")],
        ),
    )
    await cb.answer()


# --- Удаление мероприятия (полное, для чистки тестовых) ----------------------

@router.callback_query(F.data.startswith("adm:del2:"))
async def cb_admin_delete_go(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    title = event.title
    # снимаем участников (без уведомлений — это полное удаление), затем локации
    await session.execute(
        sa.delete(Registration).where(Registration.event_id == event.id)
    )
    await session.execute(sa.delete(Location).where(Location.event_id == event.id))
    await session.delete(event)
    await session.commit()
    await safe_delete(cb.message)
    await cb.message.answer(
        f"🗑 Мероприятие «{esc(title)}» удалено полностью.",
        reply_markup=kb([btn("⬅️ К мероприятиям", "adm:events")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_admin_delete_ask(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    active = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Registration).where(
                Registration.event_id == event.id,
                Registration.status != RegStatus.CANCELLED,
            )
        )
    ).scalar_one()
    warn = (
        f"\n\n⚠️ У мероприятия {active} активных записей — они тоже будут удалены "
        "БЕЗ уведомления участникам. Если нужно предупредить людей — используйте "
        "«Отменить»."
        if active
        else ""
    )
    await safe_delete(cb.message)
    await cb.message.answer(
        f"Удалить «{esc(event.title)}» без возможности восстановления?{warn}",
        reply_markup=kb(
            [btn("🗑 Да, удалить навсегда", f"adm:del2:{event.id}")],
            [btn("⬅️ Назад", f"adm:e:{event.id}")],
        ),
    )
    await cb.answer()


# --- Перенос мероприятия ----------------------------------------------------

@router.callback_query(F.data.startswith("adm:move:"))
async def cb_admin_move(cb: CallbackQuery, session, state: FSMContext):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    await state.set_state(AdminMoveEvent.date)
    await state.update_data(event_id=event.id)
    await cb.message.answer(
        f"Перенос «{esc(event.title)}».\n\nНовая дата (ДД.ММ.ГГГГ)?"
    )
    await cb.answer()


@router.message(AdminMoveEvent.date, F.text)
async def admin_move_date(message: Message, state: FSMContext):
    try:
        parse_local(message.text.strip(), "12:00", "UTC")
    except ValueError:
        await message.answer("Формат: ДД.ММ.ГГГГ, например 18.07.2026")
        return
    await state.update_data(date=message.text.strip())
    await state.set_state(AdminMoveEvent.time)
    await message.answer("Новое время (ЧЧ:ММ, местное для мероприятия)?")


@router.message(AdminMoveEvent.time, F.text)
async def admin_move_time(message: Message, state: FSMContext, session):
    data = await state.get_data()
    event = await session.get(Event, data["event_id"])
    if event is None:
        await state.clear()
        await message.answer("Мероприятие не найдено.")
        return
    try:
        new_starts = parse_local(data["date"], message.text.strip(), event.timezone)
    except ValueError:
        await message.answer("Формат: ЧЧ:ММ, например 09:00")
        return
    event.starts_at = new_starts
    event.status = EventStatus.ACTIVE
    # напоминания должны сработать заново от новой даты
    await session.execute(
        sa.update(Registration)
        .where(Registration.event_id == event.id)
        .values(reminded_24h_at=None, reminded_3h_at=None)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    await state.clear()
    notes = await reg_service.event_participant_notes(
        session, event.id,
        f"⚠️ Мероприятие «{esc(event.title)}» перенесено!\n\n"
        f"Новая дата: {format_date_ru(event.starts_at, event.timezone)}\n"
        f"Время: {format_time_ru(event.starts_at, event.timezone)} (местное)\n\n"
        "Ваша запись сохраняется. Если не сможете прийти — отмените её в «Мои записи».",
        image_file_id=event.image_file_id,
    )
    sent = await send_notes(message.bot, notes)
    await message.answer(
        f"✅ Перенесено. Уведомлений отправлено: {sent}.",
        reply_markup=kb([btn("⬅️ К мероприятию", f"adm:e:{event.id}")]),
    )


# --- Тексты разделов --------------------------------------------------------

@router.callback_query(F.data == "adm:texts")
async def cb_admin_texts(cb: CallbackQuery):
    rows = [[btn(label, f"adm:text:{key}")] for key, label in KEY_LABELS.items()]
    rows.append([btn("⬅️ Назад", "adm:menu")])
    await safe_delete(cb.message)
    await cb.message.answer("Какой текст отредактировать?", reply_markup=kb(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:text:"))
async def cb_admin_text(cb: CallbackQuery, session, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in KEY_LABELS:
        await cb.answer("Неизвестный раздел", show_alert=True)
        return
    text, image = await get_content(session, key)
    await state.set_state(AdminEditContent.text)
    await state.update_data(content_key=key)
    hint = (
        "\n\nОтправьте новый текст сообщением."
        + ("\nМожно прислать фото с подписью — оно станет картинкой раздела."
           if key == KEY_ABOUT else "")
    )
    await cb.message.answer(
        f"<b>{KEY_LABELS[key]}</b> — текущий текст:\n\n{esc(text)}{hint}"
    )
    await cb.answer()


@router.message(AdminEditContent.text, F.photo)
async def admin_content_photo(message: Message, state: FSMContext, session):
    data = await state.get_data()
    await set_content(
        session,
        data["content_key"],
        text=message.caption if message.caption else None,
        image_file_id=message.photo[-1].file_id,
    )
    await state.clear()
    await message.answer("✅ Сохранено", reply_markup=kb([btn("⬅️ К текстам", "adm:texts")]))


@router.message(AdminEditContent.text, F.text)
async def admin_content_text(message: Message, state: FSMContext, session):
    data = await state.get_data()
    await set_content(session, data["content_key"], text=message.text)
    await state.clear()
    await message.answer("✅ Сохранено", reply_markup=kb([btn("⬅️ К текстам", "adm:texts")]))
