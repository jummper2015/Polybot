# src/interfaces/telegram/handlers/start.py

from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

router = Router()


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Teclado principal del bot.
    Todos los comandos accesibles desde botones inline.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Estado",      callback_data="menu:status"),
            InlineKeyboardButton(text="💼 Posiciones",  callback_data="menu:positions"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings",    callback_data="menu:settings"),
            InlineKeyboardButton(text="📈 PnL",         callback_data="menu:pnl"),
        ],
        [
            InlineKeyboardButton(text="▶️ Iniciar Bot", callback_data="bot:start"),
            InlineKeyboardButton(text="⏹ Detener Bot", callback_data="bot:stop"),
        ],
        [
            InlineKeyboardButton(text="🔴 Modo REAL",   callback_data="bot:enable_real"),
        ],
    ])


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """
    Handler para /start.
    Muestra el menú principal con el estado actual del bot.
    """
    await message.answer(
        "🤖 *Polymarket Trading Bot*\n\n"
        "Bot algorítmico para mercados BTC/ETH\\.\n"
        "Selecciona una opción:",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(lambda c: c.data == "menu:status")
async def cb_status(callback: CallbackQuery) -> None:
    """Redirige al handler de status."""
    await callback.answer()
    # Importación diferida para evitar circular imports
    from src.interfaces.telegram.handlers.status import send_status
    await send_status(callback.message)


@router.callback_query(lambda c: c.data == "bot:start")
async def cb_bot_start(callback: CallbackQuery, container=None) -> None:
    """Inicia el bot de trading."""
    await callback.answer("Iniciando bot...")
    await callback.message.answer("▶️ *Bot iniciado* \\- comenzando ciclos de trading\\.")


@router.callback_query(lambda c: c.data == "bot:stop")
async def cb_bot_stop(callback: CallbackQuery) -> None:
    """Detiene el bot de trading."""
    await callback.answer("Deteniendo bot...")
    await callback.message.answer("⏹ *Bot detenido*\\.")


@router.callback_query(lambda c: c.data == "bot:enable_real")
async def cb_enable_real(callback: CallbackQuery) -> None:
    """
    Primer paso de confirmación para activar real trading.
    Muestra advertencia y pide confirmación explícita.
    """
    await callback.answer()
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⚠️ SÍ, activar REAL trading",
                callback_data="real:confirm_step1"
            ),
        ],
        [
            InlineKeyboardButton(
                text="❌ Cancelar",
                callback_data="real:cancel"
            ),
        ],
    ])
    await callback.message.answer(
        "⚠️ *ADVERTENCIA: Real Trading*\n\n"
        "Estás a punto de activar el modo de trading REAL\\.\n"
        "Esto usará *fondos reales* de tu wallet en Polygon\\.\n\n"
        "• Asegúrate de tener USDC en tu wallet\n"
        "• El bot operará automáticamente\n"
        "• Máx\\. 500 USDC por orden \\(hardcoded\\)\n\n"
        "¿Confirmas que quieres activar Real Trading?",
        reply_markup=confirm_keyboard,
    )


@router.callback_query(lambda c: c.data == "real:confirm_step1")
async def cb_real_confirm_step1(callback: CallbackQuery) -> None:
    """Segundo paso de confirmación — aún más explícito."""
    await callback.answer()
    final_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ CONFIRMO — Activar Real Trading",
                callback_data="real:confirm_final"
            ),
        ],
        [
            InlineKeyboardButton(
                text="❌ Cancelar",
                callback_data="real:cancel"
            ),
        ],
    ])
    await callback.message.answer(
        "🔴 *CONFIRMACIÓN FINAL*\n\n"
        "Esta es tu última oportunidad de cancelar\\.\n"
        "Al confirmar, el bot empezará a usar *dinero real*\\.\n\n"
        "Escribe /confirmar\\_real o presiona el botón:",
        reply_markup=final_keyboard,
    )


@router.callback_query(lambda c: c.data == "real:confirm_final")
async def cb_real_confirm_final(callback: CallbackQuery) -> None:
    """Activa real trading después de doble confirmación."""
    await callback.answer("✅ Real Trading activado")
    await callback.message.answer(
        "🔴 *Real Trading ACTIVADO*\n\n"
        "El bot ahora opera con fondos reales\\.\n"
        "Recibirás alertas de cada operación\\.",
    )
    # TODO en C17: llamar a container.trading_service.enable_real_mode()


@router.callback_query(lambda c: c.data == "real:cancel")
async def cb_real_cancel(callback: CallbackQuery) -> None:
    """Cancela la activación de real trading."""
    await callback.answer("Cancelado")
    await callback.message.answer("❌ Activación de Real Trading *cancelada*\\.")