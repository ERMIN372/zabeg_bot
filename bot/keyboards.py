from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def ubtn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def wbtn(text: str, url: str) -> InlineKeyboardButton:
    """Кнопка запуска mini app (доступна в приватном чате, URL обязательно https)."""
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def main_menu_kb() -> InlineKeyboardMarkup:
    return kb(
        [btn("🏃 Ближайшие мероприятия", "menu:events")],
        [btn("📋 Мои записи", "menu:regs")],
        [btn("ℹ️ О сообществе", "menu:about")],
        [btn("💬 Поддержка", "menu:support")],
        [btn("📸 Фото и видео", "menu:photos")],
    )


def back_kb(callback_data: str = "menu") -> InlineKeyboardMarkup:
    return kb([btn("⬅️ Назад", callback_data)])


def menu_btn_row() -> list[InlineKeyboardButton]:
    return [btn("🏠 Главное меню", "menu")]
