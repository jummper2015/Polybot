# tests/unit/test_regime_aware.py

"""
Tests for regime-aware strategy switching (P11.1).

Covers:
- StrategyRegimeBinding creation and validation
- RegimeDetector: streaming tick feeding, regime detection, reset
- RegimeAwareOrchestrator: strategy filtering, confidence multipliers,
  passthrough delegation, fallback behavior
- Factory helpers: create_binding_from_config, build_orchestrator
"""

import asyncio
from datetime import datetime
from unittest.mock import patch

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal, SignalType
from src.infrastructure.data.regime import Regime
from src.strategies.base import IStrategy, StrategyState
from src.strategies.engine import StrategyEngine
from src.strategies.regime_aware import (
    RegimeAwareOrchestrator,
    RegimeDetector,
    StrategyRegimeBinding,
    build_orchestrator,
    create_binding_from_config,
)

# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _make_tick(
    market_id: str = "market_1",
    yes_price: float = 0.55,
    spread: float = 0.01,
    volume_24h: float = 5000.0,
    best_bid: float = 0.545,
    best_ask: float = 0.555,
) -> MarketTick:
    return MarketTick(
        market_id=market_id,
        yes_price=yes_price,
        no_price=1.0 - yes_price,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        volume_24h=volume_24h,
        timestamp=datetime(2026, 6, 1, 12, 0, 0),
    )


def _make_market(
    market_id: str = "market_1",
    asset: Asset = Asset.BTC,
    window: Window = Window.M5,
) -> Market:
    return Market(
        id=market_id,
        asset=asset,
        window=window,
        question="Test market",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token",
        no_token_id="no_token",
        yes_price=0.55,
        no_price=0.45,
        volume_24h=5000.0,
        expiry=datetime(2099, 12, 31, 23, 59, 59),
    )


class _MockStrategy(IStrategy):
    """Mock strategy for testing regime-aware orchestration."""

    def __init__(
        self,
        name: str = "MockStrategy",
        entry_signal: Signal | None = None,
        exit_signal: Signal | None = None,
    ):
        self._name = name
        self._entry_signal = entry_signal
        self._exit_signal = exit_signal
        self._states: dict[str, StrategyState] = {}

        self.on_cycle_start_calls = 0
        self.on_tick_calls = 0
        self.should_enter_calls = 0
        self.should_exit_calls = 0
        self.on_exit_calls = 0

    @property
    def name(self) -> str:
        return self._name

    async def on_cycle_start(self, market: Market) -> None:
        self.on_cycle_start_calls += 1
        if market.id not in self._states:
            self._states[market.id] = StrategyState(
                market_id=market.id,
                strategy_name=self._name,
            )

    async def on_tick(self, market: Market, tick: MarketTick) -> None:
        self.on_tick_calls += 1
        state = self._get_state(market.id)
        state.add_tick(tick)

    async def should_enter(self, market: Market, tick: MarketTick) -> Signal:
        self.should_enter_calls += 1
        if self._entry_signal:
            return self._entry_signal
        return Signal(
            type=SignalType.HOLD,
            market_id=market.id,
            confidence=0.0,
            source_strategy=self._name,
            reason="mock_hold",
            timestamp=datetime.utcnow(),
        )

    async def should_exit(self, market: Market, tick: MarketTick) -> Signal:
        self.should_exit_calls += 1
        if self._exit_signal:
            return self._exit_signal
        return Signal(
            type=SignalType.HOLD,
            market_id=market.id,
            confidence=0.0,
            source_strategy=self._name,
            reason="mock_hold",
            timestamp=datetime.utcnow(),
        )

    async def on_exit(self, market: Market) -> None:
        self.on_exit_calls += 1

    def _get_state(self, market_id: str) -> StrategyState:
        if market_id not in self._states:
            self._states[market_id] = StrategyState(
                market_id=market_id,
                strategy_name=self._name,
            )
        return self._states[market_id]


# ══════════════════════════════════════════════════════════════════════════
# StrategyRegimeBinding
# ══════════════════════════════════════════════════════════════════════════


class TestStrategyRegimeBinding:
    """Tests for StrategyRegimeBinding dataclass."""

    def test_default_allows_common_regimes(self):
        binding = StrategyRegimeBinding()
        assert Regime.TREND in binding.allowed_regimes
        assert Regime.CHOP in binding.allowed_regimes
        assert Regime.EVENT_DRIVEN in binding.allowed_regimes
        assert binding.enabled is True
        assert binding.confidence_multipliers == {}

    def test_custom_allowed_regimes(self):
        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.TREND},
            confidence_multipliers={Regime.TREND: 1.2},
        )
        assert Regime.TREND in binding.allowed_regimes
        assert Regime.CHOP not in binding.allowed_regimes
        assert binding.confidence_multipliers[Regime.TREND] == 1.2

    def test_disabled_strategy_not_active(self):
        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.TREND},
            enabled=False,
        )
        assert binding.enabled is False

    def test_empty_allowed_regimes(self):
        binding = StrategyRegimeBinding(allowed_regimes=set())
        assert len(binding.allowed_regimes) == 0


# ══════════════════════════════════════════════════════════════════════════
# RegimeDetector
# ══════════════════════════════════════════════════════════════════════════


class TestRegimeDetector:
    """Tests for streaming RegimeDetector."""

    def test_feed_tick_accumulates_buffer(self):
        detector = RegimeDetector()
        tick = _make_tick()
        detector.feed_tick("market_1", tick)
        state = detector._streaming_states["market_1"]
        assert len(state.prices) == 1

    def test_detect_not_ready_returns_chop(self):
        detector = RegimeDetector()
        tick = _make_tick()
        detector.feed_tick("market_1", tick)
        regime, conf = detector.detect("market_1")
        assert regime == Regime.CHOP
        assert conf < 0.5  # low confidence when not ready

    def test_detect_with_enough_ticks(self):
        detector = RegimeDetector(buffer_window=50)

        # Feed ticks with stable prices → CHOP regime
        for i in range(30):
            tick = _make_tick(
                yes_price=0.50 + (i % 5) * 0.001,  # small oscillations
                spread=0.005,
                volume_24h=5000.0,
            )
            detector.feed_tick("market_1", tick)

        regime, conf = detector.detect("market_1")
        # With stable prices, should be CHOP or TREND depending on feature values
        assert regime in (Regime.CHOP, Regime.TREND)

    def test_reset_clears_state(self):
        detector = RegimeDetector()
        tick = _make_tick()
        detector.feed_tick("market_1", tick)
        assert "market_1" in detector._streaming_states

        detector.reset("market_1")
        assert "market_1" not in detector._streaming_states

    def test_get_last_regime_defaults_to_chop(self):
        detector = RegimeDetector()
        assert detector.get_last_regime("unknown") == Regime.CHOP

    def test_multiple_markets_independent(self):
        detector = RegimeDetector()

        detector.feed_tick("market_1", _make_tick(market_id="market_1"))
        detector.feed_tick("market_2", _make_tick(market_id="market_2"))

        assert "market_1" in detector._streaming_states
        assert "market_2" in detector._streaming_states
        assert len(detector._streaming_states) == 2

        detector.reset("market_1")
        assert "market_1" not in detector._streaming_states
        assert "market_2" in detector._streaming_states


# ══════════════════════════════════════════════════════════════════════════
# RegimeAwareOrchestrator — Construction & Delegation
# ══════════════════════════════════════════════════════════════════════════


class TestOrchestratorConstruction:
    """Tests for orchestrator initialization and delegation."""

    def test_construct_with_bindings(self):
        strategy = _MockStrategy(name="TestStrategy")
        engine = StrategyEngine([strategy])
        binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"TestStrategy": binding},
        )

        assert "TestStrategy" in orch.registered_strategies()
        assert orch._bindings["TestStrategy"].allowed_regimes == {Regime.CHOP}

    def test_construct_without_bindings(self):
        """Without bindings, all strategies are always active (backward compat)."""
        strategy = _MockStrategy(name="TestStrategy")
        engine = StrategyEngine([strategy])

        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        # No bindings → strategy is active in all regimes
        assert orch._is_strategy_active("TestStrategy", Regime.PANIC) is True
        assert orch._is_strategy_active("TestStrategy", Regime.CHOP) is True

    def test_regime_detection_disabled(self):
        """With regime_detection_enabled=False, all strategies always active."""
        strategy = _MockStrategy(name="TestStrategy")
        engine = StrategyEngine([strategy])
        binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"TestStrategy": binding},
            regime_detection_enabled=False,
        )

        # Even though binding only allows CHOP, should be active in TREND
        assert orch._is_strategy_active("TestStrategy", Regime.TREND) is True

    def test_delegates_on_cycle_start(self):
        strategy = _MockStrategy(name="S1")
        engine = StrategyEngine([strategy])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        market = _make_market()
        asyncio.run(orch.on_cycle_start(market))

        assert strategy.on_cycle_start_calls == 1

    def test_delegates_on_exit(self):
        strategy = _MockStrategy(name="S1")
        engine = StrategyEngine([strategy])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        market = _make_market()
        asyncio.run(orch.on_exit(market))

        assert strategy.on_exit_calls == 1

    def test_delegates_mark_entry_exit(self):
        strategy = _MockStrategy(name="S1")
        engine = StrategyEngine([strategy])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        market = _make_market()
        asyncio.run(orch.on_cycle_start(market))

        orch.mark_entry("S1", market.id, 0.55)
        state = orch.get_state("S1", market.id)
        assert state is not None
        assert state.in_position is True

        orch.mark_exit("S1", market.id)
        state = orch.get_state("S1", market.id)
        assert state.in_position is False

    def test_clear_state_resets_engine_and_detector(self):
        strategy = _MockStrategy(name="S1")
        engine = StrategyEngine([strategy])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        market = _make_market()
        asyncio.run(orch.on_cycle_start(market))
        orch.mark_entry("S1", market.id, 0.55)

        # Feed a tick to the detector
        asyncio.run(orch.on_tick(market, _make_tick()))

        orch.clear_state(market.id)

        # Engine state cleared
        assert orch.get_state("S1", market.id) is None
        # Detector state cleared
        assert market.id not in orch._detector._streaming_states


# ══════════════════════════════════════════════════════════════════════════
# RegimeAwareOrchestrator — Strategy Filtering
# ══════════════════════════════════════════════════════════════════════════


class TestOrchestratorStrategyFiltering:
    """Tests for regime-based strategy filtering in should_enter."""

    def test_active_strategy_evaluated(self):
        """A strategy whose binding allows the current regime should be evaluated."""
        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.7,
            source_strategy="ActiveStrategy",
            reason="test_entry",
            timestamp=datetime.utcnow(),
        )
        active_strat = _MockStrategy(name="ActiveStrategy", entry_signal=entry_signal)
        engine = StrategyEngine([active_strat])
        binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"ActiveStrategy": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))

        # Feed enough ticks to get past "not ready" → CHOP default
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        signal = asyncio.run(orch.should_enter(market, tick))

        # CHOP is in allowed_regimes → strategy should be evaluated
        assert signal.type == SignalType.BUY_YES
        assert active_strat.should_enter_calls >= 1

    def test_inactive_strategy_skipped(self):
        """A strategy NOT allowed in the current regime should be skipped."""
        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.7,
            source_strategy="TrendOnly",
            reason="test_entry",
            timestamp=datetime.utcnow(),
        )
        trend_strat = _MockStrategy(name="TrendOnly", entry_signal=entry_signal)
        engine = StrategyEngine([trend_strat])
        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.TREND},  # Only active in TREND
        )

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"TrendOnly": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))

        # Feed ticks with stable prices → regime will be CHOP, not TREND
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        signal = asyncio.run(orch.should_enter(market, tick))

        # TREND-only strategy should be skipped in CHOP regime
        assert signal.type == SignalType.HOLD
        assert trend_strat.should_enter_calls == 0  # never called

    def test_fallback_to_hold_when_no_strategy_active(self):
        """When no strategy is active in the current regime, return HOLD."""
        strat = _MockStrategy(name="PanicOnly")
        engine = StrategyEngine([strat])
        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.PANIC},  # Only PANIC
        )

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"PanicOnly": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        signal = asyncio.run(orch.should_enter(market, tick))

        # CHOP ≠ PANIC → strategy skipped → HOLD
        assert signal.type == SignalType.HOLD
        assert signal.source_strategy == "RegimeAwareOrchestrator"

    def test_disabled_strategy_completely_skipped(self):
        """Disabled strategies are skipped regardless of regime."""
        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.7,
            source_strategy="Disabled",
            reason="test_entry",
            timestamp=datetime.utcnow(),
        )
        strat = _MockStrategy(name="Disabled", entry_signal=entry_signal)
        engine = StrategyEngine([strat])
        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.CHOP},
            enabled=False,  # Disabled!
        )

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"Disabled": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        signal = asyncio.run(orch.should_enter(market, tick))

        # Disabled strategy → HOLD
        assert signal.type == SignalType.HOLD
        assert strat.should_enter_calls == 0

    def test_first_match_wins_across_strategies(self):
        """When multiple strategies are active, first actionable signal wins."""
        entry_s1 = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.6,
            source_strategy="S1",
            reason="s1_entry",
            timestamp=datetime.utcnow(),
        )
        entry_s2 = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.8,
            source_strategy="S2",
            reason="s2_entry",
            timestamp=datetime.utcnow(),
        )
        s1 = _MockStrategy(name="S1", entry_signal=entry_s1)
        s2 = _MockStrategy(name="S2", entry_signal=entry_s2)
        engine = StrategyEngine([s1, s2])

        # Both active in CHOP
        binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"S1": binding, "S2": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        signal = asyncio.run(orch.should_enter(market, tick))

        # S1 is first in list → S1's signal should win
        assert signal.type == SignalType.BUY_YES
        assert signal.source_strategy == "S1"
        # S2 should also be called (S1 was actionable, loop short-circuits after)
        # Actually the orchestrator stops at first actionable, so S1 fires, S2 might be called too
        assert s1.should_enter_calls >= 1

    def test_skip_strategy_in_position(self):
        """Even if active, skip strategies already in position."""
        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.7,
            source_strategy="S1",
            reason="test_entry",
            timestamp=datetime.utcnow(),
        )
        s1 = _MockStrategy(name="S1", entry_signal=entry_signal)
        engine = StrategyEngine([s1])
        binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"S1": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        # First entry should succeed
        signal1 = asyncio.run(orch.should_enter(market, tick))
        assert signal1.type == SignalType.BUY_YES

        # Mark entry as executed
        orch.mark_entry("S1", market.id, 0.55)

        # Second attempt should return HOLD (already in position)
        signal2 = asyncio.run(orch.should_enter(market, tick))
        assert signal2.type == SignalType.HOLD


# ══════════════════════════════════════════════════════════════════════════
# RegimeAwareOrchestrator — Exit Behavior
# ══════════════════════════════════════════════════════════════════════════


class TestOrchestratorExitBehavior:
    """Tests for exit signal evaluation (NOT filtered by regime)."""

    def test_exit_evaluated_regardless_of_regime(self):
        """Exit signals should be checked even if strategy not active in regime."""
        exit_signal = Signal(
            type=SignalType.EXIT,
            market_id="market_1",
            confidence=1.0,
            source_strategy="TrendOnly",
            reason="stop_loss",
            timestamp=datetime.utcnow(),
        )
        strat = _MockStrategy(name="TrendOnly", exit_signal=exit_signal)
        engine = StrategyEngine([strat])

        # Strategy only active in TREND — not in CHOP
        binding = StrategyRegimeBinding(allowed_regimes={Regime.TREND})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"TrendOnly": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        orch.mark_entry("TrendOnly", market.id, 0.55)

        # In CHOP regime, strategy is "inactive" for entry but exit should still work
        signal = asyncio.run(orch.should_exit(market, tick))

        assert signal.type == SignalType.EXIT
        assert strat.should_exit_calls == 1

    def test_exit_only_for_strategies_in_position(self):
        """Only evaluate exit for strategies that actually have a position."""
        exit_signal = Signal(
            type=SignalType.EXIT,
            market_id="market_1",
            confidence=1.0,
            source_strategy="S1",
            reason="target",
            timestamp=datetime.utcnow(),
        )
        s1 = _MockStrategy(name="S1", exit_signal=exit_signal)
        s2 = _MockStrategy(name="S2")
        engine = StrategyEngine([s1, s2])

        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        # Only S1 has a position
        orch.mark_entry("S1", market.id, 0.55)

        signal = asyncio.run(orch.should_exit(market, tick))

        assert signal.type == SignalType.EXIT
        assert s1.should_exit_calls == 1
        # S2 not in position → should_exit not called
        assert s2.should_exit_calls == 0

    def test_hold_when_no_position(self):
        """Return HOLD if no strategy has a position."""
        s1 = _MockStrategy(name="S1")
        engine = StrategyEngine([s1])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        # No position marked
        signal = asyncio.run(orch.should_exit(market, tick))

        assert signal.type == SignalType.HOLD


# ══════════════════════════════════════════════════════════════════════════
# RegimeAwareOrchestrator — Confidence Multipliers
# ══════════════════════════════════════════════════════════════════════════


class TestConfidenceMultipliers:
    """Tests for regime-based confidence adjustment."""

    def test_multiplier_applied_to_entry_signal(self):
        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.5,
            source_strategy="S1",
            reason="test",
            timestamp=datetime.utcnow(),
        )
        s1 = _MockStrategy(name="S1", entry_signal=entry_signal)
        engine = StrategyEngine([s1])

        # TREND gets 1.5x multiplier → 0.5 * 1.5 = 0.75
        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.CHOP, Regime.TREND},
            confidence_multipliers={Regime.TREND: 1.5},
        )

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"S1": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))

        # Feed ticks to build buffer
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        # Manually set the regime to TREND for deterministic test
        orch._detector._last_regime["market_1"] = Regime.TREND
        orch._detector._last_confidence["market_1"] = 0.8

        with patch.object(
            orch._detector,
            "detect",
            return_value=(Regime.TREND, 0.8),
        ):
            signal = asyncio.run(orch.should_enter(market, tick))

        assert signal.type == SignalType.BUY_YES
        # 0.5 * 1.5 = 0.75
        assert signal.confidence == 0.75

    def test_multiplier_capped_at_1_0(self):
        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.9,
            source_strategy="S1",
            reason="test",
            timestamp=datetime.utcnow(),
        )
        s1 = _MockStrategy(name="S1", entry_signal=entry_signal)
        engine = StrategyEngine([s1])

        # 2x multiplier → 0.9 * 2.0 = 1.8 → capped to 1.0
        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.CHOP},
            confidence_multipliers={Regime.CHOP: 2.0},
        )

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"S1": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        with patch.object(
            orch._detector,
            "detect",
            return_value=(Regime.CHOP, 0.9),
        ):
            signal = asyncio.run(orch.should_enter(market, tick))

        assert signal.confidence == 1.0  # capped

    def test_no_multiplier_when_not_specified(self):
        """If no multiplier for the detected regime, signal is unchanged."""
        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.5,
            source_strategy="S1",
            reason="test",
            timestamp=datetime.utcnow(),
        )
        s1 = _MockStrategy(name="S1", entry_signal=entry_signal)
        engine = StrategyEngine([s1])

        binding = StrategyRegimeBinding(
            allowed_regimes={Regime.CHOP},
            confidence_multipliers={},  # No multipliers
        )

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"S1": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        with patch.object(
            orch._detector,
            "detect",
            return_value=(Regime.CHOP, 0.9),
        ):
            signal = asyncio.run(orch.should_enter(market, tick))

        assert signal.confidence == 0.5  # unchanged


# ══════════════════════════════════════════════════════════════════════════
# RegimeAwareOrchestrator — Management
# ══════════════════════════════════════════════════════════════════════════


class TestOrchestratorManagement:
    """Tests for runtime strategy management."""

    def test_set_strategy_regime(self):
        s1 = _MockStrategy(name="S1")
        engine = StrategyEngine([s1])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        # Initially no binding → always active
        assert orch._is_strategy_active("S1", Regime.PANIC) is True

        # Set binding to only allow CHOP
        new_binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})
        orch.set_strategy_regime("S1", new_binding)

        assert orch._is_strategy_active("S1", Regime.CHOP) is True
        assert orch._is_strategy_active("S1", Regime.PANIC) is False

    def test_disable_and_enable_strategy(self):
        s1 = _MockStrategy(name="S1")
        engine = StrategyEngine([s1])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        orch.disable_strategy("S1")
        assert orch._is_strategy_active("S1", Regime.CHOP) is False

        orch.enable_strategy("S1")
        assert orch._is_strategy_active("S1", Regime.CHOP) is True

    def test_get_current_regime(self):
        s1 = _MockStrategy(name="S1")
        engine = StrategyEngine([s1])
        orch = RegimeAwareOrchestrator(strategy_engine=engine)

        assert orch.get_current_regime("unknown") == Regime.CHOP


# ══════════════════════════════════════════════════════════════════════════
# Factory Helpers
# ══════════════════════════════════════════════════════════════════════════


class TestCreateBindingFromConfig:
    """Tests for create_binding_from_config factory."""

    def test_all_regimes_by_default(self):
        binding = create_binding_from_config("Test")
        assert len(binding.allowed_regimes) == len(Regime)
        assert binding.enabled is True

    def test_specific_regimes(self):
        binding = create_binding_from_config(
            "Test",
            allowed_regimes=["trend", "chop"],
        )
        assert binding.allowed_regimes == {Regime.TREND, Regime.CHOP}

    def test_with_multipliers(self):
        binding = create_binding_from_config(
            "Test",
            allowed_regimes=["trend"],
            confidence_multipliers={"trend": 1.5},
        )
        assert binding.confidence_multipliers[Regime.TREND] == 1.5

    def test_invalid_regime_warning(self):
        """Invalid regime names are ignored with a warning."""
        binding = create_binding_from_config(
            "Test",
            allowed_regimes=["trend", "invalid_regime"],
        )
        # Should only contain TREND, not the invalid one
        assert Regime.TREND in binding.allowed_regimes
        assert len(binding.allowed_regimes) == 1

    def test_none_allowed_regimes(self):
        binding = create_binding_from_config("Test", allowed_regimes=None)
        assert len(binding.allowed_regimes) == len(Regime)


class TestBuildOrchestrator:
    """Tests for build_orchestrator factory."""

    def test_auto_builds_bindings_from_strategy_configs(self):
        """Strategies with _config.allowed_regimes get auto-bindings."""

        class ConfigWithRegimes:
            allowed_regimes = ["chop", "trend"]
            ma_window = 20

        class StrategyWithConfig(_MockStrategy):
            def __init__(self):
                super().__init__(name="MR")
                self._config = ConfigWithRegimes()

        strat = StrategyWithConfig()
        orch = build_orchestrator(strategies=[strat])

        assert orch._is_strategy_active("MR", Regime.CHOP) is True
        assert orch._is_strategy_active("MR", Regime.TREND) is True
        assert orch._is_strategy_active("MR", Regime.PANIC) is False

    def test_overrides_with_regime_configs(self):
        """Passed regime_configs override auto-detected ones."""
        strat = _MockStrategy(name="S1")
        orch = build_orchestrator(
            strategies=[strat],
            regime_configs={
                "S1": {"allowed_regimes": ["panic"]},
            },
        )

        assert orch._is_strategy_active("S1", Regime.PANIC) is True
        assert orch._is_strategy_active("S1", Regime.CHOP) is False

    def test_strategy_without_config_gets_all_regimes(self):
        """Strategies without config or regime_configs get all regimes."""
        strat = _MockStrategy(name="Plain")
        orch = build_orchestrator(strategies=[strat])

        assert orch._is_strategy_active("Plain", Regime.PANIC) is True
        assert orch._is_strategy_active("Plain", Regime.CHOP) is True


# ══════════════════════════════════════════════════════════════════════════
# RegimeAwareOrchestrator — Error Handling
# ══════════════════════════════════════════════════════════════════════════


class TestOrchestratorErrorHandling:
    """Tests for error resilience in the orchestrator."""

    def test_strategy_error_isolated(self):
        """Error in one strategy doesn't prevent others from being evaluated."""

        class FailingStrategy(_MockStrategy):
            async def should_enter(self, market, tick):
                raise RuntimeError("simulated failure")

        entry_signal = Signal(
            type=SignalType.BUY_YES,
            market_id="market_1",
            confidence=0.7,
            source_strategy="Good",
            reason="test",
            timestamp=datetime.utcnow(),
        )
        failing = FailingStrategy(name="Failing")
        good = _MockStrategy(name="Good", entry_signal=entry_signal)

        engine = StrategyEngine([failing, good])
        binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"Failing": binding, "Good": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))
        for _ in range(30):
            asyncio.run(orch.on_tick(market, tick))

        signal = asyncio.run(orch.should_enter(market, tick))

        # Good strategy should still produce a signal
        assert signal.type == SignalType.BUY_YES
        assert signal.source_strategy == "Good"

    def test_regime_detection_failure_falls_back_to_chop(self):
        """If regime detection fails, fall back to CHOP."""
        s1 = _MockStrategy(name="S1")
        engine = StrategyEngine([s1])
        binding = StrategyRegimeBinding(allowed_regimes={Regime.CHOP})

        orch = RegimeAwareOrchestrator(
            strategy_engine=engine,
            bindings={"S1": binding},
        )

        market = _make_market()
        tick = _make_tick()

        asyncio.run(orch.on_cycle_start(market))

        # Detect that raises → should fall back to CHOP
        with patch.object(
            orch._detector,
            "detect",
            side_effect=Exception("detection failed"),
        ):
            signal = asyncio.run(orch.should_enter(market, tick))

        # CHOP default → HOLD (no entry signal from mock)
        assert signal.type == SignalType.HOLD
