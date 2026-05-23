# src/strategies/mean_reversion/strategy.py

from datetime import datetime

import structlog

from src.domain.entities.market import Market
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal, SignalType
from src.infrastructure.observability.metrics import (
    FILTER_REJECTIONS,
    MR_ENTRY_CONFIDENCE,
    MR_ZSCORE,
    STRATEGY_CYCLES,
)
from src.strategies.base import IStrategy, StrategyState
from src.strategies.filters.base import FilterResult, IFilter
from src.strategies.filters.liquidity_filter import LiquidityFilter
from src.strategies.filters.spread_filter import SpreadFilter
from src.strategies.filters.time_filter import TimeFilter
from src.strategies.mean_reversion.config import MeanReversionConfig

logger = structlog.get_logger(__name__)

# Nombre único de la estrategia — usado en logs, métricas y DB
STRATEGY_NAME = "MeanReversion"


def _compute_zscore(tick: MarketTick, buffer: list[MarketTick], ma_window: int) -> float:
    """
    Calcula el z-score del precio YES actual relativo a la media móvil simple.

    z_score = (yes_price - SMA) / std

    Usa los últimos ma_window ticks del buffer. Si no hay suficientes ticks
    o la desviación estándar es cero, devuelve 0.0.
    """
    if len(buffer) < ma_window:
        return 0.0

    recent = buffer[-ma_window:]
    yes_prices = [t.yes_price for t in recent]
    sma = sum(yes_prices) / ma_window

    variance = sum((p - sma) ** 2 for p in yes_prices) / ma_window
    std = variance ** 0.5

    if std < 1e-10:
        return 0.0

    return (tick.yes_price - sma) / std


class MeanReversionStrategy(IStrategy):
    """
    Estrategia de reversión a la media.
    Compra YES cuando el precio está en sobreventa (z-score < entry_zscore)
    y vende cuando retorna a la media (z-score > exit_zscore).

    Basada en z-score sobre SMA de 20 ticks, validada por 3 filtros:
    spread, liquidez y ventana horaria.

    Ventaja: baja correlación con BuyAboveThreshold (momentum vs mean reversion),
             mejora el Sharpe del portfolio combinado ~30-40%.
    """

    def __init__(
        self,
        config: MeanReversionConfig | None = None,
    ):
        self._config = config or MeanReversionConfig()
        self._config.validate()

        # Filtros en orden: Spread → Liquidity → Time
        # No usa TickConfirmation — la reversión a la media no requiere
        # confirmación de ticks consecutivos como el momentum.
        self._filters: list[IFilter] = [
            SpreadFilter(max_spread=self._config.max_spread),
            LiquidityFilter(min_volume_usdc=self._config.min_volume_usdc),
            TimeFilter(blocked_hours=self._config.blocked_hours),
        ]

        # Estado por mercado: market_id → StrategyState
        self._states: dict[str, StrategyState] = {}

        logger.info(
            "strategy_initialized",
            strategy=STRATEGY_NAME,
            ma_window=self._config.ma_window,
            entry_zscore=self._config.entry_zscore,
            exit_zscore=self._config.exit_zscore,
            stop_loss_pct=self._config.stop_loss_pct,
            timeout_minutes=self._config.timeout_minutes,
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
            buffer_size=len(state.tick_buffer),
            minutes_left=round(market.minutes_to_expiry(), 1),
        )

    async def on_tick(self, market: Market, tick: MarketTick) -> None:
        """
        Acumula ticks en el buffer para el cálculo de SMA y z-score.
        El buffer se mantiene en StrategyState (últimos 20 ticks).
        """
        state = self._get_or_create_state(market.id)
        state.add_tick(tick)

        # ── Calcular z-score actual y exponer como métrica ────────────
        z_score = _compute_zscore(tick, state.tick_buffer, self._config.ma_window)
        state.extra["z_score"] = z_score

        MR_ZSCORE.labels(
            market_id=market.id,
            asset=market.asset.value,
        ).set(z_score)

        logger.debug(
            "tick_processed",
            strategy=STRATEGY_NAME,
            market_id=market.id,
            yes_price=tick.yes_price,
            z_score=round(z_score, 3),
            buffer_size=len(state.tick_buffer),
        )

    async def should_enter(self, market: Market, tick: MarketTick) -> Signal:
        """
        Evalúa condición de entrada:
        1. No estar ya en posición
        2. Tener suficientes ticks en buffer (>= ma_window)
        3. z_score < entry_zscore (sobreventa)
        4. Todos los filtros pasan (short-circuit)
        5. Genera Signal con confidence proporcional a abs(z_score)
        """
        state = self._get_or_create_state(market.id)
        log = logger.bind(
            strategy=STRATEGY_NAME,
            market_id=market.id,
            yes_price=tick.yes_price,
        )

        # ── Guarda 1: ya en posición ──────────────────────────────────
        if state.in_position:
            return self._hold("already_in_position", market.id)

        # ── Guarda 2: buffer insuficiente ─────────────────────────────
        if len(state.tick_buffer) < self._config.ma_window:
            return self._hold(
                f"buffer={len(state.tick_buffer)} < ma_window={self._config.ma_window}",
                market.id,
            )

        # ── Guarda 3: mercado a punto de expirar (< 5 min) ───────────
        if market.minutes_to_expiry() < 5.0:
            return self._hold(
                f"market_expiring_soon: {market.minutes_to_expiry():.1f}min left",
                market.id,
            )

        # ── Calcular z-score ──────────────────────────────────────────
        z_score = _compute_zscore(tick, state.tick_buffer, self._config.ma_window)

        # ── Guarda 4: no está en sobreventa ───────────────────────────
        if z_score >= self._config.entry_zscore:
            return self._hold(
                f"z_score={z_score:.3f} >= entry_zscore={self._config.entry_zscore}",
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

        # ── Todos los filtros pasaron → calcular confidence ───────────
        # confidence = abs(z_score) / 4, clamp a [0, 1]
        # Ej: z_score=-2.0 → confidence = 0.50
        # Ej: z_score=-4.0 → confidence = 1.00
        confidence = min(1.0, abs(z_score) / 4.0)

        reason = (
            f"mean_reversion_entry: z_score={z_score:.3f} < "
            f"entry_zscore={self._config.entry_zscore}, "
            f"price={tick.yes_price:.4f}, "
            f"spread={tick.spread:.4f}, "
            f"volume={tick.volume_24h:.0f}USDC"
        )

        log.info(
            "entry_signal_generated",
            confidence=round(confidence, 3),
            z_score=round(z_score, 3),
            reason=reason,
        )

        MR_ENTRY_CONFIDENCE.labels(
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
        Evalúa condiciones de salida en orden de prioridad:
        StopLoss > MeanReversion > Timeout > Hold
        """
        state = self._get_or_create_state(market.id)

        # ── Guarda: no hay posición ───────────────────────────────────
        if not state.in_position:
            return self._hold("no_open_position", market.id)

        entry_price = state.entry_price
        current = tick.yes_price
        minutes = state.minutes_in_position() or 0.0

        log = logger.bind(
            strategy=STRATEGY_NAME,
            market_id=market.id,
            entry_price=entry_price,
            current_price=current,
            minutes_in_position=round(minutes, 1),
        )

        # ── 1. STOP LOSS ──────────────────────────────────────────────
        if entry_price is not None and entry_price > 0:
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

        # ── 2. MEAN REVERSION (retorno a media) ───────────────────────
        if len(state.tick_buffer) >= self._config.ma_window:
            z_score = _compute_zscore(
                tick, state.tick_buffer, self._config.ma_window
            )
            if z_score > self._config.exit_zscore:
                reason = (
                    f"mean_reverted: z_score={z_score:.3f} > "
                    f"exit_zscore={self._config.exit_zscore}, "
                    f"price={current:.4f} (entry={entry_price:.4f})"
                )
                log.info("exit_mean_reverted", reason=reason)
                STRATEGY_CYCLES.labels(
                    strategy=STRATEGY_NAME, result="exit_mean_reverted"
                ).inc()
                return self._exit_signal(reason, market.id)

        # ── 3. TIMEOUT ────────────────────────────────────────────────
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

        # ── Hold ──────────────────────────────────────────────────────
        log.debug(
            "holding_position",
            current=current,
            entry=entry_price,
            minutes=round(minutes, 1),
        )
        return self._hold("holding_position", market.id)

    async def on_exit(self, market: Market) -> None:
        """
        Cierre del ciclo: emite métricas de estado y limpia datos temporales.
        """
        state = self._get_or_create_state(market.id)

        logger.debug(
            "cycle_end",
            strategy=STRATEGY_NAME,
            market_id=market.id,
            in_position=state.in_position,
            minutes_in_position=round(state.minutes_in_position() or 0, 1),
            cycle_count=state.cycle_count,
            buffer_size=len(state.tick_buffer),
        )

        # Limpia z_score temporal (se recalcula en el siguiente tick)
        state.extra.pop("z_score", None)

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

    def update_config(self, new_config: MeanReversionConfig) -> None:
        """
        Actualiza la configuración en caliente (desde Telegram /settings).
        Valida antes de aplicar — si falla, mantiene la config anterior.
        """
        new_config.validate()
        old_config = self._config
        self._config = new_config

        # Reconstruye los filtros con la nueva config
        self._filters = [
            SpreadFilter(max_spread=self._config.max_spread),
            LiquidityFilter(min_volume_usdc=self._config.min_volume_usdc),
            TimeFilter(blocked_hours=self._config.blocked_hours),
        ]

        logger.info(
            "config_updated",
            strategy=STRATEGY_NAME,
            old_entry_zscore=old_config.entry_zscore,
            new_entry_zscore=new_config.entry_zscore,
            old_stop_loss=old_config.stop_loss_pct,
            new_stop_loss=new_config.stop_loss_pct,
        )
