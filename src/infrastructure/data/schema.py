# src/infrastructure/data/schema.py

"""
Parquet schema definitions for market data recording.

Defines columnar schemas optimized for:
- Time-series replay (sorted by timestamp)
- ML feature engineering (all numeric fields)
- Compression ratio (zstd)
- Zero-copy reads via PyArrow

All schemas use physical types that map directly to PyArrow types.
"""

import pyarrow as pa

# ── Field Definitions ────────────────────────────────────────────────────────
# Every field name, type, and documentation for the canonical tick schema.
# This schema evolves by ADDING columns only — never removing or reordering.

TICK_FIELDS: list[pa.Field] = [
    # ── Identity ──────────────────────────────────────────────────────────
    pa.field("timestamp_ns",    pa.int64(),    nullable=False),
    pa.field("market_id",       pa.utf8(),     nullable=False),
    pa.field("asset",           pa.utf8(),     nullable=False),

    # ── Prices ────────────────────────────────────────────────────────────
    pa.field("yes_price",       pa.float64(),  nullable=False),
    pa.field("no_price",        pa.float64(),  nullable=False),
    pa.field("mid_price",       pa.float64(),  nullable=False),
    pa.field("best_bid",        pa.float64(),  nullable=False),
    pa.field("best_ask",        pa.float64(),  nullable=False),
    pa.field("spread",          pa.float64(),  nullable=False),

    # ── Liquidity / Volume ────────────────────────────────────────────────
    pa.field("volume_24h",      pa.float64(),  nullable=False),
    pa.field("liquidity_score", pa.float64(),  nullable=True),

    # ── Orderbook Depth ───────────────────────────────────────────────────
    pa.field("bids_vol_1",      pa.float64(),  nullable=True),
    pa.field("asks_vol_1",      pa.float64(),  nullable=True),
    pa.field("bids_vol_2",      pa.float64(),  nullable=True),
    pa.field("asks_vol_2",      pa.float64(),  nullable=True),
    pa.field("bids_vol_3",      pa.float64(),  nullable=True),
    pa.field("asks_vol_3",      pa.float64(),  nullable=True),
]

TICK_SCHEMA: pa.Schema = pa.schema(
    TICK_FIELDS,
    metadata={
        b"polybot_schema_version": b"1.0",
        b"polybot_schema_type":    b"tick",
        b"description":            b"Canonical market tick schema for Polybot",
    },
)

# ── Additional Schemas (future) ──────────────────────────────────────────────

TRADE_FIELDS: list[pa.Field] = [
    pa.field("timestamp_ns",  pa.int64(),    nullable=False),
    pa.field("trade_id",      pa.utf8(),     nullable=False),
    pa.field("market_id",     pa.utf8(),     nullable=False),
    pa.field("asset",         pa.utf8(),     nullable=False),
    pa.field("side",          pa.utf8(),     nullable=False),  # YES / NO
    pa.field("price",         pa.float64(),  nullable=False),
    pa.field("size",          pa.float64(),  nullable=False),
    pa.field("value_usdc",    pa.float64(),  nullable=False),
    pa.field("maker",         pa.bool_(),    nullable=True),
]

TRADE_SCHEMA: pa.Schema = pa.schema(
    TRADE_FIELDS,
    metadata={
        b"polybot_schema_version": b"1.0",
        b"polybot_schema_type":    b"trade",
    },
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def datetime_to_ns(dt) -> int:
    """Convert a datetime (aware or naive UTC) to nanoseconds since epoch."""
    import calendar
    from datetime import timezone

    if dt.tzinfo is None:
        # Treat naive as UTC
        return int(calendar.timegm(dt.timetuple()) * 1_000_000_000 + dt.microsecond * 1000)
    else:
        return int(dt.timestamp() * 1_000_000_000)


def tick_to_record_batch(
    ticks: list[dict],
    schema: pa.Schema = TICK_SCHEMA,
) -> pa.RecordBatch:
    """
    Convert a list of tick dicts into a PyArrow RecordBatch.

    Each tick dict should contain:
        timestamp_ns, market_id, asset, yes_price, no_price, mid_price,
        best_bid, best_ask, spread, volume_24h, liquidity_score,
        bids_vol_1, asks_vol_1, bids_vol_2, asks_vol_2, bids_vol_3, asks_vol_3

    Missing optional fields default to None (= null in Parquet).
    """
    arrays = []
    for field in schema:
        values = [t.get(field.name) for t in ticks]
        arrays.append(pa.array(values, type=field.type))

    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def tick_to_record_batch_from_market_ticks(
    ticks: list,
    market_id: str,
    asset: str,
    schema: pa.Schema = TICK_SCHEMA,
) -> pa.RecordBatch:
    """
    Convert a list of domain MarketTick objects into a PyArrow RecordBatch.

    Automatically computes mid_price and liquidity_score.
    """
    rows = []
    for tick in ticks:
        mid_price = (tick.best_bid + tick.best_ask) / 2.0
        liq_score = None
        if tick.volume_24h > 0 and tick.spread > 0:
            liq_score = round(tick.volume_24h / (1.0 + tick.spread * 100), 2)

        rows.append({
            "timestamp_ns":    datetime_to_ns(tick.timestamp),
            "market_id":       market_id,
            "asset":           asset,
            "yes_price":       tick.yes_price,
            "no_price":        tick.no_price,
            "mid_price":       mid_price,
            "best_bid":        tick.best_bid,
            "best_ask":        tick.best_ask,
            "spread":          tick.spread,
            "volume_24h":      tick.volume_24h,
            "liquidity_score": liq_score,
            "bids_vol_1":      None,
            "asks_vol_1":      None,
            "bids_vol_2":      None,
            "asks_vol_2":      None,
            "bids_vol_3":      None,
            "asks_vol_3":      None,
        })

    return tick_to_record_batch(rows, schema)


def record_batch_to_tick_dicts(batch: pa.RecordBatch) -> list[dict]:
    """Convert a RecordBatch back to a list of dicts (for CSV export)."""
    import pyarrow.compute as pc

    rows = []
    for i in range(batch.num_rows):
        row = {}
        for field in batch.schema:
            val = batch.column(field.name)[i].as_py()
            if val is not None:
                row[field.name] = val
        rows.append(row)
    return rows


def read_ticks_uniform(
    paths: list[str],
    schema: pa.Schema = TICK_SCHEMA,
) -> pa.Table:
    """
    Read multiple Parquet tick files with schema normalisation.

    PyArrow may encode low-cardinality string columns (asset, market_id) as
    dictionary<string> in some files but plain string in others, causing
    "incompatible types" errors when reading them together.

    This reader opens each file individually via ParquetFile (avoiding
    dataset auto-discovery that triggers schema merge), casts every column
    to the canonical type, and concatenates into a single uniform table.

    Extra columns not in the canonical schema are silently dropped.

    Args:
        paths: List of .parquet file paths.
        schema: Target canonical schema (default TICK_SCHEMA).

    Returns:
        A single PyArrow Table with all ticks, sorted by timestamp_ns.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    tables: list[pa.Table] = []

    for path in paths:
        # Use ParquetFile to read ONE file without dataset discovery
        pf = pq.ParquetFile(path)
        raw = pf.read()

        # Rebuild table strictly from canonical schema: every column gets
        # cast to the canonical type, and missing columns become nulls.
        cols: dict[str, pa.ChunkedArray] = {}
        for field in schema:
            if field.name in raw.column_names:
                col = raw.column(field.name)
                # pc.cast always allocates new buffers ⇒ guarantees target type
                cols[field.name] = pc.cast(col, field.type)
            else:
                cols[field.name] = pa.nulls(raw.num_rows, type=field.type)

        tables.append(pa.table(cols))

    if not tables:
        return pa.table({})

    result = pa.concat_tables(tables)

    # Sort by timestamp_ns if the column exists
    if "timestamp_ns" in result.column_names:
        indices = pc.sort_indices(result, sort_keys=[("timestamp_ns", "ascending")])
        result = result.take(indices)

    return result
