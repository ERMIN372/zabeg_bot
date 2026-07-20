import html

from aiogram.types import InlineKeyboardMarkup, Message

from bot.keyboards import consent_kb
from config import Config
from db.models import RegStatus, Registration
from services.content import KEY_CONSENT, get_content
from services.timeutil import format_date_ru, format_time_ru


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


async def ask_consent(message: Message, session, config: Config) -> None:
    text, _ = await get_content(session, KEY_CONSENT)
    await message.answer(esc(text), reply_markup=consent_kb(config.privacy_policy_url))


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
