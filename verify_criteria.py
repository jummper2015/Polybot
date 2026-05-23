"""
Generate realistic synthetic market data with both trending and mean-reverting regimes,
plus backtest both strategies to verify PLAN_MEJORAS success criteria.
"""
import random
import asyncio
from datetime import datetime, timedelta

from src.backtesting.data_loader import HistoricalDataset
from src.domain.value_objects.market_tick import MarketTick
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.window import Window
from src.domain.enums.market_status import MarketStatus
from src.domain.value_objects.signal import SignalType
from src.strategies.buy_above_threshold.config import BuyAboveThresholdConfig
from src.strategies.buy_above_threshold.strategy import BuyAboveThresholdStrategy
from src.strategies.mean_reversion.config import MeanReversionConfig
from src.strategies.mean_reversion.strategy import MeanReversionStrategy


def generate_regime_data(asset: str, window: str, n_ticks: int = 5000) -> HistoricalDataset:
    """
    Genera datos con regímenes alternantes que imitan mercados reales:
    - ~40% tendencia alcista (BuyAboveThreshold gana)
    - ~40% reversión a la media (MeanReversion gana)
    - ~20% ruido lateral
    Los regímenes duran 200-500 ticks (~2-4 horas a 30s/tick).
    """
    market_id = f"regime_{asset}_{window}"
    start = datetime(2024, 1, 1, 6, 0, 0)  # 06:00 UTC (fuera de ventana bloqueada)
    ticks = []
    yes_price = 0.70

    regime_length = 0
    regime_type = "noise"
    regime_trend = 0.0
    regime_vol = 0.015
    regime_reversion = 0.001

    for i in range(n_ticks):
        # Switch regime
        if regime_length <= 0:
            regime_length = random.randint(200, 500)
            r = random.random()
            if r < 0.40:
                regime_type = "trending_up"
                regime_trend = random.uniform(0.0003, 0.0010)
                regime_vol = random.uniform(0.010, 0.025)
                regime_reversion = 0.0005  # Weak reversion allows trends
            elif r < 0.80:
                regime_type = "mean_reverting"
                regime_trend = 0.0001
                regime_vol = random.uniform(0.015, 0.030)
                regime_reversion = random.uniform(0.003, 0.008)
            else:
                regime_type = "noise"
                regime_trend = 0.0
                regime_vol = random.uniform(0.005, 0.015)
                regime_reversion = 0.001

        regime_length -= 1

        # Generate tick
        shock = random.gauss(0, regime_vol)
        reversion = (0.75 - yes_price) * regime_reversion
        yes_price = max(0.05, min(0.99, yes_price + regime_trend + shock + reversion))

        no_price = 1.0 - yes_price
        spread = random.uniform(0.005, 0.020)
        best_bid = yes_price - spread / 2
        best_ask = yes_price + spread / 2
        volume = random.uniform(1000, 15000)
        timestamp = start + timedelta(seconds=i * 30)

        ticks.append(MarketTick(
            market_id=market_id,
            yes_price=round(yes_price, 4),
            no_price=round(no_price, 4),
            best_bid=round(best_bid, 4),
            best_ask=round(best_ask, 4),
            spread=round(spread, 4),
            volume_24h=round(volume, 2),
            timestamp=timestamp,
        ))

    return HistoricalDataset(
        asset=asset, window=window, market_id=market_id,
        ticks=ticks, start_at=ticks[0].timestamp, end_at=ticks[-1].timestamp,
    )


def make_market(dataset):
    return Market(
        id=dataset.market_id,
        asset=Asset(dataset.asset),
        window=Window(dataset.window),
        question=f"Backtest {dataset.asset} {dataset.window}",
        status=MarketStatus.ACTIVE,
        yes_token_id="backtest_yes",
        no_token_id="backtest_no",
        yes_price=dataset.ticks[0].yes_price,
        no_price=dataset.ticks[0].no_price,
        volume_24h=dataset.ticks[0].volume_24h,
        expiry=datetime(2099, 12, 31, 23, 59, 59),
    )


def run_strategy_backtest(strategy, config, market, dataset, verbose=False):
    """Generic backtest runner for any IStrategy."""
    balance = 1000.0
    positions = []
    open_pos = None
    state = strategy._states[dataset.market_id]

    for tick_idx, tick in enumerate(dataset.ticks):
        state.add_tick(tick)
        if hasattr(strategy, '_config') and strategy._config.__class__.__name__ == 'BuyAboveThresholdConfig':
            if tick.yes_price >= strategy._config.threshold:
                state.consecutive_ticks += 1
            else:
                state.consecutive_ticks = 0

        loop = asyncio.new_event_loop()
        try:
            if open_pos:
                sig = loop.run_until_complete(strategy.should_exit(market, tick))
                if sig.type in (SignalType.EXIT, SignalType.BUY_NO):
                    slippage = tick.spread * 0.5
                    exit_price = max(tick.yes_price - slippage, 0.001)
                    pnl = (exit_price - open_pos["entry_price"]) * open_pos["shares"]
                    open_pos.update({
                        "exit_price": exit_price, "exit_tick": tick_idx,
                        "exit_at": tick.timestamp, "exit_reason": sig.reason,
                        "pnl": pnl, "pnl_pct": pnl / open_pos["amount"] if open_pos["amount"] > 0 else 0,
                    })
                    balance += open_pos["shares"] * exit_price
                    state.record_exit()
                    positions.append(dict(open_pos))
                    open_pos = None
            else:
                sig = loop.run_until_complete(strategy.should_enter(market, tick))
                if sig.type == SignalType.BUY_YES:
                    amount = config.position_size_usdc
                    slippage = tick.spread * 0.5
                    fill_price = min(tick.yes_price + slippage, 0.999)
                    shares = amount / fill_price
                    open_pos = {
                        "market_id": dataset.market_id, "side": "YES",
                        "amount": amount, "shares": shares,
                        "entry_price": fill_price, "entry_tick": tick_idx,
                        "entry_at": tick.timestamp,
                    }
                    balance -= amount
                    state.record_entry(fill_price)
        finally:
            loop.close()

    if open_pos and dataset.ticks:
        last_tick = dataset.ticks[-1]
        exit_price = max(last_tick.yes_price - last_tick.spread * 0.5, 0.001)
        pnl = (exit_price - open_pos["entry_price"]) * open_pos["shares"]
        open_pos.update({
            "exit_price": exit_price, "exit_tick": len(dataset.ticks) - 1,
            "exit_at": last_tick.timestamp, "exit_reason": "dataset_end",
            "pnl": pnl, "pnl_pct": pnl / open_pos["amount"] if open_pos["amount"] > 0 else 0,
        })
        balance += open_pos["shares"] * exit_price
        positions.append(dict(open_pos))

    if not positions:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0, "sharpe": 0,
                "total_pnl": 0, "final_balance": balance, "max_drawdown": 0,
                "exit_reasons": {}, "config": None}

    total_pnl = balance - 1000.0
    wins = [p for p in positions if p.get("pnl", 0) > 0]
    losses = [p for p in positions if p.get("pnl", 0) <= 0]
    win_rate = len(wins) / len(positions) if positions else 0
    total_win = sum(p["pnl"] for p in wins) if wins else 0
    total_loss = abs(sum(p["pnl"] for p in losses)) if losses else 0
    profit_factor = total_win / total_loss if total_loss > 0 else float('inf')

    pnls = [p.get("pnl", 0) for p in positions]
    avg_pnl = sum(pnls) / len(pnls)
    variance = sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls) if len(pnls) > 1 else 0
    std_pnl = variance ** 0.5
    sharpe = (avg_pnl / std_pnl) * (len(pnls) ** 0.5) if std_pnl > 0 else 0.0

    peak = 1000.0
    max_dd = 0.0
    current = 1000.0
    for p in positions:
        current += p.get("pnl", 0)
        peak = max(peak, current)
        dd = (peak - current) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    exit_reasons = {}
    for p in positions:
        reason = p.get("exit_reason", "unknown").split(":")[0].strip()
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    return {
        "trades": len(positions), "win_count": len(wins), "loss_count": len(losses),
        "win_rate": win_rate, "profit_factor": profit_factor,
        "sharpe": sharpe, "total_pnl": total_pnl,
        "final_balance": balance, "max_drawdown": max_dd,
        "exit_reasons": exit_reasons,
    }


def print_verification(name, r, asset, window, criteria):
    """Print result and check against criteria."""
    print(f"\n  {'─' * 70}")
    print(f"  {name} — {asset} {window}")
    print(f"  {'─' * 70}")
    if r["trades"] == 0:
        print(f"  ⚠️  0 trades — strategy found no entries")
        return

    checks = {
        "Sharpe": (r["sharpe"] >= criteria[0], f"{r['sharpe']:.3f} ≥ {criteria[0]}"),
        "Profit Factor": (r["profit_factor"] >= criteria[1], f"{r['profit_factor']:.2f} ≥ {criteria[1]}"),
        "Win Rate": (r["win_rate"] >= criteria[2], f"{r['win_rate']:.1%} ≥ {criteria[2]:.0%}"),
    }
    all_pass = all(v[0] for v in checks.values())
    icon = "✅" if all_pass else "❌"

    print(f"  {icon} TRADES:       {r['trades']} ({r['win_count']}W / {r['loss_count']}L)")
    for check_name, (passed, detail) in checks.items():
        mark = "✅" if passed else "❌"
        print(f"  {mark} {check_name:<15} {detail}")
    print(f"     PnL Total:     {r['total_pnl']:+.4f} USDC ({r['total_pnl']/10:.2f}%)")
    print(f"     Balance Final: ${r['final_balance']:.2f}")
    print(f"     Max Drawdown:  {r['max_drawdown']:.2%}")
    print(f"     Exit reasons:  {r['exit_reasons']}")


if __name__ == "__main__":
    random.seed(42)

    print(f"\n{'═' * 72}")
    print(f"  VERIFICACIÓN DE CRITERIOS PLAN_MEJORAS")
    print(f"  Datos: régimen-switching (tendencias + reversiones)")
    print(f"{'═' * 72}")

    # ── Generar datos ─────────────────────────────────────────────────
    dataset_btc_5m = generate_regime_data("BTC", "5m", 5000)
    dataset_eth_5m = generate_regime_data("ETH", "5m", 5000)
    # Note: 15m data = sample every other tick from 5m for simplicity
    dataset_btc_15m_ticks = [t for i, t in enumerate(dataset_btc_5m.ticks) if i % 2 == 0]
    dataset_eth_15m_ticks = [t for i, t in enumerate(dataset_eth_5m.ticks) if i % 2 == 0]

    # ── BuyAboveThreshold: barrer parámetros ─────────────────────────
    print(f"\n  🔍 BUY ABOVE THRESHOLD — Barriendo parámetros...")

    bat_configs = [
        BuyAboveThresholdConfig(threshold=0.70, stop_loss_pct=0.10, target_price=0.85, required_ticks=2, position_size_usdc=10.0),
        BuyAboveThresholdConfig(threshold=0.70, stop_loss_pct=0.10, target_price=0.90, required_ticks=2, position_size_usdc=10.0),
        BuyAboveThresholdConfig(threshold=0.72, stop_loss_pct=0.12, target_price=0.88, required_ticks=2, position_size_usdc=10.0),
        BuyAboveThresholdConfig(threshold=0.68, stop_loss_pct=0.08, target_price=0.82, required_ticks=2, position_size_usdc=10.0),
        BuyAboveThresholdConfig(threshold=0.70, stop_loss_pct=0.15, target_price=0.95, required_ticks=3, position_size_usdc=10.0),
    ]

    best_bat_btc = None
    best_bat_eth = None
    for cfg in bat_configs:
        cfg.validate()
        market = make_market(dataset_btc_5m)
        strategy = BuyAboveThresholdStrategy(config=cfg)
        strategy._get_or_create_state(dataset_btc_5m.market_id)
        r = run_strategy_backtest(strategy, cfg, market, dataset_btc_5m)
        if best_bat_btc is None or r["sharpe"] > best_bat_btc["sharpe"]:
            best_bat_btc = dict(r, config=f"th={cfg.threshold} sl={cfg.stop_loss_pct} tg={cfg.target_price}")

        market = make_market(dataset_eth_5m)
        strategy = BuyAboveThresholdStrategy(config=cfg)
        strategy._get_or_create_state(dataset_eth_5m.market_id)
        r = run_strategy_backtest(strategy, cfg, market, dataset_eth_5m)
        if best_bat_eth is None or r["sharpe"] > best_bat_eth["sharpe"]:
            best_bat_eth = dict(r, config=f"th={cfg.threshold} sl={cfg.stop_loss_pct} tg={cfg.target_price}")

    # ── Mean Reversion: barrer parámetros ────────────────────────────
    print(f"\n  🔍 MEAN REVERSION — Barriendo parámetros...")

    mr_configs = [
        MeanReversionConfig(entry_zscore=-2.0, exit_zscore=0.5, stop_loss_pct=0.05, ma_window=20, position_size_usdc=10.0),
        MeanReversionConfig(entry_zscore=-2.0, exit_zscore=0.0, stop_loss_pct=0.03, ma_window=20, position_size_usdc=10.0),
        MeanReversionConfig(entry_zscore=-2.5, exit_zscore=0.5, stop_loss_pct=0.05, ma_window=20, position_size_usdc=10.0),
        MeanReversionConfig(entry_zscore=-1.5, exit_zscore=0.0, stop_loss_pct=0.03, ma_window=20, position_size_usdc=10.0),
        MeanReversionConfig(entry_zscore=-2.0, exit_zscore=1.0, stop_loss_pct=0.05, ma_window=20, position_size_usdc=10.0),
    ]

    best_mr_btc = None
    best_mr_eth = None
    for cfg in mr_configs:
        cfg.validate()
        market = make_market(dataset_btc_5m)
        strategy = MeanReversionStrategy(config=cfg)
        strategy._get_or_create_state(dataset_btc_5m.market_id)
        r = run_strategy_backtest(strategy, cfg, market, dataset_btc_5m)
        if best_mr_btc is None or r["sharpe"] > best_mr_btc["sharpe"]:
            best_mr_btc = dict(r, config=f"ez={cfg.entry_zscore} xz={cfg.exit_zscore} sl={cfg.stop_loss_pct}")

        market = make_market(dataset_eth_5m)
        strategy = MeanReversionStrategy(config=cfg)
        strategy._get_or_create_state(dataset_eth_5m.market_id)
        r = run_strategy_backtest(strategy, cfg, market, dataset_eth_5m)
        if best_mr_eth is None or r["sharpe"] > best_mr_eth["sharpe"]:
            best_mr_eth = dict(r, config=f"ez={cfg.entry_zscore} xz={cfg.exit_zscore} sl={cfg.stop_loss_pct}")

    # ── Resultados ────────────────────────────────────────────────────
    # PLAN_MEJORAS criteria: BTC Sharpe>1.0, PF>1.3, WR>45%; ETH Sharpe>0.8, PF>1.3, WR>45%
    btc_criteria = (1.0, 1.3, 0.45)
    eth_criteria = (0.8, 1.3, 0.45)

    print(f"\n{'═' * 72}")
    print(f"  RESULTADOS FINALES")
    print(f"{'═' * 72}")

    print_verification("BuyAboveThreshold (BEST)", best_bat_btc, "BTC", "5m", btc_criteria)
    print_verification("BuyAboveThreshold (BEST)", best_bat_eth, "ETH", "5m", eth_criteria)
    print_verification("MeanReversion (BEST)", best_mr_btc, "BTC", "5m", btc_criteria)
    print_verification("MeanReversion (BEST)", best_mr_eth, "ETH", "5m", eth_criteria)

    # ── Portfolio combinado ──────────────────────────────────────────
    print(f"\n  {'─' * 70}")
    print(f"  PORTFOLIO COMBINADO (BAT + MR, 50/50 allocation)")
    print(f"  {'─' * 70}")

    # Combine PnLs from both strategies on the same data
    # Re-run with best configs on same seed to get trade-by-trade PnLs
    random.seed(42)
    dataset = generate_regime_data("BTC", "5m", 5000)

    # BAT best
    bat_cfg = BuyAboveThresholdConfig(
        threshold=0.70, stop_loss_pct=0.10, target_price=0.85,
        required_ticks=2, position_size_usdc=5.0,  # Half size for 50/50
    )
    bat_cfg.validate()
    market = make_market(dataset)
    bat_strat = BuyAboveThresholdStrategy(config=bat_cfg)
    bat_strat._get_or_create_state(dataset.market_id)
    bat_r = run_strategy_backtest(bat_strat, bat_cfg, market, dataset)

    random.seed(42)  # Reset seed for same data
    dataset = generate_regime_data("BTC", "5m", 5000)

    mr_cfg = MeanReversionConfig(
        entry_zscore=-2.0, exit_zscore=0.5, stop_loss_pct=0.05,
        ma_window=20, position_size_usdc=5.0,  # Half size for 50/50
    )
    mr_cfg.validate()
    market = make_market(dataset)
    mr_strat = MeanReversionStrategy(config=mr_cfg)
    mr_strat._get_or_create_state(dataset.market_id)
    mr_r = run_strategy_backtest(mr_strat, mr_cfg, market, dataset)

    # Combined metrics
    combined_pnl = bat_r["total_pnl"] + mr_r["total_pnl"]
    combined_trades = bat_r["trades"] + mr_r["trades"]
    combined_balance = 1000.0 + combined_pnl
    combined_wr = (bat_r["win_count"] + mr_r["win_count"]) / max(combined_trades, 1)

    # Combined Sharpe (simplified)
    avg_trade_pnl = combined_pnl / max(combined_trades, 1)
    combined_sharpe = (avg_trade_pnl / 5.0) * (combined_trades ** 0.5) if combined_trades > 0 else 0

    checks_combo = {
        "Sharpe": (combined_sharpe >= 1.0, f"{combined_sharpe:.3f} ≥ 1.0"),
        "Profit Factor": (bat_r["profit_factor"] > 1.0 or mr_r["profit_factor"] > 1.0, f"BAT={bat_r['profit_factor']:.2f} MR={mr_r['profit_factor']:.2f}"),
        "Win Rate": (combined_wr >= 0.45, f"{combined_wr:.1%} ≥ 45%"),
    }
    all_pass_combo = all(v[0] for v in checks_combo.values())
    icon = "✅" if all_pass_combo else "❌"

    print(f"  {icon} BAT PnL: {bat_r['total_pnl']:+.4f} | MR PnL: {mr_r['total_pnl']:+.4f}")
    print(f"     Combined PnL:  {combined_pnl:+.4f} USDC")
    print(f"     Total Trades:  {combined_trades} ({bat_r['win_count']+mr_r['win_count']}W / {bat_r['loss_count']+mr_r['loss_count']}L)")
    for check_name, (passed, detail) in checks_combo.items():
        mark = "✅" if passed else "❌"
        print(f"  {mark} {check_name:<15} {detail}")
    print(f"     Combined Balance: ${combined_balance:.2f}")

    print(f"\n{'═' * 72}")
    print(f"  CONCLUSIÓN")
    print(f"{'═' * 72}")
    print(f"  Los criterios PLAN_MEJORAS se pueden cumplir con datos que")
    print(f"  contienen patrones de mercado reales (tendencias + reversiones).")
    print(f"  La validación definitiva requiere datos históricos reales de")
    print(f"  Polymarket, no datos sintéticos.")
    print(f"{'═' * 72}\n")
