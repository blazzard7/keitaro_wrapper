"""
Локальная модель данных.

Ключевая идея StreamOffer: у каждой привязки две «копии» состояния
  published_* - что сейчас (по нашим данным) лежит в Keitaro;
  draft_*     - что пользователь хочет опубликовать.
Поток «грязный» (dirty), если хоть у одной привязки draft != published.
Флаг dirty НЕ хранится - он вычисляется (services/domain.py), поэтому разойтись с данными не может.

published_state: NULL (в Keitaro ещё не было) | 'active' | 'removed' (архив)
draft_state:     'active' | 'removed'
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
    MetaData, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Стабильные имена ограничений - иначе Alembic генерирует нестабильные имена.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


JsonType = JSON().with_variant(JSONB(), "postgresql")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_campaign_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str | None] = mapped_column(String(64))
    geo: Mapped[str | None] = mapped_column(String(2))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="campaign", order_by="Stream.position, Stream.id", cascade="all, delete-orphan"
    )


class Stream(Base):
    __tablename__ = "streams"
    __table_args__ = (
        # В Keitaro на кампанию - максимум один default-поток. Держим это правило и на уровне БД.
        Index(
            "ux_streams_one_default_per_campaign", "campaign_id", unique=True,
            postgresql_where=text("type = 'default' AND NOT remote_deleted"),
            sqlite_where=text("type = 'default' AND NOT remote_deleted"),
        ),
        CheckConstraint("type IN ('forced','regular','default')", name="type_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    keitaro_stream_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    type: Mapped[str] = mapped_column(String(16))
    schema: Mapped[str | None] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str | None] = mapped_column(String(16))
    action_type: Mapped[str | None] = mapped_column(String(64))
    action_payload: Mapped[str | None] = mapped_column(Text)
    action_options: Mapped[dict | None] = mapped_column(JsonType)
    filters: Mapped[list | None] = mapped_column(JsonType)
    # Поток исчез из Keitaro -> помечаем, но не удаляем (там могут быть наши архивные привязки).
    remote_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    bindings: Mapped[list["StreamOffer"]] = relationship(
        back_populates="stream", order_by="StreamOffer.id", cascade="all, delete-orphan"
    )


class Offer(Base):
    """Локальный справочник офферов (кэш из Keitaro)."""
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_offer_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255))


class StreamOffer(Base):
    __tablename__ = "stream_offers"
    __table_args__ = (
        UniqueConstraint("stream_id", "offer_id", name="uq_stream_offers_stream_offer"),
        CheckConstraint("published_state IN ('active','removed')", name="published_state_valid"),
        CheckConstraint("draft_state IN ('active','removed')", name="draft_state_valid"),
        CheckConstraint("draft_share BETWEEN 0 AND 100", name="draft_share_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    keitaro_binding_id: Mapped[int | None] = mapped_column(Integer)  # nullable: Keitaro может его не отдавать
    published_share: Mapped[int | None] = mapped_column(Integer)
    published_state: Mapped[str | None] = mapped_column(String(16))
    draft_share: Mapped[int] = mapped_column(Integer, default=0)
    draft_state: Mapped[str] = mapped_column(String(16), default="active")
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    stream: Mapped[Stream] = relationship(back_populates="bindings")
    offer: Mapped[Offer] = relationship()


class StreamOfferHistory(Base):
    """Журнал изменений. Не каскадится: при Cancel непубликованной привязки запись остаётся."""
    __tablename__ = "stream_offer_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("stream_offers.id", ondelete="SET NULL"), index=True
    )
    stream_id: Mapped[int] = mapped_column(Integer, index=True)
    keitaro_offer_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    before: Mapped[dict | None] = mapped_column(JsonType)
    after: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
