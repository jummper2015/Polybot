# src/domain/entities/market.py

from dataclasses import dataclass, field
from datetime import datetime

from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window


@dataclass
class Market:
    """
    Entidad central del dominio.
    Representa un mercado de predicción BTC/ETH en Polymarket.
    Puro Python — sin dependencias externas.
    """
    id:            str                          # condition_id de Polymarket
    asset:         Asset                        # BTC o ETH
    window:        Window                       # 5m o 15m
    question:      str                          # Texto de la pregunta del mercado
    status:        MarketStatus
    yes_token_id:  str                          # Token ID para comprar YES
    no_token_id:   str                          # Token ID para comprar NO
    yes_price:     float                        # Último precio YES (0.0 - 1.0)
    no_price:      float                        # Último precio NO (0.0 - 1.0)
    volume_24h:    float                        # Volumen en USDC últimas 24h
    expiry:        datetime                     # Cuándo cierra el mercado
    neg_risk:      bool    = False  # Mercado neg_risk (requiere negRisk=True en órdenes)
    tick_size:     str     = "0.01"  # Tick size ("0.01", "0.001", "0.0001")
    min_order_size: float  = 1.0     # Minimum order size en pUSD
    discovered_at: datetime = field(default_factory=datetime.utcnow)
    updated_at:    datetime = field(default_factory=datetime.utcnow)

    def is_active(self) -> bool:
        """Verdadero si el mercado está activo y no ha expirado."""
        return (
            self.status == MarketStatus.ACTIVE
            and self.expiry > datetime.utcnow()
        )

    def minutes_to_expiry(self) -> float:
        """Minutos restantes hasta que cierre el mercado."""
        delta = self.expiry - datetime.utcnow()
        return max(0.0, delta.total_seconds() / 60)

    def update_prices(self, yes_price: float, no_price: float, volume: float) -> None:
        """Actualiza precios y volumen, registra timestamp."""
        self.yes_price  = yes_price
        self.no_price   = no_price
        self.volume_24h = volume
        self.updated_at = datetime.utcnow()
