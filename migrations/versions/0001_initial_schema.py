"""Initial research schema.

Revision ID: 0001
Revises: None
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "borrow_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("borrow_usd", sa.Numeric(30, 8), nullable=False),
        sa.Column("repay_usd", sa.Numeric(30, 8), nullable=False),
        sa.Column("borrow_repay_ratio", sa.Numeric(30, 12)),
        sa.Column("raw_payload_hash", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_name", "symbol", "source_timestamp"),
    )
    op.create_index(
        "ix_borrow_snapshots_symbol_source_timestamp",
        "borrow_snapshots",
        ["symbol", "source_timestamp"],
    )
    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("quote_volume_24h", sa.Numeric(30, 8)),
        sa.Column("base_volume_24h", sa.Numeric(30, 8)),
        sa.Column("price_change_percent_24h", sa.Numeric(18, 8)),
        sa.Column("open_interest", sa.Numeric(30, 8)),
        sa.Column("open_interest_usd", sa.Numeric(30, 8)),
        sa.Column("funding_rate", sa.Numeric(18, 12)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_market_snapshots_symbol_captured_at", "market_snapshots", ["symbol", "captured_at"]
    )
    op.create_table(
        "candles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_type", sa.String(16), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(30, 12), nullable=False),
        sa.Column("high", sa.Numeric(30, 12), nullable=False),
        sa.Column("low", sa.Numeric(30, 12), nullable=False),
        sa.Column("close", sa.Numeric(30, 12), nullable=False),
        sa.Column("volume", sa.Numeric(30, 8), nullable=False),
        sa.Column("quote_volume", sa.Numeric(30, 8), nullable=False),
        sa.Column("trades_count", sa.Integer(), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "market_type", "interval", "open_time"),
    )
    op.create_index("ix_candles_symbol_open_time", "candles", ["symbol", "open_time"])
    op.create_table(
        "anomaly_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("borrow_before", sa.Numeric(30, 8), nullable=False),
        sa.Column("borrow_now", sa.Numeric(30, 8), nullable=False),
        sa.Column("borrow_delta", sa.Numeric(30, 8), nullable=False),
        sa.Column("borrow_delta_pct", sa.Numeric(18, 8)),
        sa.Column("repay_before", sa.Numeric(30, 8), nullable=False),
        sa.Column("repay_now", sa.Numeric(30, 8), nullable=False),
        sa.Column("repay_delta", sa.Numeric(30, 8), nullable=False),
        sa.Column("net_borrow_delta", sa.Numeric(30, 8), nullable=False),
        sa.Column("ratio_now", sa.Numeric(30, 12)),
        sa.Column("price_at_signal", sa.Numeric(30, 12)),
        sa.Column("price_change_15m_before", sa.Numeric(18, 8)),
        sa.Column("price_change_1h_before", sa.Numeric(18, 8)),
        sa.Column("price_change_4h_before", sa.Numeric(18, 8)),
        sa.Column("price_change_24h_before", sa.Numeric(18, 8)),
        sa.Column("volume_15m", sa.Numeric(30, 8)),
        sa.Column("average_volume_15m", sa.Numeric(30, 8)),
        sa.Column("volume_spike_ratio", sa.Numeric(18, 8)),
        sa.Column("borrow_to_volume_ratio", sa.Numeric(18, 12)),
        sa.Column("open_interest_change_15m", sa.Numeric(18, 8)),
        sa.Column("funding_rate", sa.Numeric(18, 12)),
        sa.Column("anomaly_score", sa.Numeric(8, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_anomaly_events_symbol_detected_at", "anomaly_events", ["symbol", "detected_at"]
    )
    op.create_table(
        "event_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("anomaly_event_id", sa.BigInteger(), nullable=False),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("price_at_horizon", sa.Numeric(30, 12), nullable=False),
        sa.Column("return_pct", sa.Numeric(18, 8), nullable=False),
        sa.Column("max_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("min_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("max_favorable_move_pct", sa.Numeric(18, 8), nullable=False),
        sa.Column("max_adverse_move_pct", sa.Numeric(18, 8), nullable=False),
        sa.Column("max_pump_pct", sa.Numeric(18, 8), nullable=False),
        sa.Column("max_dump_pct", sa.Numeric(18, 8), nullable=False),
        sa.Column("time_to_max_minutes", sa.Integer()),
        sa.Column("time_to_min_minutes", sa.Integer()),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["anomaly_event_id"], ["anomaly_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_outcomes_event_horizon", "event_outcomes", ["anomaly_event_id", "horizon_minutes"]
    )
    op.create_table(
        "notification_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("anomaly_event_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_text", sa.Text()),
        sa.ForeignKeyConstraint(["anomaly_event_id"], ["anomaly_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_log_event_sent", "notification_log", ["anomaly_event_id", "sent_at"]
    )


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("event_outcomes")
    op.drop_table("anomaly_events")
    op.drop_table("candles")
    op.drop_table("market_snapshots")
    op.drop_table("borrow_snapshots")
