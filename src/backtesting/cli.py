# src/backtesting/cli.py
# Uso: python -m src.backtesting.cli --help

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket Bot — Backtesting CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Backtest con datos sintéticos (sin archivos)
  python -m src.backtesting.cli --asset BTC --window 5m --synthetic

  # Backtest con CSV real
  python -m src.backtesting.cli --asset ETH --window 15m --file data/historical/ETH_15m.csv

  # Parameter sweep completo
  python -m src.backtesting.cli --asset BTC --window 5m --synthetic --sweep

  # Con parámetros personalizados
  python -m src.backtesting.cli --asset BTC --window 5m --synthetic \\
    --threshold 0.80 --stop-loss 0.12 --target 0.92 --balance 500
        """,
    )

    # ── Datos ─────────────────────────────────────────────────────────
    parser.add_argument("--asset",  required=True, choices=["BTC", "ETH"])
    parser.add_argument("--window", required=True, choices=["5m", "15m"])
    parser.add_argument("--file",   help="Ruta al CSV/JSON con datos históricos")
    parser.add_argument("--synthetic", action="store_true",
                        help="Genera datos sintéticos (no necesita --file)")
    parser.add_argument("--n-ticks", type=int, default=2000,
                        help="Número de ticks sintéticos (default: 2000)")

    # ── Parámetros de estrategia ───────────────────────────────────────
    parser.add_argument("--threshold",    type=float, default=0.75)
    parser.add_argument("--stop-loss",    type=float, default=0.15)
    parser.add_argument("--target",       type=float, default=0.90)
    parser.add_argument("--ticks",        type=int,   default=3,
                        help="Ticks consecutivos requeridos")
    parser.add_argument("--position-size", type=float, default=10.0)
    parser.add_argument("--balance",      type=float, default=1000.0)

    # ── Modo ──────────────────────────────────────────────────────────
    parser.add_argument("--sweep",   action="store_true",
                        help="Ejecuta parameter sweep completo")
    parser.add_argument("--verbose", action="store_true",
                        help="Muestra cada trade en tiempo real")
    parser.add_argument("--no-save", action="store_true",
                        help="No guarda resultados en disco")

    return parser.parse_args()


def main():
    args = parse_args()

    from src.backtesting.data_loader import DataLoader
    from src.backtesting.engine import BacktestEngine
    from src.backtesting.metrics import BacktestMetrics
    from src.backtesting.reporter import BacktestReporter
    from src.risk.engine import RiskEngineConfig
    from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig

    # ── Carga de datos ────────────────────────────────────────────────
    if args.synthetic:
        print(f"\n📊 Generando {args.n_ticks} ticks sintéticos "
              f"para {args.asset} {args.window}...")
        dataset = DataLoader.generate_synthetic(
            asset=args.asset,
            window=args.window,
            n_ticks=args.n_ticks,
        )
    elif args.file:
        print(f"\n📂 Cargando datos desde {args.file}...")
        ext = args.file.rsplit(".", 1)[-1].lower()
        if ext == "csv":
            dataset = DataLoader.from_csv(args.file, args.asset, args.window)
        elif ext == "json":
            dataset = DataLoader.from_json(args.file, args.asset, args.window)
        else:
            print(f"❌ Formato no soportado: {ext} (usa CSV o JSON)")
            sys.exit(1)
    else:
        print("❌ Debes especificar --file o --synthetic")
        sys.exit(1)

    print(f"✅ Dataset cargado: {dataset.tick_count} ticks, "
          f"{dataset.duration_hours:.1f}h de datos")

    # ── Config ────────────────────────────────────────────────────────
    strategy_config = BuyAboveThresholdConfig(
        threshold          = args.threshold,
        stop_loss_pct      = args.stop_loss,
        target_price       = args.target,
        required_ticks     = args.ticks,
        position_size_pusd = args.position_size,
    )
    risk_config = RiskEngineConfig()
    reporter    = BacktestReporter()

    # ── Modo sweep ────────────────────────────────────────────────────
    if args.sweep:
        print("\n🔍 Iniciando parameter sweep...")
        engine = BacktestEngine(
            strategy_config=strategy_config,
            risk_config=risk_config,
            initial_balance=args.balance,
        )
        results = engine.run_parameter_sweep(dataset)
        comparisons = BacktestMetrics.compare(results)

        print("\n📊 TOP 10 configuraciones por Sharpe Ratio:\n")
        print(f"  {'Threshold':>10} {'StopLoss':>10} {'Target':>8} "
              f"{'Sharpe':>8} {'WinRate':>8} {'PF':>6} {'PnL':>10}")
        print("  " + "-" * 70)
        for i, c in enumerate(comparisons[:10]):
            print(
                f"  {c['threshold']:>10.2f} "
                f"{c['stop_loss_pct']:>10.0%} "
                f"{c['target_price']:>8.2f} "
                f"{c['sharpe_ratio']:>8.3f} "
                f"{c['win_rate']:>8.1%} "
                f"{c['profit_factor']:>6.2f} "
                f"{c['total_pnl']:>10.4f}"
            )

        if not args.no_save:
            path = reporter.save_sweep(
                comparisons,
                prefix=f"{args.asset}_{args.window}_sweep",
            )
            print(f"\n💾 Sweep guardado en: {path}")

    # ── Modo single backtest ──────────────────────────────────────────
    else:
        print(f"\n⚙️  Config: threshold={args.threshold} | "
              f"stop_loss={args.stop_loss:.0%} | "
              f"target={args.target} | "
              f"ticks={args.ticks}")
        print(f"   Balance inicial: ${args.balance:.2f} USDC\n")

        engine = BacktestEngine(
            strategy_config=strategy_config,
            risk_config=risk_config,
            initial_balance=args.balance,
            verbose=args.verbose,
        )

        print("🚀 Ejecutando backtest...")
        result = engine.run(dataset)

        reporter.print_summary(result)

        if not args.no_save:
            paths = reporter.save(result, prefix=f"{args.asset}_{args.window}")
            print(f"💾 Métricas: {paths['json']}")
            print(f"💾 Trades:   {paths['csv']}")


if __name__ == "__main__":
    main()
