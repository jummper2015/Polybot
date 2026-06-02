"""
Unit tests for P8.2 — Replay Engine and ParquetDataLoader.

Tests:
  - ParquetDataLoader: loading, filtering, market discovery
  - ReplayEngine: instant replay, time travel, determinism
  - Integration: ReplayEngine + Parquet data
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.backtesting.data_loader import DataLoader, HistoricalDataset
from src.backtesting.parquet_loader import ParquetDataLoader
from src.backtesting.replay_engine import (
    ReplayEngine,
    ReplayConfig,
    ReplayResult,
)
from src.domain.value_objects.market_tick import MarketTick
from src.infrastructure.data.schema import TICK_SCHEMA, datetime_to_ns


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════

def _make_tick_row(market_id: str, asset: str, ts_ns: int, price: float) -> dict:
    return {
        "timestamp_ns": ts_ns,
        "market_id": market_id,
        "asset": asset,
        "yes_price": price,
        "no_price": round(1.0 - price, 4),
        "mid_price": price,
        "best_bid": round(price - 0.01, 4),
        "best_ask": round(price + 0.01, 4),
        "spread": 0.02,
        "volume_24h": 5000.0,
        "liquidity_score": 50.0,
        "bids_vol_1": 100.0,
        "asks_vol_1": 80.0,
        "bids_vol_2": 50.0,
        "asks_vol_2": 40.0,
        "bids_vol_3": 20.0,
        "asks_vol_3": 15.0,
    }


def _create_parquet_data(base_dir: Path, asset: str, n_ticks: int = 100) -> tuple:
    """Create synthetic Parquet data. Returns (paths_list, market_id)."""
    from src.infrastructure.data.schema import tick_to_record_batch

    market_id = f"0xtest_{asset.lower()}_replay"
    ts_base = datetime_to_ns(datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc))

    ticks = []
    for i in range(n_ticks):
        price = 0.70 + (i % 10) * 0.01
        ticks.append(_make_tick_row(
            market_id=market_id, asset=asset,
            ts_ns=ts_base + i * 30_000_000_000, price=price,
        ))

    partition_dir = (
        base_dir / f"asset={asset}" / "year=2026" / "month=05" / "day=27"
    )
    partition_dir.mkdir(parents=True, exist_ok=True)

    batch = tick_to_record_batch(ticks, TICK_SCHEMA)
    table = pa.Table.from_batches([batch])
    filepath = partition_dir / "ticks_test.parquet"
    pq.write_table(table, filepath, compression="zstd", use_dictionary=False)

    return [str(filepath)], market_id


def _create_multi_market_data(base_dir: Path, asset: str) -> list[str]:
    """Create Parquet data for multiple markets. Returns market_ids."""
    market_ids = []
    from src.infrastructure.data.schema import tick_to_record_batch

    for m_idx in range(2):
        market_id = f"0xtest_{asset.lower()}_market{m_idx}"
        market_ids.append(market_id)
        ts_base = datetime_to_ns(datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc))

        ticks = []
        for i in range(50):
            price = 0.65 + (i % 5) * 0.02 + m_idx * 0.05
            ticks.append(_make_tick_row(
                market_id=market_id, asset=asset,
                ts_ns=ts_base + i * 30_000_000_000, price=price,
            ))

        partition_dir = (
            base_dir / f"asset={asset}" / "year=2026" / "month=05" / "day=27"
        )
        partition_dir.mkdir(parents=True, exist_ok=True)

        batch = tick_to_record_batch(ticks, TICK_SCHEMA)
        table = pa.Table.from_batches([batch])
        pq.write_table(
            table, partition_dir / f"ticks_test_m{m_idx}.parquet",
            compression="zstd", use_dictionary=False,
        )

    return market_ids


def _make_synthetic_dataset(asset: str = "BTC", n_ticks: int = 200) -> HistoricalDataset:
    """Create a simple synthetic dataset for replay testing."""
    ts = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    ticks = []
    for i in range(n_ticks):
        price = 0.70 + (i % 20) * 0.005
        ticks.append(MarketTick(
            market_id="test_replay",
            yes_price=price, no_price=1.0 - price,
            best_bid=price - 0.005, best_ask=price + 0.005,
            spread=0.01, volume_24h=5000.0,
            timestamp=ts + timedelta(seconds=i * 30),
        ))
    return HistoricalDataset(
        asset=asset, window="5m", market_id="test_replay",
        ticks=ticks,
        start_at=ticks[0].timestamp if ticks else ts,
        end_at=ticks[-1].timestamp if ticks else ts,
    )


# ══════════════════════════════════════════════════════════════════════════
# PARQUET DATA LOADER TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestParquetDataLoader:

    @pytest.fixture
    def tmp_parquet_dir(self):
        with tempfile.TemporaryDirectory(prefix="polybot_test_parquet_") as d:
            yield Path(d)

    def test_load_single_asset(self, tmp_parquet_dir):
        paths, market_id = _create_parquet_data(tmp_parquet_dir, "BTC", n_ticks=100)
        loader = ParquetDataLoader(base_dir=tmp_parquet_dir)
        dataset = loader.load(asset="BTC")

        assert dataset.tick_count == 100
        assert dataset.asset == "BTC"
        assert len(dataset.ticks) == 100
        assert isinstance(dataset.ticks[0], MarketTick)
        assert 0 < dataset.ticks[0].yes_price < 1.0

    def test_load_specific_market(self, tmp_parquet_dir):
        market_ids = _create_multi_market_data(tmp_parquet_dir, "ETH")
        loader = ParquetDataLoader(base_dir=tmp_parquet_dir)
        dataset = loader.load(asset="ETH", market_id=market_ids[0])

        assert dataset.tick_count == 50
        assert dataset.market_id == market_ids[0]

    def test_load_all_markets(self, tmp_parquet_dir):
        _create_multi_market_data(tmp_parquet_dir, "BTC")
        loader = ParquetDataLoader(base_dir=tmp_parquet_dir)
        datasets = loader.load_all(asset="BTC")

        assert len(datasets) == 2
        assert all(d.tick_count == 50 for d in datasets)
        assert datasets[0].market_id != datasets[1].market_id

    def test_list_markets(self, tmp_parquet_dir):
        market_ids = _create_multi_market_data(tmp_parquet_dir, "ETH")
        loader = ParquetDataLoader(base_dir=tmp_parquet_dir)
        found = loader.list_markets(asset="ETH")

        assert len(found) == 2
        for mid in market_ids:
            assert mid in found

    def test_get_tick_count(self, tmp_parquet_dir):
        _create_parquet_data(tmp_parquet_dir, "BTC", n_ticks=75)
        loader = ParquetDataLoader(base_dir=tmp_parquet_dir)
        assert loader.get_tick_count(asset="BTC") == 75

    def test_get_date_range(self, tmp_parquet_dir):
        _create_parquet_data(tmp_parquet_dir, "ETH", n_ticks=50)
        loader = ParquetDataLoader(base_dir=tmp_parquet_dir)
        date_range = loader.get_date_range(asset="ETH")

        assert date_range is not None
        start, end = date_range
        assert start < end
        assert start.year == 2026

    def test_empty_directory(self, tmp_parquet_dir):
        loader = ParquetDataLoader(base_dir=tmp_parquet_dir)
        with pytest.raises(FileNotFoundError):
            loader.load(asset="BTC")

    def test_data_loader_from_parquet_integration(self, tmp_parquet_dir):
        _create_parquet_data(tmp_parquet_dir, "BTC", n_ticks=30)
        result = DataLoader.from_parquet(base_dir=tmp_parquet_dir, asset="BTC")

        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0].tick_count == 30


# ══════════════════════════════════════════════════════════════════════════
# REPLAY ENGINE TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestReplayEngine:

    @pytest.fixture
    def engine(self):
        return ReplayEngine(initial_balance=1000.0)

    def test_replay_instant_mode(self, engine):
        """Instant replay processes all ticks and returns result."""
        dataset = _make_synthetic_dataset(n_ticks=200)
        config = ReplayConfig(speed=0)

        result = engine.replay(dataset, config)

        assert isinstance(result, ReplayResult)
        assert result.ticks_replayed == 200
        assert result.ticks_skipped == 0
        assert result.backtest is not None
        assert result.backtest.dataset_ticks == 200
        assert result.replay_duration_seconds > 0

    def test_replay_speed_10x(self, engine):
        """Replay at requested speed — all modes currently instant."""
        dataset = _make_synthetic_dataset(n_ticks=50)
        config = ReplayConfig(speed=10.0)

        result = engine.replay(dataset, config)

        assert result.ticks_replayed == 50
        assert result.effective_speed > 0

    def test_replay_time_travel(self, engine):
        """Time travel: skip ticks before a specific timestamp."""
        dataset = _make_synthetic_dataset(n_ticks=200)
        skip_until = dataset.start_at + timedelta(minutes=50)
        config = ReplayConfig(start_timestamp=skip_until, speed=0)

        result = engine.replay(dataset, config)

        assert result.ticks_skipped > 0
        assert result.ticks_replayed < 200
        assert result.ticks_replayed + result.ticks_skipped == 200
        assert dataset.ticks[result.ticks_skipped].timestamp >= skip_until

    def test_replay_max_ticks_limit(self, engine):
        """Limit replay to N ticks via config."""
        dataset = _make_synthetic_dataset(n_ticks=200)
        config = ReplayConfig(max_ticks=50, speed=0)

        result = engine.replay(dataset, config)

        assert result.ticks_replayed == 50

    def test_replay_determinism_same_seed(self, engine):
        """Same seed + same dataset → same result."""
        dataset = _make_synthetic_dataset(n_ticks=50)
        config = ReplayConfig(seed=42, speed=0)

        r1 = engine.replay(dataset, config)
        r2 = engine.replay(dataset, config)

        assert r1.backtest.final_balance == r2.backtest.final_balance
        assert len(r1.backtest.positions) == len(r2.backtest.positions)

    def test_replay_empty_dataset(self, engine):
        """Handle empty dataset gracefully."""
        dataset = _make_synthetic_dataset(n_ticks=0)
        config = ReplayConfig(speed=0)

        result = engine.replay(dataset, config)

        assert result.ticks_replayed == 0
        assert result.backtest is not None

    def test_replay_config_defaults(self):
        """Verify ReplayConfig defaults."""
        cfg = ReplayConfig()
        assert cfg.speed == 1.0
        assert cfg.seed == 42
        assert cfg.start_timestamp is None
        assert cfg.max_ticks is None
        assert cfg.verbose is False


# ══════════════════════════════════════════════════════════════════════════
# INTEGRATION: REPLAY WITH PARQUET DATA
# ══════════════════════════════════════════════════════════════════════════

class TestReplayWithParquet:

    @pytest.fixture
    def tmp_parquet_dir(self):
        with tempfile.TemporaryDirectory(prefix="polybot_test_replay_") as d:
            yield Path(d)

    def test_replay_from_parquet(self, tmp_parquet_dir):
        """End-to-end: Parquet → load → replay → result."""
        paths, market_id = _create_parquet_data(tmp_parquet_dir, "BTC", n_ticks=100)

        engine = ReplayEngine(
            parquet_base_dir=str(tmp_parquet_dir),
            initial_balance=1000.0,
        )
        config = ReplayConfig(speed=0)

        result = engine.replay_from_parquet(
            asset="BTC", market_id=market_id, config=config,
        )

        assert result.ticks_replayed == 100
        assert result.backtest.asset == "BTC"
        assert result.backtest.dataset_ticks == 100

    def test_replay_date_range(self, tmp_parquet_dir):
        """Replay a specific date range from Parquet."""
        paths, market_id = _create_parquet_data(tmp_parquet_dir, "ETH", n_ticks=100)

        engine = ReplayEngine(parquet_base_dir=str(tmp_parquet_dir))
        config = ReplayConfig(speed=0)

        result = engine.replay_date_range(
            asset="ETH",
            start_date=datetime(2026, 5, 27, tzinfo=timezone.utc),
            end_date=datetime(2026, 5, 28, tzinfo=timezone.utc),
            config=config,
        )

        assert result.ticks_replayed == 100

    def test_parquet_loader_accessor(self, tmp_parquet_dir):
        """ParquetDataLoader accessible via get_parquet_loader()."""
        _create_parquet_data(tmp_parquet_dir, "BTC", n_ticks=10)

        engine = ReplayEngine(parquet_base_dir=str(tmp_parquet_dir))
        loader = engine.get_parquet_loader()

        assert isinstance(loader, ParquetDataLoader)
        markets = loader.list_markets(asset="BTC")
        assert len(markets) == 1
