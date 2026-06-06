# tests/unit/test_ensemble.py
"""Unit tests for P11.2 — Ensemble Signal Engine."""

from datetime import datetime, timezone

import pytest

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.signal_type import SignalType
from src.domain.enums.window import Window
from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.signal import Signal
from src.strategies.ensemble import (
    EnsembleAggregator,
    EnsembleConfig,
)

# ── Helpers ────────────────────────────────────────────────────────────


def make_market() -> Market:
    return Market(
        id="0xabcd1234abcd1234abcd1234abcd1234abcd1234",
        asset=Asset.BTC,
        window=Window.M5,
        question="BTC up or down?",
        status=MarketStatus.ACTIVE,
        yes_token_id="yes_token_001",
        no_token_id="no_token_001",
        yes_price=0.55,
        no_price=0.45,
        volume_24h=5000.0,
        expiry=datetime(2026, 12, 31),
    )


def make_tick(yes_price: float = 0.55) -> MarketTick:
    return MarketTick(
        market_id="0xabcd1234",
        yes_price=yes_price,
        no_price=round(1.0 - yes_price, 4),
        best_bid=yes_price - 0.005,
        best_ask=yes_price + 0.005,
        spread=0.01,
        volume_24h=5000.0,
        timestamp=datetime.utcnow(),
    )


def make_signal(
    stype: SignalType = SignalType.BUY_YES,
    confidence: float = 0.7,
    strategy: str = "TestStrategy",
) -> Signal:
    return Signal(
        type=stype,
        market_id="0xabcd1234",
        confidence=confidence,
        source_strategy=strategy,
        reason="test signal",
        timestamp=datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════════════
# EnsembleConfig
# ═══════════════════════════════════════════════════════════════════════


class TestEnsembleConfig:

    def test_defaults(self):
        cfg = EnsembleConfig()
        assert cfg.agreement_bonus == 0.10
        assert cfg.min_confidence_threshold == 0.05
        assert cfg.conflict_to_hold is True
        assert cfg.strategy_weights == {}

    def test_custom_weights(self):
        cfg = EnsembleConfig(strategy_weights={"BAT": 0.5, "MR": 2.0})
        assert cfg.strategy_weights["BAT"] == 0.5
        assert cfg.strategy_weights["MR"] == 2.0


# ═══════════════════════════════════════════════════════════════════════
# EnsembleAggregator — Basic aggregation
# ═══════════════════════════════════════════════════════════════════════


class TestEnsembleAggregator:

    def _agg(self, **kw) -> EnsembleAggregator:
        cfg = EnsembleConfig(**kw)
        return EnsembleAggregator(config=cfg)

    # ── Single strategy ────────────────────────────────────────────

    def test_single_strategy_returns_as_is(self):
        """One actionable signal → returned directly (no bonus)."""
        agg = self._agg()
        market = make_market()
        tick = make_tick()

        sig = make_signal(confidence=0.75)
        result = agg.aggregate(
            [("BAT", sig)], market, tick
        )

        assert result.is_actionable
        assert result.agreement_level == 1
        assert result.ensemble_confidence == 0.75
        assert result.contributing_strategies == ["BAT"]
        assert not result.had_conflict

    def test_single_strategy_below_threshold_returns_hold(self):
        """Confidence below min_confidence_threshold → HOLD."""
        agg = self._agg()
        market = make_market()
        tick = make_tick()

        sig = make_signal(confidence=0.03)  # below 0.05
        result = agg.aggregate(
            [("BAT", sig)], market, tick
        )

        assert not result.is_actionable
        assert result.agreement_level == 0
        assert result.ensemble_confidence == 0.0

    # ── Two strategies agree ───────────────────────────────────────

    def test_two_agree_buy_yes_with_bonus(self):
        """Two BUY_YES → weighted avg + agreement bonus."""
        agg = self._agg()
        market = make_market()
        tick = make_tick()

        sig1 = make_signal(confidence=0.60, strategy="BAT")
        sig2 = make_signal(confidence=0.80, strategy="MR")

        result = agg.aggregate(
            [("BAT", sig1), ("MR", sig2)], market, tick
        )

        assert result.is_actionable
        assert result.agreement_level == 2
        # Weighted avg = (0.60*1.0 + 0.80*1.0) / (1.0 + 1.0) = 0.70
        # + agreement_bonus 0.10 = 0.80
        assert result.ensemble_confidence == pytest.approx(0.80, abs=0.001)
        assert set(result.contributing_strategies) == {"BAT", "MR"}
        assert not result.had_conflict

    def test_two_buy_yes_confidence_capped_at_one(self):
        """Agreement bonus cannot push confidence above 1.0."""
        agg = self._agg()
        market = make_market()
        tick = make_tick()

        sig1 = make_signal(confidence=0.95, strategy="BAT")
        sig2 = make_signal(confidence=0.95, strategy="MR")

        result = agg.aggregate(
            [("BAT", sig1), ("MR", sig2)], market, tick
        )

        assert result.is_actionable
        assert result.ensemble_confidence == 1.0  # capped

    # ── Mixed signals (HOLD + BUY) ─────────────────────────────────

    def test_buy_plus_hold_ignores_hold(self):
        """One BUY_YES + one HOLD → only BUY counts (no bonus for 1)."""
        agg = self._agg()
        market = make_market()
        tick = make_tick()

        buy_sig = make_signal(confidence=0.70, strategy="BAT")
        hold_sig = make_signal(stype=SignalType.HOLD, confidence=0.0, strategy="MR")

        result = agg.aggregate(
            [("BAT", buy_sig), ("MR", hold_sig)], market, tick
        )

        assert result.is_actionable
        assert result.agreement_level == 1  # only 1 BUY
        assert result.ensemble_confidence == 0.70
        assert result.contributing_strategies == ["BAT"]

    # ── Conflict detection ─────────────────────────────────────────

    def test_conflict_buy_vs_sell_returns_hold(self):
        """BUY_YES vs BUY_NO → conflict → HOLD."""
        agg = self._agg()
        market = make_market()
        tick = make_tick()

        buy = make_signal(stype=SignalType.BUY_YES, confidence=0.70, strategy="BAT")
        sell = make_signal(stype=SignalType.BUY_NO, confidence=0.60, strategy="MR")

        result = agg.aggregate(
            [("BAT", buy), ("MR", sell)], market, tick
        )

        assert not result.is_actionable
        assert result.had_conflict
        assert result.ensemble_confidence == 0.0

    def test_conflict_disabled_returns_buy(self):
        """conflict_to_hold=False → BUY_YES wins over conflicting signals."""
        agg = self._agg(conflict_to_hold=False)
        market = make_market()
        tick = make_tick()

        buy = make_signal(stype=SignalType.BUY_YES, confidence=0.70, strategy="BAT")
        sell = make_signal(stype=SignalType.BUY_NO, confidence=0.60, strategy="MR")

        result = agg.aggregate(
            [("BAT", buy), ("MR", sell)], market, tick
        )

        assert result.is_actionable
        # Conflict was detected (BUY_NO exists) but ignored per config
        assert result.had_conflict is True

    # ── Strategy weights ───────────────────────────────────────────

    def test_strategy_weight_multiplier(self):
        """Custom strategy_weights affect the weighted confidence."""
        agg = self._agg(strategy_weights={"BAT": 2.0, "MR": 0.5})
        market = make_market()
        tick = make_tick()

        sig1 = make_signal(confidence=0.60, strategy="BAT")  # weight=2.0 → eff=1.20
        sig2 = make_signal(confidence=0.80, strategy="MR")   # weight=0.5 → eff=0.40

        result = agg.aggregate(
            [("BAT", sig1), ("MR", sig2)], market, tick
        )

        assert result.is_actionable
        # BAT: conf 0.60 × weight 2.0 → contribution 1.20
        # MR:  conf 0.80 × weight 0.5 → contribution 0.40
        # weighted_avg = (1.20 + 0.40) / (2.0 + 0.5) = 1.60/2.50 = 0.64
        # + bonus 0.10 = 0.74
        assert result.ensemble_confidence == pytest.approx(0.74, abs=0.001)

    # ── All HOLD ───────────────────────────────────────────────────

    def test_all_hold_returns_hold(self):
        """All strategies return HOLD → ensemble HOLD."""
        agg = self._agg()
        market = make_market()
        tick = make_tick()

        s1 = make_signal(stype=SignalType.HOLD, confidence=0.0, strategy="BAT")
        s2 = make_signal(stype=SignalType.HOLD, confidence=0.0, strategy="MR")

        result = agg.aggregate(
            [("BAT", s1), ("MR", s2)], market, tick
        )

        assert not result.is_actionable
        assert result.agreement_level == 0

    # ── Empty signals ──────────────────────────────────────────────

    def test_empty_signals_returns_hold(self):
        """No signals → HOLD."""
        agg = self._agg()
        result = agg.aggregate([], make_market(), make_tick())

        assert not result.is_actionable
        assert result.contributing_strategies == []

    # ── EnsembleResult ─────────────────────────────────────────────

    def test_ensemble_result_properties(self):
        agg = self._agg()
        sig = make_signal(confidence=0.75, strategy="BAT")

        result = agg.aggregate(
            [("BAT", sig)], make_market(), make_tick()
        )

        assert result.is_actionable is True
        assert result.contributing_strategies == ["BAT"]
        assert result.agreement_level == 1
        assert result.had_conflict is False
        assert 0.7 <= result.ensemble_confidence <= 0.8

    # ── get_strategy_weight ────────────────────────────────────────

    def test_get_strategy_weight_default(self):
        agg = self._agg()
        assert agg.get_strategy_weight("Unknown") == 1.0

    def test_get_strategy_weight_custom(self):
        agg = self._agg(strategy_weights={"BAT": 0.5})
        assert agg.get_strategy_weight("BAT") == 0.5
        assert agg.get_strategy_weight("MR") == 1.0
