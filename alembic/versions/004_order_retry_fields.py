# alembic/versions/004_order_retry_fields.py
"""order retry and idempotency fields

Revision ID: 004
Create Date: 2026-05-21 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Anade campos de retry e idempotency a la tabla orders."""
    op.add_column(
        "orders",
        sa.Column(
            "retry_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "orders",
        sa.Column("last_retry_at", sa.DateTime, nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column(
            "idempotency_key",
            sa.String(32),
            nullable=True,
            unique=True,
        ),
    )
    op.create_index(
        "ix_orders_idempotency",
        "orders",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_idempotency", table_name="orders")
    op.drop_column("orders", "idempotency_key")
    op.drop_column("orders", "last_retry_at")
    op.drop_column("orders", "retry_count")
