# tests/unit/test_post_trade.py
"""
Tests for PostTradeAnalyzer — Fase 10.4 post-trade analytics engine.

Covers: expectancy, profit factor, exit-reason attribution, regime attribution,
streaks, drawdown, Sharpe estimate, edge cases.
"""

from dataclasses import dataclass

import pytest

from src.quantitative.post_trade import PostTradeAnalyzer

# ── Fake position ────────────────────────────────────────────────────────────


@dataclass
class FakePosition:
    """Simulates a BacktestPosition for testing duck-typed analyzer."""

    pnl: float | None
    exit_reason: str | None = None
    entry_tick: int | None = None
    exit_tick: int | None = None
    amount: float = 10.0


def pos(pnl, reason="target", entry=0, exit=5, amount=10.0):
    return FakePosition(pnl=pnl, exit_reason=reason, entry_tick=entry,
                         exit_tick=exit, amount=amount)


# ── Basic integration ────────────────────────────────────────────────────────


class TestPostTradeAnalyzer:
    """Integration-level tests for PostTradeAnalyzer."""

    def test_basic_analysis(self):
        positions = [
            pos(5.0, "target"), pos(-3.0, "stop_loss"), pos(2.0, "target"),
            pos(-1.0, "timeout"), pos(4.0, "target"), pos(8.0, "target"),
        ]
        report = PostTradeAnalyzer().analyze(positions, initial_balance=1000.0)

        assert report.total_trades == 6
        assert report.total_pnl == pytest.approx(15.0)
        assert report.final_balance == pytest.approx(1015.0)
        assert report.winners == 4
        assert report.losers == 2
        assert report.win_rate == pytest.approx(4 / 6)

    def test_expectancy(self):
        positions = [pos(2.0), pos(3.0), pos(-1.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.expectancy == pytest.approx(4.0 / 3)

    def test_expectancy_pct(self):
        positions = [pos(2.0, amount=20.0), pos(-1.0, amount=10.0)]
        report = PostTradeAnalyzer().analyze(positions)
        # pnl_pct per trade: 2/20=0.1, -1/10=-0.1 → avg=0.0
        assert report.expectancy_pct == pytest.approx(0.0)

    def test_profit_factor(self):
        positions = [pos(5.0), pos(3.0), pos(-2.0), pos(-1.0)]
        report = PostTradeAnalyzer().analyze(positions)
        # gross profit = 8, gross loss = 3 → PF = 8/3
        assert report.profit_factor == pytest.approx(8.0 / 3.0)

    def test_profit_factor_no_losses(self):
        positions = [pos(5.0), pos(3.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.profit_factor == float("inf")

    def test_profit_factor_no_profits(self):
        positions = [pos(-5.0), pos(-3.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.profit_factor == pytest.approx(0.0)

    def test_best_worst_trade(self):
        positions = [pos(1.0), pos(20.0), pos(-15.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.best_trade == 20.0
        assert report.worst_trade == -15.0

    def test_avg_winner_loser(self):
        positions = [pos(10.0), pos(5.0), pos(-3.0), pos(-1.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.avg_winner == pytest.approx(7.5)
        assert report.avg_loser == pytest.approx(-2.0)

    def test_avg_duration(self):
        positions = [
            pos(1.0, entry=0, exit=10),
            pos(2.0, entry=5, exit=15),
        ]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.avg_duration_ticks == pytest.approx(10.0)


# ── Exit reason attribution ──────────────────────────────────────────────────


class TestExitReasonAttribution:
    """Tests for exit-reason-based PnL attribution."""

    def test_groups_by_reason(self):
        positions = [
            pos(5.0, "target"), pos(3.0, "target"),
            pos(-2.0, "stop_loss"), pos(-1.0, "stop_loss"),
        ]
        report = PostTradeAnalyzer().analyze(positions)
        assert len(report.by_exit_reason) == 2
        # target should have higher total_pnl → sorted first
        assert report.by_exit_reason[0].reason == "target"
        assert report.by_exit_reason[0].total_pnl == pytest.approx(8.0)
        assert report.by_exit_reason[0].win_rate == pytest.approx(1.0)
        assert report.by_exit_reason[1].reason == "stop_loss"
        assert report.by_exit_reason[1].total_pnl == pytest.approx(-3.0)
        assert report.by_exit_reason[1].win_rate == pytest.approx(0.0)

    def test_strips_reason_suffix(self):
        """Exit reasons like 'stop_loss:balance' strip to 'stop_loss'."""
        positions = [pos(-1.0, "stop_loss:balance"), pos(-2.0, "stop_loss")]
        report = PostTradeAnalyzer().analyze(positions)
        assert len(report.by_exit_reason) == 1
        assert report.by_exit_reason[0].reason == "stop_loss"

    def test_best_worst_pnl_per_reason(self):
        positions = [
            pos(10.0, "target"), pos(1.0, "target"), pos(-1.0, "target"),
        ]
        report = PostTradeAnalyzer().analyze(positions)
        s = report.by_exit_reason[0]
        assert s.best_pnl == 10.0
        assert s.worst_pnl == -1.0

    def test_sorted_by_total_pnl_desc(self):
        positions = [
            pos(2.0, "A"), pos(2.0, "A"),
            pos(10.0, "B"),
        ]
        report = PostTradeAnalyzer().analyze(positions)
        # B has 10 > A has 4 → B should be first
        assert report.by_exit_reason[0].reason == "B"

    def test_best_exit_reason_property(self):
        positions = [pos(5.0, "target"), pos(-1.0, "stop_loss")]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.best_exit_reason == "target"
        assert report.worst_exit_reason == "stop_loss"

    def test_best_exit_reason_empty(self):
        positions = [pos(1.0, "target")]  # single reason
        report = PostTradeAnalyzer().analyze(positions)
        assert report.best_exit_reason == "target"
        assert report.worst_exit_reason == "target"


# ── Regime attribution ────────────────────────────────────────────────────────


class TestRegimeAttribution:
    """Tests for regime-based PnL attribution."""

    def test_regime_stats(self):
        positions = [
            pos(5.0, "target"), pos(-2.0, "stop_loss"),
            pos(3.0, "target"), pos(-1.0, "timeout"),
        ]
        regimes = ["TREND", "TREND", "CHOP", "CHOP"]
        report = PostTradeAnalyzer().analyze(positions, regimes=regimes)

        assert len(report.by_regime) == 2
        # TREND: 2 trades, CHOP: 2 trades, sorted by count desc
        assert report.by_regime[0].count == 2

    def test_regime_normalized_uppercase(self):
        positions = [pos(1.0), pos(2.0)]
        regimes = ["trend", "TREND"]
        report = PostTradeAnalyzer().analyze(positions, regimes=regimes)
        # Both normalized to "TREND"
        assert len(report.by_regime) == 1
        assert report.by_regime[0].regime == "TREND"
        assert report.by_regime[0].count == 2

    def test_no_regime_when_not_provided(self):
        positions = [pos(1.0), pos(2.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.by_regime == []

    def test_regime_mismatch_length_ignored(self):
        positions = [pos(1.0), pos(2.0)]
        regimes = ["TREND"]  # too short
        report = PostTradeAnalyzer().analyze(positions, regimes=regimes)
        assert report.by_regime == []

    def test_best_worst_regime(self):
        positions = [
            pos(5.0, "a"), pos(3.0, "a"), pos(-1.0, "a"),  # TREND
            pos(-2.0, "b"), pos(-1.0, "b"), pos(-3.0, "b"),  # CHOP
        ]
        regimes = ["TREND", "TREND", "TREND", "CHOP", "CHOP", "CHOP"]
        report = PostTradeAnalyzer().analyze(positions, regimes=regimes)
        assert report.best_regime == "TREND"
        assert report.worst_regime == "CHOP"

    def test_best_worst_regime_empty(self):
        positions = [pos(1.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.best_regime == "none"
        assert report.worst_regime == "none"


# ── Streaks ──────────────────────────────────────────────────────────────────


class TestStreaks:
    """Tests for consecutive win/loss streak computation."""

    def test_simple_streak(self):
        positions = [pos(1.0), pos(2.0), pos(3.0),
                      pos(-1.0), pos(-2.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.max_consecutive_wins == 3
        assert report.max_consecutive_losses == 2

    def test_interleaved(self):
        positions = [pos(1.0), pos(-1.0), pos(2.0), pos(-2.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.max_consecutive_wins == 1
        assert report.max_consecutive_losses == 1

    def test_all_wins(self):
        positions = [pos(i + 1) for i in range(10)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.max_consecutive_wins == 10
        assert report.max_consecutive_losses == 0

    def test_all_losses(self):
        positions = [pos(-i) for i in range(1, 6)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.max_consecutive_wins == 0
        assert report.max_consecutive_losses == 5

    def test_zero_pnl_does_not_break_or_extend(self):
        """Zero PnL trades should not extend streaks in either direction."""
        positions = [pos(1.0), pos(0.0), pos(2.0), pos(0.0), pos(-1.0)]
        report = PostTradeAnalyzer().analyze(positions)
        # streaks: [1] → 0 → [2] → 0 → [-1]
        # max wins = 1, max losses = 1
        assert report.max_consecutive_wins == 1
        assert report.max_consecutive_losses == 1


# ── Drawdown ──────────────────────────────────────────────────────────────────


class TestDrawdown:
    """Tests for drawdown computation."""

    def test_simple_drawdown(self):
        positions = [pos(1.0), pos(-2.0), pos(3.0)]
        report = PostTradeAnalyzer().analyze(positions, initial_balance=100.0)
        # Balance: 100 → 101 → 99 → 102. Peak so far: 101.
        # After -2: dd = (101-99)/101 ≈ 0.0198
        assert report.max_drawdown == pytest.approx(2.0 / 101.0)

    def test_no_drawdown(self):
        positions = [pos(1.0), pos(2.0), pos(3.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.max_drawdown == 0.0

    def test_deep_drawdown(self):
        positions = [pos(-50.0), pos(-30.0)]
        report = PostTradeAnalyzer().analyze(positions, initial_balance=100.0)
        # Balance: 100 → 50 → 20, peak=100, dd = (100-20)/100 = 0.80
        assert report.max_drawdown == pytest.approx(0.80)

    def test_drawdown_respects_peak(self):
        positions = [pos(-5.0), pos(100.0), pos(-10.0)]
        report = PostTradeAnalyzer().analyze(positions, initial_balance=100.0)
        # Balance: 100→95→195→185, peak=195, dd=(195-185)/195≈0.0513
        assert report.max_drawdown == pytest.approx(10.0 / 195.0)


# ── Sharpe estimate ───────────────────────────────────────────────────────────


class TestSharpe:
    """Tests for Sharpe ratio estimate."""

    def test_positive_sharpe(self):
        positions = [pos(2.0), pos(1.0), pos(3.0), pos(2.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.sharpe_estimate > 0

    def test_negative_sharpe(self):
        positions = [pos(-2.0), pos(-1.0), pos(-3.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.sharpe_estimate < 0

    def test_single_trade_zero_sharpe(self):
        positions = [pos(5.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.sharpe_estimate == 0.0

    def test_zero_variance(self):
        """All trades have same PnL → std=0 → Sharpe=0."""
        positions = [pos(3.0), pos(3.0), pos(3.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.sharpe_estimate == 0.0


# ── Edge cases ────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge-case tests for robustness."""

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            PostTradeAnalyzer().analyze([])

    def test_all_closed_no_pnl_raises(self):
        """Positions without .pnl set should be filtered and raise."""
        positions = [FakePosition(pnl=None)]
        with pytest.raises(ValueError):
            PostTradeAnalyzer().analyze(positions)

    def test_single_trade(self):
        positions = [pos(5.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.total_trades == 1
        assert report.total_pnl == 5.0
        assert report.win_rate == 1.0
        assert report.expectancy == 5.0

    def test_all_winners_no_losers(self):
        positions = [pos(5.0), pos(3.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.losers == 0
        assert report.avg_loser == 0.0
        assert report.profit_factor == float("inf")

    def test_all_losers_no_winners(self):
        positions = [pos(-5.0), pos(-3.0)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.winners == 0
        assert report.avg_winner == 0.0
        assert report.win_rate == 0.0
        assert report.profit_factor == 0.0

    def test_zero_amount_handled(self):
        """Trades with amount=0 don't cause division errors."""
        positions = [
            FakePosition(pnl=1.0, exit_reason="target", amount=0.0),
            FakePosition(pnl=2.0, exit_reason="target", amount=0.0),
        ]
        report = PostTradeAnalyzer().analyze(positions)
        # expectancy_pct should handle this
        assert report.expectancy_pct is not None

    def test_large_number_of_trades(self):
        """Should handle 1000 trades without issue."""
        positions = [pos(1.0 if i % 3 else -0.5) for i in range(1000)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.total_trades == 1000
        assert report.winners + report.losers == 1000


# ── PostTradeReport properties ────────────────────────────────────────────────


class TestPostTradeReportProperties:
    """Tests for PostTradeReport computed properties."""

    def test_to_dict_has_all_keys(self):
        positions = [pos(5.0, "target"), pos(-2.0, "stop_loss")]
        report = PostTradeAnalyzer().analyze(positions, regimes=["TREND", "CHOP"])
        d = report.to_dict()

        assert "summary" in d
        assert "by_exit_reason" in d
        assert "by_regime" in d

        summary = d["summary"]
        for key in ["total_trades", "total_pnl", "expectancy", "win_rate",
                     "profit_factor", "max_drawdown", "best_exit_reason",
                     "worst_exit_reason", "best_regime", "worst_regime"]:
            assert key in summary, f"Missing key: {key}"

    def test_to_dict_rounds_floats(self):
        positions = [pos(1.234567, "target")]
        report = PostTradeAnalyzer().analyze(positions)
        d = report.to_dict()
        assert d["summary"]["expectancy"] == round(1.234567, 4)

    def test_to_dict_by_exit_reason_has_pct(self):
        positions = [pos(5.0, "target")]
        report = PostTradeAnalyzer().analyze(positions)
        d = report.to_dict()
        assert d["by_exit_reason"][0]["pct_of_trades"] == 100.0

    def test_final_balance(self):
        positions = [pos(10.0), pos(-3.0), pos(8.0)]
        report = PostTradeAnalyzer().analyze(positions, initial_balance=500.0)
        assert report.final_balance == pytest.approx(515.0)
        assert report.total_pnl_pct == pytest.approx(15.0 / 500.0)


# ── Exit reason with mixed winners/losers ─────────────────────────────────────


class TestMixedExitReasons:
    """Tests for exit reasons where same reason has both wins and losses."""

    def test_same_reason_wins_and_losses(self):
        positions = [
            pos(10.0, "target"), pos(-5.0, "target"),
            pos(2.0, "stop_loss"), pos(-8.0, "stop_loss"),
        ]
        report = PostTradeAnalyzer().analyze(positions)
        # target: total=5, avg=2.5, wr=0.5
        # stop_loss: total=-6, avg=-3.0, wr=0.5
        target = [s for s in report.by_exit_reason if s.reason == "target"][0]
        sl = [s for s in report.by_exit_reason if s.reason == "stop_loss"][0]

        assert target.win_rate == 0.5
        assert target.avg_pnl == 2.5
        assert sl.win_rate == 0.5
        assert sl.avg_pnl == -3.0


# ── Missing optional attributes ──────────────────────────────────────────────


class TestMissingAttributes:
    """Tests for positions lacking optional attributes (duck typing resilience)."""

    def test_missing_exit_reason(self):
        positions = [FakePosition(pnl=1.0, exit_reason=None),
                      FakePosition(pnl=2.0, exit_reason=None)]
        report = PostTradeAnalyzer().analyze(positions)
        assert len(report.by_exit_reason) == 1
        assert report.by_exit_reason[0].reason == "unknown"

    def test_missing_entry_exit_ticks(self):
        positions = [FakePosition(pnl=1.0, exit_reason="ok",
                                   entry_tick=None, exit_tick=None)]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.avg_duration_ticks == 0.0

    def test_missing_amount(self):
        """Positions without 'amount' default to 10.0."""
        positions = [FakePosition(pnl=1.0, exit_reason="ok")]
        report = PostTradeAnalyzer().analyze(positions)
        assert report.expectancy_pct is not None
