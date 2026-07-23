import html

from aiogram import F
from aiogram.types import InlineKeyboardMarkup, Message

from db.models import RegStatus, Registration
from services.timeutil import format_date_ru, format_time_ru

# Фильтр текстового ввода для анкет/редакторов: обычный текст, но НЕ команда.
# Команды (/start, /support, …) должны отработать как команды, а не попасть
# в редактируемое поле как его значение. Если текст — команда, апдейт
# «проваливается» дальше по роутерам к нужному обработчику команды.
TEXT_INPUT = F.text & ~F.text.startswith("/")


def esc(s: str | None) -> str:
    return html.escape(s or "")


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def seats_phrase(free: int) -> str:
    if free <= 0:
        return "мест нет"
    return f"осталось {free} {plural_ru(free, 'место', 'места', 'мест')}"


async def safe_delete(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except Exception:
        pass


async def send_card(
    message: Message,
    text: str,
    image_file_id: str | None,
    markup: InlineKeyboardMarkup | None,
) -> None:
    """Отправляет карточку: фото с подписью или обычный текст."""
    if image_file_id:
        try:
            await message.answer_photo(image_file_id, caption=text, reply_markup=markup)
            return
        except Exception:
            pass  # битый file_id — падаем в текст
    await message.answer(text, reply_markup=markup)


def reg_details_text(reg: Registration, header: str = "Вы записаны!") -> str:
    event, loc = reg.event, reg.location
    lines = [
        f"<b>{header}</b>",
        "",
        f"Мероприятие: {esc(event.title)}",
        f"📍 Локация: {esc(loc.name)}",
        f"Адрес: {esc(loc.address)}",
        f"🗓 Дата: {format_date_ru(event.starts_at, loc.timezone)}",
        f"🕒 Время: {format_time_ru(event.starts_at, loc.timezone)}",
    ]
    if reg.status == RegStatus.CONFIRMED:
        lines += ["", "Пожалуйста, приезжайте за 15 минут до начала."]
    return "\n".join(lines)
