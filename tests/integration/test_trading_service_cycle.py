# tests/integration/test_trading_service_cycle.py

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.application.services.trading_service import TradingService
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal
from src.domain.value_objects.trade_result import TradeResult
from src.risk.engine import RiskEngine, RiskEngineConfig
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.engine import StrategyEngine

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_market(
    market_id: str = "test_market_1",
    asset: Asset = Asset.BTC,
    window: Window = Window.M5,
    yes_price: float = 0.76,
    volume_24h: float = 5000.0,
    minutes_to_expiry: int = 60,
) -> Market:
    return Market(
        id=market_id,
        asset=asset,
        window=window,
        question=f"Will {asset.value} price be above threshold?",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token_1",
        no_token_id="no_token_1",
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        volume_24h=volume_24h,
        expiry=datetime.utcnow() + timedelta(minutes=minutes_to_expiry),
    )


def _make_tick(
    market_id: str = "test_market_1",
    yes_price: float = 0.82,
    spread: float = 0.02,
    volume_24h: float = 5000.0,
) -> MarketTick:
    return MarketTick(
        market_id=market_id,
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=yes_price - spread / 2,
        best_ask=yes_price + spread / 2,
        spread=spread,
        volume_24h=volume_24h,
        timestamp=datetime.utcnow(),
    )


def _make_signal(
    signal_type: SignalType = SignalType.BUY_YES,
    market_id: str = "test_market_1",
    confidence: float = 0.8,
    source_strategy: str = "BuyAboveThreshold",
    reason: str = "entry_test",
) -> Signal:
    return Signal(
        type=signal_type,
        market_id=market_id,
        confidence=confidence,
        source_strategy=source_strategy,
        reason=reason,
        timestamp=datetime.utcnow(),
    )


def _make_success_trade(
    market_id: str = "test_market_1",
    fill_price: float = 0.82,
    amount: float = 10.0,
) -> TradeResult:
    return TradeResult(
        order_id="order_1",
        market_id=market_id,
        side="YES",
        amount=amount,
        target_price=fill_price - 0.01,
        fill_price=fill_price,
        slippage=0.01,
        pnl=None,
        success=True,
        mode="paper",
        timestamp=datetime.utcnow(),
    )


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_market_svc():
    svc = AsyncMock()
    svc.get_active_markets = AsyncMock(return_value=[])
    svc.get_market_tick = AsyncMock(return_value=None)
    svc.discover_markets = AsyncMock()
    return svc


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_positions = AsyncMock(return_value=[])
    repo.save_order = AsyncMock()
    repo.save_position = AsyncMock()
    return repo


@pytest.fixture
def mock_notifier():
    n = AsyncMock()
    n.send_trade_alert = AsyncMock()
    n.send_exit_alert = AsyncMock()
    n.send_risk_alert = AsyncMock()
    n.send_error_alert = AsyncMock()
    return n


@pytest.fixture
def mock_portfolio():
    p = AsyncMock()
    p.get_balance = AsyncMock(return_value=1000.0)
    return p


@pytest.fixture
def mock_execution():
    ex = AsyncMock()
    ex.execute_entry = AsyncMock(
        return_value=_make_success_trade()
    )
    ex.execute_exit = AsyncMock(
        return_value=TradeResult(
            order_id="exit_order_1",
            market_id="test_market_1",
            side="YES",
            amount=12.0,
            target_price=0.82,
            fill_price=0.81,
            slippage=-0.01,
            pnl=2.0,
            success=True,
            mode="paper",
            timestamp=datetime.utcnow(),
        )
    )
    return ex


@pytest.fixture
def strategy_engine():
    config = BuyAboveThresholdConfig(
        threshold=0.75,
        required_ticks=3,
        stop_loss_pct=0.15,
        target_price=0.90,
    )
    strategy = BuyAboveThresholdStrategy(config=config)
    return StrategyEngine(strategies=[strategy])


@pytest.fixture
def risk_engine():
    return RiskEngine(
        config=RiskEngineConfig(
            min_balance_usdc=50.0,
            max_daily_drawdown_pct=0.10,
            max_exposure_pct=0.30,
            max_open_positions=5,
        )
    )


@pytest.fixture
def trading_service(
    mock_market_svc,
    strategy_engine,
    risk_engine,
    mock_execution,
    mock_repo,
    mock_notifier,
    mock_portfolio,
):
    return TradingService(
        market_service=mock_market_svc,
        strategy_engine=strategy_engine,
        risk_engine=risk_engine,
        execution_handler=mock_execution,
        repository=mock_repo,
        notifier=mock_notifier,
        portfolio_service=mock_portfolio,
        position_size_usdc=10.0,
        trading_mode="paper",
    )


# ── Tests ────────────────────────────────────────────────────────────────


class TestTradingServiceCycle:

    @pytest.mark.asyncio
    async def test_full_entry_cycle_tick_to_execution(
        self,
        trading_service,
        mock_market_svc,
        mock_repo,
        mock_portfolio,
        mock_execution,
        mock_notifier,
    ):
        """
        Flujo completo paper: tick → señal → riesgo → ejecución.
        Verifica que una señal BUY_YES pasa por RiskEngine y se ejecuta.
        """
        market = _make_market()
        tick = _make_tick(yes_price=0.82)

        # Configurar mocks
        mock_market_svc.get_market_tick = AsyncMock(return_value=tick)
        mock_portfolio.get_balance = AsyncMock(return_value=1000.0)
        mock_repo.get_positions = AsyncMock(return_value=[])
        mock_execution.execute_entry = AsyncMock(
            return_value=_make_success_trade(market_id=market.id, fill_price=0.82)
        )
        mock_execution.execute_entry.reset_mock()

        # Simular ciclo: on_cycle_start → on_tick → should_enter
        await trading_service._strategy.on_cycle_start(market)
        for _ in range(3):  # required_ticks = 3
            await trading_service._strategy.on_tick(market, tick)

        # Verificar que la estrategia genera señal
        signal = await trading_service._strategy.should_enter(market, tick)
        assert signal.is_actionable()
        assert signal.type == SignalType.BUY_YES
        assert signal.confidence > 0.0

        # Evaluar riesgo y ejecutar con market_yes_price explícito para que
        # Kelly tenga edge positivo (confidence > market_yes_price)
        await trading_service._evaluate_risk_and_execute(market, signal, tick)

        # Verificar que la ejecución fue llamada
        mock_execution.execute_entry.assert_called()

        # Verificar que mark_entry fue llamado en el strategy engine
        state = trading_service._strategy.get_state("BuyAboveThreshold", market.id)
        assert state is not None
        assert state.in_position

    @pytest.mark.asyncio
    async def test_cycle_with_multiple_ticks_entry_confirmed(
        self,
        trading_service,
        mock_market_svc,
        mock_portfolio,
    ):
        """
        Ciclo con múltiples ticks y confirmación de entrada.
        La estrategia solo genera BUY_YES tras N ticks consecutivos sobre threshold.
        """
        market = _make_market()
        tick_above = _make_tick(yes_price=0.82)

        mock_market_svc.get_market_tick = AsyncMock(return_value=tick_above)
        mock_portfolio.get_balance = AsyncMock(return_value=1000.0)

        await trading_service._strategy.on_cycle_start(market)

        # Primer tick: aún no hay suficientes ticks consecutivos
        await trading_service._strategy.on_tick(market, tick_above)
        signal_1 = await trading_service._strategy.should_enter(market, tick_above)
        assert signal_1.type == SignalType.HOLD, (
            f"Esperado HOLD tras 1 tick, obtenido {signal_1.type.value}"
        )

        # Segundo tick: 2/3 → aún HOLD
        await trading_service._strategy.on_tick(market, tick_above)
        signal_2 = await trading_service._strategy.should_enter(market, tick_above)
        assert signal_2.type == SignalType.HOLD

        # Tercer tick: 3/3 → BUY_YES confirmado
        await trading_service._strategy.on_tick(market, tick_above)
        signal_3 = await trading_service._strategy.should_enter(market, tick_above)
        assert signal_3.is_actionable()
        assert signal_3.type == SignalType.BUY_YES

    @pytest.mark.asyncio
    async def test_exit_by_stop_loss_triggered(self):
        """
        Ciclo con salida por stop loss.
        Después de abrir posición a 0.82, un tick a 0.55 dispara stop loss.
        Usa la estrategia directamente para control preciso del estado.
        """
        config = BuyAboveThresholdConfig(
            threshold=0.75,
            required_ticks=3,
            stop_loss_pct=0.15,
            target_price=0.90,
        )
        strategy = BuyAboveThresholdStrategy(config=config)
        market = _make_market()
        tick_entry = _make_tick(yes_price=0.82)
        tick_exit = _make_tick(yes_price=0.55)  # Pérdida ~33% > stop_loss 15%

        # Inicializar ciclo
        await strategy.on_cycle_start(market)
        for _ in range(3):
            await strategy.on_tick(market, tick_entry)

        # Verificar entrada
        entry_signal = await strategy.should_enter(market, tick_entry)
        assert entry_signal.is_actionable()

        # Simular ejecución: registrar posición abierta en la estrategia
        state = strategy._get_or_create_state(market.id)
        state.record_entry(price=0.81)

        # Verificar estado
        assert state.in_position
        assert state.entry_price == 0.81

        # Procesar tick de caída y verificar salida
        await strategy.on_tick(market, tick_exit)
        exit_signal = await strategy.should_exit(market, tick_exit)

        assert exit_signal.is_actionable(), (
            f"Expected actionable exit, got {exit_signal.type.value} "
            f"with reason: {exit_signal.reason}"
        )
        assert exit_signal.type == SignalType.EXIT
        assert "stop_loss" in exit_signal.reason.lower()

    @pytest.mark.asyncio
    async def test_strategy_error_does_not_break_cycle(
        self,
        trading_service,
        mock_market_svc,
    ):
        """
        Error en strategy no rompe el ciclo — on_exit siempre se llama
        en el bloque finally de _run_market_cycle.
        """
        market = _make_market()
        tick = _make_tick(yes_price=0.82)

        # Configurar mocks para que el ciclo pueda avanzar hasta should_enter
        mock_market_svc.get_market_tick = AsyncMock(return_value=tick)

        # Espiar on_exit en la estrategia subyacente
        bat_strategy = trading_service._strategy._strategies[0]
        original_on_exit = bat_strategy.on_exit
        exit_calls = []

        async def spied_on_exit(m):
            exit_calls.append(m.id)
            await original_on_exit(m)

        bat_strategy.on_exit = spied_on_exit

        # Hacer que should_enter del engine lance excepción
        original_should_enter = trading_service._strategy.should_enter
        call_count = [0]

        async def failing_should_enter(m, t):
            call_count[0] += 1
            raise RuntimeError("simulated strategy failure")

        trading_service._strategy.should_enter = failing_should_enter

        # Parchear métricas Prometheus para evitar "Incorrect label names"
        with patch(
            "src.application.services.trading_service.CYCLE_DURATION"
        ) as mock_dur, patch(
            "src.application.services.trading_service.CYCLE_ERRORS"
        ), patch(
            "src.application.services.trading_service.SIGNALS_GENERATED"
        ):
            mock_dur.labels.return_value.time.return_value.__enter__ = (
                lambda self: None
            )
            mock_dur.labels.return_value.time.return_value.__exit__ = (
                lambda *a: None
            )

            try:
                # Ejecutar ciclo — no debe propagar excepción
                await trading_service._run_market_cycle(market)

                # should_enter fue llamado (y falló)
                assert call_count[0] == 1

                # on_exit se ejecutó en el finally
                assert len(exit_calls) == 1
                assert exit_calls[0] == market.id

            finally:
                # Restaurar siempre, incluso si la aserción falla
                trading_service._strategy.should_enter = original_should_enter
                bat_strategy.on_exit = original_on_exit

    @pytest.mark.asyncio
    async def test_risk_denies_and_notifies(
        self,
        trading_service,
        mock_notifier,
        mock_repo,
        mock_portfolio,
    ):
        """
        Cuando el RiskEngine deniega la operación (balance bajo), se notifica al usuario.
        """
        market = _make_market()
        tick = _make_tick(yes_price=0.82)
        signal = _make_signal()

        # Balance: 55 - 10 = 45 < min_balance 50 → MinBalanceRule deniega
        mock_portfolio.get_balance = AsyncMock(return_value=55.0)
        mock_repo.get_positions = AsyncMock(return_value=[])
        mock_notifier.send_risk_alert.reset_mock()

        await trading_service._evaluate_risk_and_execute(market, signal, tick)

        # Verificar que se notificó al usuario
        mock_notifier.send_risk_alert.assert_called_once()
        alert_args = mock_notifier.send_risk_alert.call_args
        assert "MinBalanceRule" in alert_args.kwargs.get("rule_triggered", "")

    @pytest.mark.asyncio
    async def test_expiring_market_blocks_entry(self):
        """
        Mercado a punto de expirar (< 5 min) no genera señal de entrada.
        La estrategia verifica market.minutes_to_expiry() < 5.0 en should_enter.
        """
        config = BuyAboveThresholdConfig(threshold=0.75, required_ticks=3)
        strategy = BuyAboveThresholdStrategy(config=config)

        # Mercado que expira en 3 minutos
        market = _make_market(minutes_to_expiry=3)
        tick = _make_tick(yes_price=0.82)

        await strategy.on_cycle_start(market)
        for _ in range(3):
            await strategy.on_tick(market, tick)

        signal = await strategy.should_enter(market, tick)
        assert not signal.is_actionable(), (
            f"Expected HOLD for expiring market, got {signal.type.value}"
        )
        assert signal.type == SignalType.HOLD
        assert "expiring" in signal.reason.lower()
