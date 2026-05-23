# src/interfaces/api/schemas/position_schema.py

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SideEnum(str, Enum):
    YES = "YES"
    NO  = "NO"


class TradingModeEnum(str, Enum):
    PAPER = "paper"
    REAL  = "real"


class PositionResponse(BaseModel):
    """Schema de respuesta para una posición abierta o cerrada."""
    id:            str
    market_id:     str
    asset:         str                          # BTC o ETH
    window:        str                          # 5m o 15m
    side:          SideEnum
    amount:        float = Field(..., ge=0.0)   # USDC invertidos
    entry_price:   float = Field(..., ge=0.0, le=1.0)
    current_price: float | None = None          # None si está cerrada
    pnl:           float | None = None          # PnL realizado o None si abierta
    pnl_pct:       float | None = None          # PnL en porcentaje
    mode:          TradingModeEnum
    opened_at:     datetime
    closed_at:     datetime | None = None

    model_config = {"from_attributes": True}


class PositionsListResponse(BaseModel):
    """Lista de posiciones con resumen de PnL."""
    total:      int
    open:       int
    closed:     int
    total_pnl:  float                           # PnL agregado de posiciones cerradas
    positions:  list[PositionResponse]
