# alembic/versions/001_initial_schema.py
"""initial schema

Revision ID: 001
Create Date: 2024-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── markets ──────────────────────────────────────────────────────
    op.create_table(
        "markets",
        sa.Column("id",            sa.String(100), primary_key=True),
        sa.Column("asset",         sa.String(10),  nullable=False),
        sa.Column("window",        sa.String(5),   nullable=False),
        sa.Column("question",      sa.Text,        nullable=False),
        sa.Column("status",        sa.String(20),  nullable=False),
        sa.Column("yes_token_id",  sa.String(100), nullable=False, server_default=""),
        sa.Column("no_token_id",   sa.String(100), nullable=False, server_default=""),
        sa.Column("yes_price",     sa.Float,       nullable=False, server_default="0.5"),
        sa.Column("no_price",      sa.Float,       nullable=False, server_default="0.5"),
        sa.Column("volume_24h",    sa.Float,       nullable=False, server_default="0.0"),
        sa.Column("expiry",        sa.DateTime,    nullable=False),
        sa.Column("discovered_at", sa.DateTime,    server_default=sa.func.now()),
        sa.Column("updated_at",    sa.DateTime,    server_default=sa.func.now()),
    )
    op.create_index("ix_markets_asset_window", "markets", ["asset", "window"])
    op.create_index("ix_markets_status",       "markets", ["status"])
    op.create_index("ix_markets_expiry",       "markets", ["expiry"])

    # ── orders ───────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id",           sa.String(36),  primary_key=True),
        sa.Column("market_id",    sa.String(100), nullable=False),
        sa.Column("side",         sa.String(5),   nullable=False),
        sa.Column("amount",       sa.Float,       nullable=False),
        sa.Column("target_price", sa.Float,       nullable=False),
        sa.Column("fill_price",   sa.Float,       nullable=True),
        sa.Column("slippage",     sa.Float,       nullable=True),
        sa.Column("status",       sa.String(20),  nullable=False),
        sa.Column("mode",         sa.String(10),  nullable=False),
        sa.Column("strategy",     sa.String(50),  nullable=False),
        sa.Column("reason",       sa.Text,        nullable=False, server_default=""),
        sa.Column("error",        sa.Text,        nullable=True),
        sa.Column("created_at",   sa.DateTime,    server_default=sa.func.now()),
        sa.Column("filled_at",    sa.DateTime,    nullable=True),
    )
    op.create_index("ix_orders_market_id", "orders", ["market_id"])
    op.create_index("ix_orders_status",    "orders", ["status"])
    op.create_index("ix_orders_mode",      "orders", ["mode"])
    op.create_index("ix_orders_created",   "orders", ["created_at"])

    # ── positions ────────────────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("id",           sa.String(36),  primary_key=True),
        sa.Column("market_id",    sa.String(100), nullable=False),
        sa.Column("asset",        sa.String(10),  nullable=False),
        sa.Column("window",       sa.String(5),   nullable=False),
        sa.Column("side",         sa.String(5),   nullable=False),
        sa.Column("amount",       sa.Float,       nullable=False),
        sa.Column("shares",       sa.Float,       nullable=False),
        sa.Column("entry_price",  sa.Float,       nullable=False),
        sa.Column("exit_price",   sa.Float,       nullable=True),
        sa.Column("pnl",          sa.Float,       nullable=True),
        sa.Column("pnl_pct",      sa.Float,       nullable=True),
        sa.Column("mode",         sa.String(10),  nullable=False),
        sa.Column("strategy",     sa.String(50),  nullable=False),
        sa.Column("exit_reason",  sa.Text,        nullable=True),
        sa.Column("opened_at",    sa.DateTime,    server_default=sa.func.now()),
        sa.Column("closed_at",    sa.DateTime,    nullable=True),
    )
    op.create_index("ix_positions_market_id", "positions", ["market_id"])
    op.create_index("ix_positions_mode",      "positions", ["mode"])
    op.create_index("ix_positions_closed_at", "positions", ["closed_at"])
    op.create_index("ix_positions_opened_at", "positions", ["opened_at"])

    # ── audit_logs ───────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id",        sa.Integer,     primary_key=True, autoincrement=True),
        sa.Column("action",    sa.String(50),  nullable=False),
        sa.Column("order_id",  sa.String(36),  nullable=True),
        sa.Column("market_id", sa.String(100), nullable=True),
        sa.Column("amount",    sa.Float,       nullable=True),
        sa.Column("details",   sa.JSON,        nullable=False),
        sa.Column("timestamp", sa.DateTime,    server_default=sa.func.now()),
    )
    op.create_index("ix_audit_action",    "audit_logs", ["action"])
    op.create_index("ix_audit_order_id",  "audit_logs", ["order_id"])
    op.create_index("ix_audit_timestamp", "audit_logs", ["timestamp"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("positions")
    op.drop_table("orders")
    op.drop_table("markets")