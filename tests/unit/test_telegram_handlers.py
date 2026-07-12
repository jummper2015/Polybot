# tests/unit/test_telegram_handlers.py
"""R1.5 — Tests unit de los 5 handlers de Telegram (aiogram).

Cubre TelegramNotifier (alerts), positions, settings, start, status.
Sin levantar Bot real: mocks puros (`spec=Bot/Message/CallbackQuery`).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from src.interfaces.telegram.handlers.alerts import TelegramNotifier
from src.interfaces.telegram.handlers.positions import (
    cb_pnl,
    cb_positions,
    cmd_positions,
    format_position,
    get_positions_data,
)
from src.interfaces.telegram.handlers.settings import (
    SettingsStates,
    cb_set_position_size,
    cb_set_stop_loss,
    cb_set_target,
    cb_set_threshold,
    cb_settings_menu,
    cmd_settings,
    process_position_size,
    process_stop_loss,
    process_target,
    process_threshold,
    settings_keyboard,
)
from src.interfaces.telegram.handlers.start import (
    cb_bot_start,
    cb_bot_stop,
    cb_enable_real,
    cb_real_cancel,
    cb_real_confirm_final,
    cb_real_confirm_step1,
    cmd_start,
    main_menu_keyboard,
)
from src.interfaces.telegram.handlers.start import (
    cb_status as cb_status_start,
)
from src.interfaces.telegram.handlers.status import cmd_status, send_status

# ── Fixtures compartidos ──────────────────────────────────────────────


@pytest.fixture
def bot():
    b = MagicMock(spec=Bot)
    b.send_message = AsyncMock()
    return b


@pytest.fixture
def message():
    m = MagicMock(spec=Message)
    m.answer = AsyncMock()
    m.from_user = MagicMock(id=12345)
    m.text = ""
    return m


@pytest.fixture
def callback():
    cb = MagicMock(spec=CallbackQuery)
    cb.answer = AsyncMock()
    cb.message = MagicMock(spec=Message)
    cb.message.answer = AsyncMock()
    cb.data = ""
    return cb


@pytest.fixture
def fsm_state():
    """FSMContext mock para flujos de settings."""
    s = MagicMock()
    s.set_state = AsyncMock()
    s.clear = AsyncMock()
    return s


@pytest.fixture
def status_payload():
    """Payload típico de container.get_bot_status."""
    return {
        "running": True,
        "mode": "paper",
        "uptime_seconds": 3725,  # 1h 2m
        "active_markets": 4,
        "open_positions": 2,
        "balance": 1050.50,
        "pnl": 50.50,
    }


@pytest.fixture
def container(status_payload):
    """Container con respuestas asíncronas configurables."""
    c = MagicMock()
    c.trading_mode = "paper"
    c.get_bot_status = AsyncMock(return_value=status_payload)
    c.start_bot = AsyncMock(return_value=(True, "Bot started"))
    c.stop_bot = AsyncMock(return_value=(True, "Bot stopped"))
    c.enable_real_mode = AsyncMock(return_value=(True, "Real mode enabled"))
    c.update_bat_setting = AsyncMock(return_value=(True, "Updated"))
    return c


def _mk_position(asset="BTC", window="5m", side="YES", pnl=None,
                 pnl_pct=None, closed=False, mode="paper", amount=10.0,
                 entry_price=0.55):
    """Position-like mock para alerts/positions."""
    p = MagicMock()
    p.asset = asset
    p.window = window
    p.side = side
    p.amount = amount
    p.entry_price = entry_price
    p.exit_price = 0.60 if closed else None
    p.pnl = pnl
    p.pnl_pct = pnl_pct
    p.mode = mode
    p.opened_at = datetime(2026, 6, 14, 11, tzinfo=timezone.utc)
    p.closed_at = datetime(2026, 6, 14, 12, tzinfo=timezone.utc) if closed else None
    return p


# ══════════════════════════════════════════════════════════════════════
# TelegramNotifier (alerts.py)
# ══════════════════════════════════════════════════════════════════════


class TestTelegramNotifier:

    @pytest.mark.asyncio
    async def test_send_trade_alert_paper_yes(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        await notifier.send_trade_alert(
            market_id="0xabc1234567890",
            side="YES",
            amount=10.5,
            price=0.55,
            mode="paper",
        )
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.call_args.kwargs
        assert kwargs["chat_id"] == 42
        assert "PAPER" in kwargs["text"]
        assert "YES" in kwargs["text"]
        assert "10.50" in kwargs["text"]

    @pytest.mark.asyncio
    async def test_send_trade_alert_real_no(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        await notifier.send_trade_alert(
            market_id="x", side="NO", amount=1.0, price=0.45, mode="real",
        )
        text = bot.send_message.call_args.kwargs["text"]
        assert "REAL" in text
        assert "NO" in text

    @pytest.mark.asyncio
    async def test_send_exit_alert_profit_uses_check_emoji(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        await notifier.send_exit_alert(
            market_id="0xabc", reason="take_profit", pnl=5.0, pnl_pct=0.05,
        )
        text = bot.send_message.call_args.kwargs["text"]
        assert "✅" in text
        # MarkdownV2 escapa el underscore — "take_profit" → "take\\_profit"
        assert "take" in text and "profit" in text

    @pytest.mark.asyncio
    async def test_send_exit_alert_loss_uses_cross_emoji(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        await notifier.send_exit_alert(
            market_id="0xabc", reason="stop_loss", pnl=-2.5, pnl_pct=-0.025,
        )
        text = bot.send_message.call_args.kwargs["text"]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_send_risk_alert_includes_rule(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        await notifier.send_risk_alert(
            rule_triggered="MaxDailyDrawdown",
            reason="DD exceeded 10%",
        )
        text = bot.send_message.call_args.kwargs["text"]
        assert "MaxDailyDrawdown" in text
        assert "DD exceeded 10" in text

    @pytest.mark.asyncio
    async def test_send_error_alert_truncates_long_error(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        long_err = "x" * 1000
        await notifier.send_error_alert(error=long_err)
        text = bot.send_message.call_args.kwargs["text"]
        # MarkdownV2 escapes every char with backslashes; el contenido cabe
        # en menos de 1500 caracteres tras el truncamiento a 300.
        assert len(text) < 1500

    @pytest.mark.asyncio
    async def test_send_daily_summary_positive_pnl(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        await notifier.send_daily_summary(
            pnl=10.5, pnl_pct=0.011, trades=8, win_rate=0.625, balance=1010.5,
        )
        text = bot.send_message.call_args.kwargs["text"]
        assert "📈" in text
        assert "8" in text  # número de trades

    @pytest.mark.asyncio
    async def test_send_daily_summary_negative_pnl(self, bot):
        notifier = TelegramNotifier(bot, chat_id=42)
        await notifier.send_daily_summary(
            pnl=-5.0, pnl_pct=-0.005, trades=3, win_rate=0.0, balance=995.0,
        )
        text = bot.send_message.call_args.kwargs["text"]
        assert "📉" in text

    @pytest.mark.asyncio
    async def test_send_swallows_bot_errors(self, bot):
        """Una excepción del bot NO debe propagar."""
        bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))
        notifier = TelegramNotifier(bot, chat_id=42)
        # No debe lanzar
        await notifier.send_error_alert(error="boom")
        bot.send_message.assert_awaited_once()

    def test_escape_special_chars(self):
        # Cada carácter especial se prefija con backslash
        for ch in r"\_*[]()~`>#+-=|{}.!":
            assert TelegramNotifier._escape(ch) == f"\\{ch}"
        # Caracteres normales pasan tal cual
        assert TelegramNotifier._escape("BTC 5m") == "BTC 5m"


# ══════════════════════════════════════════════════════════════════════
# positions.py
# ══════════════════════════════════════════════════════════════════════


class TestPositionsHandler:

    @pytest.mark.asyncio
    async def test_cmd_positions_no_container_fallback(self, message):
        await cmd_positions(message, container=None)
        message.answer.assert_awaited_once()
        text = message.answer.call_args.args[0]
        assert "Posiciones Activas" in text

    @pytest.mark.asyncio
    async def test_cmd_positions_with_container_open(self, message, container):
        open_pos = _mk_position(asset="BTC", side="YES", amount=10.0)
        closed_win = _mk_position(closed=True, pnl=5.0, pnl_pct=0.05)
        closed_loss = _mk_position(closed=True, pnl=-2.0, pnl_pct=-0.02)
        container.repository = MagicMock()
        container.repository.get_positions = AsyncMock(side_effect=[
            [open_pos],                         # open_only=True
            [open_pos, closed_win, closed_loss],  # open_only=False
        ])
        container.repository.get_total_pnl = AsyncMock(return_value=3.0)
        container.portfolio_service = MagicMock()
        container.portfolio_service.get_balance = AsyncMock(return_value=1003.0)

        await cmd_positions(message, container=container)
        message.answer.assert_awaited_once()
        text = message.answer.call_args.args[0]
        assert "Posiciones Activas" in text
        assert "BTC" in text
        assert "1003" in text or "1,003" in text

    @pytest.mark.asyncio
    async def test_cmd_positions_repository_error_falls_back(self, message, container):
        container.repository = MagicMock()
        container.repository.get_positions = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        container.repository.get_total_pnl = AsyncMock(return_value=0.0)
        container.portfolio_service = MagicMock()
        container.portfolio_service.get_balance = AsyncMock(return_value=0.0)

        # No debe lanzar: get_positions_data captura la excepción y devuelve defaults
        await cmd_positions(message, container=container)
        message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cb_positions_delegates(self, callback, container):
        container.repository = MagicMock()
        container.repository.get_positions = AsyncMock(return_value=[])
        container.repository.get_total_pnl = AsyncMock(return_value=0.0)
        container.portfolio_service = MagicMock()
        container.portfolio_service.get_balance = AsyncMock(return_value=1000.0)

        await cb_positions(callback, container=container)
        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_cb_pnl_no_container_fallback(self, callback):
        await cb_pnl(callback, container=None)
        callback.answer.assert_awaited_once()
        text = callback.message.answer.call_args.args[0]
        assert "PnL" in text

    @pytest.mark.asyncio
    async def test_cb_pnl_with_data(self, callback, container):
        win = _mk_position(closed=True, pnl=10.0)
        loss = _mk_position(closed=True, pnl=-4.0)
        container.repository = MagicMock()
        container.repository.get_positions = AsyncMock(side_effect=[
            [], [win, loss]
        ])
        container.repository.get_total_pnl = AsyncMock(return_value=6.0)
        container.portfolio_service = MagicMock()
        container.portfolio_service.get_balance = AsyncMock(return_value=1006.0)

        await cb_pnl(callback, container=container)
        text = callback.message.answer.call_args.args[0]
        assert "Resumen PnL" in text
        assert "1006" in text or "1,006" in text

    @pytest.mark.asyncio
    async def test_cb_pnl_repository_error_falls_back(self, callback, container):
        container.repository = MagicMock()
        # get_positions_data captura el error → falla la rama interna pero
        # cb_pnl también captura → fallback message.
        container.repository.get_positions = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        container.repository.get_total_pnl = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        container.portfolio_service = MagicMock()
        container.portfolio_service.get_balance = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        await cb_pnl(callback, container=container)
        callback.message.answer.assert_awaited()

    def test_format_position_open(self):
        p = _mk_position(asset="ETH", window="15m", side="NO", amount=20.0)
        s = format_position(p)
        assert "ETH" in s
        assert "15m" in s
        assert "Abierta" in s

    def test_format_position_closed_with_pnl(self):
        p = _mk_position(closed=True, pnl=2.5, pnl_pct=0.025)
        s = format_position(p)
        assert "Cerrada" in s
        assert "📈" in s

    @pytest.mark.asyncio
    async def test_get_positions_data_drawdown(self, container):
        win1 = _mk_position(closed=True, pnl=20.0)
        win1.closed_at = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
        loss = _mk_position(closed=True, pnl=-50.0)
        loss.closed_at = datetime(2026, 6, 14, 12, 5, tzinfo=timezone.utc)
        container.repository = MagicMock()
        container.repository.get_positions = AsyncMock(side_effect=[
            [], [win1, loss]
        ])
        container.repository.get_total_pnl = AsyncMock(return_value=-30.0)
        container.portfolio_service = MagicMock()
        container.portfolio_service.get_balance = AsyncMock(return_value=970.0)

        data = await get_positions_data(container)
        assert data["winners_count"] == 1
        assert data["losers_count"] == 1
        assert data["total_trades"] == 2
        # cumulative tras 20 = 20, tras -50 = -30 → drawdown = -30
        assert data["drawdown"] == -30.0
        assert data["win_rate"] == 0.5


# ══════════════════════════════════════════════════════════════════════
# settings.py — FSM + cambios en caliente
# ══════════════════════════════════════════════════════════════════════


class TestSettingsHandler:

    @pytest.mark.asyncio
    async def test_cmd_settings_renders_keyboard(self, message):
        await cmd_settings(message, container=None)
        message.answer.assert_awaited_once()
        kwargs = message.answer.call_args.kwargs
        assert "reply_markup" in kwargs

    @pytest.mark.asyncio
    async def test_cb_settings_menu(self, callback):
        await cb_settings_menu(callback, container=None)
        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited_once()

    def test_settings_keyboard_without_container(self):
        kb = settings_keyboard(container=None)
        # 5 botones + "Volver" = 6 filas con 1 botón cada una.
        assert len(kb.inline_keyboard) == 6

    def test_settings_keyboard_with_container_reads_strategy(self):
        # Stub de BAT strategy con atributo _config
        from src.strategies.buy_above_threshold.strategy import (
            BuyAboveThresholdStrategy,
        )
        bat = BuyAboveThresholdStrategy.__new__(BuyAboveThresholdStrategy)
        bat._config = MagicMock(
            threshold=0.80,
            stop_loss_pct=0.20,
            target_price=0.92,
            position_size_pusd=25.0,
            required_ticks=4,
        )
        container = MagicMock()
        container.strategy_engine._strategies = [bat]
        kb = settings_keyboard(container=container)
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("0.8" in lbl for lbl in labels)
        assert any("25" in lbl for lbl in labels)

    def test_settings_keyboard_container_exception_falls_back(self):
        container = MagicMock()
        # acceder a strategy_engine._strategies lanza
        type(container.strategy_engine).__getattr__ = MagicMock(
            side_effect=AttributeError
        )
        # No debe lanzar; cae a defaults
        kb = settings_keyboard(container=container)
        assert len(kb.inline_keyboard) == 6

    # ── Threshold ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cb_set_threshold_enters_state(self, callback, fsm_state):
        await cb_set_threshold(callback, fsm_state)
        fsm_state.set_state.assert_awaited_once_with(
            SettingsStates.waiting_threshold
        )
        callback.message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_threshold_valid(self, message, fsm_state, container):
        message.text = "0.80"
        await process_threshold(message, fsm_state, container=container)
        container.update_bat_setting.assert_awaited_once_with("threshold", 0.80)
        fsm_state.clear.assert_awaited_once()
        text = message.answer.call_args.args[0]
        assert "actualizado" in text.lower()

    @pytest.mark.asyncio
    async def test_process_threshold_out_of_range(self, message, fsm_state, container):
        message.text = "0.10"
        await process_threshold(message, fsm_state, container=container)
        container.update_bat_setting.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "inválido" in text.lower()

    @pytest.mark.asyncio
    async def test_process_threshold_garbage(self, message, fsm_state, container):
        message.text = "no-es-numero"
        await process_threshold(message, fsm_state, container=container)
        text = message.answer.call_args.args[0]
        assert "inválido" in text.lower()

    @pytest.mark.asyncio
    async def test_process_threshold_container_rejects(self, message, fsm_state, container):
        message.text = "0.80"
        container.update_bat_setting = AsyncMock(
            return_value=(False, "engine off")
        )
        await process_threshold(message, fsm_state, container=container)
        fsm_state.clear.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "engine off" in text

    # ── Stop Loss ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cb_set_stop_loss(self, callback, fsm_state):
        await cb_set_stop_loss(callback, fsm_state)
        fsm_state.set_state.assert_awaited_once_with(
            SettingsStates.waiting_stop_loss
        )

    @pytest.mark.asyncio
    async def test_process_stop_loss_valid(self, message, fsm_state, container):
        message.text = "20"
        await process_stop_loss(message, fsm_state, container=container)
        container.update_bat_setting.assert_awaited_once_with("stop_loss", 0.20)
        fsm_state.clear.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_stop_loss_out_of_range(self, message, fsm_state, container):
        message.text = "75"  # 0.75 > 0.50
        await process_stop_loss(message, fsm_state, container=container)
        container.update_bat_setting.assert_not_awaited()

    # ── Target Price ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cb_set_target(self, callback, fsm_state):
        await cb_set_target(callback, fsm_state)
        fsm_state.set_state.assert_awaited_once_with(
            SettingsStates.waiting_target_price
        )

    @pytest.mark.asyncio
    async def test_process_target_valid(self, message, fsm_state, container):
        message.text = "0.92"
        await process_target(message, fsm_state, container=container)
        container.update_bat_setting.assert_awaited_once_with(
            "target_price", 0.92
        )

    @pytest.mark.asyncio
    async def test_process_target_invalid(self, message, fsm_state, container):
        message.text = "0.70"  # < 0.76
        await process_target(message, fsm_state, container=container)
        container.update_bat_setting.assert_not_awaited()

    # ── Position Size ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cb_set_position_size(self, callback, fsm_state):
        await cb_set_position_size(callback, fsm_state)
        fsm_state.set_state.assert_awaited_once_with(
            SettingsStates.waiting_position_size
        )

    @pytest.mark.asyncio
    async def test_process_position_size_valid(self, message, fsm_state, container):
        message.text = "25"
        await process_position_size(message, fsm_state, container=container)
        container.update_bat_setting.assert_awaited_once_with(
            "position_size", 25.0
        )

    @pytest.mark.asyncio
    async def test_process_position_size_out_of_range(self, message, fsm_state, container):
        message.text = "700"  # > 500
        await process_position_size(message, fsm_state, container=container)
        container.update_bat_setting.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════
# start.py — incluye doble confirmación de REAL
# ══════════════════════════════════════════════════════════════════════


class TestStartHandler:

    def test_main_menu_keyboard_has_real_button(self):
        kb = main_menu_keyboard()
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("REAL" in lbl for lbl in labels)
        assert any("Iniciar" in lbl for lbl in labels)

    @pytest.mark.asyncio
    async def test_cmd_start_renders_menu(self, message):
        await cmd_start(message)
        message.answer.assert_awaited_once()
        kwargs = message.answer.call_args.kwargs
        assert "reply_markup" in kwargs

    @pytest.mark.asyncio
    async def test_cb_status_delegates_to_send_status(self, callback, container):
        await cb_status_start(callback, container=container)
        callback.answer.assert_awaited_once()
        callback.message.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_cb_bot_start_success(self, callback, container):
        await cb_bot_start(callback, container=container)
        container.start_bot.assert_awaited_once()
        text = callback.message.answer.call_args.args[0]
        assert "iniciado" in text.lower()

    @pytest.mark.asyncio
    async def test_cb_bot_start_failure(self, callback, container):
        container.start_bot = AsyncMock(return_value=(False, "already running"))
        await cb_bot_start(callback, container=container)
        text = callback.message.answer.call_args.args[0]
        assert "already running" in text

    @pytest.mark.asyncio
    async def test_cb_bot_start_no_container(self, callback):
        await cb_bot_start(callback, container=None)
        callback.message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cb_bot_stop_success(self, callback, container):
        await cb_bot_stop(callback, container=container)
        container.stop_bot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cb_bot_stop_failure(self, callback, container):
        container.stop_bot = AsyncMock(return_value=(False, "not running"))
        await cb_bot_stop(callback, container=container)
        text = callback.message.answer.call_args.args[0]
        assert "not running" in text

    @pytest.mark.asyncio
    async def test_cb_bot_stop_no_container(self, callback):
        await cb_bot_stop(callback, container=None)
        callback.message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cb_enable_real_already_real_short_circuits(self, callback, container):
        container.trading_mode = "real"
        await cb_enable_real(callback, container=container)
        text = callback.message.answer.call_args.args[0]
        assert "ya está en modo REAL" in text or "ya está" in text

    @pytest.mark.asyncio
    async def test_cb_enable_real_shows_confirm_keyboard(self, callback, container):
        container.trading_mode = "paper"
        await cb_enable_real(callback, container=container)
        kwargs = callback.message.answer.call_args.kwargs
        kb = kwargs["reply_markup"]
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("SÍ" in lbl for lbl in labels)
        assert any("Cancelar" in lbl for lbl in labels)
        # No debe activar real aún
        container.enable_real_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cb_enable_real_no_container(self, callback):
        await cb_enable_real(callback, container=None)
        # Debe mostrar el flujo de confirmación incluso sin container
        callback.message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cb_real_confirm_step1_asks_for_pin(
        self, callback, fsm_state, monkeypatch,
    ):
        """
        Ola 2.2: step1 ya NO muestra un botón "CONFIRMO"; en su lugar
        pide el PIN de 6 dígitos y establece el estado FSM waiting_pin.
        """
        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.pin_gate import PinGate

        # Gate configurado (con hash arbitrario) — no interesa el valor
        # exacto, solo que is_configured() == True.
        gate = PinGate(_expected_hash="a" * 64)
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        callback.message.chat = MagicMock(id=555)
        await cb_real_confirm_step1(callback, fsm_state)

        fsm_state.set_state.assert_awaited_once_with(
            start_mod.RealModeStates.waiting_pin
        )
        text = callback.message.answer.call_args.args[0]
        assert "PIN" in text
        assert "6 dígitos" in text

    @pytest.mark.asyncio
    async def test_cb_real_confirm_step1_rejects_when_pin_not_configured(
        self, callback, fsm_state, monkeypatch,
    ):
        """Sin PIN configurado, step1 rechaza sin pedir input."""
        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.pin_gate import PinGate

        gate = PinGate(_expected_hash="")
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        callback.message.chat = MagicMock(id=555)
        await cb_real_confirm_step1(callback, fsm_state)

        fsm_state.set_state.assert_not_awaited()
        text = callback.message.answer.call_args.args[0]
        assert "no configurado" in text.lower()

    @pytest.mark.asyncio
    async def test_cb_real_confirm_step1_rejects_when_locked(
        self, callback, fsm_state, monkeypatch,
    ):
        """Si el chat_id está bloqueado por rate limit, step1 no pide PIN."""
        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.pin_gate import PinGate

        gate = PinGate(_expected_hash="a" * 64)
        # Fuerza el estado bloqueado directamente
        from src.interfaces.telegram.pin_gate import _AttemptState
        gate._attempts[555] = _AttemptState(
            failed_count=3, locked_until=9999999999.0
        )
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        callback.message.chat = MagicMock(id=555)
        await cb_real_confirm_step1(callback, fsm_state)

        fsm_state.set_state.assert_not_awaited()
        text = callback.message.answer.call_args.args[0]
        assert "Bloqueado" in text or "rate limit" in text

    @pytest.mark.asyncio
    async def test_on_pin_message_ok_activates_real(
        self, message, fsm_state, container, monkeypatch,
    ):
        """PIN correcto → clear state + container.enable_real_mode()."""
        import hashlib

        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.handlers.start import on_pin_message
        from src.interfaces.telegram.pin_gate import PinGate

        gate = PinGate(
            _expected_hash=hashlib.sha256(b"246810").hexdigest()
        )
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        message.chat = MagicMock(id=42)
        message.text = "246810"
        await on_pin_message(message, fsm_state, container=container)

        fsm_state.clear.assert_awaited_once()
        container.enable_real_mode.assert_awaited_once()
        text = message.answer.call_args.args[0]
        assert "ACTIVADO" in text

    @pytest.mark.asyncio
    async def test_on_pin_message_wrong_prompts_retry(
        self, message, fsm_state, container, monkeypatch,
    ):
        """PIN incorrecto → mensaje de retry, NO limpia state."""
        import hashlib

        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.handlers.start import on_pin_message
        from src.interfaces.telegram.pin_gate import PinGate

        gate = PinGate(
            _expected_hash=hashlib.sha256(b"246810").hexdigest()
        )
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        message.chat = MagicMock(id=42)
        message.text = "000000"
        await on_pin_message(message, fsm_state, container=container)

        container.enable_real_mode.assert_not_awaited()
        fsm_state.clear.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "incorrecto" in text.lower()

    @pytest.mark.asyncio
    async def test_on_pin_message_third_wrong_triggers_lockout(
        self, message, fsm_state, container, monkeypatch,
    ):
        """3er PIN incorrecto → mensaje de lockout Y clear del state."""
        import hashlib

        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.handlers.start import on_pin_message
        from src.interfaces.telegram.pin_gate import PinGate

        gate = PinGate(
            _expected_hash=hashlib.sha256(b"246810").hexdigest(),
            _lockout_seconds=600,
        )
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        message.chat = MagicMock(id=42)
        message.text = "000000"

        # Los 2 primeros fallos NO limpian state.
        await on_pin_message(message, fsm_state, container=container)
        await on_pin_message(message, fsm_state, container=container)
        assert fsm_state.clear.await_count == 0

        # El 3ero triggerea lockout Y clear.
        await on_pin_message(message, fsm_state, container=container)
        fsm_state.clear.assert_awaited()
        text = message.answer.call_args.args[0]
        assert "Bloqueado" in text or "intentos fallidos" in text

    @pytest.mark.asyncio
    async def test_on_pin_message_cancel_clears_state(
        self, message, fsm_state, container, monkeypatch,
    ):
        """Comando /cancel dentro del flujo aborta y limpia state."""
        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.handlers.start import on_pin_message
        from src.interfaces.telegram.pin_gate import PinGate

        gate = PinGate(_expected_hash="a" * 64)
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        message.chat = MagicMock(id=42)
        message.text = "/cancel"
        await on_pin_message(message, fsm_state, container=container)

        fsm_state.clear.assert_awaited_once()
        container.enable_real_mode.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "cancelada" in text.lower()

    @pytest.mark.asyncio
    async def test_on_pin_message_invalid_format_stays_in_state(
        self, message, fsm_state, container, monkeypatch,
    ):
        """PIN mal formateado → mensaje, NO limpia state, NO cuenta como intento."""
        import hashlib

        from src.interfaces.telegram.handlers import start as start_mod
        from src.interfaces.telegram.handlers.start import on_pin_message
        from src.interfaces.telegram.pin_gate import PinGate

        gate = PinGate(
            _expected_hash=hashlib.sha256(b"246810").hexdigest()
        )
        monkeypatch.setattr(start_mod, "_pin_gate", gate)

        message.chat = MagicMock(id=42)
        message.text = "abc"
        await on_pin_message(message, fsm_state, container=container)

        fsm_state.clear.assert_not_awaited()
        container.enable_real_mode.assert_not_awaited()
        text = message.answer.call_args.args[0]
        assert "inválido" in text.lower() or "6 dígitos" in text

    @pytest.mark.asyncio
    async def test_cb_real_confirm_final_activates_when_container(self, callback, container):
        await cb_real_confirm_final(callback, container=container)
        container.enable_real_mode.assert_awaited_once()
        text = callback.message.answer.call_args.args[0]
        assert "ACTIVADO" in text

    @pytest.mark.asyncio
    async def test_cb_real_confirm_final_failure_path(self, callback, container):
        container.enable_real_mode = AsyncMock(
            return_value=(False, "credentials missing")
        )
        await cb_real_confirm_final(callback, container=container)
        text = callback.message.answer.call_args.args[0]
        assert "Error" in text
        assert "credentials missing" in text

    @pytest.mark.asyncio
    async def test_cb_real_confirm_final_no_container(self, callback):
        await cb_real_confirm_final(callback, container=None)
        text = callback.message.answer.call_args.args[0]
        assert "ACTIVADO" in text

    @pytest.mark.asyncio
    async def test_cb_real_cancel(self, callback):
        await cb_real_cancel(callback)
        callback.answer.assert_awaited_once_with("Cancelado")
        text = callback.message.answer.call_args.args[0]
        assert "cancelada" in text.lower()


# ══════════════════════════════════════════════════════════════════════
# status.py
# ══════════════════════════════════════════════════════════════════════


class TestStatusHandler:

    @pytest.mark.asyncio
    async def test_send_status_no_container_fallback(self, message):
        await send_status(message, container=None)
        text = message.answer.call_args.args[0]
        assert "Estado del Bot" in text
        assert "Container no disponible" in text or "mock" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_status_invokes_send(self, message, container):
        await cmd_status(message, container=container)
        message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_status_running_paper(self, message, container, status_payload):
        await send_status(message, container=container)
        text = message.answer.call_args.args[0]
        assert "Corriendo" in text
        assert "Paper" in text
        assert "1050" in text or "1,050" in text

    @pytest.mark.asyncio
    async def test_send_status_real_mode_negative_pnl(self, message, container):
        container.get_bot_status = AsyncMock(return_value={
            "running": False,
            "mode": "real",
            "uptime_seconds": 45,        # < 60 → "Ns"
            "active_markets": 0,
            "open_positions": 0,
            "balance": 980.0,
            "pnl": -20.0,
        })
        await send_status(message, container=container)
        text = message.answer.call_args.args[0]
        assert "REAL" in text
        assert "Detenido" in text
        assert "📉" in text
        # uptime < 60 → segundos
        assert "45s" in text

    @pytest.mark.asyncio
    async def test_send_status_uptime_minutes(self, message, container):
        container.get_bot_status = AsyncMock(return_value={
            "running": True, "mode": "paper", "uptime_seconds": 600,
            "active_markets": 0, "open_positions": 0,
            "balance": 1000.0, "pnl": 0.0,
        })
        await send_status(message, container=container)
        text = message.answer.call_args.args[0]
        assert "10m" in text

    @pytest.mark.asyncio
    async def test_send_status_uptime_hours(self, message, container):
        container.get_bot_status = AsyncMock(return_value={
            "running": True, "mode": "paper", "uptime_seconds": 7325,  # 2h 2m
            "active_markets": 0, "open_positions": 0,
            "balance": 1000.0, "pnl": 0.0,
        })
        await send_status(message, container=container)
        text = message.answer.call_args.args[0]
        assert "2h" in text

    @pytest.mark.asyncio
    async def test_send_status_container_error_falls_back(self, message, container):
        container.get_bot_status = AsyncMock(
            side_effect=RuntimeError("status unavailable")
        )
        await send_status(message, container=container)
        text = message.answer.call_args.args[0]
        # El fallback se llama al final
        assert "Estado del Bot" in text
        assert "mock" in text.lower() or "Container" in text
