# src/domain/value_objects/market_tick.py

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketTick:
    """
    Snapshot inmutable del estado de un mercado en un momento dado.
    'frozen=True' garantiza que nadie lo modifica después de crearlo.
    """
    market_id:  str
    yes_price:  float       # Precio de comprar YES (0.0 - 1.0)
    no_price:   float       # Precio de comprar NO  (0.0 - 1.0)
    best_bid:   float       # Mejor oferta de compra
    best_ask:   float       # Mejor oferta de venta
    spread:     float       # Diferencia ask - bid
    volume_24h: float       # Volumen en USDC
    timestamp:  datetime

    @property
    def is_liquid(self) -> bool:
        """Atajos rápidos usados por los filtros de estrategia."""
        return self.volume_24h > 0 and self.spread < 1.0

    @property
    def mid_price(self) -> float:
        """Precio medio entre bid y ask."""
        return (self.best_bid + self.best_ask) / 2