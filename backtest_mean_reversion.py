"""Backtest Mean Reversion strategy on synthetic data (mean-reverting to 0.75)."""
import random

random.seed(42)
import asyncio
from datetime import datetime

from src.backtesting.data_loader import DataLoader
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.domain.value_objects.signal import SignalType
from src.strategies.mean_reversion.config import MeanReversionConfig
from src.strategies.mean_reversion.strategy import MeanReversionStrategy


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
        expiry=datetime(2099, 12, 31, 23, 59, 59),  # Far future
    )


def run_mr_backtest(asset, window, n_ticks=5000, config=None, verbose=False):
    """Run Mean Reversion backtest and return metrics."""
    dataset = DataLoader.generate_synthetic(
        asset=asset, window=window, n_ticks=n_ticks,
        start_price=0.70, volatility=0.02,
    )

    if config is None:
        config = MeanReversionConfig()
    config.validate()

    market = make_market(dataset)
    strategy = MeanReversionStrategy(config=config)
    strategy._get_or_create_state(dataset.market_id)
    state = strategy._states[dataset.market_id]

    balance = 1000.0
    positions = []
    open_pos = None

    for tick_idx, tick in enumerate(dataset.ticks):
        # Update state (same as engine would do via on_tick)
        state.add_tick(tick)

        # Evaluate entry/exit via async methods
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
                    if verbose:
                        print(f"  EXIT [{tick_idx:5d}] price={exit_price:.4f} pnl={pnl:+.4f} reason={sig.reason[:80]}")
                    open_pos = None
            else:
                sig = loop.run_until_complete(strategy.should_enter(market, tick))
                if sig.type == SignalType.BUY_YES:
                    amount = config.position_size_pusd
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
                    if verbose:
                        print(f"  ENTRY [{tick_idx:5d}] price={fill_price:.4f} amount={amount:.2f} balance={balance:.2f}")
        finally:
            loop.close()

    # Close open position at dataset end
    if open_pos and dataset.ticks:
        last_tick = dataset.ticks[-1]
        exit_price = max(last_tick.yes_price - last_tick.spread * 0.5, 0.001)
        pnl = (exit_price - open_pos["entry_price"]) * open_pos["shares"]
        open_pos.update({
            "exit_price": exit_price, "exit_tick": len(dataset.ticks)-1,
            "exit_at": last_tick.timestamp, "exit_reason": "dataset_end",
            "pnl": pnl, "pnl_pct": pnl / open_pos["amount"] if open_pos["amount"] > 0 else 0,
        })
        balance += open_pos["shares"] * exit_price
        positions.append(dict(open_pos))

    # Compute metrics
    total_pnl = balance - 1000.0
    wins = [p for p in positions if p.get("pnl", 0) > 0]
    losses = [p for p in positions if p.get("pnl", 0) <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / len(positions) if positions else 0

    total_win = sum(p["pnl"] for p in wins) if wins else 0
    total_loss = abs(sum(p["pnl"] for p in losses)) if losses else 0
    profit_factor = total_win / total_loss if total_loss > 0 else float('inf')

    # Sharpe (simplified: daily returns from trade PnLs)
    if positions:
        pnls = [p.get("pnl", 0) for p in positions]
        avg_pnl = sum(pnls) / len(pnls)
        variance = sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls) if len(pnls) > 1 else 0
        std_pnl = variance ** 0.5
        sharpe = (avg_pnl / std_pnl) * (len(pnls) ** 0.5) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown
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
        "asset": asset, "window": window, "trades": len(positions),
        "win_count": win_count, "loss_count": loss_count,
        "win_rate": win_rate, "profit_factor": profit_factor,
        "sharpe": sharpe, "total_pnl": total_pnl,
        "final_balance": balance, "max_drawdown": max_dd,
        "exit_reasons": exit_reasons,
        "config": {
            "entry_zscore": config.entry_zscore,
            "exit_zscore": config.exit_zscore,
            "stop_loss_pct": config.stop_loss_pct,
            "ma_window": config.ma_window,
        },
    }


def print_result(r):
    print(f"\n{'═' * 60}")
    print(f"  MEAN REVERSION BACKTEST — {r['asset']} {r['window']}")
    print(f"{'═' * 60}")
    print(f"  Config: entry_z={r['config']['entry_zscore']} exit_z={r['config']['exit_zscore']} "
          f"stop_loss={r['config']['stop_loss_pct']:.0%} ma={r['config']['ma_window']}")
    print(f"  Final Balance: ${r['final_balance']:.2f} USDC")
    print(f"  Total PnL:     {r['total_pnl']:+.4f} USDC ({r['total_pnl']/1000*100:+.2f}%)")
    print(f"  Trades:        {r['trades']} ({r['win_count']}W / {r['loss_count']}L)")
    print(f"  Win Rate:      {r['win_rate']:.1%}")
    print(f"  Profit Factor: {r['profit_factor']:.2f}")
    print(f"  Sharpe:        {r['sharpe']:.3f}")
    print(f"  Max Drawdown:  {r['max_drawdown']:.2%}")
    print(f"  Exit reasons:  {r['exit_reasons']}")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    import sys

    asset = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    window = sys.argv[2] if len(sys.argv) > 2 else "5m"

    # Default config — Mean Reversion optimized for mean-reverting data
    config = MeanReversionConfig(
        entry_zscore=-1.5,
        exit_zscore=0.0,
        stop_loss_pct=0.10,
        ma_window=20,
        position_size_pusd=10.0,
    )
    r = run_mr_backtest(asset, window, n_ticks=5000, config=config)
    print_result(r)
