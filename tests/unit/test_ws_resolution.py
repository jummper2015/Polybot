# tests/unit/test_ws_resolution.py
"""Ola 2.1: tests para el flujo market_resolved end-to-end."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.polymarket.ws_client import PolymarketWSClient


class TestWSResolutionCallback:
    """
    Ola 2.1: cuando el WS recibe un mensaje con
    `event_type == "market_resolved"`, debe invocar el callback
    registrado con `market_id`.
    """

    def _make_client(self) -> PolymarketWSClient:
        redis = AsyncMock()
        redis.set_orderbook = AsyncMock()
        redis.set_market_metadata = AsyncMock()
        redis.set_ws_state = AsyncMock()
        redis.push_recent_tick = AsyncMock()
        return PolymarketWSClient(redis=redis)

    @pytest.mark.asyncio
    async def test_market_resolved_invokes_callback(self):
        client = self._make_client()
        seen = []

        async def handler(market_id: str) -> None:
            seen.append(market_id)

        client.set_resolution_callback(handler)

        # Simula mensaje raw sin bids/asks → parse_orderbook_message → None
        raw = json.dumps({
            "event_type": "market_resolved",
            "market": "MARKET_XYZ",
        })
        state = MagicMock()
        log = MagicMock()
        await client._process_message(raw, "MARKET_XYZ", AsyncMock(), state, log)

        assert seen == ["MARKET_XYZ"]

    @pytest.mark.asyncio
    async def test_no_callback_no_crash(self):
        """Sin callback registrado, resolved event no debe crashear."""
        client = self._make_client()
        raw = json.dumps({
            "event_type": "market_resolved",
            "market": "M1",
        })
        state = MagicMock()
        log = MagicMock()
        # No callback → sin excepción esperada
        await client._process_message(raw, "M1", AsyncMock(), state, log)

    @pytest.mark.asyncio
    async def test_callback_error_does_not_break_pipeline(self):
        """Si el handler lanza, el WS pipeline sigue vivo."""
        client = self._make_client()

        async def broken(market_id: str) -> None:
            raise RuntimeError("boom")

        client.set_resolution_callback(broken)

        raw = json.dumps({
            "event_type": "market_resolved",
            "market": "M1",
        })
        state = MagicMock()
        log = MagicMock()
        # Debe capturar el error, no propagar
        await client._process_message(raw, "M1", AsyncMock(), state, log)
        log.error.assert_called()  # Se logueó el fallo

    @pytest.mark.asyncio
    async def test_new_market_does_not_invoke_resolution_callback(self):
        """Otros eventos no-tick (new_market) NO invocan el callback."""
        client = self._make_client()
        seen = []

        async def handler(market_id: str) -> None:
            seen.append(market_id)

        client.set_resolution_callback(handler)

        raw = json.dumps({
            "event_type": "new_market",
            "market": "M1",
        })
        state = MagicMock()
        log = MagicMock()
        await client._process_message(raw, "M1", AsyncMock(), state, log)

        assert seen == []


class TestTradingServiceMarketResolvedHandler:
    """
    Ola 2.1: `TradingService._on_ws_market_resolved(market_id)` delega
    en `IRepositoryPort.mark_positions_resolved`.
    """

    def _make_service(self):
        from src.application.services.trading_service import TradingService
        svc = TradingService.__new__(TradingService)
        svc._repo = AsyncMock()
        svc._repo.mark_positions_resolved = AsyncMock(return_value=2)
        svc._audit_log = None
        return svc

    @pytest.mark.asyncio
    async def test_handler_calls_repo_with_market_id(self):
        svc = self._make_service()
        await svc._on_ws_market_resolved("MARKET_A")
        assert svc._repo.mark_positions_resolved.await_count == 1
        call_args = svc._repo.mark_positions_resolved.await_args
        assert call_args.args[0] == "MARKET_A"
        # 2do arg es datetime
        assert isinstance(call_args.args[1], datetime)

    @pytest.mark.asyncio
    async def test_handler_swallows_repo_error(self):
        """Errores de DB no deben propagarse al WS pipeline."""
        svc = self._make_service()
        svc._repo.mark_positions_resolved = AsyncMock(
            side_effect=RuntimeError("DB down")
        )
        # No debe raise
        await svc._on_ws_market_resolved("MARKET_A")

    @pytest.mark.asyncio
    async def test_handler_calls_audit_when_positions_marked(self):
        """Si audit_log está inyectado y n > 0, se emite audit entry."""
        svc = self._make_service()
        svc._audit_log = MagicMock()
        svc._audit_log.log = AsyncMock()
        svc._repo.mark_positions_resolved = AsyncMock(return_value=1)

        await svc._on_ws_market_resolved("MARKET_A")
        assert svc._audit_log.log.await_count == 1

    @pytest.mark.asyncio
    async def test_handler_skips_audit_when_zero_positions(self):
        """Con 0 posiciones marcadas, no emitimos audit (nada relevante)."""
        svc = self._make_service()
        svc._audit_log = MagicMock()
        svc._audit_log.log = AsyncMock()
        svc._repo.mark_positions_resolved = AsyncMock(return_value=0)

        await svc._on_ws_market_resolved("MARKET_A")
        assert svc._audit_log.log.await_count == 0
