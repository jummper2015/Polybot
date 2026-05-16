# src/domain/value_objects/trade_result.py

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TradeResult:
    """
    Resultado de ejecutar una orden (paper o real).
    Captura lo que realmente ocurrió vs lo que se intentó.
    """
    order_id:    str
    market_id:   str
    side:        str            # YES o NO
    amount:      float          # USDC ejecutados
    target_price: float         # Precio al que se intentó
    fill_price:  float          # Precio al que se ejecutó
    slippage:    float          # fill_price - target_price
    pnl:         float | None   # None si la posición sigue abierta
    success:     bool
    mode:        str            # paper o real
    timestamp:   datetime
    error:       str | None = None  # Si success=False, describe el error