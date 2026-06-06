# src/infrastructure/data/storage.py

"""
Buffered Parquet writer for high-frequency market tick data.

Architecture:
    TickRecorder (high-level)
        └─ ParquetTickWriter (buffered batch writer, one per partition)
              └─ pyarrow.parquet.write_to_dataset (partitioned Parquet)

Design decisions:
    - Buffered writes: accumulates ticks in memory, flushes in batches
      to avoid excessive small Parquet files.
    - Partitioned by asset/year/month/day: enables efficient time-range queries
      without full scans.
    - Compression: zstd (best compression ratio for financial floats).
    - Row group size: 65536 rows (~2MB per group for tick data).
    - File rotation: when a batch exceeds row_group_size, starts new file.

Usage:
    writer = ParquetTickWriter(base_dir="data/parquet")
    writer.write_tick({"timestamp_ns": ..., "yes_price": ..., ...})
    writer.flush()  # force flush
    writer.close()  # flush + finalize
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from src.infrastructure.data.schema import (
    TICK_SCHEMA,
    tick_to_record_batch,
)

logger = structlog.get_logger(__name__)

DEFAULT_BASE_DIR   = Path("data/parquet")
DEFAULT_BATCH_SIZE = 1000           # rows before auto-flush
DEFAULT_ROW_GROUP  = 65536          # rows per row group
COMPRESSION        = "zstd"         # best compression for float data
COMPRESSION_LEVEL  = 3              # zstd:1=fast, 3=balanced, 22=max
MAX_RECORD_BATCH_SIZE = 65536       # max rows per batch (Parquet row group size)


class ParquetTickWriter:
    """
    Buffered Parquet writer for market tick data.

    Accumulates ticks in memory and flushes them to partitioned Parquet
    files when the buffer reaches `batch_size` rows.

    Partitioning: base_dir / asset=ASSET / year=YYYY / month=MM / day=DD /

    Each partition directory contains .parquet files with zstd compression.
    """

    def __init__(
        self,
        base_dir:      str | Path = DEFAULT_BASE_DIR,
        batch_size:    int        = DEFAULT_BATCH_SIZE,
        row_group_size: int       = DEFAULT_ROW_GROUP,
        schema:        pa.Schema  = TICK_SCHEMA,
        verbose:       bool       = False,
    ):
        self._base_dir       = Path(base_dir)
        self._batch_size     = batch_size
        self._row_group_size = min(row_group_size, MAX_RECORD_BATCH_SIZE)
        self._schema         = schema
        self._verbose        = verbose

        # Internal buffer: asset → year → month → day → list[dict]
        self._buffer: dict[str, dict[int, dict[int, dict[int, list[dict]]]]] = {}
        self._total_written: int = 0
        self._flush_count: int = 0

        self._base_dir.mkdir(parents=True, exist_ok=True)
        logger.info("parquet_writer_initialized",
                     base_dir=str(self._base_dir),
                     batch_size=batch_size,
                     compression=COMPRESSION)

    # ── Public API ─────────────────────────────────────────────────────────

    def write_tick(self, tick: dict) -> None:
        """
        Record a single tick.

        The tick dict must contain at minimum:
            timestamp_ns, market_id, asset

        Optional fields (null by default):
            bids_vol_1..3, asks_vol_1..3, liquidity_score
        """
        asset = tick.get("asset", "UNKNOWN")
        ts_ns = tick.get("timestamp_ns", 0)

        # Convert to date partition keys
        dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        year  = dt.year
        month = dt.month
        day   = dt.day

        # Ensure nested dict path exists
        if asset not in self._buffer:
            self._buffer[asset] = {}
        if year not in self._buffer[asset]:
            self._buffer[asset][year] = {}
        if month not in self._buffer[asset][year]:
            self._buffer[asset][year][month] = {}
        if day not in self._buffer[asset][year][month]:
            self._buffer[asset][year][month][day] = []

        self._buffer[asset][year][month][day].append(tick)

        # Auto-flush if buffer exceeds batch_size
        total_buffered = sum(
            len(ticks)
            for asset_data in self._buffer.values()
            for year_data in asset_data.values()
            for month_data in year_data.values()
            for ticks in month_data.values()
        )
        if total_buffered >= self._batch_size:
            self.flush()

    def write_ticks(self, ticks: list[dict]) -> None:
        """Write multiple ticks at once (more efficient)."""
        for tick in ticks:
            self.write_tick(tick)

    def flush(self) -> int:
        """
        Flush all buffered ticks to Parquet files.

        Returns number of ticks flushed.
        """
        if not self._buffer:
            return 0

        total = 0
        for asset, year_data in self._buffer.items():
            for year, month_data in year_data.items():
                for month, day_data in month_data.items():
                    for day, ticks in day_data.items():
                        if not ticks:
                            continue
                        n = len(ticks)
                        self._flush_partition(asset, year, month, day, ticks)
                        total += n

        # Clear buffer
        self._buffer.clear()
        self._flush_count += 1

        if self._verbose and total > 0:
            logger.info("parquet_flush", ticks=total, flush_count=self._flush_count)

        return total

    def close(self) -> dict:
        """
        Flush remaining ticks and return summary stats.

        After close(), the writer cannot be reused.
        """
        final_flush = self.flush()
        summary = {
            "total_ticks":   self._total_written,
            "flush_count":   self._flush_count,
            "base_dir":      str(self._base_dir.absolute()),
            "closed_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("parquet_writer_closed", **summary)
        return summary

    # ── Internal ───────────────────────────────────────────────────────────

    def _flush_partition(
        self,
        asset: str,
        year: int,
        month: int,
        day: int,
        ticks: list[dict],
    ) -> None:
        """Flush ticks for a single partition (asset/year/month/day)."""
        if not ticks:
            return

        # Convert to RecordBatch
        batch = tick_to_record_batch(ticks, self._schema)
        n_rows = batch.num_rows

        # Build partition path: base_dir / asset=ASSET / year=YYYY / month=MM / day=DD
        partition_path = (
            self._base_dir
            / f"asset={asset}"
            / f"year={year:04d}"
            / f"month={month:02d}"
            / f"day={day:02d}"
        )
        partition_path.mkdir(parents=True, exist_ok=True)

        # Generate unique filename with timestamp to avoid collisions
        ts_now = datetime.now(timezone.utc).strftime("%H%M%S_%f")
        filename = f"ticks_{ts_now}.parquet"
        filepath = partition_path / filename

        # Write single Parquet file with compression
        table = pa.Table.from_batches([batch])

        pq.write_table(
            table,
            filepath,
            row_group_size=self._row_group_size,
            compression=COMPRESSION,
            compression_level=COMPRESSION_LEVEL,
            data_page_size=1024 * 1024,  # 1MB data pages
            write_statistics=True,
            store_schema=True,
            use_dictionary=False,  # Prevent dict encoding on string columns
                                  # (avoids schema mismatch: string vs dictionary<string>)
        )

        self._total_written += n_rows

        if self._verbose:
            file_size = filepath.stat().st_size
            logger.debug("parquet_partition_written",
                         path=str(filepath),
                         rows=n_rows,
                         size_bytes=file_size,
                         asset=asset,
                         date=f"{year}-{month:02d}-{day:02d}")


class MultiAssetRecorder:
    """
    High-level recorder that manages multiple ParquetTickWriter instances
    and provides a simplified interface for the recording script.

    Usage:
        recorder = MultiAssetRecorder(base_dir="data/parquet")
        recorder.start_session(asset="BTC", market_id="0x...")
        recorder.record_tick(asset="BTC", tick_data={...})
        recorder.end_session()  # flushes and saves manifest
    """

    def __init__(
        self,
        base_dir: str | Path = DEFAULT_BASE_DIR,
        batch_size: int = DEFAULT_BATCH_SIZE,
        verbose: bool = False,
    ):
        self._writer = ParquetTickWriter(
            base_dir=base_dir,
            batch_size=batch_size,
            verbose=verbose,
        )
        self._verbose = verbose
        self._session_start: datetime | None = None
        self._sessions: list[dict] = []

    def start_session(self, asset: str, market_id: str, question: str = "") -> None:
        """Start a new recording session for a specific market.

        If a previous session was active (start_session called earlier
        without an intervening end_session), it is auto-finalised and
        appended to the sessions list.
        """
        now = datetime.now(timezone.utc)

        # Auto-close any previous session
        if hasattr(self, "_current_session") and self._current_session:
            self._current_session["ended_at"] = now.isoformat()
            self._sessions.append(dict(self._current_session))

        self._session_start = now
        self._current_session = {
            "asset":       asset,
            "market_id":   market_id,
            "question":    question[:120],
            "started_at":  now.isoformat(),
        }
        logger.info("recording_session_started", **self._current_session)

    def record_tick(self, asset: str, tick_data: dict) -> None:
        """Record a single tick. Ensures asset + timestamp_ns are populated."""
        self._writer.write_tick(tick_data)

    def end_session(self, ticks_recorded: int = 0) -> dict:
        """End the current session, flush data, and return summary."""
        if not hasattr(self, "_current_session") or not self._current_session:
            return {"error": "no_active_session"}

        self._current_session["ended_at"] = datetime.now(timezone.utc).isoformat()
        self._current_session["ticks"] = ticks_recorded
        self._sessions.append(self._current_session)

        summary = self._writer.close()
        session_summary = {**self._current_session, **summary}
        self._current_session = {}

        return session_summary

    def finalize_all(self) -> dict:
        """
        Flush all remaining data and save manifest.

        Call this at the end of recording.

        Returns manifest dict with all session metadata.
        """
        summary = self._writer.close()

        # Auto-close the last active session if one exists
        now = datetime.now(timezone.utc)
        if hasattr(self, "_current_session") and self._current_session:
            self._current_session["ended_at"] = now.isoformat()
            self._sessions.append(dict(self._current_session))
            self._current_session = {}

        manifest = {
            "recorded_at":  now.isoformat(),
            "total_ticks":  summary.get("total_ticks", 0),
            "base_dir":     str(self._writer._base_dir.absolute()),
            "sessions":     self._sessions,
        }

        # Save manifest
        manifest_path = self._writer._base_dir / "manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, default=str)

        logger.info("recording_finalized", **manifest)
        return manifest
