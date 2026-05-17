# src/interfaces/telegram/handlers/positions.py

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

router = Router()


def format_position(pos: dict) -> str:
    """
    Formatea una posición como texto Markdown V2 para Telegram.
    Escapa caracteres especiales requeridos por MarkdownV2.
    """
    pnl_emoji = "📈" if (pos.get("pnl") or 0) >= 0 else "📉"
    pnl_val   = pos.get("pnl", 0) or 0
    pnl_pct   = pos.get("pnl_pct", 0) or 0
    status    = "🟢 Abierta" if pos.get("is_open") else "🔴 Cerrada"

    return (
        f"*{pos['asset']} {pos['window']}* \\- {pos['side']}\n"
        f"  {status}\n"
        f"  Entrada: `{pos['entry_price']:.4f}` USDC\n"
        f"  Monto: `{pos['amount']:.2f}` USDC\n"
        f"  {pnl_emoji} PnL: `{pnl_val:+.4f}` USDC "
        f"\\(`{pnl_pct:+.2%}`\\)\n"
        f"  Modo: `{pos['mode']}`\n"
    )


@router.message(Command("positions"))
async def cmd_positions(message: Message) -> None:
    """
    Handler para /positions.
    Muestra posiciones abiertas y resumen de PnL.
    """
    # Mock para skeleton — conectar a PortfolioService en C17
    await message.answer(
        "💼 *Posiciones Activas*\n\n"
        "_No hay posiciones abiertas actualmente\\._\n\n"
        "💵 PnL Total: `\\+0\\.00 USDC`",
    )


@router.callback_query(lambda c: c.data == "menu:positions")
async def cb_positions(callback: CallbackQuery) -> None:
    """Handler para el botón de posiciones."""
    await callback.answer()
    await cmd_positions(callback.message)


@router.callback_query(lambda c: c.data == "menu:pnl")
async def cb_pnl(callback: CallbackQuery) -> None:
    """Muestra resumen detallado de PnL."""
    await callback.answer()
    await callback.message.answer(
        "📈 *Resumen PnL*\n\n"
        "💰 Balance actual: `1000\\.00 USDC`\n"
        "📊 PnL total: `\\+0\\.00 USDC \\(0\\.00%\\)`\n"
        "✅ Trades ganadores: `0`\n"
        "❌ Trades perdedores: `0`\n"
        "📉 Max drawdown: `0\\.00%`\n"
        "🎯 Win rate: `\\-`\n"
    )