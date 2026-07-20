"""Сценарий 9: «Фото и видео» — единый архив или список по мероприятиям."""
import sqlalchemy as sa
from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.helpers import esc, safe_delete
from bot.keyboards import btn, kb, menu_btn_row, ubtn
from config import PHOTO_MODE_PER_EVENT, Config
from db.models import Event
from services.content import KEY_PHOTOS_INTRO, get_content
from services.timeutil import format_date_ru

router = Router(name="photos")


@router.callback_query(F.data == "menu:photos")
async def cb_photos(cb: CallbackQuery, session, config: Config):
    intro, _ = await get_content(session, KEY_PHOTOS_INTRO)
    await safe_delete(cb.message)

    if config.photo_mode == PHOTO_MODE_PER_EVENT:
        events = (
            await session.execute(
                sa.select(Event)
                .where(Event.album_url.is_not(None), Event.album_url != "")
                .order_by(Event.starts_at.desc())
                .limit(20)
            )
        ).scalars().all()
        if events:
            rows = [
                [btn(
                    f"{e.title} — {format_date_ru(e.starts_at, e.timezone)}",
                    f"ph:ev:{e.id}",
                )]
                for e in events
            ]
            rows.append(menu_btn_row())
            await cb.message.answer(
                f"{esc(intro)}\n\nВыберите интересующее мероприятие:",
                reply_markup=kb(*rows),
            )
            await cb.answer()
            return
        # альбомов по мероприятиям нет — падаем на общий архив, если настроен

    if config.photos_archive_url:
        await cb.message.answer(
            f"{esc(intro)}\n\nВсе фотографии и видеоматериалы с наших "
            "мероприятий доступны по ссылке ниже.",
            reply_markup=kb(
                [ubtn("📁 Открыть Яндекс Диск", config.photos_archive_url)],
                menu_btn_row(),
            ),
        )
    else:
        await cb.message.answer(
            "Архив фото и видео пока наполняется — загляните позже! 📸",
            reply_markup=kb(menu_btn_row()),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("ph:ev:"))
async def cb_photos_event(cb: CallbackQuery, session):
    event = await session.get(Event, int(cb.data.split(":")[2]))
    if event is None or not event.album_url:
        await cb.answer("Альбом не найден", show_alert=True)
        return
    await safe_delete(cb.message)
    await cb.message.answer(
        f"<b>{esc(event.title)}</b>\n"
        f"🗓 {format_date_ru(event.starts_at, event.timezone)}\n\n"
        "Фото и видео с мероприятия — по кнопке ниже.",
        reply_markup=kb(
            [ubtn("📁 Открыть альбом", event.album_url)],
            [btn("⬅️ Назад", "menu:photos")],
        ),
    )
    await cb.answer()
