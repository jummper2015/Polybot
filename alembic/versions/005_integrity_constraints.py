# alembic/versions/005_integrity_constraints.py
"""R2.5.3/4: unique constraints for positions and markets

Revision ID: 005
Create Date: 2026-07-09 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """R2.5.3: unique partial index on open positions.
    R2.5.4: unique constraint on markets (asset, window, expiry)."""

    # ── R2.5.3: Anti-duplicado en posiciones abiertas ─────────────────
    # Previene 2 posiciones ABIERTAS para el mismo (market_id, mode).
    # Partial index: solo aplica WHERE closed_at IS NULL.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_positions_open "
        "ON positions (market_id, mode) "
        "WHERE closed_at IS NULL"
    ))

    # ── R2.5.4: Anti-duplicado en markets ─────────────────────────────
    # Paso 1: deduplicar — mantener solo el registro más reciente por
    # (asset, window, expiry). Si hay varios condition_id para el mismo
    # trio, conservamos el de mayor id (más reciente en discovery).
    op.execute(sa.text(
        "DELETE FROM markets a "
        "USING markets b "
        "WHERE a.asset = b.asset "
        "AND a.\"window\" = b.\"window\" "
        "AND a.expiry = b.expiry "
        "AND a.id < b.id"
    ))

    # Paso 2: crear el índice único que previene futuros duplicados.
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_markets_asset_window_expiry "
        "ON markets (asset, \"window\", expiry)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_positions_open"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_markets_asset_window_expiry"))
