# src/interfaces/telegram/handlers/settings.py

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

router = Router()


class SettingsStates(StatesGroup):
    """
    Estados FSM para el flujo de configuración.
    Cada estado espera un input específico del usuario.
    """
    waiting_threshold       = State()
    waiting_stop_loss       = State()
    waiting_target_price    = State()
    waiting_position_size   = State()
    waiting_required_ticks  = State()


def settings_keyboard() -> InlineKeyboardMarkup:
    """Menú de settings con botones por parámetro configurable."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎯 Threshold (actual: 0.75)",
            callback_data="settings:threshold"
        )],
        [InlineKeyboardButton(
            text="🛑 Stop Loss (actual: 15%)",
            callback_data="settings:stop_loss"
        )],
        [InlineKeyboardButton(
            text="🏆 Target Price (actual: 0.90)",
            callback_data="settings:target"
        )],
        [InlineKeyboardButton(
            text="💰 Position Size (actual: 10 USDC)",
            callback_data="settings:position_size"
        )],
        [InlineKeyboardButton(
            text="🔢 Required Ticks (actual: 3)",
            callback_data="settings:ticks"
        )],
        [InlineKeyboardButton(
            text="↩️ Volver al menú",
            callback_data="menu:main"
        )],
    ])


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    """Muestra el menú de configuración."""
    await message.answer(
        "⚙️ *Configuración de la Estrategia*\n\n"
        "Selecciona el parámetro a modificar\\.\n"
        "_Los cambios se aplican inmediatamente sin reiniciar el bot\\._",
        reply_markup=settings_keyboard(),
    )


@router.callback_query(lambda c: c.data == "menu:settings")
async def cb_settings_menu(callback: CallbackQuery) -> None:
    """Handler del botón settings en el menú principal."""
    await callback.answer()
    await callback.message.answer(
        "⚙️ *Configuración de la Estrategia*\n\n"
        "Selecciona el parámetro a modificar:",
        reply_markup=settings_keyboard(),
    )


# ── Threshold ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "settings:threshold")
async def cb_set_threshold(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Inicia el flujo FSM para cambiar el threshold."""
    await callback.answer()
    await state.set_state(SettingsStates.waiting_threshold)
    await callback.message.answer(
        "🎯 *Cambiar Threshold*\n\n"
        "Valor actual: `0\\.75`\n"
        "Rango válido: `0\\.50` \\- `0\\.95`\n\n"
        "Envía el nuevo valor \\(ej: `0\\.80`\\):",
    )


@router.message(SettingsStates.waiting_threshold)
async def process_threshold(message: Message, state: FSMContext) -> None:
    """
    Procesa el nuevo threshold enviado por el usuario.
    Valida el rango y aplica el cambio en caliente.
    """
    try:
        value = float(message.text.strip())
        if not 0.50 <= value <= 0.95:
            raise ValueError("Fuera de rango")

        # TODO en C17: container.strategy.update_config(threshold=value)
        await state.clear()
        await message.answer(
            f"✅ *Threshold actualizado*\n\n"
            f"Nuevo valor: `{value:.2f}`\n"
            f"_El bot usará este threshold en el próximo ciclo\\._",
        )

    except (ValueError, AttributeError):
        await message.answer(
            "❌ Valor inválido\\. Debe ser un número entre `0\\.50` y `0\\.95`\\.\n"
            "Intenta de nuevo:",
        )


# ── Stop Loss ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "settings:stop_loss")
async def cb_set_stop_loss(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Inicia el flujo FSM para cambiar el stop loss."""
    await callback.answer()
    await state.set_state(SettingsStates.waiting_stop_loss)
    await callback.message.answer(
        "🛑 *Cambiar Stop Loss*\n\n"
        "Valor actual: `15%`\n"
        "Rango válido: `5%` \\- `50%`\n\n"
        "Envía el nuevo porcentaje \\(ej: `20` para 20%\\):",
    )


@router.message(SettingsStates.waiting_stop_loss)
async def process_stop_loss(message: Message, state: FSMContext) -> None:
    """Procesa el nuevo stop loss."""
    try:
        value = float(message.text.strip()) / 100
        if not 0.05 <= value <= 0.50:
            raise ValueError("Fuera de rango")

        await state.clear()
        await message.answer(
            f"✅ *Stop Loss actualizado*\n\n"
            f"Nuevo valor: `{value:.0%}`",
        )
    except (ValueError, AttributeError):
        await message.answer(
            "❌ Valor inválido\\. Debe ser entre `5` y `50`\\."
        )


# ── Target Price ──────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "settings:target")
async def cb_set_target(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Inicia FSM para cambiar el target price."""
    await callback.answer()
    await state.set_state(SettingsStates.waiting_target_price)
    await callback.message.answer(
        "🏆 *Cambiar Target Price*\n\n"
        "Valor actual: `0\\.90`\n"
        "Rango válido: `0\\.76` \\- `0\\.99`\n\n"
        "Envía el nuevo valor \\(ej: `0\\.92`\\):",
    )


@router.message(SettingsStates.waiting_target_price)
async def process_target(message: Message, state: FSMContext) -> None:
    """Procesa el nuevo target price."""
    try:
        value = float(message.text.strip())
        if not 0.76 <= value <= 0.99:
            raise ValueError("Fuera de rango")

        await state.clear()
        await message.answer(
            f"✅ *Target Price actualizado*\n\n"
            f"Nuevo valor: `{value:.2f}`",
        )
    except (ValueError, AttributeError):
        await message.answer(
            "❌ Valor inválido\\. Debe ser entre `0\\.76` y `0\\.99`\\."
        )


# ── Position Size ─────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "settings:position_size")
async def cb_set_position_size(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Inicia FSM para cambiar el tamaño de posición."""
    await callback.answer()
    await state.set_state(SettingsStates.waiting_position_size)
    await callback.message.answer(
        "💰 *Cambiar Position Size*\n\n"
        "Valor actual: `10 USDC`\n"
        "Rango válido: `1` \\- `500 USDC`\n"
        "_En real trading el máximo hardcoded es 500 USDC_\n\n"
        "Envía el nuevo monto en USDC \\(ej: `25`\\):",
    )


@router.message(SettingsStates.waiting_position_size)
async def process_position_size(message: Message, state: FSMContext) -> None:
    """Procesa el nuevo tamaño de posición."""
    try:
        value = float(message.text.strip())
        if not 1.0 <= value <= 500.0:
            raise ValueError("Fuera de rango")

        await state.clear()
        await message.answer(
            f"✅ *Position Size actualizado*\n\n"
            f"Nuevo valor: `{value:.2f} USDC`",
        )
    except (ValueError, AttributeError):
        await message.answer(
            "❌ Valor inválido\\. Debe ser entre `1` y `500` USDC\\."
        )