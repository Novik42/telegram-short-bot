"""Add persistent Bybit demo trade state.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "demo_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pump_watch_transition_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("signal_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("proposal_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12)),
        sa.Column("stop_loss", sa.Numeric(30, 12), nullable=False),
        sa.Column("exit_price", sa.Numeric(30, 12)),
        sa.Column("peak_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("support_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("quantity", sa.Numeric(30, 12), nullable=False),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("balance_usd", sa.Numeric(30, 8), nullable=False),
        sa.Column("risk_usd", sa.Numeric(30, 8), nullable=False),
        sa.Column("notional_usd", sa.Numeric(30, 8), nullable=False),
        sa.Column("margin_usd", sa.Numeric(30, 8), nullable=False),
        sa.Column("realized_pnl_usd", sa.Numeric(30, 8)),
        sa.Column("realized_pnl_pct", sa.Numeric(18, 8)),
        sa.Column("order_link_id", sa.String(36), nullable=False),
        sa.Column("entry_order_id", sa.String(64)),
        sa.Column("close_order_id", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column("notion_page_url", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["pump_watch_transition_id"],
            ["pump_watch_transitions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pump_watch_transition_id", name="uq_demo_trades_pump_watch_transition_id"
        ),
        sa.UniqueConstraint("order_link_id", name="uq_demo_trades_order_link_id"),
    )
    op.create_index(
        "ix_demo_trades_status_created_at", "demo_trades", ["status", "created_at"]
    )
    op.create_index(
        "ix_demo_trades_symbol_created_at", "demo_trades", ["symbol", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("demo_trades")
