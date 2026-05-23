# src/domain/entities/order.py

from dataclasses import dataclass, field
from datetime import datetime

from src.domain.enums.order_side import OrderSide
from src.domain.enums.order_status import OrderStatus
from src.domain.enums.trading_mode import TradingMode


@dataclass
class Order:
    """
    Entidad que representa una orden de compra/venta en Polymarket.
    Existe tanto en paper como en real — el modo diferencia el handler.
    """
    id:            str
    market_id:     str
    side:          OrderSide
    amount:        float          # USDC invertidos
    target_price:  float          # Precio al que se intentó ejecutar
    fill_price:    float | None   # Precio real de fill (None si pending)
    slippage:      float | None   # fill_price - target_price
    status:        OrderStatus
    mode:          TradingMode
    strategy:      str            # Nombre de la estrategia que generó la orden
    reason:        str            # Motivo de la orden (para auditoría)
    idempotency_key: str | None = None  # SHA256 determinista para desduplicación (P1.4)
    created_at:    datetime = field(default_factory=datetime.utcnow)
    filled_at:     datetime | None = None
    error:         str | None = None

    @property
    def shares(self) -> float | None:
        """Unidades de token compradas. None si no se ha ejecutado."""
        if self.fill_price and self.fill_price > 0:
            return self.amount / self.fill_price
        return None

    def mark_filled(self, fill_price: float, slippage: float) -> None:
        """Marca la orden como ejecutada con el precio real."""
        self.fill_price = fill_price
        self.slippage   = slippage
        self.status     = OrderStatus.FILLED
        self.filled_at  = datetime.utcnow()

    def mark_failed(self, error: str) -> None:
        """Marca la orden como fallida con el motivo del error."""
        self.status = OrderStatus.FAILED
        self.error  = error
