# alembic/versions/003_bot_settings_mode.py
"""bot_settings table

Revision ID: 003
Create Date: 2026-05-21 12:00:00

NOTA: No existe migracion 002 porque la tabla audit_logs ya fue creada
en 001_initial_schema.py. Se salta el numero para mantener claridad.
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Crea la tabla bot_settings para configuracion clave-valor."""
    op.create_table(
        "bot_settings",
        sa.Column("key",        sa.String(50), primary_key=True),
        sa.Column("value",      sa.Text,       nullable=False),
        sa.Column("updated_at", sa.DateTime,   server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("bot_settings")
