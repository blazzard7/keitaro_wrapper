"""initial schema

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

Json = sa.JSON().with_variant(JSONB(), "postgresql")
now = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("keitaro_campaign_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("alias", sa.String(64)),
        sa.Column("geo", sa.String(2)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
        sa.UniqueConstraint("keitaro_campaign_id", name="uq_campaigns_keitaro_campaign_id"),
    )
    op.create_table(
        "offers",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("keitaro_offer_id", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_offers"),
        sa.UniqueConstraint("keitaro_offer_id", name="uq_offers_keitaro_offer_id"),
    )
    op.create_table(
        "streams",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("campaign_id", sa.Integer, nullable=False),
        sa.Column("keitaro_stream_id", sa.Integer),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("schema", sa.String(32)),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("state", sa.String(16)),
        sa.Column("action_type", sa.String(64)),
        sa.Column("action_payload", sa.Text),
        sa.Column("action_options", Json),
        sa.Column("filters", Json),
        sa.Column("remote_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id", name="pk_streams"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"],
                                name="fk_streams_campaign_id_campaigns", ondelete="CASCADE"),
        sa.UniqueConstraint("keitaro_stream_id", name="uq_streams_keitaro_stream_id"),
        sa.CheckConstraint("type IN ('forced','regular','default')", name="ck_streams_type_valid"),
    )
    op.create_index("ix_streams_campaign_id", "streams", ["campaign_id"])
    # не более одного default-потока на кампанию
    op.create_index(
        "ux_streams_one_default_per_campaign", "streams", ["campaign_id"], unique=True,
        postgresql_where=sa.text("type = 'default' AND NOT remote_deleted"),
        sqlite_where=sa.text("type = 'default' AND NOT remote_deleted"),
    )
    op.create_table(
        "stream_offers",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("stream_id", sa.Integer, nullable=False),
        sa.Column("offer_id", sa.Integer, nullable=False),
        sa.Column("keitaro_binding_id", sa.Integer),
        sa.Column("published_share", sa.Integer),
        sa.Column("published_state", sa.String(16)),
        sa.Column("draft_share", sa.Integer, nullable=False),
        sa.Column("draft_state", sa.String(16), nullable=False),
        sa.Column("is_pinned", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stream_offers"),
        sa.ForeignKeyConstraint(["stream_id"], ["streams.id"],
                                name="fk_stream_offers_stream_id_streams", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], name="fk_stream_offers_offer_id_offers"),
        sa.UniqueConstraint("stream_id", "offer_id", name="uq_stream_offers_stream_offer"),
        sa.CheckConstraint("published_state IN ('active','removed')", name="ck_stream_offers_published_state_valid"),
        sa.CheckConstraint("draft_state IN ('active','removed')", name="ck_stream_offers_draft_state_valid"),
        sa.CheckConstraint("draft_share BETWEEN 0 AND 100", name="ck_stream_offers_draft_share_range"),
    )
    op.create_index("ix_stream_offers_stream_id", "stream_offers", ["stream_id"])
    op.create_table(
        "stream_offer_history",
        sa.Column("id", sa.Integer, nullable=False),
        sa.Column("stream_offer_id", sa.Integer),
        sa.Column("stream_id", sa.Integer, nullable=False),
        sa.Column("keitaro_offer_id", sa.Integer, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before", Json),
        sa.Column("after", Json),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stream_offer_history"),
        sa.ForeignKeyConstraint(["stream_offer_id"], ["stream_offers.id"],
                                name="fk_stream_offer_history_stream_offer_id_stream_offers",
                                ondelete="SET NULL"),
    )
    op.create_index("ix_stream_offer_history_stream_offer_id", "stream_offer_history", ["stream_offer_id"])
    op.create_index("ix_stream_offer_history_stream_id", "stream_offer_history", ["stream_id"])


def downgrade() -> None:
    op.drop_table("stream_offer_history")
    op.drop_table("stream_offers")
    op.drop_index("ux_streams_one_default_per_campaign", table_name="streams")
    op.drop_table("streams")
    op.drop_table("offers")
    op.drop_table("campaigns")
