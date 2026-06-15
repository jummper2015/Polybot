#!/usr/bin/env python3
"""
backtest_real.py — Backtest MeanReversion con datos reales de data/parquet/.

Tarea R2.1 paso 4: validar que los parámetros de
data/optimization/optimal_params_mr_real.json cumplen los criterios
de éxito del checklist (Sharpe > 0.8, PF > 1.2, WR > 45%, MaxDD < 20%)
sobre cada dataset BTC/ETH × 5m/15m con datos Parquet reales.

A diferencia de scripts/validate_criteria.py (que usa
HistoricalDataset.generate_synthetic) y verify_criteria.py (que también
genera ticks sintéticos), este script SÓLO consume data/parquet/. Si un
dataset no tiene parquets disponibles, se reporta como N/A — nunca se
mete data sintética como fallback.

Reutiliza la lógica de scripts/optimize_mr.py:
- load_parquet_ticks(asset, window, parquet_dir)
- run_mr_backtest(ticks, **params) -> MRResult

Salida:
    data/reports/backtest_real_{timestamp}.json     # report completo
    data/reports/backtest_real_latest.json          # symlink al último
    stdout: tabla por dataset + status global

Exit codes:
    0 — todos los criterios pasan
    1 — uno o más criterios fallan
    2 — error de configuración o sin datos
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path so we can import scripts.optimize_mr.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.optimize_mr import load_parquet_ticks, run_mr_backtest

REPORTS_DIR = Path("data/reports")
DEFAULT_PARAMS_PATH = Path("data/optimization/optimal_params_mr_real.json")
DEFAULT_PARQUET_DIR = Path("data/parquet")

# Criterios oficiales R2.1 (skill pre-real-trading-checklist).
CRITERIA = {
    "sharpe_min":       0.8,
    "profit_factor_min": 1.2,
    "win_rate_min":     0.45,
    "max_drawdown_max": 0.20,
}

DATASETS = [
    ("BTC", "5m"),
    ("BTC", "15m"),
    ("ETH", "5m"),
    ("ETH", "15m"),
]


@dataclass
class DatasetReport:
    asset: str
    window: str
    ticks_loaded: int
    sharpe: float | None
    profit_factor: float | None
    win_rate: float | None
    max_drawdown: float | None
    total_trades: int
    total_pnl: float | None
    criteria_passed: bool
    criteria_detail: dict
    skip_reason: str | None = None


def _params_from_json(path: Path) -> dict:
    """Lee top_config del json de optimización MR."""
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró {path}. Generar primero con "
            f"`python scripts/optimize_mr.py --csv data/parquet/`."
        )
    with path.open() as f:
        data = json.load(f)
    cfg = data.get("top_config") or {}
    required = {
        "ma_window", "entry_zscore", "exit_zscore",
        "stop_loss_pct", "timeout_minutes", "position_size_pusd",
    }
    missing = required - set(cfg)
    if missing:
        raise KeyError(f"Faltan claves en top_config: {missing}")
    return cfg


def _eval_criteria(
    sharpe: float, pf: float, wr: float, max_dd: float,
) -> tuple[bool, dict]:
    """Compara métricas contra los 4 umbrales. Retorna (all_pass, detail)."""
    detail = {
        "sharpe":         {"value": sharpe, "min": CRITERIA["sharpe_min"],
                           "pass": sharpe >= CRITERIA["sharpe_min"]},
        "profit_factor":  {"value": pf,     "min": CRITERIA["profit_factor_min"],
                           "pass": pf >= CRITERIA["profit_factor_min"]},
        "win_rate":       {"value": wr,     "min": CRITERIA["win_rate_min"],
                           "pass": wr >= CRITERIA["win_rate_min"]},
        "max_drawdown":   {"value": max_dd, "max": CRITERIA["max_drawdown_max"],
                           "pass": max_dd <= CRITERIA["max_drawdown_max"]},
    }
    all_pass = all(c["pass"] for c in detail.values())
    return all_pass, detail


def _run_dataset(
    asset: str,
    window: str,
    params: dict,
    parquet_dir: Path,
    initial_balance: float,
) -> DatasetReport:
    """Carga parquets y corre 1 backtest con los params dados."""
    ticks = load_parquet_ticks(asset, window, parquet_dir=str(parquet_dir))
    if not ticks:
        return DatasetReport(
            asset=asset, window=window, ticks_loaded=0,
            sharpe=None, profit_factor=None, win_rate=None,
            max_drawdown=None, total_trades=0, total_pnl=None,
            criteria_passed=False, criteria_detail={},
            skip_reason="sin parquets disponibles",
        )

    if len(ticks) < params["ma_window"] * 5:
        return DatasetReport(
            asset=asset, window=window, ticks_loaded=len(ticks),
            sharpe=None, profit_factor=None, win_rate=None,
            max_drawdown=None, total_trades=0, total_pnl=None,
            criteria_passed=False, criteria_detail={},
            skip_reason=f"ticks insuficientes ({len(ticks)} < {params['ma_window']*5})",
        )

    result = run_mr_backtest(
        ticks=ticks,
        entry_zscore=float(params["entry_zscore"]),
        exit_zscore=float(params["exit_zscore"]),
        ma_window=int(params["ma_window"]),
        stop_loss_pct=float(params["stop_loss_pct"]),
        timeout_minutes=float(params["timeout_minutes"]),
        position_size_pusd=float(params["position_size_pusd"]),
        initial_balance=initial_balance,
    )

    passed, detail = _eval_criteria(
        sharpe=result.sharpe_ratio,
        pf=result.profit_factor,
        wr=result.win_rate,
        max_dd=result.max_drawdown,
    )

    return DatasetReport(
        asset=asset, window=window, ticks_loaded=len(ticks),
        sharpe=round(result.sharpe_ratio, 4),
        profit_factor=round(result.profit_factor, 4),
        win_rate=round(result.win_rate, 4),
        max_drawdown=round(result.max_drawdown, 4),
        total_trades=result.total_trades,
        total_pnl=round(result.total_pnl, 4),
        criteria_passed=passed,
        criteria_detail=detail,
    )


def _print_table(reports: list[DatasetReport]) -> None:
    print()
    print("=" * 90)
    print(" BACKTEST MEANREVERSION — DATOS REALES (data/parquet/)")
    print("=" * 90)
    header = (
        f"{'Dataset':<10}{'Ticks':>8}{'Sharpe':>10}{'PF':>8}"
        f"{'WR':>8}{'MaxDD':>8}{'Trades':>9}{'PnL':>10}  Status"
    )
    print(header)
    print("-" * 90)
    for r in reports:
        if r.skip_reason:
            print(
                f"{r.asset+'_'+r.window:<10}{r.ticks_loaded:>8}"
                f"{'N/A':>10}{'N/A':>8}{'N/A':>8}{'N/A':>8}"
                f"{r.total_trades:>9}{'N/A':>10}  ⏭️  {r.skip_reason}"
            )
            continue
        status = "✅ PASS" if r.criteria_passed else "❌ FAIL"
        print(
            f"{r.asset+'_'+r.window:<10}{r.ticks_loaded:>8}"
            f"{r.sharpe:>10.3f}{r.profit_factor:>8.3f}"
            f"{r.win_rate:>8.1%}{r.max_drawdown:>8.1%}"
            f"{r.total_trades:>9}{r.total_pnl:>10.2f}  {status}"
        )
    print("-" * 90)


def _print_criteria(reports: list[DatasetReport]) -> None:
    print()
    print(
        "Criterios R2.1 (skill pre-real-trading-checklist):"
        f" Sharpe ≥ {CRITERIA['sharpe_min']},"
        f" PF ≥ {CRITERIA['profit_factor_min']},"
        f" WR ≥ {CRITERIA['win_rate_min']:.0%},"
        f" MaxDD ≤ {CRITERIA['max_drawdown_max']:.0%}"
    )
    valid = [r for r in reports if not r.skip_reason]
    if not valid:
        print("⚠️  Ningún dataset tenía parquets utilizables — no se puede evaluar.")
        return
    n_pass = sum(1 for r in valid if r.criteria_passed)
    if n_pass == len(valid):
        print(f"✅ TODOS los datasets pasan ({n_pass}/{len(valid)}).")
    else:
        print(f"❌ {n_pass}/{len(valid)} datasets pasan. Real trading BLOQUEADO.")
        for r in valid:
            if not r.criteria_passed:
                fails = [
                    k for k, v in r.criteria_detail.items() if not v["pass"]
                ]
                print(f"   {r.asset}_{r.window}: fallan {', '.join(fails)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--params", type=Path, default=DEFAULT_PARAMS_PATH,
        help=f"JSON de parámetros (default: {DEFAULT_PARAMS_PATH})",
    )
    p.add_argument(
        "--parquet-dir", type=Path, default=DEFAULT_PARQUET_DIR,
        help=f"Directorio de parquets (default: {DEFAULT_PARQUET_DIR})",
    )
    p.add_argument(
        "--initial-balance", type=float, default=1000.0,
        help="Balance inicial USDC (default: 1000)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=REPORTS_DIR,
        help=f"Carpeta de reportes (default: {REPORTS_DIR})",
    )
    args = p.parse_args()

    try:
        params = _params_from_json(args.params)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"❌ Error leyendo {args.params}: {e}")
        return 2

    print(f"📊 Usando parámetros de {args.params}:")
    print(f"   ma_window={params['ma_window']} "
          f"entry_z={params['entry_zscore']} "
          f"exit_z={params['exit_zscore']} "
          f"SL={params['stop_loss_pct']} "
          f"timeout={params['timeout_minutes']}min "
          f"size={params['position_size_pusd']} pUSD")

    reports = [
        _run_dataset(
            asset=asset, window=window, params=params,
            parquet_dir=args.parquet_dir,
            initial_balance=args.initial_balance,
        )
        for asset, window in DATASETS
    ]

    _print_table(reports)
    _print_criteria(reports)

    # Persistir JSON
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = args.output_dir / f"backtest_real_{ts}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "parquet",
        "parquet_dir": str(args.parquet_dir),
        "initial_balance": args.initial_balance,
        "params": params,
        "criteria": CRITERIA,
        "datasets": [asdict(r) for r in reports],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    latest = args.output_dir / "backtest_real_latest.json"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.write_text(json.dumps(payload, indent=2))
    print(f"\n📁 Reporte: {out_path}")
    print(f"📁 Latest:  {latest}")

    valid = [r for r in reports if not r.skip_reason]
    if not valid:
        return 2
    return 0 if all(r.criteria_passed for r in valid) else 1


if __name__ == "__main__":
    sys.exit(main())
