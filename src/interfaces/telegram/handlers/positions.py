# src/interfaces/telegram/handlers/positions.py

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

router = Router()
logger = structlog.get_logger(__name__)


def _escape(text: str) -> str:
    """Escapa caracteres especiales para MarkdownV2 de Telegram."""
    special = r"\\.*[]()~`>#+-=|{}.!"
    return "".join(f"\\.{c}" if c in special else c for c in str(text))


def format_position(pos) -> str:
    """
    Formatea una Position entity como texto Markdown V2 para Telegram.
    """
    pnl = pos.pnl or 0
    pnl_pct = pos.pnl_pct or 0
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    sign = "\\.+" if pnl >= 0 else ""
    status = "🟢 Abierta" if pos.closed_at is None else "🔴 Cerrada"
    mode_emoji = "🔴 REAL" if getattr(pos, 'mode', 'paper') == 'real' else "📋 PAPER"

    return (
        f"*{_escape(pos.asset)} {_escape(pos.window)}* \\.- {_escape(pos.side)}\\."
        f"  {status}\\."
        f"  Entrada: `{pos.entry_price:.4f}` USDC\\."
        f"  Monto: `{pos.amount:.2f}` USDC\\."
        f"  {pnl_emoji} PnL: `{sign}{pnl:.4f}` USDC "
        f"\\.(`{sign}{pnl_pct:.2%}`\\.)\\."
        f"  Modo: {mode_emoji}\\."
    )


async def get_positions_data(container) -> dict:
    """Obtiene datos reales de posiciones desde el container."""
    try:
        open_positions = await container.repository.get_positions(open_only=True)
        all_positions = await container.repository.get_positions()
        total_pnl = await container.repository.get_total_pnl()
        balance = await container.portfolio_service.get_balance()
    except Exception:
        open_positions = []
        all_positions = []
        total_pnl = 0.0
        balance = 0.0

    # Calcula win rate y stats
    closed = [p for p in all_positions if p.closed_at is not None]
    winners = [p for p in closed if (p.pnl or 0) > 0]
    losers  = [p for p in closed if (p.pnl or 0) <= 0]
    win_rate = len(winners) / len(closed) if closed else 0.0

    # Calcula drawdown (PnL mínimo acumulado)
    drawdown = 0.0
    cumulative = 0.0
    for p in sorted(closed, key=lambda x: x.closed_at or x.opened_at):
        cumulative += (p.pnl or 0)
        drawdown = min(drawdown, cumulative)

    return {
        "open_positions": open_positions,
        "all_positions": all_positions,
        "total_pnl": total_pnl,
        "balance": balance,
        "win_rate": win_rate,
        "winners_count": len(winners),
        "losers_count": len(losers),
        "drawdown": drawdown,
        "total_trades": len(closed),
    }


@router.message(Command("positions"))
async def cmd_positions(message: Message, container=None) -> None:
    """
    Handler para /positions.
    Muestra posiciones abiertas y resumen de PnL.
    """
    if container is not None:
        try:
            data = await get_positions_data(container)
            open_pos = data["open_positions"]

            if open_pos:
                lines = []
                for pos in open_pos:
                    lines.append(format_position(pos))
                body = "\\.".join(lines)
            else:
                body = "_No hay posiciones abiertas actualmente\\.._"

            total_pnl = data["total_pnl"]
            sign = "\\.+" if total_pnl >= 0 else ""

            text = (
                f"💼 *Posiciones Activas* ({len(open_pos)})\\.\\."
                f"{body}\\.\\."
                f"💵 Balance: `{data['balance']:.2f} USDC`\\."
                f"📊 PnL Total: `{sign}{total_pnl:.4f} USDC`"
            )
            await message.answer(text)
            return
        except Exception as e:
            logger.warning("positions_real_data_failed", error=str(e))

    # Fallback
    await message.answer(
        "💼 *Posiciones Activas*\\.\\."
        "_No hay posiciones abiertas actualmente\\.._\\.\\."
        "💵 PnL Total: `\\.+0\\..00 USDC`",
    )


@router.callback_query(lambda c: c.data == "menu:positions")
async def cb_positions(callback: CallbackQuery, container=None) -> None:
    """Handler para el botón de posiciones."""
    await callback.answer()
    await cmd_positions(callback.message, container=container)


@router.callback_query(lambda c: c.data == "menu:pnl")
async def cb_pnl(callback: CallbackQuery, container=None) -> None:
    """Muestra resumen detallado de PnL."""
    await callback.answer()

    if container is not None:
        try:
            data = await get_positions_data(container)
            total_pnl = data["total_pnl"]
            sign = "\\.+" if total_pnl >= 0 else ""
            has_trades = (data['winners_count'] + data['losers_count']) > 0
            wl = f"`{data['winners_count']}W/{data['losers_count']}L`" if has_trades else "`\\.-`"
            wr_text = f"`{data['win_rate']:.1%}`" if data['total_trades'] > 0 else "`\\.-`"

            text = (
                f"📈 *Resumen PnL*\\.\\."
                f"💰 Balance actual: `{data['balance']:.2f} USDC`\\."
                f"📊 PnL total: `{sign}{total_pnl:.4f} USDC`\\."
                f"🔢 Trades: `{data['total_trades']}` {wl}\\."
                f"📉 Max drawdown: `{data['drawdown']:.4f} USDC`\\."
                f"🎯 Win rate: {wr_text}\\."
            )
            await callback.message.answer(text)
            return
        except Exception as e:
            logger.warning("pnl_real_data_failed", error=str(e))

    # Fallback
    await callback.message.answer(
        "📈 *Resumen PnL*\\.\\."
        "💰 Balance actual: `1000\\..00 USDC`\\."
        "📊 PnL total: `\\.+0\\.00 USDC \\.(0\\.00%\\.)`\\."
        "✅ Trades ganadores: `0`\\."
        "❌ Trades perdedores: `0`\\."
        "📉 Max drawdown: `0\\.00%`\\."
        "🎯 Win rate: `\\.-`\\."
    )
