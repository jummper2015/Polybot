"""
Tests for PolymarketHTTPClient.get_active_markets — cursor stateless contract.

Bug fixed (2026-06-22, dashboard fix):
  The old implementation resumed from a persisted `discovery:cursor` in
  Redis (TTL 2h). Keyset pagination on Gamma is ordered by volume24hr desc;
  resuming from a stale cursor skipped page 1 where the top live crypto
  M5/M15 markets live. Reboots with a warm Redis silently truncated
  discovery to whatever lived after the cursor.

These tests pin the new contract:
  - get_active_markets never reads `get_discovery_cursor` from Redis.
  - get_active_markets never writes `set_discovery_cursor` to Redis.
  - Two consecutive invocations re-fetch from page 1 each time.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.infrastructure.polymarket.http_client import PolymarketHTTPClient


def _page_response(events: list[dict], next_cursor: str | None) -> httpx.Response:
    """Builds a fake httpx.Response mimicking the Gamma /events/keyset shape."""
    body = {"events": events, "next_cursor": next_cursor}
    return httpx.Response(
        status_code=200,
        json=body,
        request=httpx.Request("GET", "https://gamma-api.polymarket.com/events/keyset"),
    )


def _event_with_one_market(condition_id: str = "0x" + "f" * 64) -> dict:
    """Minimal /events/keyset event payload with one valid market inside."""
    return {
        "markets": [
            {
                "conditionId": condition_id,
                "question":    "Test market",
                "slug":        "test-market",
                "active":      True,
                "endDate":     "2099-01-01T00:00:00Z",
                "clobTokenIds": '["1", "2"]',
                "outcomes":     '["Up", "Down"]',
                "outcomePrices": '["0.5", "0.5"]',
            }
        ]
    }


@pytest.fixture
def ws_client() -> MagicMock:
    mock = MagicMock()
    mock.unsubscribe_all = AsyncMock()
    return mock


class TestCursorStateless:
    """get_active_markets must NOT read or write the persisted cursor."""

    @pytest.mark.asyncio
    async def test_redis_cursor_never_read(self, ws_client: MagicMock) -> None:
        redis = MagicMock()
        # Stub all redis methods the discovery COULD call. If get_discovery_cursor
        # is invoked, the test fails (AttributeError) — the method no longer exists.
        client = PolymarketHTTPClient(ws_client=ws_client, redis=redis)

        # Mock the HTTP transport to return a single page with next_cursor=None.
        async def fake_get(*args, **kwargs):
            return _page_response([_event_with_one_market()], next_cursor=None)

        client._http.get = AsyncMock(side_effect=fake_get)

        markets = await client.get_active_markets(asset="all", window="all")
        assert len(markets) == 1

        # Redis should have ZERO interactions for cursor-related operations
        for method_name in (
            "get_discovery_cursor",
            "set_discovery_cursor",
            "_redis",
        ):
            attr = getattr(redis, method_name, None)
            if isinstance(attr, MagicMock) or isinstance(attr, AsyncMock):
                assert not attr.called, (
                    f"Redis.{method_name} should not be touched by discovery"
                )

        await client.close()

    @pytest.mark.asyncio
    async def test_redis_cursor_never_written(self, ws_client: MagicMock) -> None:
        redis = MagicMock()
        client = PolymarketHTTPClient(ws_client=ws_client, redis=redis)

        # Two pages so there IS a next_cursor mid-iteration. The old code
        # would have called set_discovery_cursor with the final cursor.
        responses = iter([
            _page_response([_event_with_one_market("0x" + "1" * 64)],
                           next_cursor="cursor-page-2"),
            _page_response([_event_with_one_market("0x" + "2" * 64)],
                           next_cursor=None),
        ])

        async def fake_get(*args, **kwargs):
            return next(responses)

        client._http.get = AsyncMock(side_effect=fake_get)

        markets = await client.get_active_markets(asset="all", window="all")
        assert len(markets) == 2

        # No set_discovery_cursor call should exist
        set_cursor = getattr(redis, "set_discovery_cursor", None)
        if set_cursor is not None and isinstance(set_cursor, (MagicMock, AsyncMock)):
            assert not set_cursor.called

        await client.close()

    @pytest.mark.asyncio
    async def test_two_consecutive_calls_start_from_page_one(
        self, ws_client: MagicMock
    ) -> None:
        """Two back-to-back calls must each issue a request WITHOUT after_cursor."""
        redis = MagicMock()
        client = PolymarketHTTPClient(ws_client=ws_client, redis=redis)

        captured_params: list[dict] = []

        async def fake_get(*args, **kwargs):
            captured_params.append(dict(kwargs.get("params", {})))
            return _page_response(
                [_event_with_one_market(f"0x{len(captured_params):064x}"[:66])],
                next_cursor=None,
            )

        client._http.get = AsyncMock(side_effect=fake_get)

        await client.get_active_markets(asset="all", window="all")
        await client.get_active_markets(asset="all", window="all")

        assert len(captured_params) == 2
        for call_params in captured_params:
            assert "after_cursor" not in call_params, (
                f"Second call should not carry a cursor across calls. "
                f"Got: {call_params}"
            )

        await client.close()
