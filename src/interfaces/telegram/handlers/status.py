# src/interfaces/telegram/handlers/status.py
# mypy: disable-error-code="union-attr,arg-type"

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

router = Router()
logger = structlog.get_logger(__name__)


def _escape(text: str) -> str:
    """Escapa caracteres especiales para MarkdownV2 de Telegram."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


async def send_status(message: Message, container=None) -> None:  # type: ignore[arg-type]  # aiogram typing: callback.message may be InaccessibleMessage

    """
    Función reutilizable que envía el estado del bot.
    Llamada desde /status y desde el menú principal.
    Si el container está disponible (vía middleware), usa datos reales.
    """
    if container is not None:
        try:
            data = await container.get_bot_status()
            running_emoji = "🟢" if data["running"] else "🔴"
            running_text  = "Corriendo" if data["running"] else "Detenido"
            mode_text     = "REAL 🔴" if data["mode"] == "real" else "Paper 📋"

            # Formatea uptime
            uptime_s = data.get("uptime_seconds", 0)
            if uptime_s < 60:
                uptime_text = f"{int(uptime_s)}s"
            elif uptime_s < 3600:
                uptime_text = f"{int(uptime_s / 60)}m"
            else:
                h = int(uptime_s / 3600)
                m = int((uptime_s % 3600) / 60)
                uptime_text = f"{h}h {m}m"

            pnl = data["pnl"]
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            sign = "\\+" if pnl >= 0 else ""

            status_text = (
                f"📊 *Estado del Bot*\n\n"
                f"{running_emoji} Estado: *{running_text}*\n"
                f"💰 Modo: *{mode_text}*\n"
                f"📈 Mercados activos: `{data['active_markets']}`\n"
                f"💼 Posiciones abiertas: `{data['open_positions']}`\n"
                f"💵 Balance: `{data['balance']:.2f} USDC`\n"
                f"{pnl_emoji} PnL: `{sign}{pnl:.4f} USDC`\n\n"
                f"⏱ Uptime: `{uptime_text}`\n"
            )
            await message.answer(status_text)
            return
        except Exception as e:
            logger.warning("status_real_data_failed", error=str(e))

    # Fallback: mock data (cuando container no está disponible)
    status_text = (
        "📊 *Estado del Bot*\n\n"
        "🟢 Estado: *Corriendo*\n"
        "💰 Modo: *Paper Trading*\n"
        "📈 Mercados activos: `4`\n"
        "💼 Posiciones abiertas: `0`\n"
        "💵 Balance: `1000\\.00 USDC`\n"
        "📉 PnL: `\\+0\\.00 USDC`\n\n"
        "_Container no disponible\\. Usando datos mock\\._"
    )
    await message.answer(status_text)


@router.message(Command("status"))
async def cmd_status(message: Message, container=None) -> None:
    """Handler para el comando /status."""
    await send_status(message, container=container)


@router.callback_query(lambda c: c.data == "menu:status")
async def cb_status(callback: CallbackQuery, container=None) -> None:
    """Handler para el botón de status en el menú."""
    await callback.answer()
    await send_status(callback.message, container=container)
