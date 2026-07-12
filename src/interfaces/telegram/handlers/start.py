# src/interfaces/telegram/handlers/start.py
# mypy: disable-error-code="union-attr,arg-type"

import structlog
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.interfaces.telegram.pin_gate import PinGate, PinResult

router = Router()
logger = structlog.get_logger(__name__)


# Ola 2.2: PinGate global — Capa 2 de las 3 capas de confirmación real.
# Inicializado al import; el módulo lee REAL_MODE_PIN_HASH del entorno.
# En tests se sustituye con monkeypatch de `_pin_gate`.
_pin_gate: PinGate = PinGate.from_env()


class RealModeStates(StatesGroup):
    """Ola 2.2: FSM para el flujo de confirmación con PIN."""
    waiting_pin = State()


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
async def cb_status(callback: CallbackQuery, container=None) -> None:
    """Redirige al handler de status."""
    await callback.answer()
    from src.interfaces.telegram.handlers.status import send_status
    await send_status(callback.message, container=container)


@router.callback_query(lambda c: c.data == "bot:start")
async def cb_bot_start(callback: CallbackQuery, container=None) -> None:
    """Inicia el bot de trading vía container."""
    await callback.answer("Iniciando bot...")
    if container is not None:
        success, msg = await container.start_bot()
        if success:
            await callback.message.answer(
                f"▶️ *Bot iniciado*\n\n{msg}\n_Comenzando ciclos de trading\\._"
            )
        else:
            await callback.message.answer(f"⚠️ {msg}")
    else:
        await callback.message.answer(
            "▶️ *Bot iniciado* \\- comenzando ciclos de trading\\."
        )


@router.callback_query(lambda c: c.data == "bot:stop")
async def cb_bot_stop(callback: CallbackQuery, container=None) -> None:
    """Detiene el bot de trading vía container."""
    await callback.answer("Deteniendo bot...")
    if container is not None:
        success, msg = await container.stop_bot()
        if success:
            await callback.message.answer(
                f"⏹ *Bot detenido*\n\n{msg}"
            )
        else:
            await callback.message.answer(f"⚠️ {msg}")
    else:
        await callback.message.answer("⏹ *Bot detenido*\\.")


@router.callback_query(lambda c: c.data == "bot:enable_real")
async def cb_enable_real(callback: CallbackQuery, container=None) -> None:
    """
    Primer paso de confirmación para activar real trading.
    Muestra advertencia y pide confirmación explícita.
    """
    await callback.answer()

    if container is not None and container.trading_mode == "real":
        await callback.message.answer(
            "🔴 *El bot ya está en modo REAL*\\.\n"
            "No es necesario activarlo de nuevo\\."
        )
        return

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
async def cb_real_confirm_step1(callback: CallbackQuery, state: FSMContext) -> None:
    """
    Ola 2.2: paso PIN (Capa 2 de las 3 capas). En vez de saltar
    directamente a la confirmación final, pide el PIN de 6 dígitos
    y establece el estado FSM `waiting_pin`. El siguiente Message
    del chat_id se interpreta como el PIN.
    """
    await callback.answer()

    if not _pin_gate.is_configured():
        # Sin PIN configurado, el paper-vs-real-execution skill dice
        # rechazar. No permitimos "solo dos botones" como en pre-Ola 2.2.
        await callback.message.answer(
            "❌ *Real trading no configurado*\\.\n\n"
            "Falta la variable `REAL_MODE_PIN_HASH` \\(o `REAL_MODE_PIN`\\) "
            "en el entorno\\. Configúrala y reinicia el bot antes de "
            "activar real trading\\."
        )
        return

    chat_id = callback.message.chat.id
    if _pin_gate.is_locked(chat_id):
        wait = _pin_gate.seconds_until_unlock(chat_id)
        await callback.message.answer(
            f"🔒 *Bloqueado por rate limit*\\.\n\n"
            f"Demasiados intentos fallidos\\. Vuelve a intentar en "
            f"{wait // 60}min {wait % 60}s\\."
        )
        return

    await state.set_state(RealModeStates.waiting_pin)
    await callback.message.answer(
        "🔐 *Introduce el PIN de 6 dígitos*\n\n"
        "Envía el PIN como un mensaje normal\\.\n"
        "Tienes 3 intentos antes del bloqueo de 10 min\\.\n\n"
        "Escribe `/cancel` para abortar\\."
    )


@router.message(RealModeStates.waiting_pin)
async def on_pin_message(message: Message, state: FSMContext, container=None) -> None:
    """
    Ola 2.2: recibe el PIN tipeado por el usuario y lo valida
    contra `_pin_gate`. Éxito → activa real trading. Fallo → mensaje
    y sigue en `waiting_pin` hasta agotar intentos o `/cancel`.
    """
    pin = (message.text or "").strip()
    chat_id = message.chat.id

    # Escape hatch — el usuario puede cancelar en cualquier momento.
    if pin.lower() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer("❌ Activación de Real Trading *cancelada*\\.")
        return

    result = _pin_gate.verify(chat_id=chat_id, pin=pin)

    if result == PinResult.OK:
        await state.clear()
        if container is not None:
            success, msg = await container.enable_real_mode()
            if success:
                await message.answer(
                    "🔴 *Real Trading ACTIVADO*\n\n"
                    "PIN verificado\\. El bot ahora opera con fondos reales\\.\n"
                    f"_{msg}_"
                )
            else:
                await message.answer(
                    f"❌ *Error al activar Real Trading*\n\n{msg}"
                )
        else:
            await message.answer(
                "🔴 *Real Trading ACTIVADO*\n\n"
                "PIN verificado\\."
            )
        return

    if result == PinResult.INVALID_FORMAT:
        await message.answer(
            "⚠️ Formato inválido\\. El PIN debe ser exactamente 6 dígitos\\.\n"
            "Vuelve a intentarlo o escribe `/cancel`\\."
        )
        return

    if result == PinResult.LOCKED_OUT:
        wait = _pin_gate.seconds_until_unlock(chat_id)
        await state.clear()
        await message.answer(
            f"🔒 *Bloqueado por rate limit*\\.\n\n"
            f"Vuelve a intentar en {wait // 60}min {wait % 60}s\\."
        )
        return

    if result == PinResult.WRONG:
        # Chequea si este WRONG desencadenó lockout (contador ≥ max).
        wait = _pin_gate.seconds_until_unlock(chat_id)
        if wait > 0:
            await state.clear()
            await message.answer(
                f"🔒 *3 intentos fallidos*\\.\n\n"
                f"Bloqueado por {wait // 60}min {wait % 60}s\\."
            )
        else:
            await message.answer(
                "❌ PIN incorrecto\\. Intenta de nuevo o escribe `/cancel`\\."
            )
        return

    # NOT_CONFIGURED — no debería llegar aquí porque step1 ya lo verificó,
    # pero defensa en profundidad.
    await state.clear()
    await message.answer("❌ Real trading no configurado\\.")


@router.callback_query(lambda c: c.data == "real:confirm_final")
async def cb_real_confirm_final(callback: CallbackQuery, container=None) -> None:
    """Activa real trading después de doble confirmación."""
    await callback.answer()

    if container is not None:
        success, msg = await container.enable_real_mode()
        if success:
            await callback.message.answer(
                "🔴 *Real Trading ACTIVADO*\n\n"
                "El bot ahora opera con fondos reales\\.\n"
                "Recibirás alertas de cada operación\\.\n\n"
                f"_{msg}_"
            )
        else:
            await callback.message.answer(
                f"❌ *Error al activar Real Trading*\n\n{msg}"
            )
    else:
        await callback.message.answer(
            "🔴 *Real Trading ACTIVADO*\n\n"
            "El bot ahora opera con fondos reales\\.\n"
            "Recibirás alertas de cada operación\\.",
        )


@router.callback_query(lambda c: c.data == "real:cancel")
async def cb_real_cancel(callback: CallbackQuery) -> None:
    """Cancela la activación de real trading."""
    await callback.answer("Cancelado")
    await callback.message.answer("❌ Activación de Real Trading *cancelada*\\.")
