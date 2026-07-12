# src/infrastructure/db/models.py

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.session import Base


class MarketModel(Base):
    """
    Tabla: markets
    Almacena mercados BTC/ETH descubiertos en Polymarket.
    """
    __tablename__ = "markets"

    id:            Mapped[str]      = mapped_column(String(100), primary_key=True)
    asset:         Mapped[str]      = mapped_column(String(10), nullable=False)
    window:        Mapped[str]      = mapped_column(String(5),  nullable=False)
    question:      Mapped[str]      = mapped_column(Text,       nullable=False)
    status:        Mapped[str]      = mapped_column(String(20), nullable=False, default="active")
    yes_token_id:  Mapped[str]      = mapped_column(String(100), nullable=False, default="")
    no_token_id:   Mapped[str]      = mapped_column(String(100), nullable=False, default="")
    yes_price:     Mapped[float]    = mapped_column(Float, nullable=False, default=0.5)
    no_price:      Mapped[float]    = mapped_column(Float, nullable=False, default=0.5)
    volume_24h:    Mapped[float]    = mapped_column(Float, nullable=False, default=0.0)
    expiry:        Mapped[datetime] = mapped_column(DateTime, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at:    Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Índices para queries frecuentes
    __table_args__ = (
        Index("ix_markets_asset_window", "asset", "window"),
        Index("ix_markets_status",       "status"),
        Index("ix_markets_expiry",       "expiry"),
        # R2.5.4: unique on (asset, window, expiry) — previene inserciones
        # duplicadas durante el discovery (mismo mercado lógico, distinto
        # condition_id). Gestionado vía raw SQL en migración 005.
    )


class OrderModel(Base):
    """
    Tabla: orders
    Almacena todas las órdenes (paper y real), incluyendo fallidas.
    Nunca se borra — registro histórico completo.
    """
    __tablename__ = "orders"

    id:           Mapped[str]            = mapped_column(String(36), primary_key=True)  # UUID
    market_id:    Mapped[str]            = mapped_column(String(100), nullable=False)
    side:         Mapped[str]            = mapped_column(String(5),   nullable=False)   # YES/NO
    amount:       Mapped[float]          = mapped_column(Float, nullable=False)
    target_price: Mapped[float]          = mapped_column(Float, nullable=False)
    fill_price:   Mapped[float | None]   = mapped_column(Float, nullable=True)
    slippage:     Mapped[float | None]   = mapped_column(Float, nullable=True)
    status:       Mapped[str]            = mapped_column(String(20), nullable=False)
    mode:         Mapped[str]            = mapped_column(String(10), nullable=False)    # paper/real
    strategy:     Mapped[str]            = mapped_column(String(50), nullable=False)
    reason:       Mapped[str]            = mapped_column(Text, nullable=False, default="")
    error:        Mapped[str | None]     = mapped_column(Text, nullable=True)
    retry_count:  Mapped[int]            = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_retry_at:Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    idempotency_key: Mapped[str | None]  = mapped_column(
        String(32), nullable=True, unique=True
    )
    created_at:   Mapped[datetime]       = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    filled_at:    Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_orders_market_id", "market_id"),
        Index("ix_orders_status",    "status"),
        Index("ix_orders_mode",      "mode"),
        Index("ix_orders_created",   "created_at"),
        # Index for idempotency key lookups to prevent duplicate orders
        Index("ix_orders_idempotency", "idempotency_key"),
    )


class PositionModel(Base):
    """
    Tabla: positions
    Almacena posiciones abiertas y cerradas con su PnL.
    `closed_at IS NULL` = posición abierta.
    """
    __tablename__ = "positions"

    id:           Mapped[str]            = mapped_column(String(36), primary_key=True)
    market_id:    Mapped[str]            = mapped_column(String(100), nullable=False)
    asset:        Mapped[str]            = mapped_column(String(10),  nullable=False)
    window:       Mapped[str]            = mapped_column(String(5),   nullable=False)
    side:         Mapped[str]            = mapped_column(String(5),   nullable=False)
    amount:       Mapped[float]          = mapped_column(Float, nullable=False)
    shares:       Mapped[float]          = mapped_column(Float, nullable=False)
    entry_price:  Mapped[float]          = mapped_column(Float, nullable=False)
    exit_price:   Mapped[float | None]   = mapped_column(Float, nullable=True)
    pnl:          Mapped[float | None]   = mapped_column(Float, nullable=True)
    pnl_pct:      Mapped[float | None]   = mapped_column(Float, nullable=True)
    mode:         Mapped[str]            = mapped_column(String(10), nullable=False)
    strategy:     Mapped[str]            = mapped_column(String(50), nullable=False)
    exit_reason:  Mapped[str | None]     = mapped_column(Text, nullable=True)
    opened_at:    Mapped[datetime]       = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    closed_at:    Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_positions_market_id",  "market_id"),
        Index("ix_positions_mode",       "mode"),
        Index("ix_positions_closed_at",  "closed_at"),   # NULL = abierta
        Index("ix_positions_opened_at",  "opened_at"),
        # R2.5.3: unique partial index on (market_id, mode) WHERE closed_at IS NULL.
        # Gestionado vía raw SQL en migración 005 (SQLAlchemy no soporta
        # partial indexes nativamente en __table_args__).
    )


class AuditLogModel(Base):
    """
    Tabla: audit_logs
    Registro inmutable de todas las operaciones reales.
    NUNCA se hace UPDATE ni DELETE sobre esta tabla.
    INSERT only.
    """
    __tablename__ = "audit_logs"

    id:          Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    action:      Mapped[str]      = mapped_column(String(50),  nullable=False)
    order_id:    Mapped[str|None] = mapped_column(String(36),  nullable=True)
    market_id:   Mapped[str|None] = mapped_column(String(100), nullable=True)
    amount:      Mapped[float|None] = mapped_column(Float,     nullable=True)
    details:     Mapped[dict]     = mapped_column(JSON,        nullable=False, default=dict)
    timestamp:   Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_action",    "action"),
        Index("ix_audit_order_id",  "order_id"),
        Index("ix_audit_timestamp", "timestamp"),
    )


class BotSettingsModel(Base):
    """
    Tabla: bot_settings
    Almacena configuración del bot como clave-valor.
    Permite cambios en caliente sin reiniciar la aplicación.
    """
    __tablename__ = "bot_settings"

    key:        Mapped[str]      = mapped_column(String(50), primary_key=True)
    value:      Mapped[str]      = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
