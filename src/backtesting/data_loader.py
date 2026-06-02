# src/backtesting/data_loader.py

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from src.domain.value_objects.market_tick import MarketTick

logger = structlog.get_logger(__name__)


@dataclass
class HistoricalDataset:
    """
    Conjunto de ticks históricos para un mercado.
    Contiene todos los ticks en orden cronológico.
    """
    asset:     str              # BTC o ETH
    window:    str              # 5m o 15m
    market_id: str              # ID sintético para backtesting
    ticks:     list[MarketTick]
    start_at:  datetime
    end_at:    datetime

    @property
    def duration_hours(self) -> float:
        """Duración total del dataset en horas."""
        delta = self.end_at - self.start_at
        return delta.total_seconds() / 3600

    @property
    def tick_count(self) -> int:
        return len(self.ticks)


class DataLoader:
    """
    Carga datos históricos desde CSV o JSON.

    Formato CSV esperado (una fila por tick):
    timestamp,yes_price,no_price,best_bid,best_ask,spread,volume_24h
    2024-01-01T00:00:00,0.76,0.24,0.755,0.765,0.010,5000.0
    2024-01-01T00:00:30,0.77,0.23,0.765,0.775,0.010,5100.0

    Formato JSON esperado:
    [
      {
        "timestamp": "2024-01-01T00:00:00",
        "yes_price": 0.76,
        "no_price": 0.24,
        "best_bid": 0.755,
        "best_ask": 0.765,
        "spread": 0.010,
        "volume_24h": 5000.0
      }
    ]
    """

    @staticmethod
    def from_csv(
        path:      str | Path,
        asset:     str,
        window:    str,
        market_id: str | None = None,
    ) -> HistoricalDataset:
        """
        Carga ticks desde un archivo CSV.
        Valida que los datos sean coherentes antes de retornar.
        """
        path      = Path(path)
        market_id = market_id or f"backtest_{asset}_{window}"
        ticks     = []

        logger.info(
            "loading_csv_data",
            path=str(path),
            asset=asset,
            window=window,
        )

        if not path.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {path}")

        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                try:
                    tick = DataLoader._row_to_tick(row, market_id)
                    ticks.append(tick)
                except (KeyError, ValueError) as e:
                    logger.warning(
                        "csv_row_parse_error",
                        row=i + 2,   # +2 porque header es fila 1
                        error=str(e),
                    )
                    continue

        if not ticks:
            raise ValueError(f"No se pudieron cargar ticks desde {path}")

        # Ordena por timestamp (garantía)
        ticks.sort(key=lambda t: t.timestamp)

        logger.info(
            "csv_data_loaded",
            tick_count=len(ticks),
            start=ticks[0].timestamp.isoformat(),
            end=ticks[-1].timestamp.isoformat(),
        )

        return HistoricalDataset(
            asset=asset,
            window=window,
            market_id=market_id,
            ticks=ticks,
            start_at=ticks[0].timestamp,
            end_at=ticks[-1].timestamp,
        )

    @staticmethod
    def from_json(
        path:      str | Path,
        asset:     str,
        window:    str,
        market_id: str | None = None,
    ) -> HistoricalDataset:
        """Carga ticks desde un archivo JSON."""
        path      = Path(path)
        market_id = market_id or f"backtest_{asset}_{window}"

        if not path.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {path}")

        with open(path, "r") as f:
            raw_list = json.load(f)

        ticks = []
        for i, raw in enumerate(raw_list):
            try:
                tick = MarketTick(
                    market_id  = market_id,
                    yes_price  = float(raw["yes_price"]),
                    no_price   = float(raw.get("no_price", 1.0 - raw["yes_price"])),
                    best_bid   = float(raw.get("best_bid", raw["yes_price"] - 0.005)),
                    best_ask   = float(raw.get("best_ask", raw["yes_price"] + 0.005)),
                    spread     = float(raw.get("spread", 0.010)),
                    volume_24h = float(raw.get("volume_24h", 1000.0)),
                    timestamp  = datetime.fromisoformat(raw["timestamp"]),
                )
                ticks.append(tick)
            except (KeyError, ValueError) as e:
                logger.warning("json_item_parse_error", index=i, error=str(e))

        if not ticks:
            raise ValueError(f"No se pudieron cargar ticks desde {path}")

        ticks.sort(key=lambda t: t.timestamp)

        return HistoricalDataset(
            asset=asset,
            window=window,
            market_id=market_id,
            ticks=ticks,
            start_at=ticks[0].timestamp,
            end_at=ticks[-1].timestamp,
        )

    @staticmethod
    def generate_synthetic(
        asset:              str,
        window:             str,
        n_ticks:            int   = 2000,
        start_price:        float = 0.70,
        volatility:         float = 0.02,
        trend:              float = 0.0001,
        reversion_strength: float = 0.002,  # Antes hardcodeado a 0.01 — ahora configurable
        reversion_center:   float = 0.75,   # Centro de atracción de la reversión
        start_datetime:     datetime | None = None,
        interval_secs:      int   = 30,
    ) -> HistoricalDataset:
        """
        Genera datos sintéticos con regime-switching + latent fair value.

        Usa un modelo de dos regímenes:
        - Consolidación (70-80%): baja volatilidad, reversión fuerte al fair_value
        - Tendencia (20-30%): movimientos direccionales de 0.15-0.40 de magnitud

        El precio revierte al fair_value actual (no a un centro fijo),
        permitiendo tendencias sostenidas y breakouts que la estrategia
        BAT puede explotar.
        """
        import random
        from datetime import timedelta

        market_id  = f"synthetic_{asset}_{window}"
        start      = start_datetime or datetime(2024, 1, 1, 0, 0, 0)
        ticks      = []

        # ── Regime state machine ─────────────────────────────────────
        regime: str = "consolidation"
        regime_ticks: int = random.randint(200, 500)
        fair_value: float = start_price
        trend_velocity: float = 0.0
        latent_prob: float = fair_value

        # ── Expiry drift in last 20% ─────────────────────────────────
        expiry_resolves_yes: bool = random.random() < 0.50
        expiry_target: float = 0.95 if expiry_resolves_yes else 0.05
        expiry_start_tick: int = int(n_ticks * 0.80)

        for i in range(n_ticks):
            regime_ticks -= 1

            # Regime transition
            if regime_ticks <= 0:
                if regime == "consolidation":
                    regime = "trend"
                    regime_ticks = random.randint(50, 150)
                    direction = 1 if random.random() < 0.50 else -1
                    magnitude = random.uniform(0.15, 0.40)
                    trend_velocity = (direction * magnitude) / regime_ticks
                    # 30% chance of continuation trend
                    if random.random() < 0.30:
                        regime_ticks += random.randint(30, 80)
                else:
                    regime = "consolidation"
                    regime_ticks = random.randint(200, 500)
                    trend_velocity = 0.0

            # Regime dynamics
            if regime == "trend":
                fair_value += trend_velocity
                fair_value = max(0.05, min(0.95, fair_value))
                noise = random.gauss(0, 0.0025)  # Lower noise for trend tracking
            else:
                noise = random.gauss(0, 0.0015)

            # Expiry effect (last 20%)
            if i >= expiry_start_tick:
                fair_value += (expiry_target - fair_value) * 0.008

            # Price evolution: mean-revert to current fair_value
            latent_prob += (fair_value - latent_prob) * 0.25 + noise
            latent_prob = max(0.02, min(0.98, latent_prob))

            yes_price = latent_prob + random.gauss(0, 0.003)
            yes_price = max(0.01, min(0.99, yes_price))
            no_price  = 1.0 - yes_price

            uncertainty = 1.0 - abs(yes_price - 0.5) * 1.5
            spread    = random.uniform(0.005, 0.025) * (0.4 + uncertainty * 0.6)
            best_bid  = yes_price - spread / 2
            best_ask  = yes_price + spread / 2
            volume    = random.uniform(500, 10000)
            timestamp = start + timedelta(seconds=i * interval_secs)

            ticks.append(MarketTick(
                market_id  = market_id,
                yes_price  = round(yes_price, 4),
                no_price   = round(no_price, 4),
                best_bid   = round(best_bid, 4),
                best_ask   = round(best_ask, 4),
                spread     = round(spread, 4),
                volume_24h = round(volume, 2),
                timestamp  = timestamp,
            ))

        logger.info(
            "synthetic_data_generated",
            asset=asset,
            window=window,
            n_ticks=n_ticks,
            start_price=start_price,
        )

        return HistoricalDataset(
            asset=asset,
            window=window,
            market_id=market_id,
            ticks=ticks,
            start_at=ticks[0].timestamp,
            end_at=ticks[-1].timestamp,
        )

    @staticmethod
    def from_polymarket_csv(
        path: str | Path,
        asset: str,
        window: str,
        market_id: str | None = None,
    ) -> HistoricalDataset:
        """
        Load ticks from a CSV file generated by:
        - scripts/download_historical_data.py (Polymarket /prices-history)
        - scripts/record_live_data.py (WebSocket live recorder)

        Columns expected:
        timestamp,yes_price,no_price,best_bid,best_ask,spread,volume_24h
        """
        path = Path(path)
        market_id = market_id or f"pm_{asset}_{window}"
        ticks = []

        logger.info("loading_polymarket_csv", path=str(path))

        if not path.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {path}")

        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                try:
                    yes_price = float(row["yes_price"])
                    tick = MarketTick(
                        market_id=market_id,
                        yes_price=yes_price,
                        no_price=float(row.get("no_price", 1.0 - yes_price)),
                        best_bid=float(row.get("best_bid", yes_price - 0.005)),
                        best_ask=float(row.get("best_ask", yes_price + 0.005)),
                        spread=float(row.get("spread", 0.010)),
                        volume_24h=float(row.get("volume_24h", 1000.0)),
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                    )
                    ticks.append(tick)
                except (KeyError, ValueError) as e:
                    logger.warning("polymarket_csv_parse_error", row=i + 2, error=str(e))
                    continue

        if not ticks:
            raise ValueError(f"No se pudieron cargar ticks desde {path}")

        ticks.sort(key=lambda t: t.timestamp)

        logger.info("polymarket_csv_loaded", tick_count=len(ticks),
                     start=ticks[0].timestamp.isoformat(),
                     end=ticks[-1].timestamp.isoformat())

        return HistoricalDataset(
            asset=asset,
            window=window,
            market_id=market_id,
            ticks=ticks,
            start_at=ticks[0].timestamp,
            end_at=ticks[-1].timestamp,
        )

    @staticmethod
    def from_parquet(
        base_dir: str | Path = "data/parquet",
        asset: str | None = None,
        window: str = "5m",
        market_id: str | None = None,
    ) -> HistoricalDataset | list[HistoricalDataset]:
        """
        Load ticks from Parquet files (P8.2). Delegates to ParquetDataLoader.

        Args:
            base_dir: Parquet data directory.
            asset: "BTC", "ETH", or None to load all assets.
            window: Dataset window label.
            market_id: Specific market to load. If None, loads all markets.

        Returns:
            A single HistoricalDataset if market_id is specified,
            or a list of HistoricalDatasets (one per market) otherwise.
        """
        from src.backtesting.parquet_loader import ParquetDataLoader

        loader = ParquetDataLoader(base_dir=base_dir)

        if asset is None:
            # Load all assets
            all_datasets = []
            for a in ("BTC", "ETH"):
                try:
                    if market_id:
                        ds = loader.load(asset=a, market_id=market_id, window=window)
                        all_datasets.append(ds)
                    else:
                        all_datasets.extend(loader.load_all(asset=a, window=window))
                except FileNotFoundError:
                    logger.info("parquet_no_data_for_asset", asset=a)
                    continue
            if not all_datasets:
                raise FileNotFoundError(
                    f"No Parquet data found for any asset in {base_dir}"
                )
            return all_datasets

        if market_id:
            return loader.load(asset=asset, market_id=market_id, window=window)
        return loader.load_all(asset=asset, window=window)

    @staticmethod
    def _row_to_tick(row: dict, market_id: str) -> MarketTick:
        """Convierte una fila del CSV a un MarketTick."""
        yes_price = float(row["yes_price"])
        return MarketTick(
            market_id  = market_id,
            yes_price  = yes_price,
            no_price   = float(row.get("no_price",  1.0 - yes_price)),
            best_bid   = float(row.get("best_bid",  yes_price - 0.005)),
            best_ask   = float(row.get("best_ask",  yes_price + 0.005)),
            spread     = float(row.get("spread",    0.010)),
            volume_24h = float(row.get("volume_24h", 1000.0)),
            timestamp  = datetime.fromisoformat(row["timestamp"]),
        )
