# src/domain/value_objects/signal.py

from dataclasses import dataclass
from datetime import datetime

from src.domain.enums.signal_type import SignalType


@dataclass(frozen=True)
class Signal:
    """
    Decisión de la estrategia sobre qué hacer con un mercado.
    Incluye metadatos para auditoría y métricas.
    """
    type:            SignalType
    market_id:       str
    confidence:      float          # 0.0 - 1.0, qué tan fuerte es la señal
    source_strategy: str            # Nombre de la estrategia que la generó
    reason:          str            # Descripción legible del motivo
    timestamp:       datetime

    def is_actionable(self) -> bool:
        """Solo BUY_YES, BUY_NO y EXIT requieren acción del execution handler."""
        return self.type != SignalType.HOLD
