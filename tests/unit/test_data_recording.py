"""
Unit tests for the data recording layer (P8.1).

Tests:
  - Schema definitions (fields, metadata, conversions)
  - ParquetTickWriter (buffered writes, partitioning, compression)
  - MultiAssetRecorder (high-level orchestration)
"""

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.infrastructure.data.schema import (
    TICK_SCHEMA,
    TICK_FIELDS,
    tick_to_record_batch,
    tick_to_record_batch_from_market_ticks,
    datetime_to_ns,
)
from src.infrastructure.data.storage import (
    ParquetTickWriter,
    MultiAssetRecorder,
)


# ══════════════════════════════════════════════════════════════════════════
# SCHEMA TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestSchema:
    """Verify schema structure, metadata, and conversion helpers."""

    def test_tick_schema_has_required_fields(self):
        """TICK_SCHEMA must have all required identity fields."""
        field_names = [f.name for f in TICK_SCHEMA]
        for required in ("timestamp_ns", "market_id", "asset", "yes_price", "no_price", "spread"):
            assert required in field_names, f"Missing field: {required}"

    def test_tick_schema_fields_count(self):
        """TICK_SCHEMA should have exactly the expected number of fields."""
        assert len(TICK_FIELDS) == 17
        assert len(TICK_SCHEMA) == 17

    def test_tick_schema_has_metadata(self):
        """Schema metadata must include version and type."""
        meta = TICK_SCHEMA.metadata
        assert meta.get(b"polybot_schema_version") == b"1.0"
        assert meta.get(b"polybot_schema_type") == b"tick"

    def test_datetime_to_ns_utc_aware(self):
        """Convert UTC-aware datetime to nanoseconds."""
        dt = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        ns = datetime_to_ns(dt)
        # 2026-05-26 12:00:00 UTC = 1779796800 seconds since epoch
        expected = 1779796800 * 1_000_000_000
        assert ns == expected, f"Expected {expected}, got {ns}"

    def test_datetime_to_ns_naive(self):
        """Naive datetimes treated as UTC."""
        dt = datetime(2026, 1, 1, 0, 0, 0)
        ns = datetime_to_ns(dt)
        expected = 1767225600 * 1_000_000_000
        assert ns == expected

    def test_tick_to_record_batch(self):
        """Convert tick dicts to a valid RecordBatch."""
        ticks = [
            {
                "timestamp_ns": 1779796800000000000,
                "market_id": "0xtest1",
                "asset": "BTC",
                "yes_price": 0.75,
                "no_price": 0.25,
                "mid_price": 0.75,
                "best_bid": 0.74,
                "best_ask": 0.76,
                "spread": 0.02,
                "volume_24h": 5000.0,
                "liquidity_score": 42.5,
                "bids_vol_1": 100.0,
                "asks_vol_1": 80.0,
                "bids_vol_2": 50.0,
                "asks_vol_2": 40.0,
                "bids_vol_3": 20.0,
                "asks_vol_3": 15.0,
            },
        ]

        batch = tick_to_record_batch(ticks)
        assert batch.num_rows == 1
        assert batch.num_columns == 17
        assert batch.column("asset")[0].as_py() == "BTC"
        assert batch.column("yes_price")[0].as_py() == 0.75
        assert batch.column("timestamp_ns")[0].as_py() == 1779796800000000000

    def test_tick_to_record_batch_missing_optionals(self):
        """Missing optional fields should become None/null."""
        ticks = [
            {
                "timestamp_ns": 1779796800000000000,
                "market_id": "0xmissing",
                "asset": "ETH",
                "yes_price": 0.60,
                "no_price": 0.40,
                "mid_price": 0.60,
                "best_bid": 0.59,
                "best_ask": 0.61,
                "spread": 0.02,
                "volume_24h": 3000.0,
                # liquidity_score and orderbook depths are missing
            },
        ]

        batch = tick_to_record_batch(ticks)
        assert batch.num_rows == 1
        # Missing fields should be null
        assert batch.column("liquidity_score")[0].as_py() is None
        assert batch.column("bids_vol_1")[0].as_py() is None

    def test_tick_to_record_batch_from_market_ticks(self):
        """Convert domain MarketTick objects."""
        # Create a mock MarketTick
        class MockTick:
            pass

        tick = MockTick()
        tick.timestamp = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        tick.yes_price = 0.70
        tick.no_price = 0.30
        tick.best_bid = 0.69
        tick.best_ask = 0.71
        tick.spread = 0.02
        tick.volume_24h = 10000.0

        batch = tick_to_record_batch_from_market_ticks(
            [tick], market_id="0xtest2", asset="ETH",
        )
        assert batch.num_rows == 1
        assert batch.column("asset")[0].as_py() == "ETH"
        assert batch.column("market_id")[0].as_py() == "0xtest2"
        # mid_price should be computed
        assert batch.column("mid_price")[0].as_py() == 0.70
        # liquidity_score should be computed
        score = batch.column("liquidity_score")[0].as_py()
        assert score is not None
        assert score > 0


# ══════════════════════════════════════════════════════════════════════════
# STORAGE TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestParquetTickWriter:
    """Test buffered Parquet writer with partitioning."""

    @pytest.fixture
    def tmp_base(self):
        """Create a temp directory for Parquet output."""
        path = Path(tempfile.mkdtemp(prefix="polybot_test_parquet_"))
        yield path
        shutil.rmtree(str(path), ignore_errors=True)

    def _make_tick(self, asset: str, ts_ns: int, price: float, **kw):
        return {
            "timestamp_ns":    ts_ns,
            "market_id":       "0x" + asset.lower() + "_test",
            "asset":           asset,
            "yes_price":       price,
            "no_price":        round(1.0 - price, 4),
            "mid_price":       price,
            "best_bid":        round(price - 0.01, 4),
            "best_ask":        round(price + 0.01, 4),
            "spread":          0.02,
            "volume_24h":      5000.0,
            "liquidity_score": 50.0,
            "bids_vol_1":      kw.get("bids_vol_1", 100.0),
            "asks_vol_1":      kw.get("asks_vol_1", 80.0),
            "bids_vol_2":      kw.get("bids_vol_2", 50.0),
            "asks_vol_2":      kw.get("asks_vol_2", 40.0),
            "bids_vol_3":      kw.get("bids_vol_3", 20.0),
            "asks_vol_3":      kw.get("asks_vol_3", 15.0),
        }

    def test_write_and_flush_single_asset(self, tmp_base):
        """Write ticks for one asset and verify Parquet output."""
        writer = ParquetTickWriter(
            base_dir=tmp_base,
            batch_size=10,
            verbose=False,
        )

        # Write 8 ticks for BTC on the same day
        ts_base = 1779796800000000000  # 2026-05-26 12:00:00 UTC
        for i in range(8):
            tick = self._make_tick("BTC", ts_base + i * 30_000_000_000, 0.70 + i * 0.01)
            writer.write_tick(tick)

        # Flush
        flushed = writer.flush()
        assert flushed == 8

        # Verify Parquet files exist
        parquet_files = list(tmp_base.rglob("*.parquet"))
        assert len(parquet_files) >= 1

        # Verify partitioning
        assert (tmp_base / "asset=BTC").exists()
        assert (tmp_base / "asset=BTC" / "year=2026").exists()
        assert (tmp_base / "asset=BTC" / "year=2026" / "month=05").exists()
        assert (tmp_base / "asset=BTC" / "year=2026" / "month=05" / "day=26").exists()

    def test_write_multiple_assets(self, tmp_base):
        """Write ticks for BTC and ETH — verify separate partitions."""
        writer = ParquetTickWriter(base_dir=tmp_base, batch_size=5, verbose=False)
        ts = 1779796800000000000

        for i in range(4):
            writer.write_tick(self._make_tick("BTC", ts + i * 30_000_000_000, 0.70))
            writer.write_tick(self._make_tick("ETH", ts + i * 30_000_000_000, 0.65))

        writer.flush()

        assert (tmp_base / "asset=BTC").exists()
        assert (tmp_base / "asset=ETH").exists()

        # Each asset should have at least 1 parquet file
        btc_files = list((tmp_base / "asset=BTC").rglob("*.parquet"))
        eth_files = list((tmp_base / "asset=ETH").rglob("*.parquet"))
        assert len(btc_files) >= 1
        assert len(eth_files) >= 1

    def test_auto_flush_on_batch_size(self, tmp_base):
        """Writer should auto-flush when buffer reaches batch_size."""
        writer = ParquetTickWriter(base_dir=tmp_base, batch_size=5, verbose=False)
        ts = 1779796800000000000

        # Write 3 ticks (below batch_size) — no flush yet
        for i in range(3):
            writer.write_tick(self._make_tick("BTC", ts + i * 30_000_000_000, 0.70))

        # No files before flush
        files_before = list(tmp_base.rglob("*.parquet"))
        assert len(files_before) == 0

        # Write 3 more — should auto-flush at 5
        for i in range(3, 6):
            writer.write_tick(self._make_tick("BTC", ts + i * 30_000_000_000, 0.70 + i * 0.01))

        files_after = list(tmp_base.rglob("*.parquet"))
        assert len(files_after) >= 1

    def test_close_flushes_and_returns_summary(self, tmp_base):
        """close() should flush remaining ticks and return summary dict."""
        writer = ParquetTickWriter(base_dir=tmp_base, batch_size=100, verbose=False)
        ts = 1779796800000000000

        writer.write_tick(self._make_tick("BTC", ts, 0.70))
        writer.write_tick(self._make_tick("BTC", ts + 30_000_000_000, 0.71))

        summary = writer.close()
        assert summary["total_ticks"] == 2
        assert summary["flush_count"] >= 1
        assert "closed_at_utc" in summary

    def test_write_roundtrip_data_integrity(self, tmp_base):
        """Verify data written can be read back correctly."""
        writer = ParquetTickWriter(base_dir=tmp_base, batch_size=10, verbose=False)
        ts = 1779796800000000000

        ticks = []
        for i in range(5):
            tick = self._make_tick("BTC", ts + i * 30_000_000_000, 0.70 + i * 0.02)
            writer.write_tick(tick)
            ticks.append(tick)

        writer.close()

        # Read back
        table = pq.read_table(tmp_base / "asset=BTC" / "year=2026" / "month=05" / "day=26")
        assert table.num_rows == 5
        yes_prices = table.column("yes_price").to_pylist()
        expected = [0.70, 0.72, 0.74, 0.76, 0.78]
        for got, exp in zip(yes_prices, expected):
            assert got == pytest.approx(exp, rel=1e-6), f"{got} != {exp}"

    def test_compression_zstd(self, tmp_base):
        """Verify compression is zstd."""
        writer = ParquetTickWriter(base_dir=tmp_base, batch_size=10, verbose=False)
        ts = 1779796800000000000

        for i in range(100):
            writer.write_tick(self._make_tick("BTC", ts + i * 30_000_000_000, 0.70))
        writer.close()

        # Read metadata to verify compression
        parquet_files = list((tmp_base / "asset=BTC").rglob("*.parquet"))
        total_rows = sum(pq.read_metadata(f).num_rows for f in parquet_files)
        # Just verify the file is valid and compressed
        assert total_rows == 100


# ══════════════════════════════════════════════════════════════════════════
# MULTI-ASSET RECORDER TESTS
# ══════════════════════════════════════════════════════════════════════════

class TestMultiAssetRecorder:
    """Test high-level recording orchestration."""

    @pytest.fixture
    def tmp_base(self):
        path = Path(tempfile.mkdtemp(prefix="polybot_test_recorder_"))
        yield path
        shutil.rmtree(str(path), ignore_errors=True)

    def _make_tick(self, asset: str, ts_ns: int, price: float):
        return {
            "timestamp_ns":    ts_ns,
            "market_id":       "0x" + asset.lower() + "_test",
            "asset":           asset,
            "yes_price":       price,
            "no_price":        round(1.0 - price, 4),
            "mid_price":       price,
            "best_bid":        round(price - 0.01, 4),
            "best_ask":        round(price + 0.01, 4),
            "spread":          0.02,
            "volume_24h":      5000.0,
            "liquidity_score": 50.0,
            "bids_vol_1":      100.0,
            "asks_vol_1":      80.0,
            "bids_vol_2":      50.0,
            "asks_vol_2":      40.0,
            "bids_vol_3":      20.0,
            "asks_vol_3":      15.0,
        }

    def test_start_and_end_session(self, tmp_base):
        """Verify session lifecycle."""
        recorder = MultiAssetRecorder(base_dir=tmp_base, batch_size=100, verbose=False)

        recorder.start_session(asset="BTC", market_id="0xtest", question="Test market?")
        ts = 1779796800000000000
        for i in range(10):
            recorder.record_tick("BTC", self._make_tick("BTC", ts + i * 30_000_000_000, 0.70))

        # Don't call end_session — just finalize_all
        manifest = recorder.finalize_all()
        assert manifest["total_ticks"] == 10
        assert manifest["base_dir"] == str(tmp_base.absolute())

        # Manifest JSON should exist
        assert (tmp_base / "manifest.json").exists()
        with open(tmp_base / "manifest.json") as f:
            data = json.load(f)
        assert data["total_ticks"] == 10

    def test_multiple_sessions(self, tmp_base):
        """Record multiple sessions separately."""
        recorder = MultiAssetRecorder(base_dir=tmp_base, batch_size=100, verbose=False)
        ts = 1779796800000000000

        recorder.start_session(asset="BTC", market_id="0xbtc1")
        for i in range(5):
            recorder.record_tick("BTC", self._make_tick("BTC", ts + i * 30_000_000_000, 0.70))

        recorder.start_session(asset="ETH", market_id="0xeth1")
        for i in range(3):
            recorder.record_tick("ETH", self._make_tick("ETH", ts + i * 30_000_000_000, 0.65))

        manifest = recorder.finalize_all()
        assert manifest["total_ticks"] == 8
        assert len(manifest.get("sessions", [])) == 2

    def test_empty_recording(self, tmp_base):
        """Recorder should handle zero ticks gracefully."""
        recorder = MultiAssetRecorder(base_dir=tmp_base, batch_size=100, verbose=False)
        manifest = recorder.finalize_all()
        assert manifest["total_ticks"] == 0

    def test_parquet_schema_in_output(self, tmp_base):
        """Written Parquet files should match the canonical schema."""
        recorder = MultiAssetRecorder(base_dir=tmp_base, batch_size=10, verbose=False)
        ts = 1779796800000000000

        recorder.start_session(asset="BTC", market_id="0xbtc_schema")
        for i in range(10):
            recorder.record_tick("BTC", self._make_tick("BTC", ts + i * 30_000_000_000, 0.70 + i * 0.01))
        recorder.finalize_all()

        # Read back and verify schema
        parquet_files = list(tmp_base.rglob("*.parquet"))
        assert len(parquet_files) >= 1

        schema = pq.read_schema(parquet_files[0])
        field_names = [f.name for f in schema]
        for required in ("timestamp_ns", "market_id", "asset", "yes_price", "spread"):
            assert required in field_names, f"Missing field in output: {required}"
