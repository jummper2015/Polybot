"""Unit tests for scripts/verify_polymarket_connectivity.py.

Covers:
- Env validation (paso 0).
- Happy path: 6 pasos OK con SDK + Data API mockeados.
- Fallo de auth L1/L2 → exit code 2.
- Posiciones vacías y balance 0 (escenario "wallet recién creada").
- Salida JSON.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import scripts.verify_polymarket_connectivity as vpc

# ── Helpers ────────────────────────────────────────────────────────────


def _set_env(monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc123")
    monkeypatch.setenv("POLYMARKET_API_KEY", "k")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "s")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "p")
    monkeypatch.setenv(
        "POLYMARKET_WALLET_ADDRESS",
        "0x1234567890abcdef1234567890abcdef12345678",
    )


def _make_args(**overrides):
    args = MagicMock()
    args.json = False
    args.trades_limit = 50
    args.trades_sample = 3
    args.positions_sample = 3
    args.orders_sample = 3
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


class FakeCLOB:
    def __init__(self, **overrides):
        self.responses = {
            "assert_auth": {
                "wallet": "0x1234567890abcdef1234567890abcdef12345678",
                "l1": True,
                "l2": True,
            },
            "get_balance": 123.456789,
            "get_open_orders": [{"id": "o1", "side": "BUY"}],
            "get_trades": [
                {"side": "BUY", "price": "0.5", "size": "10"},
                {"side": "SELL", "price": "0.6", "size": "10"},
                {"side": "BUY", "price": "0.55", "size": "5"},
            ],
        }
        self.responses.update(overrides)
        self.closed = False

    async def assert_auth(self):
        return self._resolve("assert_auth")

    async def get_balance(self):
        return self._resolve("get_balance")

    async def get_open_orders(self):
        return self._resolve("get_open_orders")

    async def get_trades(self, limit=None):
        return self._resolve("get_trades")

    def _resolve(self, key):
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self):
        self.closed = True


class FakeDataAPI:
    def __init__(self, **overrides):
        self.responses = {
            "get_positions": [
                {
                    "conditionId": "0xaa",
                    "size": 10,
                    "avgPrice": 0.5,
                    "initialValue": 5.0,
                    "currentValue": 6.0,
                    "cashPnl": 1.0,
                    "redeemable": False,
                },
                {
                    "conditionId": "0xbb",
                    "size": 5,
                    "avgPrice": 0.4,
                    "initialValue": 2.0,
                    "currentValue": 2.5,
                    "cashPnl": 0.5,
                    "redeemable": True,
                },
            ],
            "get_activity": [{"type": "TRADE"}, {"type": "REDEEM"}],
        }
        self.responses.update(overrides)
        self.closed = False

    async def get_positions(self):
        return self._resolve("get_positions")

    async def get_activity(self, limit=50, activity_type=None):
        return self._resolve("get_activity")

    def _resolve(self, key):
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self):
        self.closed = True


# ── check_env ──────────────────────────────────────────────────────────


class TestCheckEnv:
    def test_missing_all(self, monkeypatch):
        for var in vpc.REQUIRED_ENV:
            monkeypatch.delenv(var, raising=False)
        report = vpc.Report()
        assert vpc.check_env(report) is False
        assert report.steps[0].ok is False
        assert "Faltan variables" in report.steps[0].detail

    def test_partial(self, monkeypatch):
        for var in vpc.REQUIRED_ENV:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x")
        report = vpc.Report()
        assert vpc.check_env(report) is False

    def test_all_present(self, monkeypatch):
        _set_env(monkeypatch)
        report = vpc.Report()
        assert vpc.check_env(report) is True
        assert report.steps[0].ok is True


# ── Aggregations ───────────────────────────────────────────────────────


class TestSummarize:
    def test_positions_aggregates(self):
        positions = [
            {"initialValue": 10, "currentValue": 12, "cashPnl": 2, "redeemable": True},
            {"initialValue": 5, "currentValue": 4, "cashPnl": -1, "redeemable": False},
        ]
        summary = vpc.summarize_positions(positions)
        assert summary["total"] == 2
        assert summary["redeemable"] == 1
        assert summary["initial_value_pusd"] == 15.0
        assert summary["current_value_pusd"] == 16.0
        assert summary["cash_pnl_pusd"] == 1.0

    def test_positions_handles_none_fields(self):
        positions = [{"initialValue": None, "currentValue": None, "cashPnl": None}]
        summary = vpc.summarize_positions(positions)
        assert summary["cash_pnl_pusd"] == 0.0

    def test_trades_counts_sides(self):
        trades = [{"side": "BUY"}, {"side": "buy"}, {"side": "SELL"}, {}]
        summary = vpc.summarize_trades(trades)
        assert summary["total"] == 4
        assert summary["buy"] == 2
        assert summary["sell"] == 2


# ── verify() — async pipeline ──────────────────────────────────────────


class TestVerify:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        _set_env(monkeypatch)

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        clob = FakeCLOB()
        data_api = FakeDataAPI()

        monkeypatch.setattr(
            "src.infrastructure.security.key_manager.KeyManager",
            lambda: MagicMock(
                wallet_address="0x1234567890abcdef1234567890abcdef12345678"
            ),
        )
        monkeypatch.setattr(
            "src.infrastructure.polymarket.clob_client.PolymarketCLOBClient",
            lambda key_manager: clob,
        )
        monkeypatch.setattr(
            "src.infrastructure.polymarket.data_api_client.DataAPIClient",
            lambda wallet_address: data_api,
        )

        report = await vpc.verify(_make_args())

        assert report.all_ok is True
        names = [s.name for s in report.steps]
        assert names == [
            "env",
            "init_clients",
            "auth_l1_l2",
            "balance_pusd",
            "data_api_positions",
            "clob_open_orders",
            "clob_trades",
            "data_api_activity",
        ]
        balance_step = next(s for s in report.steps if s.name == "balance_pusd")
        assert balance_step.data == 123.456789
        pos_step = next(s for s in report.steps if s.name == "data_api_positions")
        assert pos_step.data["summary"]["total"] == 2
        assert pos_step.data["summary"]["redeemable"] == 1
        trades_step = next(s for s in report.steps if s.name == "clob_trades")
        assert trades_step.data["summary"]["buy"] == 2
        assert clob.closed is True
        assert data_api.closed is True

    @pytest.mark.asyncio
    async def test_auth_failure_marks_step_but_continues(self, monkeypatch):
        clob = FakeCLOB(assert_auth=RuntimeError("L2 rejected"))
        data_api = FakeDataAPI()

        monkeypatch.setattr(
            "src.infrastructure.security.key_manager.KeyManager",
            lambda: MagicMock(
                wallet_address="0x1234567890abcdef1234567890abcdef12345678"
            ),
        )
        monkeypatch.setattr(
            "src.infrastructure.polymarket.clob_client.PolymarketCLOBClient",
            lambda key_manager: clob,
        )
        monkeypatch.setattr(
            "src.infrastructure.polymarket.data_api_client.DataAPIClient",
            lambda wallet_address: data_api,
        )

        report = await vpc.verify(_make_args())

        assert report.all_ok is False
        auth_step = next(s for s in report.steps if s.name == "auth_l1_l2")
        assert auth_step.ok is False
        assert "L2 rejected" in auth_step.detail
        # Cleanup ran despite the failure
        assert clob.closed is True
        assert data_api.closed is True

    @pytest.mark.asyncio
    async def test_empty_wallet(self, monkeypatch):
        clob = FakeCLOB(get_balance=0.0, get_open_orders=[], get_trades=[])
        data_api = FakeDataAPI(get_positions=[], get_activity=[])

        monkeypatch.setattr(
            "src.infrastructure.security.key_manager.KeyManager",
            lambda: MagicMock(
                wallet_address="0x1234567890abcdef1234567890abcdef12345678"
            ),
        )
        monkeypatch.setattr(
            "src.infrastructure.polymarket.clob_client.PolymarketCLOBClient",
            lambda key_manager: clob,
        )
        monkeypatch.setattr(
            "src.infrastructure.polymarket.data_api_client.DataAPIClient",
            lambda wallet_address: data_api,
        )

        report = await vpc.verify(_make_args())

        assert report.all_ok is True
        balance_step = next(s for s in report.steps if s.name == "balance_pusd")
        assert balance_step.data == 0.0
        pos_step = next(s for s in report.steps if s.name == "data_api_positions")
        assert pos_step.data["summary"]["total"] == 0

    @pytest.mark.asyncio
    async def test_init_failure_recorded_as_step(self, monkeypatch):
        """Bad private key (hex mal formado) debe registrarse como init_clients=fail."""

        def boom():
            raise ValueError("Non-hexadecimal digit found")

        monkeypatch.setattr(
            "src.infrastructure.security.key_manager.KeyManager",
            boom,
        )

        report = await vpc.verify(_make_args())

        assert report.all_ok is False
        names = [s.name for s in report.steps]
        assert names == ["env", "init_clients"]
        assert report.steps[-1].ok is False
        assert "Non-hexadecimal" in report.steps[-1].detail

    @pytest.mark.asyncio
    async def test_missing_env_short_circuits(self, monkeypatch):
        for var in vpc.REQUIRED_ENV:
            monkeypatch.delenv(var, raising=False)

        # KeyManager should NOT be instantiated when env is missing
        sentinel = MagicMock(side_effect=AssertionError("KeyManager should not run"))
        monkeypatch.setattr(
            "src.infrastructure.security.key_manager.KeyManager",
            sentinel,
        )

        report = await vpc.verify(_make_args())

        assert report.all_ok is False
        assert len(report.steps) == 1
        assert report.steps[0].name == "env"
        assert report.steps[0].ok is False


# ── main() — exit codes & rendering ────────────────────────────────────


class TestMain:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        _set_env(monkeypatch)

    def test_exit_ok_when_all_steps_pass(self, monkeypatch, capsys):
        report = vpc.Report(wallet_masked="0x1234...5678")
        report.steps.append(vpc.StepResult(name="env", ok=True))
        report.steps.append(
            vpc.StepResult(name="balance_pusd", ok=True, data=10.0, elapsed_ms=1.2)
        )

        async def fake_verify(args):
            return report

        monkeypatch.setattr(vpc, "verify", fake_verify)
        monkeypatch.setattr("sys.argv", ["verify_polymarket_connectivity.py"])

        code = vpc.main()
        assert code == vpc.EXIT_OK
        out = capsys.readouterr().out
        assert "Conectividad verificada" in out
        assert "0x1234...5678" in out

    def test_exit_missing_env(self, monkeypatch, capsys):
        report = vpc.Report()
        report.steps.append(
            vpc.StepResult(name="env", ok=False, detail="Faltan variables: [...]")
        )

        async def fake_verify(args):
            return report

        monkeypatch.setattr(vpc, "verify", fake_verify)
        monkeypatch.setattr("sys.argv", ["verify_polymarket_connectivity.py"])

        code = vpc.main()
        assert code == vpc.EXIT_MISSING_ENV

    def test_exit_failed_when_step_fails(self, monkeypatch):
        report = vpc.Report()
        report.steps.append(vpc.StepResult(name="env", ok=True))
        report.steps.append(
            vpc.StepResult(name="auth_l1_l2", ok=False, detail="HTTPError")
        )

        async def fake_verify(args):
            return report

        monkeypatch.setattr(vpc, "verify", fake_verify)
        monkeypatch.setattr("sys.argv", ["verify_polymarket_connectivity.py"])

        code = vpc.main()
        assert code == vpc.EXIT_FAILED

    def test_json_output(self, monkeypatch, capsys):
        report = vpc.Report(wallet_masked="0x1234...5678")
        report.steps.append(vpc.StepResult(name="env", ok=True))
        report.steps.append(
            vpc.StepResult(name="balance_pusd", ok=True, data=10.0)
        )

        async def fake_verify(args):
            return report

        monkeypatch.setattr(vpc, "verify", fake_verify)
        monkeypatch.setattr(
            "sys.argv", ["verify_polymarket_connectivity.py", "--json"]
        )

        code = vpc.main()
        assert code == vpc.EXIT_OK
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["all_ok"] is True
        assert parsed["wallet"] == "0x1234...5678"
        assert len(parsed["steps"]) == 2


# ── New CLOB & Data API wrappers (read-only) ───────────────────────────


class TestClobReadOnlyWrappers:
    """Tests for the new assert_auth / get_open_orders / get_trades."""

    def _make_client(self, sdk):
        from src.infrastructure.polymarket.clob_client import PolymarketCLOBClient

        km = MagicMock()
        km.api_key = "k"
        km.api_secret = "s"
        km.api_passphrase = "p"
        km.private_key = "0xabc"
        km.wallet_address = "0x1234567890abcdef1234567890abcdef12345678"
        km.builder_code = ""
        km.signature_type = 1

        with patch(
            "src.infrastructure.polymarket.clob_client.ClobClient"
        ) as mock_sdk:
            mock_sdk.return_value = sdk
            with patch(
                "src.infrastructure.polymarket.clob_client.httpx.AsyncClient"
            ) as mock_http:
                mock_http.return_value = AsyncMock()
                return PolymarketCLOBClient(key_manager=km)

    @pytest.mark.asyncio
    async def test_assert_auth_calls_l1_and_l2(self):
        sdk = MagicMock()
        sdk.get_address = MagicMock(
            return_value="0x1234567890abcdef1234567890abcdef12345678"
        )
        sdk.assert_level_1_auth = MagicMock(return_value=True)
        sdk.assert_level_2_auth = MagicMock(return_value=True)

        client = self._make_client(sdk)

        result = await client.assert_auth()

        sdk.get_address.assert_called_once()
        sdk.assert_level_1_auth.assert_called_once()
        sdk.assert_level_2_auth.assert_called_once()
        assert result == {
            "wallet": "0x1234567890abcdef1234567890abcdef12345678",
            "l1": True,
            "l2": True,
        }

    @pytest.mark.asyncio
    async def test_assert_auth_propagates_l1_failure(self):
        sdk = MagicMock()
        sdk.get_address = MagicMock(return_value="0xwallet")
        sdk.assert_level_1_auth = MagicMock(side_effect=RuntimeError("bad sig"))

        client = self._make_client(sdk)

        with pytest.raises(RuntimeError, match="bad sig"):
            await client.assert_auth()

    @pytest.mark.asyncio
    async def test_get_open_orders_returns_list(self):
        sdk = MagicMock()
        sdk.get_open_orders = MagicMock(
            return_value=[{"id": "o1"}, {"id": "o2"}]
        )

        client = self._make_client(sdk)

        result = await client.get_open_orders()

        assert result == [{"id": "o1"}, {"id": "o2"}]

    @pytest.mark.asyncio
    async def test_get_open_orders_empty(self):
        sdk = MagicMock()
        sdk.get_open_orders = MagicMock(return_value=None)

        client = self._make_client(sdk)

        result = await client.get_open_orders()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_trades_truncates_to_limit(self):
        sdk = MagicMock()
        sdk.get_trades = MagicMock(
            return_value=[{"id": str(i)} for i in range(10)]
        )

        client = self._make_client(sdk)

        result = await client.get_trades(limit=3)
        assert len(result) == 3
        assert result[0]["id"] == "0"

    @pytest.mark.asyncio
    async def test_get_trades_no_limit_keeps_all(self):
        sdk = MagicMock()
        sdk.get_trades = MagicMock(
            return_value=[{"id": "1"}, {"id": "2"}]
        )

        client = self._make_client(sdk)

        result = await client.get_trades()
        assert len(result) == 2


class TestDataAPIActivity:
    def _make_client(self):
        from src.infrastructure.polymarket.data_api_client import DataAPIClient

        return DataAPIClient(
            wallet_address="0x1234567890abcdef1234567890abcdef12345678"
        )

    @pytest.mark.asyncio
    async def test_get_activity_returns_list(self):
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value=[{"type": "TRADE"}, {"type": "REDEEM"}]
        )

        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=mock_response)

        result = await client.get_activity(limit=10)

        assert len(result) == 2
        client._http.get.assert_called_once()
        call_args = client._http.get.call_args
        assert call_args[0][0] == "/activity"
        assert call_args[1]["params"]["limit"] == "10"
        assert "type" not in call_args[1]["params"]

    @pytest.mark.asyncio
    async def test_get_activity_with_type_filter(self):
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[])

        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=mock_response)

        await client.get_activity(limit=5, activity_type="TRADE")

        params = client._http.get.call_args[1]["params"]
        assert params["type"] == "TRADE"

    @pytest.mark.asyncio
    async def test_get_activity_non_list_response_returns_empty(self):
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"error": "no data"})

        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=mock_response)

        result = await client.get_activity()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_activity_http_error_propagates(self):
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 500
        err = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_response
        )

        client._http = AsyncMock()
        client._http.get = AsyncMock(side_effect=err)

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_activity()
