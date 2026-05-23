# src/backtesting/reporter.py

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import structlog

from src.backtesting.engine import BacktestResult
from src.backtesting.metrics import BacktestMetrics

logger = structlog.get_logger(__name__)

RESULTS_DIR = Path("src/backtesting/results")


class BacktestReporter:
    """
    Exporta resultados de backtesting a JSON y CSV.
    Crea la carpeta de resultados si no existe.
    """

    def __init__(self, results_dir: Path | None = None):
        self._dir = results_dir or RESULTS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        result:    BacktestResult,
        prefix:    str | None = None,
    ) -> dict[str, Path]:
        """
        Guarda el resultado completo en JSON y las posiciones en CSV.
        Retorna las rutas de los archivos creados.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        prefix    = prefix or f"{result.asset}_{result.window}"
        stem      = f"{prefix}_{timestamp}"

        metrics   = BacktestMetrics(result).compute_all()

        json_path = self._save_json(result, metrics, stem)
        csv_path  = self._save_csv(result, stem)

        logger.info(
            "backtest_results_saved",
            json=str(json_path),
            csv=str(csv_path),
        )

        return {"json": json_path, "csv": csv_path}

    def save_sweep(
        self,
        comparisons: list[dict],
        prefix:      str = "sweep",
    ) -> Path:
        """
        Guarda los resultados de un parameter sweep en CSV.
        Ordenados por Sharpe ratio.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path      = self._dir / f"{prefix}_{timestamp}.csv"

        if not comparisons:
            logger.warning("sweep_empty_results")
            return path

        fieldnames = list(comparisons[0].keys())

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(comparisons)

        logger.info("sweep_results_saved", path=str(path), rows=len(comparisons))
        return path

    def print_summary(self, result: BacktestResult) -> None:
        """Imprime un resumen legible en consola."""
        metrics = BacktestMetrics(result).compute_all()
        pnl     = metrics["pnl"]
        perf    = metrics["performance"]
        risk    = metrics["risk"]
        summary = metrics["summary"]

        print("\n" + "═" * 60)
        print(f"  BACKTEST RESULTS — {result.asset} {result.window}")
        print("═" * 60)
        print(f"  Dataset:        {summary['dataset_ticks']} ticks "
              f"({summary['dataset_hours']:.1f}h)")
        print(f"  Periodo:        {summary['dataset_start'][:10]} → "
              f"{summary['dataset_end'][:10]}")
        print()
        print(f"  Balance inicial: ${result.initial_balance:.2f} USDC")
        print(f"  Balance final:   ${result.final_balance:.2f} USDC")
        print(f"  PnL Total:       {pnl['total_pnl_usdc']:+.4f} USDC "
              f"({pnl['total_pnl_pct']:+.2%})")
        print()
        print(f"  Trades totales:  {summary['closed_positions']}")
        print(f"  Win Rate:        {perf['win_rate']:.1%} "
              f"({perf['winners']}W / {perf['losers']}L)")
        print(f"  Profit Factor:   {perf['profit_factor']:.2f}")
        print()
        print(f"  Sharpe Ratio:    {risk['sharpe_ratio']:.3f}")
        print(f"  Sortino Ratio:   {risk['sortino_ratio']:.3f}")
        print(f"  Max Drawdown:    {risk['max_drawdown_pct']:.2%} "
              f"(${risk['max_drawdown_usdc']:.2f})")
        print(f"  Calmar Ratio:    {risk['calmar_ratio']:.3f}")
        print()
        print("  Config usada:")
        cfg = metrics["config"]
        print(f"    threshold={cfg['threshold']} | "
              f"stop_loss={cfg['stop_loss_pct']:.0%} | "
              f"target={cfg['target_price']} | "
              f"ticks={cfg['required_ticks']}")
        print()
        print("  Exit reasons:")
        for reason, count in metrics["duration"]["exit_reasons"].items():
            pct = count / summary["closed_positions"] if summary["closed_positions"] else 0
            print(f"    {reason:<25} {count:>4} ({pct:.1%})")
        print("═" * 60 + "\n")

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _save_json(
        self,
        result:  BacktestResult,
        metrics: dict,
        stem:    str,
    ) -> Path:
        """Guarda métricas completas en JSON."""
        path = self._dir / f"{stem}_metrics.json"

        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics":      metrics,
        }

        with open(path, "w") as f:
            json.dump(output, f, indent=2, default=str)

        return path

    def _save_csv(self, result: BacktestResult, stem: str) -> Path:
        """Guarda cada posición como fila en CSV."""
        path = self._dir / f"{stem}_trades.csv"

        fieldnames = [
            "market_id", "side", "amount", "shares",
            "entry_price", "exit_price", "pnl", "pnl_pct",
            "entry_tick", "exit_tick", "duration_ticks",
            "entry_at", "exit_at", "exit_reason",
        ]

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for pos in result.closed_positions:
                writer.writerow({
                    "market_id":      pos.market_id,
                    "side":           pos.side,
                    "amount":         round(pos.amount, 4),
                    "shares":         round(pos.shares, 4),
                    "entry_price":    round(pos.entry_price, 4),
                    "exit_price":     round(pos.exit_price or 0, 4),
                    "pnl":            round(pos.pnl or 0, 4),
                    "pnl_pct":        round(pos.pnl_pct or 0, 4),
                    "entry_tick":     pos.entry_tick,
                    "exit_tick":      pos.exit_tick,
                    "duration_ticks": pos.duration_ticks,
                    "entry_at":       pos.entry_at.isoformat(),
                    "exit_at":        pos.exit_at.isoformat() if pos.exit_at else "",
                    "exit_reason":    pos.exit_reason or "",
                })

        return path
