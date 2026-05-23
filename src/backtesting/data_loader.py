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
        Genera datos sintéticos para testing cuando no hay datos reales.
        Usa un random walk con tendencia y reversión a la media configurable.

        Parámetros de realismo:
        - reversion_strength: 0.002 = reversión suave (realista)
          0.01 = reversión fuerte (original, poco realista)
        - reversion_center: centro de atracción (default 0.75)
        """
        import random
        from datetime import timedelta

        market_id  = f"synthetic_{asset}_{window}"
        start      = start_datetime or datetime(2024, 1, 1, 0, 0, 0)
        ticks      = []
        yes_price  = start_price

        for i in range(n_ticks):
            # Random walk con tendencia y reversión a la media configurable
            shock     = random.gauss(0, volatility)
            reversion = (reversion_center - yes_price) * reversion_strength
            yes_price = max(0.01, min(0.99,
                yes_price + trend + shock + reversion
            ))

            no_price  = 1.0 - yes_price
            spread    = random.uniform(0.005, 0.025)
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
