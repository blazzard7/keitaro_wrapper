
from datetime import datetime
from typing import Any

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    CheckConstraint,
    UniqueConstraint,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)

    keitaro_id: Mapped[int] = mapped_column(
        Integer, unique=True, index=True
    )

    name: Mapped[str] = mapped_column(String(255))

    domain_id: Mapped[int | None] = mapped_column(Integer)
    group_id: Mapped[int | None] = mapped_column(Integer)
    traffic_source_id: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True)

    keitaro_id: Mapped[int] = mapped_column(
        Integer, unique=True, index=True
    )

    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE")
    )

    name: Mapped[str] = mapped_column(String(255))

    stream_type: Mapped[str] = mapped_column(
        "type", String(30)
    )

    state: Mapped[str] = mapped_column(
        String(30), default="active"
    )

    action_type: Mapped[str | None] = mapped_column(String(50))
    schema_name: Mapped[str | None] = mapped_column(
        "schema", String(50)
    )

    action_options: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    filters: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )

    triggers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    campaign: Mapped["Campaign"] = relationship(
        back_populates="streams"
    )

    offer_bindings: Mapped[list["StreamOffer"]] = relationship(
        back_populates="stream",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "uq_campaign_default_stream",
            "campaign_id",
            unique=True,
            postgresql_where=text("type = 'default'"),
        ),
    )


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)

    keitaro_id: Mapped[int] = mapped_column(
        Integer, unique=True, index=True
    )

    name: Mapped[str] = mapped_column(String(255))

    state: Mapped[str] = mapped_column(
        String(30), default="active"
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    stream_bindings: Mapped[list["StreamOffer"]] = relationship(
        back_populates="offer"
    )


class StreamOffer(Base):
    __tablename__ = "stream_offers"

    id: Mapped[int] = mapped_column(primary_key=True)

    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", ondelete="CASCADE")
    )

    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT")
    )

    # ID самой привязки в Keitaro, не ID оффера.
    keitaro_binding_id: Mapped[int | None] = mapped_column(Integer)

    # Опубликованное состояние в Keitaro.
    published_share: Mapped[int | None] = mapped_column(Integer)
    published_state: Mapped[str | None] = mapped_column(String(30))
    published_is_pinned: Mapped[bool] = mapped_column(
        Boolean, default=False
    )

    # Локальные изменения, ещё не отправленные в Keitaro.
    draft_share: Mapped[int] = mapped_column(Integer, default=0)
    draft_state: Mapped[str] = mapped_column(
        String(30), default="active"
    )
    draft_is_pinned: Mapped[bool] = mapped_column(
        Boolean, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    stream: Mapped["Stream"] = relationship(
        back_populates="offer_bindings"
    )

    offer: Mapped["Offer"] = relationship(
        back_populates="stream_bindings"
    )

    history: Mapped[list["StreamOfferHistory"]] = relationship(
        back_populates="binding",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "stream_id",
            "offer_id",
            name="uq_stream_offer",
        ),
        CheckConstraint(
            "draft_share >= 0 AND draft_share <= 100",
            name="check_draft_share",
        ),
        CheckConstraint(
            "published_share IS NULL OR "
            "(published_share >= 0 AND published_share <= 100)",
            name="check_published_share",
        ),
    )


class StreamOfferHistory(Base):
    __tablename__ = "stream_offer_history"

    id: Mapped[int] = mapped_column(primary_key=True)

    binding_id: Mapped[int] = mapped_column(
        ForeignKey("stream_offers.id", ondelete="CASCADE")
    )

    action: Mapped[str] = mapped_column(String(50))

    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    source: Mapped[str] = mapped_column(
        String(30), default="wrapper"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    binding: Mapped["StreamOffer"] = relationship(
        back_populates="history"
    )
