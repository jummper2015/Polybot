# tests/unit/test_clob_client.py
"""Unit tests for PolymarketCLOBClient — CLOB V2 market info & order operations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.polymarket.clob_client import (
    CTF_CONTRACT_ADDRESS,
    CLOBRedeemNotSupportedError,
    PolymarketCLOBClient,
    _ensure_dict,
    _mask_wallet,
)

# ── Helpers ────────────────────────────────────────────────────────────

def make_mock_key_manager():
    """KeyManager mock that doesn't touch environment variables."""
    km = MagicMock()
    km.api_key = "test_api_key"
    km.api_secret = "test_api_secret"
    km.api_passphrase = "test_api_passphrase"
    km.private_key = "0xabc123"
    km.wallet_address = "0x1234567890abcdef1234567890abcdef12345678"
    km.builder_code = ""
    km.signature_type = 1
    return km


def make_mock_clob_client(mock_sdk=None, redis_client=None):
    """Builds a PolymarketCLOBClient with the SDK constructor patched out."""
    km = make_mock_key_manager()

    with patch(
        "src.infrastructure.polymarket.clob_client.ClobClient"
    ) as MockClobClient:
        sdk_instance = mock_sdk or MagicMock()
        MockClobClient.return_value = sdk_instance

        with patch(
            "src.infrastructure.polymarket.clob_client.httpx.AsyncClient"
        ) as MockHTTP:
            MockHTTP.return_value = AsyncMock()
            client = PolymarketCLOBClient(
                key_manager=km,
                redis_client=redis_client,
            )

    return client, sdk_instance, MockClobClient


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

class TestHelpers:

    def test_mask_wallet_normal(self):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        masked = _mask_wallet(addr)
        assert masked == "0x1234...5678"

    def test_mask_wallet_short(self):
        assert _mask_wallet("0xshort") == "***"

    def test_mask_wallet_empty(self):
        assert _mask_wallet("") == "***"

    def test_ensure_dict_returns_dict_as_is(self):
        d = {"key": "value"}
        assert _ensure_dict(d) is d

    def test_ensure_dict_converts_object(self):
        class FakeResponse:
            def __init__(self):
                self.price = "0.55"
                self.status = "FILLED"
        result = _ensure_dict(FakeResponse())
        assert result == {"price": "0.55", "status": "FILLED"}

    def test_ensure_dict_fallback_to_raw(self):
        result = _ensure_dict(42)
        assert result == {"raw": "42"}


# ═══════════════════════════════════════════════════════════════════════
# get_market_info — CLOB V2
# ═══════════════════════════════════════════════════════════════════════

class TestGetMarketInfo:

    @pytest.mark.asyncio
    async def test_returns_full_market_info(self):
        """Happy path: SDK returns complete market info with fees, tokens, tick size."""
        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()  # reference for arg verification

        client, _, _ = make_mock_clob_client(mock_sdk=sdk)

        expected = {
            "mts": "0.01",
            "mos": "5.0",
            "fd": {"r": "100", "e": "4", "to": True},
            "t": [
                {"t": "106090...", "o": "Yes"},
                {"t": "839510...", "o": "No"},
            ],
            "rfqe": False,
        }

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = expected
            result = await client.get_market_info(
                "0xaf5e903876ad42de97e1cf02c2ef8484df69bcfc5541b96a400116557d1e504e"
            )

        assert result["mts"] == "0.01"
        assert result["mos"] == "5.0"
        assert result["fd"]["to"] is True
        assert result["fd"]["r"] == "100"
        assert len(result["t"]) == 2
        assert result["t"][0]["o"] == "Yes"
        assert result["rfqe"] is False
        # Also verifies asyncio.to_thread was called with the SDK method
        mock_to_thread.assert_called_once()
        assert mock_to_thread.call_args[0][0] == sdk.get_clob_market_info

    @pytest.mark.asyncio
    async def test_passes_condition_id_to_sdk(self):
        """Verify the condition_id is forwarded correctly to the SDK call."""
        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()

        client, _, _ = make_mock_clob_client(mock_sdk=sdk)
        condition_id = "0x625d0091f4c647e5497bd9b03f8526bd486d6c339380b8046e4dd5b3373046b7"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = {"mts": "0.001"}
            await client.get_market_info(condition_id)

        mock_to_thread.assert_called_once()
        # First positional arg is the SDK method, condition_id is a kwarg
        assert mock_to_thread.call_args[0][0] == sdk.get_clob_market_info
        assert mock_to_thread.call_args[1]["condition_id"] == condition_id

    @pytest.mark.asyncio
    async def test_minimal_response_no_fees(self):
        """Market info without fee details — common for markets pre-fee config."""
        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()

        client, _, _ = make_mock_clob_client(mock_sdk=sdk)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = {
                "mts": "0.01",
                "mos": "5.0",
                "t": [{"t": "123", "o": "Yes"}],
            }
            result = await client.get_market_info("0xabc")

        assert result["mts"] == "0.01"
        assert "fd" not in result
        assert len(result["t"]) == 1

    @pytest.mark.asyncio
    async def test_converts_object_to_dict(self):
        """SDK returns an object with attributes — _ensure_dict converts it."""
        class MarketInfoObj:
            def __init__(self):
                self.mts = "0.001"
                self.mos = "10.0"
                self.fd = {"r": "50", "e": "6", "to": False}
                self.t = [{"t": "abc", "o": "Yes"}]
                self.rfqe = True

        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()

        client, _, _ = make_mock_clob_client(mock_sdk=sdk)

        obj = MarketInfoObj()
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = obj
            result = await client.get_market_info("0xdef")

        assert result["mts"] == "0.001"
        assert result["mos"] == "10.0"
        assert result["fd"]["to"] is False
        assert result["rfqe"] is True
        # Verify nested dict (fd) was preserved as a dict, not converted to vars
        assert isinstance(result["fd"], dict)

    @pytest.mark.asyncio
    async def test_error_from_sdk_propagates(self):
        """If the SDK raises, the exception propagates to the caller."""
        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()

        client, _, _ = make_mock_clob_client(mock_sdk=sdk)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.side_effect = RuntimeError("SDK network timeout")

            with pytest.raises(RuntimeError, match="SDK network timeout"):
                await client.get_market_info("0xbad")

    @pytest.mark.asyncio
    async def test_fee_details_structure(self):
        """Fee details (fd) follow the expected CLOB V2 structure."""
        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()

        client, _, _ = make_mock_clob_client(mock_sdk=sdk)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = {
                "mts": "0.01",
                "mos": "5.0",
                "fd": {"r": "200", "e": "6", "to": True},
                "t": [{"t": "yes_token", "o": "Yes"}, {"t": "no_token", "o": "No"}],
            }
            result = await client.get_market_info("0xabc")

        fd = result["fd"]
        assert "r" in fd   # fee rate numerator
        assert "e" in fd   # fee exponent (denominator = 10^e)
        assert "to" in fd  # takerOnly flag
        assert isinstance(fd["r"], str)
        assert isinstance(fd["to"], bool)
        # Maker = no fees in V2 (to=True means only taker pays)
        assert fd["to"] is True


# ═══════════════════════════════════════════════════════════════════════
# R1.7 — signature_type explícito al SDK
# ═══════════════════════════════════════════════════════════════════════


class TestSignatureTypePropagation:

    def test_signature_type_passed_to_sdk_constructor(self):
        """El ClobClient del SDK debe recibir signature_type explícito (no None)."""
        _, _, mock_ctor = make_mock_clob_client()
        kwargs = mock_ctor.call_args.kwargs
        assert "signature_type" in kwargs
        assert kwargs["signature_type"] == 1  # default POLY_PROXY del mock

    def test_signature_type_custom_value_propagates(self):
        """Override del signature_type en KeyManager se refleja en el SDK."""
        km = make_mock_key_manager()
        km.signature_type = 3  # POLY_1271

        with patch(
            "src.infrastructure.polymarket.clob_client.ClobClient"
        ) as MockClobClient:
            MockClobClient.return_value = MagicMock()
            with patch(
                "src.infrastructure.polymarket.clob_client.httpx.AsyncClient"
            ) as MockHTTP:
                MockHTTP.return_value = AsyncMock()
                PolymarketCLOBClient(key_manager=km)

        kwargs = MockClobClient.call_args.kwargs
        assert kwargs["signature_type"] == 3


# ═══════════════════════════════════════════════════════════════════════
# R1.7 — get_market_info_cached
# ═══════════════════════════════════════════════════════════════════════


class TestGetMarketInfoCached:

    @pytest.mark.asyncio
    async def test_cache_miss_calls_sdk_and_stores(self):
        """Sin entrada en cache: consulta SDK, guarda en Redis con TTL fijo."""
        from src.infrastructure.polymarket.clob_client import (
            CLOB_MARKET_INFO_TTL_SECONDS,
        )

        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()
        redis = MagicMock()
        redis.get_clob_market_info = AsyncMock(return_value=None)  # cache miss
        redis.set_clob_market_info = AsyncMock()

        client, _, _ = make_mock_clob_client(mock_sdk=sdk, redis_client=redis)

        expected = {"mts": "0.01", "fd": {"r": "100", "e": "4", "to": True}}
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = expected
            result = await client.get_market_info_cached("0xabc")

        assert result == expected
        redis.get_clob_market_info.assert_awaited_once_with("0xabc")
        redis.set_clob_market_info.assert_awaited_once()
        kwargs = redis.set_clob_market_info.call_args.kwargs
        assert kwargs["condition_id"] == "0xabc"
        assert kwargs["info"] == expected
        assert kwargs["ttl_seconds"] == CLOB_MARKET_INFO_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_cache_hit_skips_sdk(self):
        """Con entrada en cache: no llama al SDK ni reescribe Redis."""
        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()
        cached = {"mts": "0.001", "fd": {"r": "50", "e": "4", "to": True}}
        redis = MagicMock()
        redis.get_clob_market_info = AsyncMock(return_value=cached)
        redis.set_clob_market_info = AsyncMock()

        client, _, _ = make_mock_clob_client(mock_sdk=sdk, redis_client=redis)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            result = await client.get_market_info_cached("0xdef")

        assert result == cached
        mock_to_thread.assert_not_called()  # SDK no se invoca
        redis.set_clob_market_info.assert_not_awaited()  # no se reescribe el cache

    @pytest.mark.asyncio
    async def test_without_redis_falls_back_to_sdk(self):
        """Sin redis_client el método degrada a una llamada directa al SDK."""
        sdk = MagicMock()
        sdk.get_clob_market_info = MagicMock()
        client, _, _ = make_mock_clob_client(mock_sdk=sdk, redis_client=None)

        expected = {"mts": "0.01"}
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = expected
            result = await client.get_market_info_cached("0xghi")

        assert result == expected
        mock_to_thread.assert_called_once()




# ═══════════════════════════════════════════════════════════════════════
# redeem_position — CLOB V2 redeem es on-chain via CTF
# ═══════════════════════════════════════════════════════════════════════

class TestRedeemPositionV2:
    """
    CLOB V2 (abril 2026) eliminó el endpoint REST `/redeem`. La redención
    de tokens ganadores se hace on-chain via CTF (Conditional Tokens
    Framework). Hasta que se implemente la integración web3, el método
    debe fallar rápido sin tocar la red.
    """

    def test_redeem_not_supported_error_is_not_implemented(self):
        """CLOBRedeemNotSupportedError extiende NotImplementedError."""
        assert issubclass(CLOBRedeemNotSupportedError, NotImplementedError)

    def test_ctf_contract_address_documented(self):
        """El address oficial del CTF en Polygon Mainnet está documentado."""
        assert CTF_CONTRACT_ADDRESS == "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

    @pytest.mark.asyncio
    async def test_redeem_position_raises_clob_redeem_not_supported(self):
        """redeem_position lanza CLOBRedeemNotSupportedError."""
        client, sdk, _ = make_mock_clob_client()

        with pytest.raises(CLOBRedeemNotSupportedError) as exc_info:
            await client.redeem_position(
                token_id="0xtoken123",
                market_id="0xcondition456",
            )

        msg = str(exc_info.value)
        # El mensaje guía al desarrollador hacia el flujo correcto (CTF)
        assert "CTF" in msg
        assert "redeemPositions" in msg

    @pytest.mark.asyncio
    async def test_redeem_position_does_not_touch_network(self):
        """Falla antes de cualquier llamada HTTP o SDK."""
        client, sdk, _ = make_mock_clob_client()
        # Si el SDK o el HTTP client se invocaran, los AsyncMock lo registrarían.
        with pytest.raises(CLOBRedeemNotSupportedError):
            await client.redeem_position(token_id="t", market_id="m")

        # No se llama al SDK
        sdk.get_address.assert_not_called()
        # No se hace POST: _http es un AsyncMock; verificamos await_count == 0
        post_method = client._http.post
        if hasattr(post_method, "await_count"):
            assert post_method.await_count == 0

    @pytest.mark.asyncio
    async def test_ctf_redeemer_property_exposes_injected_redeemer(self):
        """
        Cuando se inyecta ctf_redeemer, debe estar accesible via property.
        R2.0-redeem-impl F1: callers acceden via client.ctf_redeemer.redeem(...).
        """
        from unittest.mock import MagicMock
        client, _, _ = make_mock_clob_client()
        mock_redeemer = MagicMock()
        # Inyectar manualmente (en prod se inyecta via container)
        client._ctf_redeemer = mock_redeemer
        assert client.ctf_redeemer is mock_redeemer

    @pytest.mark.asyncio
    async def test_ctf_redeemer_property_none_when_not_injected(self):
        """Sin ctf_redeemer inyectado, property retorna None (path legacy)."""
        client, _, _ = make_mock_clob_client()
        assert client.ctf_redeemer is None
