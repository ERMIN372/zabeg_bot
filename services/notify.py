"""Уведомления, которые бизнес-логика возвращает наверх.

Сервисы не отправляют сообщения сами — они возвращают список Note,
а бот или планировщик отправляют их. Это упрощает тестирование.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# кнопка = (текст, callback_data)
Buttons = list[list[tuple[str, str]]]


@dataclass
class Note:
    chat_id: int
    text: str
    image_file_id: str | None = None
    buttons: Buttons | None = field(default=None)


async def send_notes(bot, notes: list[Note]) -> int:
    """Отправляет уведомления, не падая на заблокировавших бота пользователях."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    sent = 0
    for note in notes:
        markup = None
        if note.buttons:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=t, callback_data=cb) for t, cb in row]
                    for row in note.buttons
                ]
            )
        try:
            if note.image_file_id:
                await bot.send_photo(
                    note.chat_id, note.image_file_id,
                    caption=note.text, reply_markup=markup,
                )
            else:
                await bot.send_message(note.chat_id, note.text, reply_markup=markup)
            sent += 1
        except Exception as e:  # пользователь заблокировал бота и т.п.
            log.warning("Не удалось отправить уведомление chat_id=%s: %s", note.chat_id, e)
    return sent
