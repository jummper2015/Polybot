# src/strategies/buy_above_threshold/strategy.py

from datetime import datetime

import structlog

from src.domain.entities.market import Market
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal, SignalType
from src.infrastructure.observability.metrics import (
    BAT_CONSECUTIVE_TICKS,
    BAT_ENTRY_CONFIDENCE,
    FILTER_REJECTIONS,
    STRATEGY_CYCLES,
)
from src.strategies.base import IStrategy, StrategyState
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.filters.base import FilterResult, IFilter
from src.strategies.filters.liquidity_filter import LiquidityFilter
from src.strategies.filters.multi_timeframe import MultiTimeframeFilter
from src.strategies.filters.spread_filter import SpreadFilter
from src.strategies.filters.tick_confirmation import TickConfirmationFilter
from src.strategies.filters.time_filter import TimeFilter

logger = structlog.get_logger(__name__)

# Nombre único de la estrategia — usado en logs, métricas y DB
STRATEGY_NAME = "BuyAboveThreshold"


class BuyAboveThresholdStrategy(IStrategy):
    """
    Estrategia principal del bot.
    Compra YES cuando el precio supera un threshold,
    confirmado por N ticks consecutivos y validado por 4 filtros.
    Sale por StopLoss, StopDrop, Timeout o Target.
    Puede emitir señal de Hedge ante caídas bruscas.
    """

    def __init__(
        self,
        config: BuyAboveThresholdConfig | None = None,
    ):
        self._config = config or BuyAboveThresholdConfig()
        self._config.validate()

        # Construye los filtros con los parámetros de config
        # Orden de evaluación: Spread → Liquidity → Time → TickConfirmation
        self._filters: list[IFilter] = [
            SpreadFilter(max_spread=self._config.max_spread),
            LiquidityFilter(min_volume_usdc=self._config.min_volume_usdc),
            TimeFilter(blocked_hours=self._config.blocked_hours),
            TickConfirmationFilter(required_ticks=self._config.required_ticks),
        ]

        # Registro de estados por mercado: market_id → StrategyState
        # El StrategyEngine inyecta el estado, pero la estrategia
        # también mantiene referencia local para acceso directo
        self._states: dict[str, StrategyState] = {}

        # Filtro multi-timeframe (inyectado por el container)
        # Si es None, la confirmación multi-timeframe está deshabilitada
        self._mtf_filter: MultiTimeframeFilter | None = None

        logger.info(
            "strategy_initialized",
            strategy=STRATEGY_NAME,
            threshold=self._config.threshold,
            required_ticks=self._config.required_ticks,
            stop_loss_pct=self._config.stop_loss_pct,
            target_price=self._config.target_price,
        )

    # ------------------------------------------------------------------
    # CONTRATO IStrategy
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    async def on_cycle_start(self, market: Market) -> None:
        """
        Inicializa el estado del mercado si es la primera vez.
        Registra el inicio del ciclo en el log.
        """
        if market.id not in self._states:
            self._states[market.id] = StrategyState(
                market_id=market.id,
                strategy_name=STRATEGY_NAME,
            )

        state = self._states[market.id]

        logger.debug(
            "cycle_start",
            strategy=STRATEGY_NAME,
            market_id=market.id,
            asset=market.asset.value,
            window=market.window.value,
            in_position=state.in_position,
            consecutive_ticks=state.consecutive_ticks,
            minutes_left=round(market.minutes_to_expiry(), 1),
        )

    async def on_tick(self, market: Market, tick: MarketTick) -> None:
        """
        Actualiza contadores de confirmación y detecta caídas para hedge.
        Es el único lugar donde se incrementa/resetea consecutive_ticks.
        """
        state = self._get_or_create_state(market.id)
        state.add_tick(tick)

        # ── Contador de confirmación ──────────────────────────────────
        if tick.yes_price >= self._config.threshold:
            state.consecutive_ticks += 1
        else:
            # El precio bajó del threshold → reset completo del contador
            if state.consecutive_ticks > 0:
                logger.debug(
                    "threshold_broken",
                    market_id=market.id,
                    yes_price=tick.yes_price,
                    threshold=self._config.threshold,
                    ticks_reset=state.consecutive_ticks,
                )
            state.consecutive_ticks = 0

        # ── Detección de caída brusca para hedge ──────────────────────
        if len(state.tick_buffer) >= 2:
            prev_tick = state.tick_buffer[-2]
            if prev_tick.yes_price > 0:
                drop_pct = (
                    prev_tick.yes_price - tick.yes_price
                ) / prev_tick.yes_price
                state.extra["last_drop_pct"]   = drop_pct
                state.extra["last_drop_price"]  = tick.yes_price
                state.extra["last_drop_prev"]   = prev_tick.yes_price
            else:
                state.extra["last_drop_pct"] = 0.0
        else:
            state.extra["last_drop_pct"] = 0.0

        # Métrica: ticks consecutivos actuales por mercado
        BAT_CONSECUTIVE_TICKS.labels(
            market_id=market.id,
            asset=market.asset.value,
        ).set(state.consecutive_ticks)

    async def should_enter(self, market: Market, tick: MarketTick) -> Signal:
        """
        Evalúa condición de entrada:
        1. No estar ya en posición
        2. Precio YES >= threshold
        3. Todos los filtros pasan (short-circuit)
        4. Genera Signal con confidence proporcional a la distancia del threshold
        """
        state = self._get_or_create_state(market.id)
        log   = logger.bind(
            strategy=STRATEGY_NAME,
            market_id=market.id,
            yes_price=tick.yes_price,
        )

        # ── Guarda 1: ya en posición ──────────────────────────────────
        if state.in_position:
            return self._hold("already_in_position", market.id)

        # ── Guarda 2: precio bajo threshold ──────────────────────────
        if tick.yes_price < self._config.threshold:
            return self._hold(
                f"price={tick.yes_price:.4f} < threshold={self._config.threshold}",
                market.id,
            )

        # ── Guarda 3: mercado a punto de expirar (< 5 min) ───────────
        if market.minutes_to_expiry() < 5.0:
            return self._hold(
                f"market_expiring_soon: {market.minutes_to_expiry():.1f}min left",
                market.id,
            )

        # ── Filtros en cadena (short-circuit) ─────────────────────────
        for filt in self._filters:
            result: FilterResult = filt.apply(tick, state)
            if not result.passed:
                log.debug(
                    "filter_rejected",
                    filter=result.filter_name,
                    reason=result.reason,
                )
                FILTER_REJECTIONS.labels(filter_name=result.filter_name).inc()
                return self._hold(result.reason, market.id)

        # ── Confirmación multi-timeframe (M5 → M15) ──────────────────
        if self._mtf_filter is not None:
            mtf_result = await self._mtf_filter.apply(tick, state, market)
            if not mtf_result.passed:
                log.debug(
                    "mtf_filter_rejected",
                    filter=mtf_result.filter_name,
                    reason=mtf_result.reason,
                )
                return self._hold(mtf_result.reason, market.id)

        # ── Todos los filtros pasaron → calcular confidence ───────────
        # Normaliza la distancia entre threshold y 1.0
        # Ej: precio=0.82, threshold=0.75 → confidence = (0.82-0.75)/(1.0-0.75) = 0.28
        # Ej: precio=0.95, threshold=0.75 → confidence = (0.95-0.75)/(1.0-0.75) = 0.80
        price_range  = 1.0 - self._config.threshold
        confidence   = min(
            1.0,
            (tick.yes_price - self._config.threshold) / price_range
        ) if price_range > 0 else 1.0

        # ── Boost de confidence si M15 confirma (solo M5) ────────────
        if self._mtf_filter is not None and market.window == Window.M5:
            base_conf = confidence
            confidence = min(1.0, confidence * 1.25)
            log.debug(
                "mtf_confidence_boost",
                base_confidence=round(base_conf, 3),
                boosted_confidence=round(confidence, 3),
            )

        reason = (
            f"price={tick.yes_price:.4f} >= threshold={self._config.threshold}, "
            f"ticks={state.consecutive_ticks}, "
            f"spread={tick.spread:.4f}, "
            f"volume={tick.volume_24h:.0f}USDC"
        )

        log.info(
            "entry_signal_generated",
            confidence=round(confidence, 3),
            consecutive_ticks=state.consecutive_ticks,
            reason=reason,
        )

        BAT_ENTRY_CONFIDENCE.labels(
            market_id=market.id,
            asset=market.asset.value,
        ).observe(confidence)

        STRATEGY_CYCLES.labels(
            strategy=STRATEGY_NAME,
            result="signal_entry",
        ).inc()

        return Signal(
            type=SignalType.BUY_YES,
            market_id=market.id,
            confidence=round(confidence, 4),
            source_strategy=STRATEGY_NAME,
            reason=reason,
            timestamp=datetime.utcnow(),
        )

    async def should_exit(self, market: Market, tick: MarketTick) -> Signal:
        """
        Evalúa condiciones de salida en orden de prioridad.
        También evalúa hedge si está habilitado.
        Prioridad: StopLoss > StopDrop > Timeout > Target > Hedge > Hold
        """
        state = self._get_or_create_state(market.id)

        # ── Guarda: no hay posición ───────────────────────────────────
        if not state.in_position:
            return self._hold("no_open_position", market.id)

        entry_price = state.entry_price
        current     = tick.yes_price
        minutes     = state.minutes_in_position() or 0.0

        log = logger.bind(
            strategy=STRATEGY_NAME,
            market_id=market.id,
            entry_price=entry_price,
            current_price=current,
            minutes_in_position=round(minutes, 1),
        )

        # ── 1. STOP LOSS ──────────────────────────────────────────────
        # Calcula pérdida porcentual desde entrada
        loss_pct = (current - entry_price) / entry_price
        if loss_pct <= -self._config.stop_loss_pct:
            reason = (
                f"stop_loss: loss={loss_pct:.2%} <= "
                f"-{self._config.stop_loss_pct:.2%} "
                f"(entry={entry_price:.4f}, current={current:.4f})"
            )
            log.warning("exit_stop_loss", reason=reason)
            STRATEGY_CYCLES.labels(
                strategy=STRATEGY_NAME, result="exit_stop_loss"
            ).inc()
            return self._exit_signal(reason, market.id)

        # ── 2. STOP DROP ──────────────────────────────────────────────
        # Precio cayó por debajo del suelo absoluto
        if current < self._config.stop_drop_floor:
            reason = (
                f"stop_drop: price={current:.4f} < "
                f"floor={self._config.stop_drop_floor:.4f}"
            )
            log.warning("exit_stop_drop", reason=reason)
            STRATEGY_CYCLES.labels(
                strategy=STRATEGY_NAME, result="exit_stop_drop"
            ).inc()
            return self._exit_signal(reason, market.id)

        # ── 3. TIMEOUT ────────────────────────────────────────────────
        # Posición lleva demasiado tiempo abierta
        if minutes >= self._config.timeout_minutes:
            reason = (
                f"timeout: {minutes:.1f}min >= "
                f"{self._config.timeout_minutes:.0f}min limit"
            )
            log.info("exit_timeout", reason=reason)
            STRATEGY_CYCLES.labels(
                strategy=STRATEGY_NAME, result="exit_timeout"
            ).inc()
            return self._exit_signal(reason, market.id)

        # ── 4. TARGET ─────────────────────────────────────────────────
        # Precio alcanzó el objetivo de ganancia
        if current >= self._config.target_price:
            reason = (
                f"target_reached: price={current:.4f} >= "
                f"target={self._config.target_price:.4f}"
            )
            log.info("exit_target", reason=reason)
            STRATEGY_CYCLES.labels(
                strategy=STRATEGY_NAME, result="exit_target"
            ).inc()
            return self._exit_signal(reason, market.id)

        # ── 5. HEDGE ──────────────────────────────────────────────────
        # Caída brusca en 2 ticks → señal de cobertura parcial
        if self._config.hedge_enabled:
            last_drop = state.extra.get("last_drop_pct", 0.0)

            if (
                last_drop >= self._config.hedge_drop_pct
                and len(state.tick_buffer) >= 2
                and not state.in_position  # No hacer hedge sobre hedge
            ):
                reason = (
                    f"hedge_signal: drop={last_drop:.2%} >= "
                    f"threshold={self._config.hedge_drop_pct:.2%} "
                    f"(prev={state.extra.get('last_drop_prev', 0):.4f}, "
                    f"curr={state.extra.get('last_drop_price', 0):.4f})"
                )
                log.warning("hedge_signal_generated", reason=reason)
                STRATEGY_CYCLES.labels(
                    strategy=STRATEGY_NAME, result="signal_hedge"
                ).inc()

                return Signal(
                    type=SignalType.BUY_NO,
                    market_id=market.id,
                    confidence=min(1.0, last_drop / self._config.hedge_drop_pct),
                    source_strategy=STRATEGY_NAME,
                    reason=reason,
                    timestamp=datetime.utcnow(),
                )

        # ── Hold: ninguna condición de salida cumplida ────────────────
        log.debug(
            "holding_position",
            loss_pct=round(loss_pct, 4),
            minutes=round(minutes, 1),
            current=current,
        )
        return self._hold("holding_position", market.id)

    async def on_exit(self, market: Market) -> None:
        """
        Cierre del ciclo: emite métricas de estado y limpia datos temporales.
        No toma decisiones — solo observabilidad.
        """
        state = self._get_or_create_state(market.id)

        logger.debug(
            "cycle_end",
            strategy=STRATEGY_NAME,
            market_id=market.id,
            consecutive_ticks=state.consecutive_ticks,
            in_position=state.in_position,
            minutes_in_position=round(state.minutes_in_position() or 0, 1),
            cycle_count=state.cycle_count,
        )

        # Limpia el drop detectado en este ciclo (se recalcula en el siguiente)
        state.extra.pop("last_drop_pct",   None)
        state.extra.pop("last_drop_price", None)
        state.extra.pop("last_drop_prev",  None)

    # ------------------------------------------------------------------
    # UTILIDADES INTERNAS
    # ------------------------------------------------------------------

    def _get_or_create_state(self, market_id: str) -> StrategyState:
        """Obtiene o crea el estado local de la estrategia para un mercado."""
        if market_id not in self._states:
            self._states[market_id] = StrategyState(
                market_id=market_id,
                strategy_name=STRATEGY_NAME,
            )
        return self._states[market_id]

    def _hold(self, reason: str, market_id: str) -> Signal:
        """Factory para señales HOLD — evita repetición de boilerplate."""
        return Signal(
            type=SignalType.HOLD,
            market_id=market_id,
            confidence=0.0,
            source_strategy=STRATEGY_NAME,
            reason=reason,
            timestamp=datetime.utcnow(),
        )

    def _exit_signal(self, reason: str, market_id: str) -> Signal:
        """Factory para señales EXIT — siempre con confidence=1.0."""
        return Signal(
            type=SignalType.EXIT,
            market_id=market_id,
            confidence=1.0,
            source_strategy=STRATEGY_NAME,
            reason=reason,
            timestamp=datetime.utcnow(),
        )

    def set_mtf_filter(self, mtf_filter: MultiTimeframeFilter | None) -> None:
        """
        Inyecta el filtro multi-timeframe desde el container.
        None lo deshabilita. Llamado tras la creación de MarketService.
        """
        self._mtf_filter = mtf_filter
        logger.info(
            "mtf_filter_set",
            strategy=STRATEGY_NAME,
            enabled=mtf_filter is not None,
        )

    def update_config(self, new_config: BuyAboveThresholdConfig) -> None:
        """
        Actualiza la configuración en caliente (desde Telegram /settings).
        Valida antes de aplicar — si falla, mantiene la config anterior.
        """
        new_config.validate()
        old_config    = self._config
        self._config  = new_config

        # Reconstruye los filtros con la nueva config
        self._filters = [
            SpreadFilter(max_spread=self._config.max_spread),
            LiquidityFilter(min_volume_usdc=self._config.min_volume_usdc),
            TimeFilter(blocked_hours=self._config.blocked_hours),
            TickConfirmationFilter(required_ticks=self._config.required_ticks),
        ]

        logger.info(
            "config_updated",
            strategy=STRATEGY_NAME,
            old_threshold=old_config.threshold,
            new_threshold=new_config.threshold,
            old_stop_loss=old_config.stop_loss_pct,
            new_stop_loss=new_config.stop_loss_pct,
        )
