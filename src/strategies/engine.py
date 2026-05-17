# src/strategies/engine.py

import structlog
from datetime import datetime

from src.domain.entities.market import Market
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal, SignalType
from src.strategies.base import IStrategy, StrategyState
from src.infrastructure.observability.metrics import (
    STRATEGY_CYCLES,
    STRATEGY_ERRORS,
    FILTER_REJECTIONS,
)

logger = structlog.get_logger(__name__)


class StrategyEngine:
    """
    Orquestador de estrategias.
    Mantiene el registro de estrategias activas y gestiona
    el estado por (estrategia, mercado).
    Garantiza el orden de llamada del contrato IStrategy.
    """

    def __init__(self, strategies: list[IStrategy]):
        # Lista de estrategias registradas (en orden de prioridad)
        self._strategies = strategies

        # Estado por (strategy_name, market_id) → StrategyState
        self._states: dict[tuple[str, str], StrategyState] = {}

        # Señal de HOLD por defecto (reutilizable)
        self._HOLD = lambda market_id, strategy_name: Signal(
            type=SignalType.HOLD,
            market_id=market_id,
            confidence=0.0,
            source_strategy=strategy_name,
            reason="no_signal",
            timestamp=datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # GESTIÓN DE ESTADO
    # ------------------------------------------------------------------

    def _get_state(self, strategy: IStrategy, market: Market) -> StrategyState:
        """
        Obtiene o crea el StrategyState para un par (estrategia, mercado).
        Estado persiste entre ciclos para el mismo mercado.
        """
        key = (strategy.name, market.id)
        if key not in self._states:
            self._states[key] = StrategyState(
                market_id=market.id,
                strategy_name=strategy.name,
            )
        return self._states[key]

    def _clear_state(self, market_id: str) -> None:
        """
        Elimina el estado de todas las estrategias para un mercado.
        Llamado cuando el mercado expira o se desactiva.
        """
        keys_to_remove = [
            k for k in self._states if k[1] == market_id
        ]
        for k in keys_to_remove:
            del self._states[k]

    # ------------------------------------------------------------------
    # CICLO COMPLETO (implementa el contrato IStrategy para N estrategias)
    # ------------------------------------------------------------------

    async def on_cycle_start(self, market: Market) -> None:
        """
        Notifica a todas las estrategias que comienza un ciclo.
        Inyecta el StrategyState correspondiente antes de llamar.
        """
        for strategy in self._strategies:
            state = self._get_state(strategy, market)
            state.cycle_count    += 1
            state.last_cycle_at   = datetime.utcnow()
            try:
                await strategy.on_cycle_start(market)
            except Exception as e:
                logger.error(
                    "strategy_on_cycle_start_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()

    async def on_tick(self, market: Market, tick: MarketTick) -> None:
        """
        Pasa el tick a todas las estrategias para que actualicen su estado.
        Errores en una estrategia no afectan a las demás.
        """
        for strategy in self._strategies:
            state = self._get_state(strategy, market)
            state.add_tick(tick)
            try:
                await strategy.on_tick(market, tick)
            except Exception as e:
                logger.error(
                    "strategy_on_tick_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()

    async def should_enter(
        self, market: Market, tick: MarketTick
    ) -> Signal:
        """
        Consulta a cada estrategia si debe entrar.
        Devuelve la PRIMERA señal accionable encontrada.
        Si ninguna estrategia tiene señal → HOLD.
        Las estrategias se evalúan en orden de registro.
        """
        for strategy in self._strategies:
            state = self._get_state(strategy, market)

            # No entra si ya hay posición abierta para este mercado
            if state.in_position:
                continue

            try:
                signal = await strategy.should_enter(market, tick)

                if signal.is_actionable():
                    logger.info(
                        "entry_signal",
                        strategy=strategy.name,
                        market_id=market.id,
                        signal_type=signal.type.value,
                        confidence=signal.confidence,
                        reason=signal.reason,
                    )
                    STRATEGY_CYCLES.labels(
                        strategy=strategy.name,
                        result="signal_entry",
                    ).inc()
                    return signal

            except Exception as e:
                logger.error(
                    "strategy_should_enter_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()

        return self._HOLD(market.id, "StrategyEngine")

    async def should_exit(
        self, market: Market, tick: MarketTick
    ) -> Signal:
        """
        Consulta a cada estrategia si debe salir de posición existente.
        Devuelve la PRIMERA señal de salida encontrada.
        Solo evalúa estrategias que tienen posición abierta.
        """
        for strategy in self._strategies:
            state = self._get_state(strategy, market)

            # Solo evalúa salida si hay posición abierta
            if not state.in_position:
                continue

            try:
                signal = await strategy.should_exit(market, tick)

                if signal.is_actionable():
                    logger.info(
                        "exit_signal",
                        strategy=strategy.name,
                        market_id=market.id,
                        signal_type=signal.type.value,
                        reason=signal.reason,
                    )
                    STRATEGY_CYCLES.labels(
                        strategy=strategy.name,
                        result="signal_exit",
                    ).inc()
                    return signal

            except Exception as e:
                logger.error(
                    "strategy_should_exit_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()

        return self._HOLD(market.id, "StrategyEngine")

    async def on_cycle_end(self, market: Market) -> None:
        """
        Notifica a todas las estrategias que terminó el ciclo.
        Oportunidad para limpiar estado temporal y emitir métricas.
        """
        for strategy in self._strategies:
            try:
                await strategy.on_cycle_end(market)
            except Exception as e:
                logger.error(
                    "strategy_on_cycle_end_error",
                    strategy=strategy.name,
                    market_id=market.id,
                    error=str(e),
                )
                STRATEGY_ERRORS.labels(strategy=strategy.name).inc()

    # ------------------------------------------------------------------
    # UTILIDADES
    # ------------------------------------------------------------------

    def mark_entry(self, strategy_name: str, market_id: str, price: float) -> None:
        """
        Llamado por TradingService cuando una orden se ejecuta exitosamente.
        Actualiza el estado de la estrategia para reflejar posición abierta.
        """
        key   = (strategy_name, market_id)
        state = self._states.get(key)
        if state:
            state.record_entry(price)
            logger.debug(
                "strategy_entry_recorded",
                strategy=strategy_name,
                market_id=market_id,
                price=price,
            )

    def mark_exit(self, strategy_name: str, market_id: str) -> None:
        """
        Llamado por TradingService cuando se cierra una posición.
        Resetea el estado de la estrategia para ese mercado.
        """
        key   = (strategy_name, market_id)
        state = self._states.get(key)
        if state:
            state.record_exit()
            logger.debug(
                "strategy_exit_recorded",
                strategy=strategy_name,
                market_id=market_id,
            )

    def get_state(
        self, strategy_name: str, market_id: str
    ) -> StrategyState | None:
        """Acceso de lectura al estado de una estrategia para un mercado."""
        return self._states.get((strategy_name, market_id))

    def registered_strategies(self) -> list[str]:
        """Lista de nombres de estrategias registradas."""
        return [s.name for s in self._strategies]