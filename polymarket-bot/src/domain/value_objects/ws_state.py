# src/domain/value_objects/ws_state.py

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class WSConnectionStatus(str, Enum):
    CONNECTING    = "connecting"
    CONNECTED     = "connected"
    RECONNECTING  = "reconnecting"
    DISCONNECTED  = "disconnected"
    FAILED        = "failed"        # Superó máximo de reintentos


@dataclass
class WSMarketState:
    """
    Estado mutable de la conexión WebSocket para un mercado específico.
    Guardado en Redis para que sea visible desde cualquier parte del sistema.
    """
    market_id:          str
    status:             WSConnectionStatus = WSConnectionStatus.DISCONNECTED
    last_tick:          MarketTick | None  = None
    last_message_at:    datetime | None    = None
    reconnect_attempts: int                = 0
    connected_at:       datetime | None    = None
    error:              str | None         = None

    def record_connected(self) -> None:
        """Marca la conexión como establecida y resetea reintentos."""
        self.status             = WSConnectionStatus.CONNECTED
        self.connected_at       = datetime.utcnow()
        self.reconnect_attempts = 0
        self.error              = None

    def record_message(self, tick: "MarketTick") -> None:
        """Actualiza el último tick recibido y timestamp."""
        self.last_tick       = tick
        self.last_message_at = datetime.utcnow()

    def record_reconnecting(self, error: str) -> None:
        """Incrementa contador de reintentos y guarda el error."""
        self.status             = WSConnectionStatus.RECONNECTING
        self.reconnect_attempts += 1
        self.error              = error

    def is_stale(self, timeout_seconds: int = 30) -> bool:
        """
        Verdadero si no hemos recibido mensajes en más de timeout_seconds.
        Indica que la conexión está colgada sin haberse cerrado.
        """
        if not self.last_message_at:
            return True
        elapsed = (datetime.utcnow() - self.last_message_at).total_seconds()
        return elapsed > timeout_seconds