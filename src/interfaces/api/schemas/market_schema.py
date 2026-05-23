# src/interfaces/api/schemas/market_schema.py

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AssetEnum(str, Enum):
    BTC = "BTC"
    ETH = "ETH"


class WindowEnum(str, Enum):
    M5  = "5m"
    M15 = "15m"


class MarketStatusEnum(str, Enum):
    ACTIVE   = "active"
    EXPIRED  = "expired"
    RESOLVED = "resolved"


class MarketResponse(BaseModel):
    """Schema de respuesta para un mercado individual."""
    id:            str
    asset:         AssetEnum
    window:        WindowEnum
    status:        MarketStatusEnum
    yes_price:     float = Field(..., ge=0.0, le=1.0, description="Precio YES entre 0 y 1")
    no_price:      float = Field(..., ge=0.0, le=1.0, description="Precio NO entre 0 y 1")
    volume_24h:    float = Field(..., ge=0.0, description="Volumen 24h en USDC")
    expiry:        datetime
    discovered_at: datetime

    model_config = {"from_attributes": True}  # Pydantic v2: permite from ORM


class MarketsListResponse(BaseModel):
    """Lista paginada de mercados."""
    total:   int
    markets: list[MarketResponse]
