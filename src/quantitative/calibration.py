# src/quantitative/calibration.py

"""
Confidence calibration tools for strategy evaluation.

Measures how well a strategy's confidence scores align with actual
trade outcomes. A well-calibrated strategy should have:
- High-confidence trades that win often
- Low-confidence trades that win less often
- Brier score < 0.25 (better than random)
- Reliability curve close to the diagonal

Architecture:
    Trade outcomes (confidence, win/loss) → ConfidenceCalibrator
        ├── Brier score: mean squared error between confidence and outcome
        ├── Reliability curve: binned confidence vs. actual win rate
        ├── Calibration metrics: ECE, MCE, sharpness
        └── CalibrationReport: summary + per-bin breakdown

Usage:
    from src.quantitative.calibration import ConfidenceCalibrator

    outcomes = [(0.75, True), (0.60, False), (0.85, True), ...]
    calibrator = ConfidenceCalibrator()
    report = calibrator.calibrate(outcomes)

    print(f"Brier score: {report.brier_score:.4f}")
    print(f"ECE: {report.ece:.4f}")
"""

import math
from dataclasses import dataclass, field

# ── Results ──────────────────────────────────────────────────────────────────


@dataclass
class ReliabilityBin:
    """Single bin in a reliability curve."""

    bin_label: str
    """Human-readable label, e.g. '0.0-0.2'."""

    confidence_min: float
    confidence_max: float
    """Range of confidence scores in this bin (inclusive-exclusive)."""

    count: int
    """Number of trades in this bin."""

    mean_confidence: float
    """Average confidence score in this bin."""

    actual_frequency: float
    """Observed win rate in this bin."""

    @property
    def calibration_error(self) -> float:
        """Absolute difference between mean confidence and actual frequency."""
        return abs(self.mean_confidence - self.actual_frequency)

    @property
    def is_calibrated(self) -> bool:
        """A bin is calibrated if the error is within ±10%."""
        return self.calibration_error <= 0.10


@dataclass
class CalibrationReport:
    """Full confidence calibration report."""

    total_trades: int
    """Number of trades evaluated."""

    overall_win_rate: float
    """Overall proportion of winning trades."""

    mean_confidence: float
    """Average confidence across all trades."""

    # ── Brier Score ──────────────────────────────────────────────────────

    brier_score: float
    """Brier score: mean squared error between confidence and binary outcome.
    Range: [0, 1]. Lower is better. Random guess (confidence=0.5) = 0.25."""

    brier_skill_score: float
    """Brier Skill Score: 1 - (brier_score / brier_ref).
    Positive means better than reference (always-predict-mean)."""

    brier_ref: float
    """Reference Brier score (always predicting the base rate)."""

    # ── Reliability ──────────────────────────────────────────────────────

    bins: list[ReliabilityBin] = field(default_factory=list)
    """Reliability curve bins."""

    sharpness: float = 0.0
    """Sharpness: std deviation of predicted confidences.
    Higher = more decisive predictions. Independent of calibration."""

    # ── Calibration Error ─────────────────────────────────────────────────

    @property
    def ece(self) -> float:
        """Expected Calibration Error: weighted average of per-bin errors."""
        if self.total_trades == 0:
            return 0.0
        weighted = sum(b.count * b.calibration_error for b in self.bins)
        return weighted / self.total_trades

    @property
    def mce(self) -> float:
        """Maximum Calibration Error: worst bin error."""
        if not self.bins:
            return 0.0
        return max(b.calibration_error for b in self.bins)

    @property
    def calibration_grade(self) -> str:
        """Human-readable calibration grade."""
        if self.total_trades < 10:
            return "INSUFFICIENT_DATA"
        if self.brier_score <= 0.15 and self.ece <= 0.05:
            return "EXCELLENT"
        if self.brier_score <= 0.20 and self.ece <= 0.10:
            return "GOOD"
        if self.brier_score <= 0.25 and self.ece <= 0.15:
            return "FAIR"
        if self.brier_score <= 0.30:
            return "POOR"
        return "UNRELIABLE"

    # ── Serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize report to JSON-friendly dict."""
        return {
            "summary": {
                "total_trades": self.total_trades,
                "overall_win_rate": round(self.overall_win_rate, 4),
                "mean_confidence": round(self.mean_confidence, 4),
                "brier_score": round(self.brier_score, 4),
                "brier_skill_score": round(self.brier_skill_score, 4),
                "brier_ref": round(self.brier_ref, 4),
                "ece": round(self.ece, 4),
                "mce": round(self.mce, 4),
                "calibration_grade": self.calibration_grade,
            },
            "reliability_curve": [
                {
                    "bin": b.bin_label,
                    "count": b.count,
                    "mean_confidence": round(b.mean_confidence, 4),
                    "actual_frequency": round(b.actual_frequency, 4),
                    "error": round(b.calibration_error, 4),
                    "calibrated": b.is_calibrated,
                }
                for b in self.bins
            ],
        }


# ── Calibrator ──────────────────────────────────────────────────────────────


class ConfidenceCalibrator:
    """
    Evaluates confidence calibration of a trading strategy.

    Takes a list of (confidence, outcome) pairs where:
    - confidence: float in [0, 1] — the strategy's confidence at entry
    - outcome: bool — True if the trade was profitable, False otherwise

    Computes:
    - Brier score: how close predictions are to outcomes
    - Reliability curve: per-bin confidence vs. actual frequency
    - ECE/MCE: calibration error metrics

    Usage with backtest positions:
        outcomes = []
        for pos in backtest_result.closed_positions:
            if pos.pnl is not None:
                outcomes.append((pos.entry_confidence or 0.5, pos.pnl > 0))
        report = ConfidenceCalibrator().calibrate(outcomes)
    """

    def __init__(self, n_bins: int = 5):
        if n_bins < 2 or n_bins > 20:
            raise ValueError(f"n_bins must be between 2 and 20, got {n_bins}")
        self._n_bins = n_bins

    # ── Public API ─────────────────────────────────────────────────────────

    def calibrate(
        self, outcomes: list[tuple[float, bool]],
    ) -> CalibrationReport:
        """
        Run confidence calibration analysis.

        Args:
            outcomes: List of (confidence, is_winner) pairs.
                      Confidence must be in [0, 1].

        Returns:
            CalibrationReport with Brier score, reliability curve, ECE, MCE.
        """
        if not outcomes:
            raise ValueError("No outcomes provided for calibration")

        confidences = [c for c, _ in outcomes]
        wins = [1.0 if w else 0.0 for _, w in outcomes]

        total = len(outcomes)
        win_rate = sum(wins) / total
        mean_conf = sum(confidences) / total

        # ── Brier Score ─────────────────────────────────────────────────
        brier = sum((c - w) ** 2 for c, w in zip(confidences, wins)) / total

        # Brier reference: always predict the base rate (mean win rate)
        brier_ref = sum((win_rate - w) ** 2 for w in wins) / total
        bss = 1.0 - (brier / brier_ref) if brier_ref > 0 else 0.0

        # ── Reliability Curve ───────────────────────────────────────────
        bins = self._build_bins(confidences, wins)

        # ── Sharpness ───────────────────────────────────────────────────
        variance = sum((c - mean_conf) ** 2 for c in confidences) / total
        sharp = math.sqrt(variance) if variance > 0 else 0.0

        report = CalibrationReport(
            total_trades=total,
            overall_win_rate=win_rate,
            mean_confidence=mean_conf,
            brier_score=brier,
            brier_skill_score=bss,
            brier_ref=brier_ref,
            bins=bins,
        )
        report.sharpness = sharp

        return report

    def calibrate_from_positions(
        self,
        positions: list,
        confidence_attr: str = "entry_confidence",
    ) -> CalibrationReport:
        """
        Convenience method: extract outcomes from BacktestPosition-like objects.

        Each position should have:
        - A confidence attribute (default: entry_confidence)
        - A pnl attribute

        Args:
            positions: List of position objects with .pnl and confidence.
            confidence_attr: Name of the confidence attribute on the position.

        Returns:
            CalibrationReport.
        """
        outcomes = []
        for pos in positions:
            if pos.pnl is None:
                continue
            conf = getattr(pos, confidence_attr, None)
            if conf is None:
                conf = 0.5  # default neutral confidence
            outcomes.append((float(conf), pos.pnl > 0))

        if not outcomes:
            raise ValueError(
                "No closed positions with PnL found for calibration"
            )

        return self.calibrate(outcomes)

    # ── Internal ───────────────────────────────────────────────────────────

    def _build_bins(
        self,
        confidences: list[float],
        wins: list[float],
    ) -> list[ReliabilityBin]:
        """Build reliability curve bins from confidence/outcome data."""
        bin_width = 1.0 / self._n_bins
        bins: list[ReliabilityBin] = []

        for i in range(self._n_bins):
            lo = i * bin_width
            hi = lo + bin_width
            # Last bin is inclusive on the upper bound
            is_last = (i == self._n_bins - 1)

            # Collect trades in this bin
            bin_confs = []
            bin_wins = []
            for c, w in zip(confidences, wins):
                in_bin = (
                    (lo <= c <= hi) if is_last
                    else (lo <= c < hi)
                )
                if in_bin:
                    bin_confs.append(c)
                    bin_wins.append(w)

            count = len(bin_confs)
            mean_c = sum(bin_confs) / count if count > 0 else 0.0
            freq = sum(bin_wins) / count if count > 0 else 0.0

            bins.append(ReliabilityBin(
                bin_label=f"{lo:.1f}-{hi:.1f}",
                confidence_min=lo,
                confidence_max=hi,
                count=count,
                mean_confidence=round(mean_c, 4),
                actual_frequency=round(freq, 4),
            ))

        return bins
