"""Редактируемые тексты разделов бота (хранятся в БД, дефолты — здесь)."""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Content

KEY_WELCOME = "welcome"
KEY_ABOUT = "about"
KEY_CONSENT = "consent"
KEY_PHOTOS_INTRO = "photos_intro"
KEY_SUPPORT_GREETING = "support_greeting"

DEFAULTS: dict[str, str] = {
    KEY_WELCOME: (
        "Привет! На связи беговое сообщество от сети кулинарных лавок "
        "«Братья Караваевы» — «Свои в ритме города». Мы объединяем людей, "
        "которые любят движение и выбирают лучшее для себя и близких.\n\n"
        "Здесь можно узнать о сообществе, зарегистрироваться на пробежку "
        "и узнать новости."
    ),
    KEY_ABOUT: (
        "«Свои в ритме города» — беговое сообщество от сети кулинарных лавок "
        "«Братья Караваевы».\n\n"
        "Мы регулярно проводим открытые пробежки в разных локациях города, "
        "закрытые мероприятия для участников и просто любим движение. "
        "Присоединяйтесь!"
    ),
    KEY_CONSENT: (
        "Для регистрации на мероприятия нам нужно ваше согласие на обработку "
        "персональных данных (152-ФЗ).\n\n"
        "Мы попросим имя, фамилию, телефон и e-mail — они используются только "
        "для организации мероприятий сообщества и не передаются третьим лицам. "
        "Полный текст — по кнопке «Политика конфиденциальности»."
    ),
    KEY_PHOTOS_INTRO: (
        "Здесь собраны фотографии и видеоматериалы с наших пробежек и мероприятий."
    ),
    KEY_SUPPORT_GREETING: (
        "Здравствуйте! Чем мы можем вам помочь?\n\n"
        "Напишите ваш вопрос одним или несколькими сообщениями — "
        "мы ответим прямо в этом чате."
    ),
}

# подписи для админки
KEY_LABELS: dict[str, str] = {
    KEY_WELCOME: "Приветствие (/start)",
    KEY_ABOUT: "О сообществе",
    KEY_CONSENT: "Текст согласия на ПДн",
    KEY_PHOTOS_INTRO: "Фото и видео (вступление)",
    KEY_SUPPORT_GREETING: "Приветствие поддержки",
}


async def get_content(session: AsyncSession, key: str) -> tuple[str, str | None]:
    """Возвращает (текст, image_file_id) раздела."""
    row = (
        await session.execute(sa.select(Content).where(Content.key == key))
    ).scalars().first()
    if row is None:
        return DEFAULTS.get(key, ""), None
    return row.text or DEFAULTS.get(key, ""), row.image_file_id


async def set_content(
    session: AsyncSession,
    key: str,
    text: str | None = None,
    image_file_id: str | None = None,
) -> None:
    row = (
        await session.execute(sa.select(Content).where(Content.key == key))
    ).scalars().first()
    if row is None:
        row = Content(key=key, text=DEFAULTS.get(key, ""))
        session.add(row)
    if text is not None:
        row.text = text
    if image_file_id is not None:
        row.image_file_id = image_file_id
    await session.commit()
