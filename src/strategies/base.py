# src/strategies/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.domain.entities.market import Market
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal, SignalType


@dataclass
class StrategyState:
    """
    Estado mutable de una estrategia para un mercado específico.
    Cada par (estrategia, mercado) tiene su propio estado aislado.
    Se resetea en cada on_cycle_start() si el ciclo es nuevo.
    """
    market_id:          str
    strategy_name:      str

    # Acumulador de ticks para confirmación
    tick_buffer:        list[MarketTick]    = field(default_factory=list)
    consecutive_ticks:  int                 = 0     # Ticks consecutivos cumpliendo condición

    # Estado de la señal actual
    last_signal:        SignalType | None   = None
    last_signal_at:     datetime | None     = None

    # Precio de entrada (para calcular stop loss)
    entry_price:        float | None        = None
    entry_at:           datetime | None     = None

    # Flags de control
    in_position:        bool                = False
    cycle_count:        int                 = 0     # Cuántos ciclos llevamos
    last_cycle_at:      datetime | None     = None

    # Metadata libre para estrategias específicas
    extra:              dict[str, Any]      = field(default_factory=dict)

    def reset_tick_buffer(self) -> None:
        """Limpia el buffer de ticks y el contador de confirmación."""
        self.tick_buffer       = []
        self.consecutive_ticks = 0

    def add_tick(self, tick: MarketTick) -> None:
        """
        Añade un tick al buffer.
        Mantiene solo los últimos 20 ticks para no crecer indefinidamente.
        """
        self.tick_buffer.append(tick)
        if len(self.tick_buffer) > 20:
            self.tick_buffer.pop(0)

    def record_entry(self, price: float) -> None:
        """Registra el precio de entrada cuando se abre posición."""
        self.entry_price = price
        self.entry_at    = datetime.utcnow()
        self.in_position = True

    def record_exit(self) -> None:
        """Limpia el estado de posición al cerrarla."""
        self.entry_price = None
        self.entry_at    = None
        self.in_position = False
        self.reset_tick_buffer()

    def minutes_in_position(self) -> float | None:
        """Minutos transcurridos desde la entrada. None si no hay posición."""
        if not self.entry_at:
            return None
        return (datetime.utcnow() - self.entry_at).total_seconds() / 60


class IStrategy(ABC):
    """
    Contrato que TODA estrategia debe implementar.
    Define el ciclo completo de vida de una estrategia por mercado.

    Contrato de uso (orden de llamada garantizado por StrategyEngine):
        1. on_cycle_start(market)
        2. on_tick(market, tick)          ← puede llamarse N veces por ciclo
        3. should_enter(market, tick)     → Signal
        4. should_exit(market, tick)      → Signal
        5. on_exit(market)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Identificador único de la estrategia. Usado en logs y métricas."""
        ...

    @abstractmethod
    async def on_cycle_start(self, market: Market) -> None:
        """
        Llamado al inicio de cada ciclo (cada 30s).
        Inicializa o actualiza el estado para este mercado.
        NO hace llamadas externas — solo prepara estado interno.
        """
        ...

    @abstractmethod
    async def on_tick(self, market: Market, tick: MarketTick) -> None:
        """
        Llamado cada vez que llega un tick nuevo.
        Actualiza el estado interno: buffers, contadores, histórico.
        NO toma decisiones de trading — solo acumula información.
        """
        ...

    @abstractmethod
    async def should_enter(self, market: Market, tick: MarketTick) -> Signal:
        """
        Evalúa si se debe abrir una posición.
        SIEMPRE devuelve una Signal (nunca None).
        Si no hay señal de entrada: devuelve Signal(type=HOLD).
        """
        ...

    @abstractmethod
    async def should_exit(self, market: Market, tick: MarketTick) -> Signal:
        """
        Evalúa si se debe cerrar la posición existente.
        SIEMPRE devuelve una Signal (nunca None).
        Si no hay razón para salir: devuelve Signal(type=HOLD).
        """
        ...

    @abstractmethod
    async def on_exit(self, market: Market) -> None:
        """
        Llamado al final de cada ciclo.
        Limpieza, persistencia de estado, métricas por ciclo.
        NO toma decisiones — solo cierra el ciclo limpiamente.
        """
        ...
