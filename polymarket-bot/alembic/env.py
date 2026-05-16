# alembic/env.py

import asyncio
from logging.config import fileConfig
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context
import os

# Importa todos los modelos para que Alembic los detecte
from src.infrastructure.db.session import Base
from src.infrastructure.db.models import (  # noqa: F401
    MarketModel, OrderModel, PositionModel, AuditLogModel
)

config      = context.config
fileConfig(config.config_file_name)
target_metadata = Base.metadata


def get_url() -> str:
    """Lee la URL de DB desde el entorno — nunca hardcodeada."""
    return os.environ["DATABASE_URL"]


def run_migrations_offline() -> None:
    """Modo offline: genera SQL sin conectar a la DB."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,         # Detecta cambios de tipo en columnas
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Modo online: conecta async a la DB y ejecuta migraciones."""
    engine = create_async_engine(get_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())