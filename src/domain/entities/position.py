# src/domain/entities/position.py

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    """
    Representa una posición abierta o cerrada en un mercado.
    Una posición agrupa una o varias órdenes del mismo mercado/lado.
    """
    id:            str
    market_id:     str
    asset:         str            # BTC o ETH
    window:        str            # 5m o 15m
    side:          str            # YES o NO
    amount:        float          # USDC totales invertidos
    shares:        float          # Tokens comprados
    entry_price:   float          # Precio medio de entrada
    exit_price:    float | None   # Precio de cierre (None si abierta)
    pnl:           float | None   # PnL realizado (None si abierta)
    pnl_pct:       float | None   # PnL % (None si abierta)
    mode:          str            # paper o real
    strategy:      str            # Estrategia que abrió la posición
    exit_reason:   str | None     # Motivo de cierre
    opened_at:     datetime = field(default_factory=datetime.utcnow)
    closed_at:     datetime | None = None
    # Ola 2.1: timestamp de resolución del mercado (event WS market_resolved).
    # Marcado en TradingService._on_ws_market_resolved. Cuando != None,
    # la posición NO se puede vender (mercado ya expirado en el CLOB) —
    # solo se puede redimir vía CTF (R2.0 pendiente).
    resolved_at:   datetime | None = None

    @property
    def is_open(self) -> bool:
        """Verdadero si la posición sigue abierta."""
        return self.closed_at is None

    @property
    def is_resolved(self) -> bool:
        """
        Ola 2.1: True si el WS notificó que el mercado se resolvió.
        Una posición resuelta abierta requiere redeem CTF (R2.0), no exit.
        """
        return self.resolved_at is not None

    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """
        PnL no realizado: cuánto valdría la posición si se cerrara ahora.
        Fórmula: (precio_actual - precio_entrada) * shares
        """
        return (current_price - self.entry_price) * self.shares

    def calculate_unrealized_pnl_pct(self, current_price: float) -> float:
        """PnL no realizado en porcentaje sobre el capital invertido."""
        if self.amount <= 0:
            return 0.0
        return self.calculate_unrealized_pnl(current_price) / self.amount

    def close(
        self,
        exit_price: float,
        reason:     str,
    ) -> None:
        """
        Cierra la posición calculando PnL realizado.
        Fórmula: (exit_price - entry_price) * shares
        """
        self.exit_price  = exit_price
        self.exit_reason = reason
        self.closed_at   = datetime.utcnow()
        self.pnl         = (exit_price - self.entry_price) * self.shares
        self.pnl_pct     = self.pnl / self.amount if self.amount > 0 else 0.0
