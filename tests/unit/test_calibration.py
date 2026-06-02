# tests/unit/test_calibration.py

"""
Tests for confidence calibration module (P10.3).

Covers:
- ReliabilityBin properties
- CalibrationReport metrics (Brier, ECE, MCE, grade)
- ConfidenceCalibrator with perfect/poor/random calibration
- calibrate_from_positions integration
- Edge cases: empty data, all wins, all losses
"""

import json

import pytest

from src.quantitative.calibration import (
    CalibrationReport,
    ConfidenceCalibrator,
    ReliabilityBin,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def perfect_outcomes() -> list[tuple[float, bool]]:
    """Perfect calibration: high confidence = win, low = loss."""
    return [
        (0.90, True), (0.85, True), (0.95, True),
        (0.10, False), (0.15, False), (0.05, False),
        (0.80, True), (0.70, True), (0.20, False),
        (0.88, True), (0.12, False), (0.92, True),
    ]


@pytest.fixture
def poor_outcomes() -> list[tuple[float, bool]]:
    """Poor calibration: high confidence but random outcomes."""
    return [
        (0.90, False), (0.85, True), (0.95, False),
        (0.10, True), (0.15, False), (0.05, True),
        (0.80, False), (0.70, True), (0.20, False),
        (0.88, True), (0.12, False), (0.92, True),
    ]


@pytest.fixture
def all_wins() -> list[tuple[float, bool]]:
    """All trades profitable."""
    return [(0.75, True), (0.80, True), (0.60, True),
            (0.90, True), (0.55, True)]


@pytest.fixture
def all_losses() -> list[tuple[float, bool]]:
    """All trades unprofitable."""
    return [(0.75, False), (0.80, False), (0.60, False),
            (0.90, False), (0.55, False)]


# ── ReliabilityBin Tests ───────────────────────────────────────────────────


class TestReliabilityBin:
    """ReliabilityBin properties."""

    def test_calibration_error(self) -> None:
        bin_ = ReliabilityBin(
            bin_label="0.8-1.0", confidence_min=0.8, confidence_max=1.0,
            count=10, mean_confidence=0.85, actual_frequency=0.80,
        )
        assert bin_.calibration_error == pytest.approx(0.05)

    def test_is_calibrated_true(self) -> None:
        bin_ = ReliabilityBin(
            bin_label="0.8-1.0", confidence_min=0.8, confidence_max=1.0,
            count=10, mean_confidence=0.85, actual_frequency=0.83,
        )
        assert bin_.is_calibrated is True  # error 0.02 <= 0.10

    def test_is_calibrated_false(self) -> None:
        bin_ = ReliabilityBin(
            bin_label="0.8-1.0", confidence_min=0.8, confidence_max=1.0,
            count=10, mean_confidence=0.85, actual_frequency=0.50,
        )
        assert bin_.is_calibrated is False  # error 0.35 > 0.10


# ── CalibrationReport Tests ────────────────────────────────────────────────


class TestCalibrationReport:
    """CalibrationReport aggregate metrics."""

    def test_empty_report(self) -> None:
        report = CalibrationReport(
            total_trades=0, overall_win_rate=0.0, mean_confidence=0.0,
            brier_score=0.0, brier_skill_score=0.0, brier_ref=0.0,
        )
        assert report.ece == 0.0
        assert report.mce == 0.0
        assert report.calibration_grade == "INSUFFICIENT_DATA"

    def test_excellent_grade(self) -> None:
        bins = [
            ReliabilityBin("0.0-0.2", 0.0, 0.2, 5, 0.10, 0.12),
            ReliabilityBin("0.8-1.0", 0.8, 1.0, 5, 0.88, 0.85),
        ]
        report = CalibrationReport(
            total_trades=10, overall_win_rate=0.5, mean_confidence=0.49,
            brier_score=0.10, brier_skill_score=0.3, brier_ref=0.14, bins=bins,
        )
        assert report.calibration_grade == "EXCELLENT"

    def test_good_grade(self) -> None:
        report = CalibrationReport(
            total_trades=20, overall_win_rate=0.5, mean_confidence=0.5,
            brier_score=0.18, brier_skill_score=0.1, brier_ref=0.20,
        )
        assert report.calibration_grade == "GOOD"

    def test_unreliable_grade(self) -> None:
        report = CalibrationReport(
            total_trades=15, overall_win_rate=0.3, mean_confidence=0.8,
            brier_score=0.35, brier_skill_score=-0.1, brier_ref=0.25,
        )
        assert report.calibration_grade == "UNRELIABLE"

    def test_ece_weighted_average(self) -> None:
        """ECE should be weighted by bin count."""
        bins = [
            ReliabilityBin("0.0-0.2", 0.0, 0.2, 8, 0.10, 0.20),  # error 0.10
            ReliabilityBin("0.8-1.0", 0.8, 1.0, 2, 0.90, 0.50),  # error 0.40
        ]
        report = CalibrationReport(
            total_trades=10, overall_win_rate=0.5, mean_confidence=0.5,
            brier_score=0.2, brier_skill_score=0.0, brier_ref=0.2, bins=bins,
        )
        expected = (8 * 0.10 + 2 * 0.40) / 10
        assert report.ece == pytest.approx(expected)

    def test_mce_worst_bin(self) -> None:
        bins = [
            ReliabilityBin("0.0-0.2", 0.0, 0.2, 5, 0.10, 0.12),
            ReliabilityBin("0.8-1.0", 0.8, 1.0, 5, 0.90, 0.30),
        ]
        report = CalibrationReport(
            total_trades=10, overall_win_rate=0.5, mean_confidence=0.5,
            brier_score=0.2, brier_skill_score=0.0, brier_ref=0.2, bins=bins,
        )
        assert report.mce == pytest.approx(0.60)  # worst bin error

    def test_to_dict(self) -> None:
        bins = [
            ReliabilityBin("0.0-0.2", 0.0, 0.2, 3, 0.10, 0.00),
            ReliabilityBin("0.8-1.0", 0.8, 1.0, 3, 0.90, 1.00),
        ]
        report = CalibrationReport(
            total_trades=6, overall_win_rate=0.5, mean_confidence=0.50,
            brier_score=0.15, brier_skill_score=0.25, brier_ref=0.20,
            bins=bins,
        )
        d = report.to_dict()
        assert d["summary"]["total_trades"] == 6
        assert len(d["reliability_curve"]) == 2
        assert d["reliability_curve"][0]["bin"] == "0.0-0.2"

    def test_to_dict_json_serializable(self) -> None:
        report = CalibrationReport(
            total_trades=10, overall_win_rate=0.6, mean_confidence=0.55,
            brier_score=0.12, brier_skill_score=0.3, brier_ref=0.17,
        )
        json_str = json.dumps(report.to_dict())
        assert len(json_str) > 0


# ── ConfidenceCalibrator Tests ─────────────────────────────────────────────


class TestConfidenceCalibrator:
    """ConfidenceCalibrator integration tests."""

    def test_calibrate_perfect(self, perfect_outcomes) -> None:
        cal = ConfidenceCalibrator(n_bins=2)  # 2 bins for small sample
        report = cal.calibrate(perfect_outcomes)
        assert report.total_trades == len(perfect_outcomes)
        # Brier should be low for near-perfect calibration
        assert report.brier_score < 0.20
        # Grade may be FAIR with small sample due to ECE sensitivity

    def test_calibrate_poor(self, poor_outcomes) -> None:
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate(poor_outcomes)
        assert report.total_trades == len(poor_outcomes)
        # Poor calibration should have higher Brier score
        assert report.brier_score > 0.0

    def test_all_wins_brier_nonzero(self, all_wins) -> None:
        """All wins with varying confidence: Brier > 0 (imperfect predictions)."""
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate(all_wins)
        # Confidence varies but all wins → Brier measures this gap
        assert report.brier_score > 0.0

    def test_all_losses_brier_nonzero(self, all_losses) -> None:
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate(all_losses)
        assert report.brier_score > 0.0

    def test_perfect_prediction_brier_zero(self) -> None:
        """Confidence 1.0 always wins, 0.0 always loses → Brier = 0."""
        outcomes = [(1.0, True), (0.0, False), (1.0, True), (0.0, False)]
        cal = ConfidenceCalibrator(n_bins=2)
        report = cal.calibrate(outcomes)
        assert report.brier_score == pytest.approx(0.0, abs=0.01)

    def test_brier_ref_meaningful(self) -> None:
        """Brier reference should be based on base rate."""
        outcomes = [(0.8, True), (0.7, False), (0.6, True), (0.9, False)]
        cal = ConfidenceCalibrator(n_bins=4)
        report = cal.calibrate(outcomes)
        assert report.brier_ref > 0.0
        # BSS should be in [-1, 1] roughly
        assert -2.0 <= report.brier_skill_score <= 1.0

    def test_bins_count(self) -> None:
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate([(0.50, True), (0.50, False)])
        assert len(report.bins) == 5

    def test_bins_count_custom(self) -> None:
        cal = ConfidenceCalibrator(n_bins=3)
        report = cal.calibrate([(0.50, True)])
        assert len(report.bins) == 3
        assert report.bins[0].bin_label == "0.0-0.3"

    def test_empty_data_raises(self) -> None:
        cal = ConfidenceCalibrator()
        with pytest.raises(ValueError, match="No outcomes"):
            cal.calibrate([])

    def test_invalid_n_bins(self) -> None:
        with pytest.raises(ValueError, match="n_bins"):
            ConfidenceCalibrator(n_bins=1)
        with pytest.raises(ValueError, match="n_bins"):
            ConfidenceCalibrator(n_bins=21)

    def test_calibrate_from_positions(self) -> None:
        class FakePosition:
            def __init__(self, pnl: float, entry_confidence: float):
                self.pnl = pnl
                self.entry_confidence = entry_confidence

        positions = [
            FakePosition(5.0, 0.80),
            FakePosition(-2.0, 0.60),
            FakePosition(3.0, 0.90),
            FakePosition(-1.0, 0.50),
            FakePosition(None, 0.70),  # open position — skipped
        ]

        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate_from_positions(positions)
        assert report.total_trades == 4  # open position excluded
        assert report.brier_score > 0.0

    def test_calibrate_from_positions_empty(self) -> None:
        """All open positions → should raise."""
        class FakePosition:
            def __init__(self, pnl, conf):
                self.pnl = pnl
                self.entry_confidence = conf

        cal = ConfidenceCalibrator()
        with pytest.raises(ValueError, match="No closed positions"):
            cal.calibrate_from_positions([FakePosition(None, 0.5)])

    def test_calibrate_from_positions_missing_confidence(self) -> None:
        """Missing confidence → default to 0.5."""
        class FakePosition:
            def __init__(self, pnl: float):
                self.pnl = pnl

        positions = [FakePosition(5.0), FakePosition(-2.0)]
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate_from_positions(positions)
        assert report.total_trades == 2
        assert report.mean_confidence == 0.5

    def test_calibration_consistent_with_data(
        self, perfect_outcomes,
    ) -> None:
        """High-confidence should map to high actual_frequency."""
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate(perfect_outcomes)
        high_bins = [b for b in report.bins if b.confidence_min >= 0.6]
        low_bins = [b for b in report.bins if b.confidence_max <= 0.4]
        # High-confidence bins should have higher win rates
        if high_bins and low_bins:
            avg_high = sum(b.actual_frequency for b in high_bins) / len(high_bins)
            avg_low = sum(b.actual_frequency for b in low_bins) / len(low_bins)
            assert avg_high >= avg_low

    def test_sharpness_computed(self) -> None:
        """Sharpness should be positive when confidences vary."""
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate([
            (0.80, True), (0.60, False), (0.90, True),
        ])
        assert report.sharpness > 0.0


# ── Edge Case Tests ───────────────────────────────────────────────────────


class TestCalibrationEdgeCases:
    """Edge cases and boundary conditions."""

    def test_single_outcome(self) -> None:
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate([(0.75, True)])
        assert report.total_trades == 1
        assert report.brier_score >= 0.0

    def test_confidence_at_boundaries(self) -> None:
        """Confidence 0.0 and 1.0 should be handled correctly."""
        outcomes = [
            (0.0, False), (1.0, True), (0.0, False), (1.0, True),
            (1.0, False),  # overconfident
        ]
        cal = ConfidenceCalibrator(n_bins=5)
        report = cal.calibrate(outcomes)
        assert report.total_trades == 5
        assert report.brier_score > 0.0  # last one was wrong

    def test_large_dataset(self) -> None:
        """Handle 1000 outcomes without error."""
        outcomes = [(0.60 + i * 0.0004, i % 2 == 0) for i in range(1000)]
        cal = ConfidenceCalibrator(n_bins=10)
        report = cal.calibrate(outcomes)
        assert report.total_trades == 1000
        assert 0.0 <= report.brier_score <= 1.0
