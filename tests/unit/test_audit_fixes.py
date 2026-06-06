# tests/unit/test_audit_fixes.py
"""Unit tests for the 6 Polymarket API audit fixes (P11.1 audit).

Covers new methods added during the audit:
  - PolymarketAdapter.parse_neg_risk
  - PolymarketAdapter.parse_tick_size
  - PolymarketWSClient._handle_tick_size_change
  - RealTradingHandler._apply_mos_guardrail
  - MarketService._cache_market_info
  - PolymarketAdapter.parse_orderbook_message (price_change deltas)
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window
from src.infrastructure.polymarket.adapters import PolymarketAdapter

# ═══════════════════════════════════════════════════════════════════════
# 1. parse_neg_risk
# ═══════════════════════════════════════════════════════════════════════

class TestParseNegRisk:

    def test_neg_risk_true_snake_case(self):
        """neg_risk: true → True."""
        assert PolymarketAdapter.parse_neg_risk({"neg_risk": True}) is True

    def test_neg_risk_true_camel_case(self):
        """negRisk: true (camelCase from REST /book) → True."""
        assert PolymarketAdapter.parse_neg_risk({"negRisk": True}) is True

    def test_neg_risk_false(self):
        """neg_risk: false → False."""
        assert PolymarketAdapter.parse_neg_risk({"neg_risk": False}) is False

    def test_neg_risk_absent(self):
        """Field not present → False."""
        assert PolymarketAdapter.parse_neg_risk({"bids": [], "asks": []}) is False

    def test_neg_risk_empty_dict(self):
        """Empty dict → False."""
        assert PolymarketAdapter.parse_neg_risk({}) is False

    def test_neg_risk_falsy_string(self):
        """Falsy string value → False (bool() of empty/falsy)."""
        assert PolymarketAdapter.parse_neg_risk({"neg_risk": ""}) is False

    def test_neg_risk_truthy_non_bool(self):
        """Truthy non-bool value → True."""
        assert PolymarketAdapter.parse_neg_risk({"neg_risk": "true"}) is True
        assert PolymarketAdapter.parse_neg_risk({"neg_risk": 1}) is True


# ═══════════════════════════════════════════════════════════════════════
# 2. parse_tick_size
# ═══════════════════════════════════════════════════════════════════════

class TestParseTickSize:

    def test_tick_size_snake_case(self):
        """tick_size field → string."""
        assert PolymarketAdapter.parse_tick_size(
            {"tick_size": "0.01"}
        ) == "0.01"

    def test_tick_size_camel_case(self):
        """tickSize field (camelCase from WS) → string."""
        assert PolymarketAdapter.parse_tick_size(
            {"tickSize": "0.001"}
        ) == "0.001"

    def test_new_tick_size_snake(self):
        """new_tick_size field (from tick_size_change event) → string."""
        assert PolymarketAdapter.parse_tick_size(
            {"new_tick_size": "0.0001"}
        ) == "0.0001"

    def test_new_tick_size_camel(self):
        """newTickSize field → string."""
        assert PolymarketAdapter.parse_tick_size(
            {"newTickSize": "0.01"}
        ) == "0.01"

    def test_no_tick_size_fields(self):
        """No tick size fields → None."""
        assert PolymarketAdapter.parse_tick_size(
            {"event_type": "book", "bids": [], "asks": []}
        ) is None

    def test_empty_dict(self):
        """Empty dict → None."""
        assert PolymarketAdapter.parse_tick_size({}) is None

    def test_prefers_tick_size_over_new(self):
        """tick_size takes priority over new_tick_size."""
        result = PolymarketAdapter.parse_tick_size({
            "tick_size": "0.01",
            "new_tick_size": "0.001",
        })
        assert result == "0.01"

    def test_numeric_tick_size_coerced(self):
        """Numeric tick size → str."""
        result = PolymarketAdapter.parse_tick_size({"tick_size": 0.01})
        assert result == "0.01"
        assert isinstance(result, str)

    def test_large_tick_size(self):
        """Unusual tick size like '1' → string."""
        assert PolymarketAdapter.parse_tick_size(
            {"tick_size": "1"}
        ) == "1"


# ═══════════════════════════════════════════════════════════════════════
# 3. parse_orderbook_message — price_change deltas
# ═══════════════════════════════════════════════════════════════════════

class TestPriceChangeParsing:

    def test_book_event_with_bids_asks(self):
        """Standard 'book' event with bids/asks → produces a valid tick."""
        raw = {
            "event_type": "book",
            "bids": [
                {"price": "0.60", "size": "100"},
                {"price": "0.59", "size": "50"},
            ],
            "asks": [
                {"price": "0.62", "size": "200"},
                {"price": "0.63", "size": "80"},
            ],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is not None
        assert tick.yes_price == 0.61  # mid of 0.60-0.62
        assert tick.best_bid == 0.60
        assert tick.best_ask == 0.62
        assert tick.spread == 0.02

    def test_price_change_with_best_bid_ask(self):
        """price_change with only best_bid/best_ask (no bids/asks arrays)."""
        raw = {
            "event_type": "price_change",
            "best_bid": "0.55",
            "best_ask": "0.57",
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is not None
        assert tick.yes_price == 0.56
        assert tick.best_bid == 0.55
        assert tick.best_ask == 0.57
        assert tick.spread == 0.02

    def test_price_change_with_bids_asks(self):
        """price_change that includes full bids/asks → uses arrays."""
        raw = {
            "event_type": "price_change",
            "bids": [{"price": "0.70", "size": "100"}],
            "asks": [{"price": "0.71", "size": "200"}],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is not None
        assert tick.yes_price == 0.705

    def test_price_change_deltas_with_price_changes(self):
        """price_change with price_changes array (deltas)."""
        raw = {
            "event_type": "price_change",
            "price_changes": [
                {"price": "0.45", "size": "50", "side": "BUY"},
                {"price": "0.44", "size": "30", "side": "BUY"},
                {"price": "0.47", "size": "100", "side": "SELL"},
                {"price": "0.48", "size": "60", "side": "SELL"},
            ],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is not None
        assert tick.best_bid == 0.45  # max of BUY prices
        assert tick.best_ask == 0.47  # min of SELL prices
        assert tick.yes_price == 0.46

    def test_price_change_all_sell_only(self):
        """price_changes with only SELL sides → cannot extract best_bid → None."""
        raw = {
            "event_type": "price_change",
            "price_changes": [
                {"price": "0.47", "size": "100", "side": "SELL"},
                {"price": "0.48", "size": "60", "side": "SELL"},
            ],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None  # No buy prices to form best_bid

    def test_price_change_all_buy_only(self):
        """price_changes with only BUY sides → cannot extract best_ask → None."""
        raw = {
            "event_type": "price_change",
            "price_changes": [
                {"price": "0.45", "size": "50", "side": "BUY"},
            ],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None  # No sell prices to form best_ask

    def test_price_change_empty_deltas(self):
        """Empty price_changes array → no data → None."""
        raw = {
            "event_type": "price_change",
            "price_changes": [],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None

    def test_last_trade_price_ignored(self):
        """last_trade_price without bids/asks → None."""
        raw = {
            "event_type": "last_trade_price",
            "price": "0.75",
            "size": "50",
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None

    def test_unknown_event_type(self):
        """Unknown event types are silently ignored."""
        raw = {
            "event_type": "tick_size_change",
            "new_tick_size": "0.001",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None

    def test_ms_timestamp_normalization(self):
        """Timestamp in milliseconds → normalized to seconds."""
        raw = {
            "event_type": "book",
            "bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.51", "size": "100"}],
            "timestamp": "1718112000000",  # 13 digits = ms
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is not None
        expected = datetime.utcfromtimestamp(1718112000)
        assert tick.timestamp == expected

    def test_missing_timestamp_uses_utcnow(self):
        """No timestamp → uses utcnow."""
        raw = {
            "event_type": "book",
            "bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.51", "size": "100"}],
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is not None
        assert tick.timestamp is not None

    def test_invalid_price_data_returns_none(self):
        """Malformed price data → returns None (doesn't crash)."""
        raw = {
            "event_type": "book",
            "bids": [{"price": "not_a_number", "size": "100"}],
            "asks": [{"price": "0.51", "size": "100"}],
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None

    def test_price_change_deltas_no_side_field(self):
        """price_changes entries without side → appear in both buy/sell lists."""
        raw = {
            "event_type": "price_change",
            "price_changes": [
                {"price": "0.50", "size": "50"},
                {"price": "0.51", "size": "30"},
            ],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is not None
        assert tick.best_bid == 0.51  # max of 0.50, 0.51
        assert tick.best_ask == 0.50  # min of 0.50, 0.51

    def test_price_change_deltas_zero_bid_returns_none(self):
        """price_changes path: max BUY price <= 0 blocked by guard → None."""
        raw = {
            "event_type": "price_change",
            "price_changes": [
                {"price": "0.0", "size": "50", "side": "BUY"},
                {"price": "0.50", "size": "30", "side": "SELL"},
            ],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None

    def test_price_change_deltas_ask_at_one_returns_none(self):
        """price_changes path: min SELL price >= 1 blocked by guard → None."""
        raw = {
            "event_type": "price_change",
            "price_changes": [
                {"price": "0.50", "size": "50", "side": "BUY"},
                {"price": "1.0", "size": "30", "side": "SELL"},
            ],
            "timestamp": "1718112000",
        }
        tick = PolymarketAdapter.parse_orderbook_message("market_001", raw)
        assert tick is None


# ═══════════════════════════════════════════════════════════════════════
# 4. _apply_mos_guardrail (RealTradingHandler)
# ═══════════════════════════════════════════════════════════════════════

class TestMOSGuardrail:

    def test_ok_when_amount_above_mos(self):
        """Amount >= MOS → None (pass)."""
        from src.execution.real_handler import RealTradingHandler

        result = RealTradingHandler._apply_mos_guardrail(
            amount=10.0, mos=5.0, market_id="0xabcd1234abcd1234abcd1234"
        )
        assert result is None

    def test_ok_when_amount_equals_mos(self):
        """Amount == MOS → None (pass)."""
        from src.execution.real_handler import RealTradingHandler

        result = RealTradingHandler._apply_mos_guardrail(
            amount=5.0, mos=5.0, market_id="0xabcd1234abcd1234abcd1234"
        )
        assert result is None

    def test_blocks_when_amount_below_mos(self):
        """Amount < MOS → returns GUARDRAIL error string."""
        from src.execution.real_handler import RealTradingHandler

        result = RealTradingHandler._apply_mos_guardrail(
            amount=3.0, mos=10.0, market_id="0xabcd1234abcd1234abcd1234"
        )
        assert result is not None
        assert "GUARDRAIL" in result
        assert "3.00" in result
        assert "10.00" in result
        assert "market_min_order_size" in result

    def test_blocks_with_market_id_snippet(self):
        """Error message includes first 20 chars of market_id."""
        from src.execution.real_handler import RealTradingHandler

        result = RealTradingHandler._apply_mos_guardrail(
            amount=1.0,
            mos=5.0,
            market_id="0xabcdef0123456789abcdef0123456789abcdef01",
        )
        assert result is not None
        # market_id[:20] should be truncated in error
        assert "0xabcdef0123456789ab" in result

    def test_zero_mos(self):
        """MOS=0 → amount >= 0 always passes."""
        from src.execution.real_handler import RealTradingHandler

        result = RealTradingHandler._apply_mos_guardrail(
            amount=0.01, mos=0.0, market_id="0xabcd1234abcd1234"
        )
        assert result is None

    def test_zero_amount_with_positive_mos(self):
        """amount=0 with positive MOS → blocked."""
        from src.execution.real_handler import RealTradingHandler

        result = RealTradingHandler._apply_mos_guardrail(
            amount=0.0, mos=5.0, market_id="0xabcd1234abcd1234"
        )
        assert result is not None
        assert "0.00" in result


# ═══════════════════════════════════════════════════════════════════════
# 5. _handle_tick_size_change (PolymarketWSClient)
# ═══════════════════════════════════════════════════════════════════════

class TestHandleTickSizeChange:

    @pytest.mark.asyncio
    async def test_updates_redis_with_new_tick_size(self):
        """New tick size → set_market_metadata called with updated value."""
        from src.infrastructure.polymarket.ws_client import PolymarketWSClient

        redis = AsyncMock()
        redis.set_market_metadata = AsyncMock()

        ws = PolymarketWSClient(redis=redis)
        data = {"event_type": "tick_size_change", "new_tick_size": "0.001"}

        import structlog
        log = structlog.get_logger("test")

        await ws._handle_tick_size_change("market_001", data, log)

        redis.set_market_metadata.assert_called_once_with(
            "market_001", {"tick_size": "0.001"}
        )

    @pytest.mark.asyncio
    async def test_uses_tick_size_field(self):
        """If tick_size present (not new_tick_size), uses it."""
        from src.infrastructure.polymarket.ws_client import PolymarketWSClient

        redis = AsyncMock()
        redis.set_market_metadata = AsyncMock()

        ws = PolymarketWSClient(redis=redis)
        data = {"event_type": "tick_size_change", "tick_size": "0.01"}

        import structlog
        log = structlog.get_logger("test")

        await ws._handle_tick_size_change("market_001", data, log)

        redis.set_market_metadata.assert_called_once_with(
            "market_001", {"tick_size": "0.01"}
        )

    @pytest.mark.asyncio
    async def test_none_tick_size_no_redis_call(self):
        """parse_tick_size returns None → no Redis call."""
        from src.infrastructure.polymarket.ws_client import PolymarketWSClient

        redis = AsyncMock()
        redis.set_market_metadata = AsyncMock()

        ws = PolymarketWSClient(redis=redis)
        data = {"event_type": "tick_size_change"}  # No tick size fields

        import structlog
        log = structlog.get_logger("test")

        await ws._handle_tick_size_change("market_001", data, log)

        redis.set_market_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_error_propagates(self):
        """Redis error propagates from _handle_tick_size_change → caught by
        _process_message's outer except handler at runtime."""
        from src.infrastructure.polymarket.ws_client import PolymarketWSClient

        redis = AsyncMock()
        redis.set_market_metadata = AsyncMock(
            side_effect=RuntimeError("Redis connection lost")
        )

        ws = PolymarketWSClient(redis=redis)
        data = {"event_type": "tick_size_change", "new_tick_size": "0.001"}

        import structlog
        log = structlog.get_logger("test")

        # Error propagates to caller (handled at higher level by _process_message)
        with pytest.raises(RuntimeError, match="Redis connection lost"):
            await ws._handle_tick_size_change("market_001", data, log)


# ═══════════════════════════════════════════════════════════════════════
# 6. _cache_market_info (MarketService)
# ═══════════════════════════════════════════════════════════════════════

class TestCacheMarketInfo:

    def _make_market(self) -> Market:
        return Market(
            id="0xabcd1234abcd1234abcd1234abcd1234abcd1234",
            asset=Asset.BTC,
            window=Window.M5,
            question="BTC up or down?",
            status=MarketStatus.ACTIVE,
            yes_token_id="12345678901234567890",
            no_token_id="98765432109876543210",
            yes_price=0.55,
            no_price=0.45,
            volume_24h=5000.0,
            expiry=datetime(2026, 12, 31),
            neg_risk=False,
            tick_size="0.01",
            min_order_size=1.0,
        )

    @pytest.mark.asyncio
    async def test_caches_metadata_from_redis(self):
        """Fetches tick, reads metadata from Redis, updates Market entity."""
        from src.application.services.market_service import MarketService

        market = self._make_market()

        # Mocks
        market_data = AsyncMock()
        market_data.get_market_tick = AsyncMock(
            return_value=MagicMock()
        )

        redis = AsyncMock()
        redis.get_market_metadata = AsyncMock(return_value={
            "tick_size": "0.001",
            "neg_risk": True,
            "min_order_size": 10.0,
        })
        redis.set_market = AsyncMock()

        repo = AsyncMock()

        service = MarketService(
            market_data_port=market_data,
            repository=repo,
            redis=redis,
        )

        await service._cache_market_info(market)

        # Verify market entity was updated
        assert market.tick_size == "0.001"
        assert market.neg_risk is True
        assert market.min_order_size == 10.0

        # Verify updated entity was persisted to Redis
        redis.set_market.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_metadata_only_tick_size(self):
        """Only tick_size in metadata → only that field updated."""
        from src.application.services.market_service import MarketService

        market = self._make_market()
        original_neg_risk = market.neg_risk
        original_mos = market.min_order_size

        market_data = AsyncMock()
        market_data.get_market_tick = AsyncMock(return_value=MagicMock())

        redis = AsyncMock()
        redis.get_market_metadata = AsyncMock(return_value={
            "tick_size": "0.001",
        })
        redis.set_market = AsyncMock()

        repo = AsyncMock()

        service = MarketService(
            market_data_port=market_data,
            repository=repo,
            redis=redis,
        )

        await service._cache_market_info(market)

        assert market.tick_size == "0.001"
        assert market.neg_risk == original_neg_risk  # unchanged
        assert market.min_order_size == original_mos  # unchanged

    @pytest.mark.asyncio
    async def test_empty_metadata_no_change(self):
        """Empty metadata from Redis → Market fields unchanged."""
        from src.application.services.market_service import MarketService

        market = self._make_market()
        original = (market.tick_size, market.neg_risk, market.min_order_size)

        market_data = AsyncMock()
        market_data.get_market_tick = AsyncMock(return_value=MagicMock())

        redis = AsyncMock()
        redis.get_market_metadata = AsyncMock(return_value={})
        redis.set_market = AsyncMock()

        repo = AsyncMock()

        service = MarketService(
            market_data_port=market_data,
            repository=repo,
            redis=redis,
        )

        await service._cache_market_info(market)

        assert (market.tick_size, market.neg_risk, market.min_order_size) == original

    @pytest.mark.asyncio
    async def test_get_market_tick_failure_handled(self):
        """get_market_tick raises → method doesn't crash, skips silently."""
        from src.application.services.market_service import MarketService

        market = self._make_market()

        market_data = AsyncMock()
        market_data.get_market_tick = AsyncMock(
            side_effect=RuntimeError("REST API timeout")
        )

        redis = AsyncMock()
        redis.get_market_metadata = AsyncMock(return_value={})

        repo = AsyncMock()

        service = MarketService(
            market_data_port=market_data,
            repository=repo,
            redis=redis,
        )

        # Should NOT raise
        await service._cache_market_info(market)

    @pytest.mark.asyncio
    async def test_redis_get_metadata_failure_handled(self):
        """get_market_metadata raises → skips silently, Market unchanged."""
        from src.application.services.market_service import MarketService

        market = self._make_market()
        original = (market.tick_size, market.neg_risk, market.min_order_size)

        market_data = AsyncMock()
        market_data.get_market_tick = AsyncMock(return_value=MagicMock())

        redis = AsyncMock()
        redis.get_market_metadata = AsyncMock(
            side_effect=RuntimeError("Redis connection lost")
        )

        repo = AsyncMock()

        service = MarketService(
            market_data_port=market_data,
            repository=repo,
            redis=redis,
        )

        # Should NOT raise
        await service._cache_market_info(market)
        assert (market.tick_size, market.neg_risk, market.min_order_size) == original

    @pytest.mark.asyncio
    async def test_all_metadata_fields_updated(self):
        """All three fields present → all updated correctly."""
        from src.application.services.market_service import MarketService

        market = self._make_market()

        market_data = AsyncMock()
        market_data.get_market_tick = AsyncMock(return_value=MagicMock())

        redis = AsyncMock()
        redis.get_market_metadata = AsyncMock(return_value={
            "tick_size": "0.0001",
            "neg_risk": False,
            "min_order_size": 25.0,
        })
        redis.set_market = AsyncMock()

        repo = AsyncMock()

        service = MarketService(
            market_data_port=market_data,
            repository=repo,
            redis=redis,
        )

        await service._cache_market_info(market)

        assert market.tick_size == "0.0001"
        assert market.neg_risk is False
        assert market.min_order_size == 25.0
