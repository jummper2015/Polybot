"""Unit tests for scripts/smoke_test_pipeline.py.

Cubre:
- fetch_active_crypto_markets: ranking por volume24hr y filtrado por asset.
- build_market_from_gamma: parseo de los JSON-strings de Gamma (clobTokenIds,
  outcomePrices) y fallbacks ante campos faltantes.
- _parse_json_list / _parse_end_date: helpers internos defensivos.
- run_single_cycle: captura excepciones del TradingService sin propagar.
- build_report: validaciones por objetivo según el resultado de los ciclos.
- main (CLI): exit codes 0/1/2 según escenario.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scripts.smoke_test_pipeline as smoke
from src.domain.entities.market import Market
from src.domain.enums.asset import Asset
from src.domain.enums.market_status import MarketStatus
from src.domain.enums.window import Window

# ── Helpers ───────────────────────────────────────────────────────────────────


def _btc_gamma_dict(volume: float = 1000.0, slug_suffix: str = "btc-1") -> dict:
    return {
        "conditionId": f"0xbtc{slug_suffix}",
        "question": "Will Bitcoin hit $1m before GTA VI?",
        "slug": f"will-bitcoin-hit-1m-before-{slug_suffix}",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.4925", "0.5075"]),
        "clobTokenIds": json.dumps(["10526756", "91863162"]),
        "volume24hr": volume,
        "endDate": "2026-07-31T12:00:00Z",
        "orderPriceMinTickSize": 0.001,
        "orderMinSize": 5,
        "negRisk": False,
    }


def _eth_gamma_dict(volume: float = 500.0, slug_suffix: str = "eth-1") -> dict:
    return {
        "conditionId": f"0xeth{slug_suffix}",
        "question": "Will Ethereum airdrop happen?",
        "slug": f"will-ethereum-airdrop-{slug_suffix}",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.2", "0.8"]),
        "clobTokenIds": json.dumps(["111", "222"]),
        "volume24hr": volume,
        "endDate": "2026-08-01T00:00:00Z",
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 1,
    }


def _make_market(market_id: str = "0xabc", asset: Asset = Asset.BTC) -> Market:
    return Market(
        id=market_id,
        asset=asset,
        window=Window.M15,
        question="q",
        status=MarketStatus.ACTIVE,
        yes_token_id="t1",
        no_token_id="t2",
        yes_price=0.5,
        no_price=0.5,
        volume_24h=100.0,
        expiry=datetime.utcnow() + timedelta(days=1),
    )


def _default_args(**overrides) -> argparse.Namespace:
    base = dict(
        n_cycles=2,
        warmup_ticks=2,
        cycle_interval=0.0,
        warmup_interval=0.0,
        limit_per_asset=2,
        force_fake_signal=False,
        force_amount=5.0,
        output=None,
        log_level="WARNING",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# ── 1. fetch_active_crypto_markets ────────────────────────────────────────────


class TestFetchActiveCryptoMarkets:
    @pytest.mark.asyncio
    async def test_returns_top_by_volume_per_asset(self):
        markets = [
            _btc_gamma_dict(volume=100, slug_suffix="btc-low"),
            _btc_gamma_dict(volume=900, slug_suffix="btc-high"),
            _btc_gamma_dict(volume=500, slug_suffix="btc-mid"),
            _eth_gamma_dict(volume=50, slug_suffix="eth-low"),
            _eth_gamma_dict(volume=600, slug_suffix="eth-high"),
        ]
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                json=MagicMock(return_value=markets),
                raise_for_status=MagicMock(),
            )
        )
        result = await smoke.fetch_active_crypto_markets(
            limit_per_asset=2, client=mock_client,
        )
        # 2 BTC + 2 ETH = 4
        assert len(result) == 4
        btc = [m for m in result if "bitcoin" in m["question"].lower()]
        eth = [m for m in result if "ethereum" in m["question"].lower()]
        assert len(btc) == 2 and len(eth) == 2
        # Top BTC = volume 900
        assert btc[0]["volume24hr"] == 900
        # Top ETH = volume 600
        assert eth[0]["volume24hr"] == 600

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self):
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                json=MagicMock(return_value=[]),
                raise_for_status=MagicMock(),
            )
        )
        result = await smoke.fetch_active_crypto_markets(client=mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_non_list_response_returns_empty(self):
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                json=MagicMock(return_value={"error": "bad"}),
                raise_for_status=MagicMock(),
            )
        )
        result = await smoke.fetch_active_crypto_markets(client=mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_filters_out_non_crypto_markets(self):
        # 2 BTC, 1 ETH, 1 político → debe quedar 2 BTC + 1 ETH
        markets = [
            _btc_gamma_dict(volume=100),
            _btc_gamma_dict(volume=200, slug_suffix="btc-2"),
            _eth_gamma_dict(volume=50),
            {
                "conditionId": "0xpol",
                "question": "Will Trump win 2028?",
                "slug": "trump-2028",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.5","0.5"]',
                "clobTokenIds": '["1","2"]',
                "volume24hr": 99999.0,
                "endDate": "2028-11-01T00:00:00Z",
            },
        ]
        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=MagicMock(
                json=MagicMock(return_value=markets),
                raise_for_status=MagicMock(),
            )
        )
        result = await smoke.fetch_active_crypto_markets(
            limit_per_asset=5, client=mock_client,
        )
        assert len(result) == 3
        assert all("Trump" not in m["question"] for m in result)


# ── 2. build_market_from_gamma ────────────────────────────────────────────────


class TestBuildMarketFromGamma:
    def test_parses_standard_btc_dict(self):
        raw = _btc_gamma_dict()
        market = smoke.build_market_from_gamma(raw)
        assert market.asset == Asset.BTC
        assert market.window == Window.M15  # placeholder
        assert market.id == "0xbtcbtc-1"
        assert market.yes_token_id == "10526756"
        assert market.no_token_id == "91863162"
        assert market.yes_price == pytest.approx(0.4925)
        assert market.no_price == pytest.approx(0.5075)
        assert market.tick_size == "0.001"
        assert market.min_order_size == 5.0
        assert market.status == MarketStatus.ACTIVE

    def test_parses_eth_dict(self):
        raw = _eth_gamma_dict()
        market = smoke.build_market_from_gamma(raw)
        assert market.asset == Asset.ETH
        assert market.tick_size == "0.01"

    def test_missing_token_ids_fallback_empty(self):
        raw = _btc_gamma_dict()
        raw["clobTokenIds"] = None
        market = smoke.build_market_from_gamma(raw)
        assert market.yes_token_id == ""
        assert market.no_token_id == ""

    def test_missing_outcome_prices_fallback_default(self):
        raw = _btc_gamma_dict()
        raw["outcomePrices"] = None
        market = smoke.build_market_from_gamma(raw)
        assert market.yes_price == 0.5
        assert market.no_price == 0.5

    def test_missing_end_date_defaults_to_future(self):
        raw = _btc_gamma_dict()
        raw["endDate"] = None
        raw["endDateIso"] = None
        market = smoke.build_market_from_gamma(raw)
        assert market.expiry > datetime.utcnow()

    def test_invalid_asset_raises(self):
        raw = {
            "conditionId": "0xpol",
            "question": "Will Trump win?",
            "slug": "trump",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "clobTokenIds": '["1","2"]',
        }
        with pytest.raises(ValueError, match="asset BTC/ETH"):
            smoke.build_market_from_gamma(raw)

    def test_handles_neg_risk_true(self):
        raw = _btc_gamma_dict()
        raw["negRisk"] = True
        market = smoke.build_market_from_gamma(raw)
        assert market.neg_risk is True


# ── 3. Helpers: _parse_json_list / _parse_end_date ────────────────────────────


class TestParseHelpers:
    def test_parse_json_list_string(self):
        assert smoke._parse_json_list('["a","b"]') == ["a", "b"]

    def test_parse_json_list_none(self):
        assert smoke._parse_json_list(None) == []

    def test_parse_json_list_already_list(self):
        assert smoke._parse_json_list(["a", "b"]) == ["a", "b"]

    def test_parse_json_list_invalid_json(self):
        assert smoke._parse_json_list("not json") == []

    def test_parse_end_date_iso_z(self):
        result = smoke._parse_end_date({"endDate": "2026-07-31T12:00:00Z"})
        assert result.year == 2026 and result.month == 7

    def test_parse_end_date_missing_defaults_future(self):
        result = smoke._parse_end_date({})
        assert result > datetime.utcnow()


# ── 4. run_single_cycle ───────────────────────────────────────────────────────


class TestRunSingleCycle:
    @pytest.mark.asyncio
    async def test_returns_record_on_success(self):
        market = _make_market()
        container = MagicMock()
        container.trading_service._run_market_cycle = AsyncMock(return_value=None)
        container.repository.get_positions = AsyncMock(return_value=[])
        container.execution_handler.get_balance = MagicMock(return_value=1000.0)

        rec = await smoke.run_single_cycle(container, market, cycle_num=1)
        assert rec.error is None
        assert rec.cycle == 1
        assert rec.market_id == market.id
        assert rec.duration_ms >= 0
        assert rec.balance_before == 1000.0
        assert rec.balance_after == 1000.0

    @pytest.mark.asyncio
    async def test_captures_exception_without_propagating(self):
        market = _make_market()
        container = MagicMock()
        container.trading_service._run_market_cycle = AsyncMock(
            side_effect=RuntimeError("boom"),
        )
        container.repository.get_positions = AsyncMock(return_value=[])
        container.execution_handler.get_balance = MagicMock(return_value=999.0)

        rec = await smoke.run_single_cycle(container, market, cycle_num=2)
        assert rec.error is not None
        assert "RuntimeError" in rec.error
        assert "boom" in rec.error

    @pytest.mark.asyncio
    async def test_detects_new_position(self):
        market = _make_market()
        container = MagicMock()
        container.trading_service._run_market_cycle = AsyncMock(return_value=None)
        # 0 antes, 1 después → orden inferida
        container.repository.get_positions = AsyncMock(
            side_effect=[[], [MagicMock()]],
        )
        container.execution_handler.get_balance = MagicMock(return_value=995.0)

        rec = await smoke.run_single_cycle(container, market, cycle_num=3)
        assert rec.positions_before == 0
        assert rec.positions_after == 1


# ── 5. build_report ───────────────────────────────────────────────────────────


class TestBuildReport:
    def test_no_markets_returns_fail_status(self):
        report = smoke.build_report([], [], [], [], 0.5, {})
        assert report["status"] == "fail_no_markets"
        assert report["validations"][smoke.OBJ1_KEY] == "FAIL_NO_MARKETS"
        assert report["validations"][smoke.OBJ2_KEY] == "NOT_RUN"
        assert report["validations"][smoke.OBJ3_KEY] == smoke.OBJ3_BLOCKED

    def test_pass_no_signal_when_no_errors_no_orders(self):
        m = _make_market()
        wr = smoke.WarmupResult(
            market_id=m.id, ticks_fetched=20, ticks_unavailable=0,
            price_min=0.49, price_max=0.50,
        )
        cr = smoke.CycleRecord(
            cycle=1, market_id=m.id, duration_ms=100.0, error=None,
            positions_before=0, positions_after=0,
            balance_before=1000.0, balance_after=1000.0,
        )
        report = smoke.build_report([m], [wr], [cr], [], 1.0, {})
        assert report["status"] == "success"
        assert report["validations"][smoke.OBJ1_KEY] == "PASS"
        assert report["validations"][smoke.OBJ2_KEY] == "PASS_NO_SIGNAL"

    def test_pass_with_order_when_position_opened(self):
        m = _make_market()
        wr = smoke.WarmupResult(
            market_id=m.id, ticks_fetched=20, ticks_unavailable=0,
            price_min=0.5, price_max=0.5,
        )
        cr = smoke.CycleRecord(
            cycle=1, market_id=m.id, duration_ms=100.0, error=None,
            positions_before=0, positions_after=1,
            balance_before=1000.0, balance_after=995.0,
        )
        report = smoke.build_report([m], [wr], [cr], [], 1.0, {})
        assert report["validations"][smoke.OBJ2_KEY] == "PASS_WITH_ORDER"
        assert report["summary"]["total_orders"] >= 1

    def test_fail_pipeline_on_error(self):
        m = _make_market()
        wr = smoke.WarmupResult(
            market_id=m.id, ticks_fetched=10, ticks_unavailable=0,
            price_min=0.5, price_max=0.5,
        )
        cr = smoke.CycleRecord(
            cycle=1, market_id=m.id, duration_ms=10.0,
            error="RuntimeError: x",
            positions_before=0, positions_after=0,
            balance_before=1000.0, balance_after=1000.0,
        )
        report = smoke.build_report([m], [wr], [cr], [], 1.0, {})
        assert report["status"] == "partial"
        assert report["validations"][smoke.OBJ2_KEY] == "FAIL_PIPELINE_ERROR"

    def test_no_ticks_fetched_marks_obj1_fail(self):
        m = _make_market()
        wr = smoke.WarmupResult(
            market_id=m.id, ticks_fetched=0, ticks_unavailable=25,
            price_min=None, price_max=None,
        )
        cr = smoke.CycleRecord(
            cycle=1, market_id=m.id, duration_ms=10.0, error=None,
            positions_before=0, positions_after=0,
            balance_before=1000.0, balance_after=1000.0,
        )
        report = smoke.build_report([m], [wr], [cr], [], 1.0, {})
        assert report["validations"][smoke.OBJ1_KEY] == "FAIL_NO_TICKS"

    def test_forced_signal_counted_in_orders(self):
        m = _make_market()
        wr = smoke.WarmupResult(
            market_id=m.id, ticks_fetched=10, ticks_unavailable=0,
            price_min=0.5, price_max=0.5,
        )
        cr = smoke.CycleRecord(
            cycle=1, market_id=m.id, duration_ms=10.0, error=None,
            positions_before=0, positions_after=0,
            balance_before=1000.0, balance_after=1000.0,
        )
        forced = [{"market_id": m.id, "success": True, "fill_price": 0.5,
                   "slippage": 0.001, "error": None}]
        report = smoke.build_report([m], [wr], [cr], forced, 1.0, {})
        assert report["validations"][smoke.OBJ2_KEY] == "PASS_WITH_ORDER"
        assert report["summary"]["forced_signals_executed"] == 1


# ── 6. CLI / main ─────────────────────────────────────────────────────────────


class TestMainCLI:
    @pytest.mark.asyncio
    async def test_exit_no_markets_when_gamma_empty(self, tmp_path):
        args = _default_args(output=str(tmp_path / "out.json"))
        with patch.object(
            smoke, "fetch_active_crypto_markets", new=AsyncMock(return_value=[]),
        ):
            report, code = await smoke.run_smoke(args)
        assert code == smoke.EXIT_NO_MARKETS
        assert report["status"] == "fail_no_markets"

    @pytest.mark.asyncio
    async def test_exit_ok_happy_path(self, tmp_path):
        args = _default_args()
        raw_markets = [_btc_gamma_dict()]
        # Mock bootstrap a un container con métodos async
        fake_container = MagicMock()
        fake_container.repository.save_market = AsyncMock()
        fake_container.repository.get_positions = AsyncMock(return_value=[])
        fake_container.redis.set_market = AsyncMock()
        fake_container.market_service.get_market_tick = AsyncMock(return_value=None)
        fake_container.strategy_orchestrator.on_tick = AsyncMock()
        fake_container.trading_service._run_market_cycle = AsyncMock()
        fake_container.execution_handler.get_balance = MagicMock(return_value=1000.0)
        fake_container.shutdown = AsyncMock()

        with patch.object(
            smoke, "fetch_active_crypto_markets",
            new=AsyncMock(return_value=raw_markets),
        ), patch.object(
            smoke, "bootstrap_smoke_container",
            new=AsyncMock(return_value=fake_container),
        ):
            report, code = await smoke.run_smoke(args)

        assert code == smoke.EXIT_OK
        assert report["summary"]["markets_count"] == 1
        assert report["summary"]["total_errors"] == 0

    @pytest.mark.asyncio
    async def test_exit_pipeline_error_when_cycle_throws(self, tmp_path):
        args = _default_args(n_cycles=1, warmup_ticks=0)
        raw_markets = [_btc_gamma_dict()]
        fake_container = MagicMock()
        fake_container.repository.save_market = AsyncMock()
        fake_container.repository.get_positions = AsyncMock(return_value=[])
        fake_container.redis.set_market = AsyncMock()
        fake_container.market_service.get_market_tick = AsyncMock(return_value=None)
        fake_container.strategy_orchestrator.on_tick = AsyncMock()
        fake_container.trading_service._run_market_cycle = AsyncMock(
            side_effect=RuntimeError("pipeline broken"),
        )
        fake_container.execution_handler.get_balance = MagicMock(return_value=1000.0)
        fake_container.shutdown = AsyncMock()

        with patch.object(
            smoke, "fetch_active_crypto_markets",
            new=AsyncMock(return_value=raw_markets),
        ), patch.object(
            smoke, "bootstrap_smoke_container",
            new=AsyncMock(return_value=fake_container),
        ):
            report, code = await smoke.run_smoke(args)

        assert code == smoke.EXIT_PIPELINE_ERROR
        assert report["summary"]["total_errors"] >= 1

    @pytest.mark.asyncio
    async def test_force_fake_signal_invokes_execute_entry(self, tmp_path):
        args = _default_args(n_cycles=1, warmup_ticks=0, force_fake_signal=True)
        raw_markets = [_btc_gamma_dict()]
        fake_container = MagicMock()
        fake_container.repository.save_market = AsyncMock()
        fake_container.repository.get_positions = AsyncMock(return_value=[])
        fake_container.redis.set_market = AsyncMock()
        fake_container.market_service.get_market_tick = AsyncMock(return_value=None)
        fake_container.strategy_orchestrator.on_tick = AsyncMock()
        fake_container.trading_service._run_market_cycle = AsyncMock()
        fake_container.execution_handler.get_balance = MagicMock(return_value=1000.0)
        fake_container.execution_handler.execute_entry = AsyncMock(
            return_value=MagicMock(success=True, fill_price=0.5, slippage=0.001),
        )
        fake_container.shutdown = AsyncMock()

        with patch.object(
            smoke, "fetch_active_crypto_markets",
            new=AsyncMock(return_value=raw_markets),
        ), patch.object(
            smoke, "bootstrap_smoke_container",
            new=AsyncMock(return_value=fake_container),
        ):
            report, code = await smoke.run_smoke(args)

        assert code == smoke.EXIT_OK
        assert fake_container.execution_handler.execute_entry.await_count >= 1
        assert report["summary"]["forced_signals_executed"] == 1


# ── 7. write_report ───────────────────────────────────────────────────────────


class TestWriteReport:
    def test_writes_json_and_latest_pointer(self, tmp_path):
        report = {"status": "success", "validations": {}}
        out = tmp_path / "smoke_test_pipeline_20260615.json"
        result_path = smoke.write_report(report, out)
        assert result_path == out
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["status"] == "success"
        latest = tmp_path / "smoke_test_pipeline_latest.json"
        assert latest.exists()


# ── 8. CLI parser ─────────────────────────────────────────────────────────────


class TestParseArgs:
    def test_defaults(self):
        args = smoke.parse_args([])
        assert args.n_cycles == smoke.DEFAULT_N_CYCLES
        assert args.warmup_ticks == smoke.DEFAULT_WARMUP_TICKS
        assert args.force_fake_signal is False

    def test_overrides(self):
        args = smoke.parse_args(
            ["--n-cycles", "3", "--warmup-ticks", "5", "--force-fake-signal"],
        )
        assert args.n_cycles == 3
        assert args.warmup_ticks == 5
        assert args.force_fake_signal is True
