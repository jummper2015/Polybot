# src/infrastructure/db/session.py

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    """
    Base declarativa compartida por todos los modelos ORM.
    Todos los modelos heredan de esta clase.
    """
    pass


def create_engine(database_url: str) -> AsyncEngine:
    """
    Crea el engine async con pool de conexiones configurado.
    `pool_pre_ping=True` verifica conexiones antes de usarlas.
    """
    engine = create_async_engine(
        database_url,
        pool_size=5,          # Conexiones permanentes en el pool
        max_overflow=10,      # Conexiones extra permitidas bajo carga
        pool_recycle=3600,    # Reciclar conexiones cada hora (evita stale connections)
        pool_timeout=30,      # Segundos de espera para obtener conexión
        pool_pre_ping=True,   # Verifica que la conexión esté viva
        echo=False,           # True solo en desarrollo para ver SQL
        connect_args={
            "server_settings": {"application_name": "polybot"}
        },
    )
    logger.info(
        "db_engine_created",
        pool_size=5,
        max_overflow=10,
    )
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """
    Crea la fábrica de sesiones async.
    `expire_on_commit=False`: los objetos siguen accesibles tras commit.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
