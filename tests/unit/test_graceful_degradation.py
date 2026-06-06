# tests/unit/test_graceful_degradation.py
"""
Tests unitarios para P2.4 — Graceful Degradation WS → REST.

Verifica:
  - WS sano → tick WS, métrica = 2
  - WS stale → activa degradación, tick REST, métrica = 1
  - Modo degradado con cache hit → devuelve cache sin REST call
  - Modo degradado con cache miss → llama REST, cachea
  - Recovery probe → WS recuperado, sale de degradación
  - Recovery probe → WS aún caído, continúa REST
  - REST fetch failure → excepción propagada
  - is_degraded() / degraded_seconds()
"""

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.value_objects.market_tick import MarketTick
from src.domain.value_objects.ws_state import WSConnectionStatus, WSMarketState
from src.infrastructure.polymarket.http_client import PolymarketHTTPClient

# ── Fixtures ─────────────────────────────────────────────────────────

def make_tick(yes_price: float = 0.55) -> MarketTick:
    return MarketTick(
        market_id="test_market",
        yes_price=yes_price,
        no_price=round(1.0 - yes_price, 2),
        best_bid=yes_price - 0.005,
        best_ask=yes_price + 0.005,
        spread=0.01,
        volume_24h=5000.0,
        timestamp=datetime.utcnow(),
    )


def make_ws_state(
    status: WSConnectionStatus = WSConnectionStatus.CONNECTED,
    last_tick: MarketTick | None = None,
    last_message_at: float | None = None,
) -> WSMarketState:
    state = WSMarketState(market_id="test_market")
    state.status = status
    if last_tick:
        state.last_tick = last_tick
    if last_message_at:
        import datetime
        state.last_message_at = datetime.datetime.fromtimestamp(last_message_at)
    else:
        import datetime
        state.last_message_at = datetime.datetime.utcnow()
    return state


@pytest.fixture
def ws_client_mock():
    mock = AsyncMock()
    mock.get_state = AsyncMock()
    mock.subscribe = AsyncMock()
    mock.unsubscribe = AsyncMock()
    return mock


@pytest.fixture
def http_client(ws_client_mock):
    return PolymarketHTTPClient(ws_client=ws_client_mock)


# ── Tests: WS sano ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws_healthy_returns_ws_tick(http_client, ws_client_mock):
    """
    Si WS está CONNECTED y el tick no está stale, debe devolver
    el tick del WS sin hacer llamada REST. Métrica = 2 (ws).
    """
    tick = make_tick(0.60)
    state = make_ws_state(
        status=WSConnectionStatus.CONNECTED,
        last_tick=tick,
        last_message_at=time.time() - 5,  # hace 5 segundos, no stale
    )
    ws_client_mock.get_state.return_value = state

    result = await http_client.get_market_tick("test_market")

    assert result is tick
    assert result.yes_price == 0.60
    # Verifica que NO entró en modo degradado
    assert not http_client.is_degraded("test_market")


@pytest.mark.asyncio
async def test_ws_stale_activates_degradation(http_client, ws_client_mock):
    """
    Si WS está stale (último mensaje hace > 60s), debe activar
    el modo degradado y hacer una llamada REST.
    """
    tick = make_tick(0.55)
    state = make_ws_state(
        status=WSConnectionStatus.CONNECTED,
        last_tick=tick,
        last_message_at=time.time() - 120,  # hace 120s → STALE
    )
    ws_client_mock.get_state.return_value = state

    rest_tick = make_tick(0.50)

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = rest_tick

        result = await http_client.get_market_tick("test_market")

        mock_fetch.assert_awaited_once_with("test_market")
        assert result is rest_tick
        assert result.yes_price == 0.50
        assert http_client.is_degraded("test_market")


@pytest.mark.asyncio
async def test_ws_disconnected_activates_degradation(http_client, ws_client_mock):
    """
    Si WS está DISCONNECTED, debe activar degradación y usar REST.
    """
    state = make_ws_state(status=WSConnectionStatus.DISCONNECTED)
    state.last_tick = None
    state.last_message_at = None
    ws_client_mock.get_state.return_value = state

    rest_tick = make_tick(0.45)

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = rest_tick

        result = await http_client.get_market_tick("test_market")

        mock_fetch.assert_awaited_once()
        assert result is rest_tick
        assert http_client.is_degraded("test_market")


@pytest.mark.asyncio
async def test_ws_no_state_activates_degradation(http_client, ws_client_mock):
    """
    Si get_state devuelve None (nunca hubo conexión), debe degradar.
    """
    ws_client_mock.get_state.return_value = None

    rest_tick = make_tick(0.40)

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = rest_tick

        result = await http_client.get_market_tick("test_market")

        mock_fetch.assert_awaited_once()
        assert result is rest_tick
        assert http_client.is_degraded("test_market")


# ── Tests: Modo degradado con cache ──────────────────────────────────

@pytest.mark.asyncio
async def test_degraded_cache_hit_returns_cached(http_client, ws_client_mock):
    """
    Estando en modo degradado, la segunda llamada antes de 15s
    debe devolver el tick cacheado sin hacer REST call.
    """
    # Pre-condición: forzar entrada en modo degradado
    http_client._degraded_since["test_market"] = time.monotonic() - 10
    cached_tick = make_tick(0.52)
    # Cache age = 1s (safely within REST_CACHE_TTL=5s, avoiding boundary)
    http_client._rest_cache["test_market"] = (time.monotonic() - 1, cached_tick)
    http_client._last_recovery_probe["test_market"] = time.monotonic() - 1

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        result = await http_client._get_tick_degraded(
            "test_market", time.monotonic()
        )

        # No debe llamar a REST porque el cache es válido (< 15s)
        mock_fetch.assert_not_awaited()
        assert result is cached_tick
        assert result.yes_price == 0.52


@pytest.mark.asyncio
async def test_degraded_cache_expired_fetches_rest(http_client, ws_client_mock):
    """
    Estando en modo degradado con cache expirado (> 15s),
    debe llamar a REST y actualizar la cache.
    """
    now = time.monotonic()
    http_client._degraded_since["test_market"] = now - 30
    old_tick = make_tick(0.50)
    http_client._rest_cache["test_market"] = (now - 20, old_tick)  # expirado
    http_client._last_recovery_probe["test_market"] = now - 5  # probe reciente, no se ejecutará

    new_tick = make_tick(0.55)

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = new_tick

        result = await http_client._get_tick_degraded("test_market", now)

        mock_fetch.assert_awaited_once_with("test_market")
        assert result is new_tick
        # Cache actualizada
        cached_at, cached_tick = http_client._rest_cache["test_market"]
        assert cached_at == now
        assert cached_tick is new_tick


# ── Tests: Recovery probe ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_degraded_recovery_probe_ws_recovered(http_client, ws_client_mock):
    """
    En modo degradado, tras RECOVERY_PROBE_INTERVAL (30s),
    si el WS se recuperó, debe salir de degradación y usar WS.
    """
    now = time.monotonic()
    http_client._degraded_since["test_market"] = now - 40
    http_client._last_recovery_probe["test_market"] = now - 35  # > 30s → probe
    ws_tick = make_tick(0.65)
    recovered_state = make_ws_state(
        status=WSConnectionStatus.CONNECTED,
        last_tick=ws_tick,
        last_message_at=time.time() - 2,  # muy reciente (wall-clock, no monotonic)
    )
    ws_client_mock.get_state.return_value = recovered_state

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        result = await http_client._get_tick_degraded("test_market", now)

        # No debe llamar a REST porque WS se recuperó
        mock_fetch.assert_not_awaited()
        assert result is ws_tick
        assert not http_client.is_degraded("test_market")
        assert "test_market" not in http_client._rest_cache


@pytest.mark.asyncio
async def test_degraded_recovery_probe_ws_still_down(http_client, ws_client_mock):
    """
    En modo degradado, tras RECOVERY_PROBE_INTERVAL,
    si WS aún está caído, debe continuar con REST.
    """
    now = time.monotonic()
    http_client._degraded_since["test_market"] = now - 40
    http_client._last_recovery_probe["test_market"] = now - 35
    still_dead_state = make_ws_state(
        status=WSConnectionStatus.DISCONNECTED
    )
    ws_client_mock.get_state.return_value = still_dead_state

    rest_tick = make_tick(0.48)

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = rest_tick

        result = await http_client._get_tick_degraded("test_market", now)

        mock_fetch.assert_awaited_once()
        assert result is rest_tick
        assert http_client.is_degraded("test_market")


@pytest.mark.asyncio
async def test_degraded_recovery_probe_ws_stale(http_client, ws_client_mock):
    """
    Recovery probe donde WS está CONNECTED pero el tick sigue stale.
    Debe continuar en modo degradado.
    """
    now = time.monotonic()
    http_client._degraded_since["test_market"] = now - 40
    http_client._last_recovery_probe["test_market"] = now - 35
    stale_state = make_ws_state(
        status=WSConnectionStatus.CONNECTED,
        last_tick=make_tick(0.60),
        last_message_at=time.time() - 120,  # stale (wall-clock, no monotonic)
    )
    ws_client_mock.get_state.return_value = stale_state

    rest_tick = make_tick(0.47)

    with patch.object(
        http_client, "_fetch_tick_rest", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.return_value = rest_tick

        result = await http_client._get_tick_degraded("test_market", now)

        mock_fetch.assert_awaited_once()
        assert result is rest_tick
        assert http_client.is_degraded("test_market")


# ── Tests: REST failure ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rest_fetch_raises_propagates(http_client, ws_client_mock):
    """
    Si la llamada REST falla, la excepción debe propagarse.
    """
    ws_client_mock.get_state.return_value = None

    with patch.object(
        http_client._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.side_effect = Exception("REST API down")

        with pytest.raises(Exception, match="REST API down"):
            await http_client.get_market_tick("test_market")


@pytest.mark.asyncio
async def test_rest_fetch_degraded_propagates(http_client, ws_client_mock):
    """
    En modo degradado sin cache, si REST falla, debe propagar.
    """
    now = time.monotonic()
    http_client._degraded_since["test_market"] = now - 40
    http_client._last_recovery_probe["test_market"] = now - 5  # probe reciente
    ws_client_mock.get_state.return_value = make_ws_state(
        status=WSConnectionStatus.DISCONNECTED
    )

    with patch.object(
        http_client._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.side_effect = Exception("REST down")

        with pytest.raises(Exception, match="REST down"):
            await http_client._get_tick_degraded("test_market", now)


# ── Tests: Queries de estado ─────────────────────────────────────────

def test_is_degraded_queries(http_client):
    assert not http_client.is_degraded("unknown")

    http_client._degraded_since["test_market"] = time.monotonic()
    assert http_client.is_degraded("test_market")


def test_degraded_seconds(http_client):
    assert http_client.degraded_seconds("unknown") is None

    now = time.monotonic()
    http_client._degraded_since["test_market"] = now - 12.5
    secs = http_client.degraded_seconds("test_market")
    assert secs is not None
    assert 12.0 <= secs <= 13.5  # tolerancia de ejecución


# ── Test: _fetch_tick_rest unitario ──────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_tick_rest_parses_book_response(http_client):
    """
    _fetch_tick_rest debe llamar a /book y parsear la respuesta.
    """
    with patch.object(
        http_client._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "bids": [{"price": "0.55", "size": "100"}],
            "asks": [{"price": "0.56", "size": "200"}],
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = await http_client._fetch_tick_rest("test_market")

        mock_get.assert_awaited_once()
        call_args = mock_get.call_args
        assert call_args[0][0] == "https://clob.polymarket.com/book"
        assert call_args[1]["params"]["token_id"] == "test_market"
        assert isinstance(result, MarketTick)
