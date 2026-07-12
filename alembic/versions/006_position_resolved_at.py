"""Ola 2.1: positions.resolved_at para tracking de mercados resueltos

Revision ID: 006
Create Date: 2026-07-12 00:00:00

Añade `positions.resolved_at TIMESTAMPTZ NULL` para persistir el momento en
que el WebSocket detectó `event_type == market_resolved` para el mercado
subyacente. La posición NO puede venderse tras ese punto (el CLOB rechaza
órdenes en mercados resueltos); solo puede redimirse vía CTF on-chain (R2.0).

Reversible: `downgrade` elimina la columna y su índice.
"""
import sqlalchemy as sa

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Ola 2.1: añade positions.resolved_at + índice."""
    op.add_column(
        "positions",
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_positions_resolved_at",
        "positions",
        ["resolved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_positions_resolved_at", table_name="positions")
    op.drop_column("positions", "resolved_at")
