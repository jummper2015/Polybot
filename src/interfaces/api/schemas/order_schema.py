# src/interfaces/api/schemas/order_schema.py

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class OrderStatusEnum(str, Enum):
    PENDING   = "pending"
    FILLED    = "filled"
    CANCELLED = "cancelled"
    FAILED    = "failed"


class OrderResponse(BaseModel):
    """Schema de respuesta para una orden individual."""
    id:           str
    market_id:    str
    side:         str                           # YES o NO
    amount:       float = Field(..., ge=0.0)
    price:        float = Field(..., ge=0.0, le=1.0)
    fill_price:   float | None = None           # Precio real de fill
    slippage:     float | None = None           # Diferencia entry vs fill
    status:       OrderStatusEnum
    mode:         str                           # paper o real
    strategy:     str                           # Nombre de la estrategia
    created_at:   datetime
    filled_at:    datetime | None = None

    model_config = {"from_attributes": True}


class OrdersListResponse(BaseModel):
    """Lista paginada de órdenes."""
    total:  int
    orders: list[OrderResponse]
