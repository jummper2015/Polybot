#!/usr/bin/env python3
"""
Monte Carlo equity simulation para MR (R1.2-ter, Eslabón 2 del protocolo).

Diferencia con walk-forward:
- Walk-forward (eslabón 1) cuantifica ROBUSTEZ temporal (¿generaliza el edge
  entre tramos del histórico?).
- Monte Carlo (eslabón 2) cuantifica COLA estadística (¿cuál es la peor caída
  plausible si la distribución empírica de PnLs por trade se repite?).

Son ortogonales — el protocolo exige ambos.

Procedimiento:
  1. Cargar ticks de Parquet del asset.
  2. Cargar params MR óptimos (data/optimization/optimal_params_mr_real.json
     o argumentos --ma --ez --xz --sl --tm --ps).
  3. Correr UN backtest MR sobre todo el histórico → lista de trade PnLs.
  4. EquitySimulator.run(pnls) con N simulaciones bootstrap + ruin_threshold.

Criterios del strategy-validation-protocol:
  - ≥ 1000 simulaciones
  - P5 del PnL final > 0 (peor 5% no es pérdida)
  - P(drawdown > 50%) < 1%

Uso:
  python scripts/monte_carlo_mr.py --asset BTC --simulations 2000
  python scripts/monte_carlo_mr.py --asset ETH --params-file data/optimization/optimal_params_mr_real.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.optimize_mr import load_parquet_ticks, run_mr_backtest  # noqa: E402
from src.quantitative.monte_carlo import (  # noqa: E402
    EquitySimulator,
    MonteCarloConfig,
)

DEFAULT_PARAMS_FILE = Path("data/optimization/optimal_params_mr_real.json")
DEFAULT_REPORTS_DIR = Path("data/reports")


def _load_params(path: Path) -> dict:
    """Lee top_config de optimal_params_mr_real.json (formato emitido por
    optimize_mr.py).
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Params file not found: {path}. "
            f"Run optimize_mr.py first or pass --ma/--ez/--xz/--sl/--tm/--ps."
        )
    with open(path) as f:
        data = json.load(f)
    return data.get("top_config", data)


def _percentile(values: list[float], pct: float) -> float:
    """Percentil pct (0-100) de una lista. Usa interpolación lineal estándar."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _passes_protocol(report, p5_pnl: float, p95_dd: float) -> dict:
    """Aplica los criterios del strategy-validation-protocol skill (eslabón 2):
    - simulations ≥ 1000
    - P5 del PnL final > 0
    - P(ruina) < 1% (drawdown > ruin_threshold_pct)
    """
    ruin_pct = report.ruin_probability
    sims = report.simulations_run

    criteria = {
        "n_simulations": sims,
        "passes_simulation_count": sims >= 1000,
        "p5_terminal_pnl_usdc": round(p5_pnl, 4),
        "passes_p5_positive": p5_pnl > 0,
        "ruin_probability": round(ruin_pct, 6),
        "passes_ruin_threshold": ruin_pct < 0.01,
        "p95_max_drawdown": round(p95_dd, 4),
    }
    criteria["all_pass"] = (
        criteria["passes_simulation_count"]
        and criteria["passes_p5_positive"]
        and criteria["passes_ruin_threshold"]
    )
    return criteria


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Monte Carlo equity simulation para MR sobre PnLs reales del "
            "backtest contra Parquet."
        )
    )
    parser.add_argument("--asset", required=True, choices=["BTC", "ETH"])
    parser.add_argument(
        "--simulations", type=int, default=2000,
        help="Número de trayectorias (default 2000; protocolo ≥ 1000).",
    )
    parser.add_argument(
        "--trades-per-sim", type=int, default=100,
        help="Trades por simulación (default 100).",
    )
    parser.add_argument(
        "--ruin-threshold-pct", type=float, default=0.50,
        help="Drawdown que define ruina (default 0.50 = 50%, per protocolo).",
    )
    parser.add_argument(
        "--n-ticks", type=int, default=500000,
        help="Cap de ticks para el backtest histórico (default 500000).",
    )
    parser.add_argument(
        "--balance", type=float, default=1000.0,
        help="Balance inicial USDC (default 1000).",
    )
    parser.add_argument(
        "--parquet-dir", default="data/parquet",
        help="Directorio raíz de parquets (default data/parquet).",
    )
    parser.add_argument(
        "--params-file", default=str(DEFAULT_PARAMS_FILE),
        help=(
            f"JSON con top_config MR (default {DEFAULT_PARAMS_FILE}). "
            "Ignorado si se pasan los flags --ma/--ez/--xz/--sl/--tm/--ps."
        ),
    )
    parser.add_argument("--ma", type=int, default=None, help="ma_window")
    parser.add_argument("--ez", type=float, default=None, help="entry_zscore")
    parser.add_argument("--xz", type=float, default=None, help="exit_zscore")
    parser.add_argument("--sl", type=float, default=None, help="stop_loss_pct")
    parser.add_argument("--tm", type=float, default=None, help="timeout_minutes")
    parser.add_argument("--ps", type=float, default=None, help="position_size_pusd")
    parser.add_argument("--method", default="bootstrap",
                        choices=["bootstrap", "parametric", "combined"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output-dir", default=str(DEFAULT_REPORTS_DIR),
        help="Donde escribir el JSON (default data/reports).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 70)
    print(f"  MONTE CARLO MR — {args.asset} — {args.simulations} sims × "
          f"{args.trades_per_sim} trades")
    print("=" * 70)

    # ── Params ──
    cli_params = {
        "ma_window": args.ma,
        "entry_zscore": args.ez,
        "exit_zscore": args.xz,
        "stop_loss_pct": args.sl,
        "timeout_minutes": args.tm,
        "position_size_pusd": args.ps,
    }
    if all(v is not None for v in cli_params.values()):
        params = cli_params
        params_source = "cli_args"
    else:
        try:
            params = _load_params(Path(args.params_file))
            params_source = args.params_file
        except FileNotFoundError as exc:
            print(f"  ❌ {exc}")
            return 2

    print(f"  Params source: {params_source}")
    print(
        f"    ma={params['ma_window']} "
        f"ez={params['entry_zscore']:+.1f} "
        f"xz={params['exit_zscore']:+.1f} "
        f"sl={params['stop_loss_pct']:.0%} "
        f"tm={params['timeout_minutes']:.0f}m "
        f"ps={params['position_size_pusd']:.0f} USDC"
    )

    # ── Cargar ticks ──
    print(f"  Loading {args.asset} ticks from {args.parquet_dir} …")
    t0 = time.monotonic()
    ticks = load_parquet_ticks(args.asset, window="raw", parquet_dir=args.parquet_dir)
    if not ticks:
        print(f"  ❌ No ticks for {args.asset}.")
        return 2

    if len(ticks) > args.n_ticks:
        ticks = ticks[-args.n_ticks:]
    print(f"  {len(ticks):,} ticks loaded in {time.monotonic() - t0:.1f}s.")

    # ── Backtest histórico ──
    print("  Running historical MR backtest …")
    t0 = time.monotonic()
    result = run_mr_backtest(
        ticks=ticks,
        entry_zscore=params["entry_zscore"],
        exit_zscore=params["exit_zscore"],
        ma_window=params["ma_window"],
        stop_loss_pct=params["stop_loss_pct"],
        timeout_minutes=params["timeout_minutes"],
        position_size_pusd=params["position_size_pusd"],
        initial_balance=args.balance,
    )
    print(
        f"  Backtest: {result.total_trades} trades, "
        f"Sharpe={result.sharpe_ratio:+.3f}, "
        f"PnL={result.total_pnl:+.2f} USDC, "
        f"WR={result.win_rate:.1%}  ({time.monotonic() - t0:.1f}s)"
    )
    if result.total_trades < 5:
        print(f"  ❌ Need ≥ 5 trades for Monte Carlo, got {result.total_trades}.")
        return 2

    # Reutiliza los trade PnLs que `run_mr_backtest` ahora expone vía
    # `MRResult.trade_pnls`. No re-ejecutamos el loop para evitar drift
    # (filtros, slippage, exit reasons). Si `result.trade_pnls` quedara
    # vacío por una versión vieja del dataclass, falla rápido.
    pnls = result.trade_pnls
    if not pnls:
        print(
            "  ❌ run_mr_backtest devolvió trade_pnls vacío. "
            "¿Versión desactualizada de scripts/optimize_mr.py?"
        )
        return 2
    print(f"  Using {len(pnls)} trade PnLs from MRResult for MC resampling.")

    if not pnls or len(pnls) < 5:
        print(f"  ❌ Need ≥ 5 trade PnLs, got {len(pnls)}.")
        return 2

    # ── Monte Carlo ──
    cfg = MonteCarloConfig(
        simulations=args.simulations,
        trades_per_sim=args.trades_per_sim,
        initial_balance=args.balance,
        method=args.method,
        ruin_threshold_pct=args.ruin_threshold_pct,
        seed=args.seed,
    )
    simulator = EquitySimulator(cfg)
    print(
        f"  Running {cfg.simulations} simulations "
        f"({cfg.method}, ruin_threshold={cfg.ruin_threshold_pct:.0%}) …"
    )
    t0 = time.monotonic()
    report = simulator.run(pnls)
    print(f"  Done in {time.monotonic() - t0:.1f}s.")

    # ── Métricas ──
    p5 = _percentile(report.terminal_pnls, 5.0)
    p50 = _percentile(report.terminal_pnls, 50.0)
    p95 = _percentile(report.terminal_pnls, 95.0)
    p95_dd = _percentile(report.max_drawdowns, 95.0)
    print()
    print(f"  Terminal PnL distribution (sims={cfg.simulations}):")
    print(f"    P5  = {p5:+.2f} USDC")
    print(f"    P50 = {p50:+.2f} USDC")
    print(f"    P95 = {p95:+.2f} USDC")
    print(f"    Mean = {report.mean_terminal_pnl:+.2f} USDC")
    print(f"    Profitable sims: {report.profitable_probability:.1%}")
    print(f"    Mean max DD:     {report.mean_max_drawdown:.1%}")
    print(f"    P95 max DD:      {p95_dd:.1%}")
    print(
        f"    Ruin probability (DD > {cfg.ruin_threshold_pct:.0%}): "
        f"{report.ruin_probability:.4f}"
    )

    checks = _passes_protocol(report, p5, p95_dd)
    print()
    print("  Protocol checks (strategy-validation-protocol):")
    print(f"    {'✅' if checks['passes_simulation_count'] else '❌'} "
          f"simulations ≥ 1000: {checks['n_simulations']}")
    print(f"    {'✅' if checks['passes_p5_positive'] else '❌'} "
          f"P5 terminal PnL > 0: {checks['p5_terminal_pnl_usdc']:+.2f}")
    print(f"    {'✅' if checks['passes_ruin_threshold'] else '❌'} "
          f"P(ruin) < 1%: {checks['ruin_probability']:.4f}")
    print(f"  → {'ALL PASS' if checks['all_pass'] else 'INCOMPLETE'}")

    # ── Persistir ──
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"monte_carlo_mr_{args.asset}_{ts}.json"
    out = {
        "asset": args.asset,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": params,
        "params_source": params_source,
        "config": {
            "simulations": cfg.simulations,
            "trades_per_sim": cfg.trades_per_sim,
            "initial_balance": cfg.initial_balance,
            "method": cfg.method,
            "ruin_threshold_pct": cfg.ruin_threshold_pct,
            "seed": cfg.seed,
        },
        "historical_backtest": {
            "trades": result.total_trades,
            "sharpe": result.sharpe_ratio,
            "pnl_usdc": result.total_pnl,
            "win_rate": result.win_rate,
        },
        "monte_carlo": {
            "p5_terminal_pnl": p5,
            "p50_terminal_pnl": p50,
            "p95_terminal_pnl": p95,
            "mean_terminal_pnl": report.mean_terminal_pnl,
            "profitable_probability": report.profitable_probability,
            "mean_max_drawdown": report.mean_max_drawdown,
            "p95_max_drawdown": p95_dd,
            "ruin_probability": report.ruin_probability,
            "var_95": report.var_95,
            "cvar_95": report.cvar_95,
        },
        "protocol_check": checks,
    }
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  💾 Report: {output_path}")

    latest_path = output_dir / f"monte_carlo_mr_{args.asset}_latest.json"
    try:
        if latest_path.exists() or latest_path.is_symlink():
            latest_path.unlink()
        latest_path.symlink_to(output_path.name)
    except OSError:
        pass

    return 0 if checks["all_pass"] else 1


# NOTE: Antes de R2.2-paper-verify, este script re-implementaba el loop de
# `run_mr_backtest` en `_extract_trade_pnls` para extraer los PnLs de cada
# trade cerrado. Riesgo de drift con `optimize_mr.py`. Ahora `MRResult`
# expone `trade_pnls` directamente y `_extract_trade_pnls` ya no existe.


if __name__ == "__main__":
    sys.exit(main())
