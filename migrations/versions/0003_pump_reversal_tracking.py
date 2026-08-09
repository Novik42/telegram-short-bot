"""Add persistent pump watch and reversal transitions.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pump_watches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warning_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("price_at_watch", sa.Numeric(30, 12), nullable=False),
        sa.Column("peak_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("peak_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("drawdown_pct", sa.Numeric(18, 8), nullable=False),
        sa.Column("support_price", sa.Numeric(30, 12)),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pump_watches_symbol_started_at", "pump_watches", ["symbol", "started_at"]
    )
    op.create_index(
        "ix_pump_watches_status_expires_at", "pump_watches", ["status", "expires_at"]
    )

    op.create_table(
        "pump_watch_transitions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pump_watch_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(30, 12), nullable=False),
        sa.Column("peak_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("drawdown_pct", sa.Numeric(18, 8), nullable=False),
        sa.Column("support_price", sa.Numeric(30, 12)),
        sa.Column("borrow_usd", sa.Numeric(30, 8)),
        sa.Column("repay_usd", sa.Numeric(30, 8)),
        sa.Column("borrow_repay_ratio", sa.Numeric(30, 12)),
        sa.Column("borrow_delta_3m", sa.Numeric(30, 8)),
        sa.Column("borrow_delta_15m", sa.Numeric(30, 8)),
        sa.Column("price_change_1h", sa.Numeric(18, 8)),
        sa.Column("price_change_4h", sa.Numeric(18, 8)),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["pump_watch_id"], ["pump_watches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pump_watch_transitions_watch_occurred",
        "pump_watch_transitions",
        ["pump_watch_id", "occurred_at"],
    )
    op.create_index(
        "ix_pump_watch_transitions_notification",
        "pump_watch_transitions",
        ["status", "notified_at"],
    )


def downgrade() -> None:
    op.drop_table("pump_watch_transitions")
    op.drop_table("pump_watches")
