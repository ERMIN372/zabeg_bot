from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from services.timeutil import utcnow


class EventKind:
    RUN = "run"        # пробежка: выбор из нескольких локаций
    SIMPLE = "simple"  # закрытое мероприятие с одной локацией (дегустация и т.п.)


class EventStatus:
    ACTIVE = "active"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"


class RegStatus:
    CONFIRMED = "confirmed"
    WAITLIST = "waitlist"
    CANCELLED = "cancelled"


class ConsentKind:
    PDN = "pdn"
    MARKETING = "marketing"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(100))
    surname: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    consent_pdn_at: Mapped[datetime | None] = mapped_column(DateTime)
    consent_pdn_version: Mapped[str | None] = mapped_column(String(32))
    consent_marketing_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def has_pdn_consent(self) -> bool:
        return self.consent_pdn_at is not None

    @property
    def profile_complete(self) -> bool:
        return bool(self.name and self.surname and self.phone)

    @property
    def full_name(self) -> str:
        return " ".join(x for x in (self.name, self.surname) if x) or "—"


class ConsentLog(Base):
    __tablename__ = "consent_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # pdn | marketing
    version: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    image_file_id: Mapped[str | None] = mapped_column(String(255))
    album_url: Mapped[str | None] = mapped_column(String(512))
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # наивный UTC
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    kind: Mapped[str] = mapped_column(String(16), default=EventKind.RUN)
    status: Mapped[str] = mapped_column(String(16), default=EventStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    locations: Mapped[list[Location]] = relationship(
        back_populates="event", lazy="selectin", order_by="Location.id"
    )


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(512), default="")
    capacity: Mapped[int] = mapped_column(Integer, default=0)
    taken: Mapped[int] = mapped_column(Integer, default=0)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")

    event: Mapped[Event] = relationship(back_populates="locations", lazy="selectin")


class Registration(Base):
    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=RegStatus.CONFIRMED)
    # лист ожидания
    queue_pos: Mapped[int | None] = mapped_column(Integer)
    offered_at: Mapped[datetime | None] = mapped_column(DateTime)
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    # напоминания
    reminded_24h_at: Mapped[datetime | None] = mapped_column(DateTime)
    reminded_3h_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(lazy="selectin")
    event: Mapped[Event] = relationship(lazy="selectin")
    location: Mapped[Location] = relationship(lazy="selectin")

    __table_args__ = (
        # Идемпотентность: не более одной активной записи на пользователя
        # в рамках мероприятия (частичный уникальный индекс).
        Index(
            "uq_active_registration",
            "user_id",
            "event_id",
            unique=True,
            sqlite_where=text("status != 'cancelled'"),
            postgresql_where=text("status != 'cancelled'"),
        ),
    )


class SupportMessage(Base):
    """Связка «сообщение в админ-группе -> пользователь» для ответов поддержки."""

    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    group_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Content(Base):
    """Редактируемые тексты и картинки разделов бота."""

    __tablename__ = "content"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")
    image_file_id: Mapped[str | None] = mapped_column(String(255))
