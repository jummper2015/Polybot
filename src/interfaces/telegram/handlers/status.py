# src/interfaces/telegram/handlers/status.py

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

router = Router()


async def send_status(message: Message, container=None) -> None:
    """
    Función reutilizable que envía el estado del bot.
    Llamada desde /status y desde el menú principal.
    """
    # En C17 conectaremos el container real
    # Por ahora muestra estado mock para que funcione el skeleton
    status_text = (
        "📊 *Estado del Bot*\n\n"
        "🟢 Estado: *Corriendo*\n"
        "💰 Modo: *Paper Trading*\n"
        "📈 Mercados activos: `4`\n"
        "💼 Posiciones abiertas: `0`\n"
        "💵 Balance: `1000\\.00 USDC`\n"
        "📉 PnL hoy: `\\+0\\.00 USDC \\(0\\.00%\\)`\n\n"
        "_Actualizado hace 0s_"
    )
    await message.answer(status_text)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Handler para el comando /status."""
    await send_status(message)


@router.callback_query(lambda c: c.data == "menu:status")
async def cb_status(callback: CallbackQuery) -> None:
    """Handler para el botón de status en el menú."""
    await callback.answer()
    await send_status(callback.message)