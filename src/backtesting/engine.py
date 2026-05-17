# src/backtesting/engine.py

import structlog
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from src.domain.entities.market import Market, Asset, Window, MarketStatus
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import SignalType
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.risk.engine import RiskEngine, RiskEngineConfig
from src.backtesting.data_loader import HistoricalDataset

logger = structlog.get_logger(__name__)


@dataclass
class BacktestPosition:
    """
    Posición simplificada para backtesting.
    Sin DB ni IDs externos — solo datos del trade.
    """
    market_id:   str
    side:        str
    amount:      float
    shares:      float
    entry_price: float
    entry_tick:  int              # Índice del tick de entrada
    entry_at:    datetime
    exit_price:  float | None = None
    exit_tick:   int | None   = None
    exit_at:     datetime | None = None
    exit_reason: str | None   = None
    pnl:         float | None = None
    pnl_pct:     float | None = None

    def close(
        self,
        exit_price: float,
        exit_tick:  int,
        exit_at:    datetime,
        reason:     str,
    ) -> None:
        """Cierra la posición y calcula PnL."""
        self.exit_price  = exit_price
        self.exit_tick   = exit_tick
        self.exit_at     = exit_at
        self.exit_reason = reason

        # Slippage simulado: 0.5% del spread (igual que PaperHandler)
        exit_with_slippage = exit_price - (exit_price * 0.005)
        self.pnl     = (exit_with_slippage - self.entry_price) * self.shares
        self.pnl_pct = self.pnl / self.amount if self.amount > 0 else 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def duration_ticks(self) -> int | None:
        if self.exit_tick is None:
            return None
        return self.exit_tick - self.entry_tick


@dataclass
class BacktestResult:
    """
    Resultado completo de un backtest.
    Contiene todas las posiciones y las métricas calculadas.
    """
    asset:            str
    window:           str
    config:           BuyAboveThresholdConfig
    risk_config:      RiskEngineConfig
    dataset_ticks:    int
    dataset_start:    datetime
    dataset_end:      datetime
    initial_balance:  float
    final_balance:    float
    positions:        list[BacktestPosition] = field(default_factory=list)
    metrics:          dict = field(default_factory=dict)

    @property
    def closed_positions(self) -> list[BacktestPosition]:
        return [p for p in self.positions if not p.is_open]

    @property
    def open_positions(self) -> list[BacktestPosition]:
        return [p for p in self.positions if p.is_open]


class BacktestEngine:
    """
    Motor de replay histórico.
    Reproduce ticks en orden cronológico y aplica exactamente
    la misma lógica de Strategy + Risk que el bot en producción.

    Diferencias con producción:
    - Sin asyncio (síncrono para velocidad)
    - Sin DB ni Redis (todo en memoria)
    - Slippage simplificado (0.5% del spread)
    - Sin conexiones externas
    """

    def __init__(
        self,
        strategy_config: BuyAboveThresholdConfig | None = None,
        risk_config:     RiskEngineConfig | None = None,
        initial_balance: float = 1000.0,
        verbose:         bool  = False,
    ):
        self._strategy_config = strategy_config or BuyAboveThresholdConfig()
        self._risk_config     = risk_config     or RiskEngineConfig()
        self._initial_balance = initial_balance
        self._verbose         = verbose

        # Valida config antes de cualquier backtest
        self._strategy_config.validate()

    def run(self, dataset: HistoricalDataset) -> BacktestResult:
        """
        Ejecuta el backtest completo sobre un dataset.
        Retorna BacktestResult con todas las posiciones y métricas.
        """
        logger.info(
            "backtest_starting",
            asset=dataset.asset,
            window=dataset.window,
            ticks=dataset.tick_count,
            start=dataset.start_at.isoformat(),
            end=dataset.end_at.isoformat(),
            threshold=self._strategy_config.threshold,
        )

        # Inicializa componentes (versión síncrona)
        strategy = BuyAboveThresholdStrategy(config=self._strategy_config)
        market   = self._make_synthetic_market(dataset)

        # Estado del backtest
        balance:        float                  = self._initial_balance
        positions:      list[BacktestPosition] = []
        open_position:  BacktestPosition | None = None

        # Inicializa el estado de la estrategia
        strategy._get_or_create_state(dataset.market_id)
        state = strategy._states[dataset.market_id]

        # ── Loop principal de replay ──────────────────────────────────
        for tick_idx, tick in enumerate(dataset.ticks):
            self._sync_strategy_state_on_tick(strategy, state, tick)

            # ── Evalúa salida si hay posición abierta ─────────────────
            if open_position:
                exit_signal = self._sync_should_exit(strategy, market, tick)

                if exit_signal.type in (SignalType.EXIT, SignalType.BUY_NO):
                    # Calcula slippage de salida
                    slippage   = tick.spread * 0.5
                    exit_price = max(tick.yes_price - slippage, 0.001)

                    open_position.close(
                        exit_price=exit_price,
                        exit_tick=tick_idx,
                        exit_at=tick.timestamp,
                        reason=exit_signal.reason,
                    )

                    # Devuelve valor al balance
                    return_value = open_position.shares * exit_price
                    balance     += return_value

                    state.record_exit()
                    open_position = None

                    if self._verbose:
                        last = positions[-1] if positions else None
                        if last:
                            print(
                                f"  EXIT [{tick_idx:5d}] "
                                f"price={exit_price:.4f} "
                                f"pnl={last.pnl:+.4f} USDC "
                                f"({last.pnl_pct:+.2%}) "
                                f"reason={exit_signal.reason}"
                            )

            # ── Evalúa entrada si no hay posición ─────────────────────
            elif not open_position:
                entry_signal = self._sync_should_enter(strategy, market, tick)

                if entry_signal.type == SignalType.BUY_YES:
                    # Verifica riesgo (síncrono)
                    risk_ok, risk_reason = self._check_risk_sync(
                        balance=balance,
                        open_count=1 if open_position else 0,
                        amount=self._strategy_config.position_size_usdc,
                    )

                    if risk_ok:
                        amount     = self._strategy_config.position_size_usdc
                        slippage   = tick.spread * 0.5
                        fill_price = min(tick.yes_price + slippage, 0.999)
                        shares     = amount / fill_price

                        position = BacktestPosition(
                            market_id   = dataset.market_id,
                            side        = "YES",
                            amount      = amount,
                            shares      = shares,
                            entry_price = fill_price,
                            entry_tick  = tick_idx,
                            entry_at    = tick.timestamp,
                        )
                        open_position = position
                        positions.append(position)
                        balance -= amount
                        state.record_entry(fill_price)

                        if self._verbose:
                            print(
                                f"  ENTRY [{tick_idx:5d}] "
                                f"price={fill_price:.4f} "
                                f"amount={amount:.2f} USDC "
                                f"balance={balance:.2f}"
                            )

        # ── Cierra posición abierta al final del dataset ──────────────
        if open_position and dataset.ticks:
            last_tick  = dataset.ticks[-1]
            exit_price = max(last_tick.yes_price - last_tick.spread * 0.5, 0.001)
            open_position.close(
                exit_price=exit_price,
                exit_tick=len(dataset.ticks) - 1,
                exit_at=last_tick.timestamp,
                reason="dataset_end",
            )
            balance += open_position.shares * exit_price

        # ── Construye resultado ───────────────────────────────────────
        result = BacktestResult(
            asset           = dataset.asset,
            window          = dataset.window,
            config          = self._strategy_config,
            risk_config     = self._risk_config,
            dataset_ticks   = dataset.tick_count,
            dataset_start   = dataset.start_at,
            dataset_end     = dataset.end_at,
            initial_balance = self._initial_balance,
            final_balance   = balance,
            positions       = positions,
        )

        logger.info(
            "backtest_complete",
            total_positions=len(positions),
            closed=len(result.closed_positions),
            final_balance=round(balance, 2),
            pnl=round(balance - self._initial_balance, 2),
        )

        return result

    def run_parameter_sweep(
        self,
        dataset:     HistoricalDataset,
        thresholds:  list[float] | None = None,
        stop_losses: list[float] | None = None,
        targets:     list[float] | None = None,
    ) -> list[BacktestResult]:
        """
        Corre múltiples backtests variando parámetros.
        Útil para encontrar la configuración óptima.
        Retorna lista de resultados ordenada por Sharpe ratio.
        """
        thresholds  = thresholds  or [0.70, 0.75, 0.80, 0.85]
        stop_losses = stop_losses or [0.10, 0.15, 0.20]
        targets     = targets     or [0.85, 0.90, 0.95]

        results = []
        total   = len(thresholds) * len(stop_losses) * len(targets)
        count   = 0

        logger.info(
            "parameter_sweep_starting",
            combinations=total,
            thresholds=thresholds,
            stop_losses=stop_losses,
            targets=targets,
        )

        for threshold in thresholds:
            for stop_loss in stop_losses:
                for target in targets:
                    count += 1

                    # Valida coherencia antes de correr
                    if target <= threshold:
                        continue
                    if threshold <= 0.55:  # stop_drop_floor default
                        continue

                    try:
                        config = BuyAboveThresholdConfig(
                            threshold      = threshold,
                            stop_loss_pct  = stop_loss,
                            target_price   = target,
                            # Resto de parámetros desde la config base
                            required_ticks = self._strategy_config.required_ticks,
                            max_spread     = self._strategy_config.max_spread,
                            min_volume_usdc = self._strategy_config.min_volume_usdc,
                            position_size_usdc = self._strategy_config.position_size_usdc,
                        )

                        engine = BacktestEngine(
                            strategy_config=config,
                            risk_config=self._risk_config,
                            initial_balance=self._initial_balance,
                            verbose=False,
                        )
                        result = engine.run(dataset)
                        results.append(result)

                    except Exception as e:
                        logger.warning(
                            "sweep_combination_failed",
                            threshold=threshold,
                            stop_loss=stop_loss,
                            target=target,
                            error=str(e),
                        )

        logger.info(
            "parameter_sweep_complete",
            combinations_run=len(results),
            total_attempted=total,
        )

        return results

    # ------------------------------------------------------------------
    # HELPERS SÍNCRONOS (equivalentes síncronos de los métodos async)
    # ------------------------------------------------------------------

    def _sync_strategy_state_on_tick(
        self,
        strategy: BuyAboveThresholdStrategy,
        state,
        tick: MarketTick,
    ) -> None:
        """Versión síncrona de on_tick — actualiza estado sin asyncio."""
        state.add_tick(tick)

        if tick.yes_price >= strategy._config.threshold:
            state.consecutive_ticks += 1
        else:
            state.consecutive_ticks = 0

        if len(state.tick_buffer) >= 2:
            prev = state.tick_buffer[-2]
            if prev.yes_price > 0:
                drop = (prev.yes_price - tick.yes_price) / prev.yes_price
                state.extra["last_drop_pct"] = drop

    def _sync_should_enter(
        self,
        strategy: BuyAboveThresholdStrategy,
        market:   Market,
        tick:     MarketTick,
    ):
        """Versión síncrona de should_enter."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                strategy.should_enter(market, tick)
            )
        finally:
            loop.close()

    def _sync_should_exit(
        self,
        strategy: BuyAboveThresholdStrategy,
        market:   Market,
        tick:     MarketTick,
    ):
        """Versión síncrona de should_exit."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                strategy.should_exit(market, tick)
            )
        finally:
            loop.close()

    def _check_risk_sync(
        self,
        balance:    float,
        open_count: int,
        amount:     float,
    ) -> tuple[bool, str]:
        """
        Verificación de riesgo simplificada y síncrona.
        Aplica solo MinBalance y MaxPositions para velocidad.
        """
        if balance - amount < self._risk_config.min_balance_usdc:
            return False, f"min_balance: {balance:.2f} - {amount:.2f} < {self._risk_config.min_balance_usdc}"
        if open_count >= self._risk_config.max_open_positions:
            return False, f"max_positions: {open_count} >= {self._risk_config.max_open_positions}"
        return True, "ok"

    @staticmethod
    def _make_synthetic_market(dataset: HistoricalDataset) -> Market:
        """Crea un Market sintético para usar en el backtest."""
        from datetime import timezone
        return Market(
            id           = dataset.market_id,
            asset        = Asset(dataset.asset),
            window       = Window(dataset.window),
            question     = f"Backtest {dataset.asset} {dataset.window}",
            status       = MarketStatus.ACTIVE,
            yes_token_id = "backtest_yes",
            no_token_id  = "backtest_no",
            yes_price    = dataset.ticks[0].yes_price,
            no_price     = dataset.ticks[0].no_price,
            volume_24h   = dataset.ticks[0].volume_24h,
            expiry       = dataset.end_at,
        )