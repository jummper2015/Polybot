# src/quantitative/post_trade.py

"""
Post-trade analytics engine for strategy evaluation.

Analyzes closed trade performance to attribute PnL to specific sources:
exit reasons, market regimes, trade duration, and win/loss patterns.

Architecture:
    BacktestResult.closed_positions → PostTradeAnalyzer
        ├── Expectancy: mean PnL per trade, profit factor
        ├── Attribution by exit_reason: stop_loss, target, timeout, etc.
        ├── Attribution by regime (optional, with RegimeClassifier)
        ├── Win/Loss streaks and drawdown analysis
        └── PostTradeReport: summary + per-category breakdown

Usage:
    from src.quantitative.post_trade import PostTradeAnalyzer

    analyzer = PostTradeAnalyzer()
    report = analyzer.analyze(backtest_result.closed_positions)

    print(f"Expectancy: {report.expectancy:+.4f} USDC/trade")
    print(f"Best exit reason: {report.best_exit_reason}")
"""

import math
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


# ── Results ──────────────────────────────────────────────────────────────────


@dataclass
class ExitReasonStats:
    """Performance stats for a specific exit reason."""

    reason: str
    """Exit reason label (e.g. 'stop_loss', 'target_reached', 'timeout')."""

    count: int
    """Number of trades with this exit reason."""

    total_pnl: float
    """Cumulative PnL for this exit reason."""

    avg_pnl: float
    """Average PnL per trade for this exit reason."""

    win_rate: float
    """Win rate (fraction of profitable trades)."""

    best_pnl: float
    """Best single-trade PnL."""

    worst_pnl: float
    """Worst single-trade PnL."""


@dataclass
class RegimeStats:
    """Performance stats for a specific market regime."""

    regime: str
    """Regime label (e.g. 'TREND', 'CHOP', 'PANIC')."""

    count: int
    """Number of trades in this regime."""

    total_pnl: float
    win_rate: float
    avg_pnl: float


@dataclass
class PostTradeReport:
    """Full post-trade analytics report."""

    total_trades: int
    """Number of closed trades analyzed."""

    initial_balance: float
    """Starting balance."""

    final_balance: float
    """Ending balance."""

    total_pnl: float
    """Cumulative PnL across all trades."""

    total_pnl_pct: float
    """Cumulative PnL as percentage of initial balance."""

    # ── Expectancy ──────────────────────────────────────────────────────

    expectancy: float
    """Mean PnL per trade (USDC)."""

    expectancy_pct: float
    """Mean PnL per trade as percentage of trade amount."""

    win_rate: float
    """Fraction of winning trades."""

    profit_factor: float
    """Gross profit / gross loss."""

    winners: int
    losers: int

    # ── Trade-level metrics ─────────────────────────────────────────────

    best_trade: float
    worst_trade: float
    avg_winner: float
    avg_loser: float
    avg_duration_ticks: float

    # ── Risk ────────────────────────────────────────────────────────────

    max_drawdown: float = 0.0
    max_consecutive_losses: int = 0
    max_consecutive_wins: int = 0
    sharpe_estimate: float = 0.0

    # ── Attribution ─────────────────────────────────────────────────────

    by_exit_reason: list[ExitReasonStats] = field(default_factory=list)
    by_regime: list[RegimeStats] = field(default_factory=list)

    # ── Aggregated ──────────────────────────────────────────────────────

    @property
    def best_exit_reason(self) -> str:
        """Exit reason with highest average PnL."""
        if not self.by_exit_reason:
            return "none"
        return max(self.by_exit_reason, key=lambda e: e.avg_pnl).reason

    @property
    def worst_exit_reason(self) -> str:
        """Exit reason with lowest average PnL."""
        if not self.by_exit_reason:
            return "none"
        return min(self.by_exit_reason, key=lambda e: e.avg_pnl).reason

    @property
    def best_regime(self) -> str:
        """Regime with highest win rate."""
        if not self.by_regime:
            return "none"
        return max(self.by_regime, key=lambda r: r.win_rate).regime

    @property
    def worst_regime(self) -> str:
        """Regime with lowest win rate."""
        if not self.by_regime:
            return "none"
        return min(self.by_regime, key=lambda r: r.win_rate).regime

    def to_dict(self) -> dict:
        """Serialize report to JSON-friendly dict."""
        return {
            "summary": {
                "total_trades": self.total_trades,
                "total_pnl": round(self.total_pnl, 4),
                "total_pnl_pct": round(self.total_pnl_pct, 4),
                "expectancy": round(self.expectancy, 4),
                "expectancy_pct": round(self.expectancy_pct, 4),
                "win_rate": round(self.win_rate, 4),
                "profit_factor": round(self.profit_factor, 4),
                "winners": self.winners,
                "losers": self.losers,
                "best_trade": round(self.best_trade, 4),
                "worst_trade": round(self.worst_trade, 4),
                "avg_winner": round(self.avg_winner, 4),
                "avg_loser": round(self.avg_loser, 4),
                "max_drawdown": round(self.max_drawdown, 4),
                "max_consecutive_losses": self.max_consecutive_losses,
                "max_consecutive_wins": self.max_consecutive_wins,
                "sharpe_estimate": round(self.sharpe_estimate, 4),
                "best_exit_reason": self.best_exit_reason,
                "worst_exit_reason": self.worst_exit_reason,
                "best_regime": self.best_regime,
                "worst_regime": self.worst_regime,
            },
            "by_exit_reason": [
                {
                    "reason": e.reason,
                    "count": e.count,
                    "total_pnl": round(e.total_pnl, 4),
                    "avg_pnl": round(e.avg_pnl, 4),
                    "win_rate": round(e.win_rate, 4),
                    "best_pnl": round(e.best_pnl, 4),
                    "worst_pnl": round(e.worst_pnl, 4),
                    "pct_of_trades": round(e.count / max(1, self.total_trades) * 100, 1),
                }
                for e in self.by_exit_reason
            ],
            "by_regime": [
                {
                    "regime": r.regime,
                    "count": r.count,
                    "total_pnl": round(r.total_pnl, 4),
                    "win_rate": round(r.win_rate, 4),
                    "avg_pnl": round(r.avg_pnl, 4),
                }
                for r in self.by_regime
            ],
        }


# ── Analyzer ────────────────────────────────────────────────────────────────


class PostTradeAnalyzer:
    """
    Post-trade performance analyzer.

    Computes expectancy, profit factor, win/loss streaks, and
    attributes PnL to exit reasons and market regimes.

    Usage:
        analyzer = PostTradeAnalyzer()
        report = analyzer.analyze(positions, initial_balance=1000.0)
        # Optional: with regime labels
        report = analyzer.analyze(positions, regimes=['TREND','CHOP',...])
    """

    def __init__(self):
        pass

    # ── Public API ─────────────────────────────────────────────────────────

    def analyze(
        self,
        positions: list,
        initial_balance: float = 1000.0,
        regimes: Optional[list[str]] = None,
    ) -> PostTradeReport:
        """
        Analyze a list of closed BacktestPosition objects.

        Args:
            positions: List of position objects with .pnl, .exit_reason,
                       .entry_tick, .exit_tick, .amount.
            initial_balance: Starting balance for PnL percentage.
            regimes: Optional list of regime labels (same length as positions)
                     for regime-based attribution.

        Returns:
            PostTradeReport with full analytics.
        """
        closed = [p for p in positions if p.pnl is not None]
        if not closed:
            raise ValueError("No closed positions with PnL found")

        pnls = [p.pnl for p in closed]
        amounts = [getattr(p, "amount", 10.0) for p in closed]
        reasons = [getattr(p, "exit_reason", "unknown") or "unknown"
                    for p in closed]

        total = len(pnls)
        total_pnl = sum(pnls)
        final_balance = initial_balance + total_pnl
        win_rate = sum(1 for p in pnls if p > 0) / total

        # ── Expectancy ────────────────────────────────────────────────
        expectancy = total_pnl / total
        pnl_pcts = [
            pnls[i] / amounts[i] if amounts[i] > 0 else 0.0
            for i in range(total)
        ]
        expectancy_pct = sum(pnl_pcts) / total

        # ── Profit Factor ──────────────────────────────────────────────
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        profit_factor = (
            gross_profit / gross_loss if gross_loss > 0
            else float("inf") if gross_profit > 0 else 0.0
        )

        # ── Trade-level ────────────────────────────────────────────────
        best_trade = max(pnls)
        worst_trade = min(pnls)
        winners_list = [p for p in pnls if p > 0]
        losers_list = [p for p in pnls if p < 0]
        avg_winner = sum(winners_list) / len(winners_list) if winners_list else 0.0
        avg_loser = sum(losers_list) / len(losers_list) if losers_list else 0.0

        # Avg duration
        durations = []
        for p in closed:
            et = getattr(p, "entry_tick", None)
            xt = getattr(p, "exit_tick", None)
            if et is not None and xt is not None:
                durations.append(xt - et)
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        # ── Attribution by exit reason ─────────────────────────────────
        by_reason = self._attribute_by_reason(closed, reasons)

        # ── Attribution by regime ──────────────────────────────────────
        by_regime: list[RegimeStats] = []
        if regimes and len(regimes) == total:
            by_regime = self._attribute_by_regime(pnls, regimes)

        # ── Streaks and drawdown ───────────────────────────────────────
        max_dd = self._compute_max_drawdown(pnls, initial_balance)
        max_cl, max_cw = self._compute_streaks(pnls)

        # ── Sharpe estimate ────────────────────────────────────────────
        sharpe = self._estimate_sharpe(pnls)

        logger.info(
            "post_trade_analysis_complete",
            total_trades=total,
            total_pnl=round(total_pnl, 4),
            expectancy=round(expectancy, 4),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            reasons=len(by_reason),
        )

        return PostTradeReport(
            total_trades=total,
            initial_balance=initial_balance,
            final_balance=final_balance,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl / initial_balance,
            expectancy=expectancy,
            expectancy_pct=expectancy_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            winners=len(winners_list),
            losers=len(losers_list),
            best_trade=best_trade,
            worst_trade=worst_trade,
            avg_winner=avg_winner,
            avg_loser=avg_loser,
            avg_duration_ticks=avg_duration,
            by_exit_reason=by_reason,
            by_regime=by_regime,
            max_drawdown=max_dd,
            max_consecutive_losses=max_cl,
            max_consecutive_wins=max_cw,
            sharpe_estimate=sharpe,
        )

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _attribute_by_reason(
        positions: list, reasons: list[str],
    ) -> list[ExitReasonStats]:
        """Group trades by exit reason and compute per-reason stats."""
        groups: dict[str, list[float]] = {}
        for p, reason in zip(positions, reasons):
            clean = reason.split(":")[0].strip()
            if clean not in groups:
                groups[clean] = []
            groups[clean].append(p.pnl)

        result = []
        for reason, pnls in sorted(groups.items(),
                                    key=lambda x: sum(x[1]), reverse=True):
            total = sum(pnls)
            avg = total / len(pnls)
            wr = sum(1 for p in pnls if p > 0) / len(pnls)
            result.append(ExitReasonStats(
                reason=reason,
                count=len(pnls),
                total_pnl=total,
                avg_pnl=avg,
                win_rate=wr,
                best_pnl=max(pnls),
                worst_pnl=min(pnls),
            ))

        return result

    @staticmethod
    def _attribute_by_regime(
        pnls: list[float], regimes: list[str],
    ) -> list[RegimeStats]:
        """Group trades by regime and compute per-regime stats."""
        groups: dict[str, list[float]] = {}
        for pnl, regime in zip(pnls, regimes):
            key = regime.upper()
            if key not in groups:
                groups[key] = []
            groups[key].append(pnl)

        result = []
        for regime, rpnls in sorted(groups.items(),
                                     key=lambda x: len(x[1]), reverse=True):
            result.append(RegimeStats(
                regime=regime,
                count=len(rpnls),
                total_pnl=sum(rpnls),
                win_rate=sum(1 for p in rpnls if p > 0) / len(rpnls),
                avg_pnl=sum(rpnls) / len(rpnls),
            ))

        return result

    @staticmethod
    def _compute_max_drawdown(
        pnls: list[float], initial: float,
    ) -> float:
        """Max drawdown from peak equity (as fraction)."""
        balance = initial
        peak = balance
        max_dd = 0.0
        for pnl in pnls:
            balance += pnl
            peak = max(peak, balance)
            if peak > 0:
                dd = (peak - balance) / peak
                max_dd = max(max_dd, dd)
        return max_dd

    @staticmethod
    def _compute_streaks(pnls: list[float]) -> tuple[int, int]:
        """Max consecutive losses and wins."""
        max_losses = 0
        max_wins = 0
        cur_losses = 0
        cur_wins = 0

        for p in pnls:
            if p < 0:
                cur_losses += 1
                cur_wins = 0
                max_losses = max(max_losses, cur_losses)
            elif p > 0:
                cur_wins += 1
                cur_losses = 0
                max_wins = max(max_wins, cur_wins)
            else:
                # p == 0: break both streaks
                cur_losses = 0
                cur_wins = 0

        return max_losses, max_wins

    @staticmethod
    def _estimate_sharpe(pnls: list[float]) -> float:
        """Simple Sharpe estimate from trade-level PnLs."""
        n = len(pnls)
        if n < 2:
            return 0.0
        mean = sum(pnls) / n
        variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        if variance < 1e-12:
            return 0.0
        std = math.sqrt(variance)
        return (mean / std) * math.sqrt(n)
