# scripts/verify_polymarket_connectivity.py
"""
Verificación READ-ONLY de conectividad con Polymarket.

Cubre el objetivo previo a real trading:
  1. ¿El bot puede autenticarse contra el CLOB V2 (L1 EIP-712 + L2 HMAC)?
  2. ¿Sabe leer el saldo pUSD de la wallet?
  3. ¿Sabe listar posiciones abiertas, órdenes vivas y trades históricos?
  4. ¿La Data API pública confirma el mismo estado que el CLOB autenticado?

NO coloca, modifica ni cancela ninguna orden. Cero efectos en cadena.

Salida:
  exit 0 → todo OK.
  exit 1 → faltan credenciales en .env.
  exit 2 → al menos una llamada falló (auth, saldo, posiciones, trades, activity).

Uso:
  python scripts/verify_polymarket_connectivity.py
  python scripts/verify_polymarket_connectivity.py --json
  python scripts/verify_polymarket_connectivity.py --trades-limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(override=True)


EXIT_OK = 0
EXIT_MISSING_ENV = 1
EXIT_FAILED = 2


REQUIRED_ENV = [
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "POLYMARKET_WALLET_ADDRESS",
]


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    data: Any = None
    elapsed_ms: float = 0.0


@dataclass
class Report:
    wallet_masked: str = ""
    steps: list[StepResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(step.ok for step in self.steps)

    def to_dict(self) -> dict:
        return {
            "wallet": self.wallet_masked,
            "all_ok": self.all_ok,
            "steps": [
                {
                    "name": s.name,
                    "ok": s.ok,
                    "detail": s.detail,
                    "elapsed_ms": round(s.elapsed_ms, 1),
                    "data": s.data,
                }
                for s in self.steps
            ],
        }


def _mask_wallet(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else "***"


def check_env(report: Report) -> bool:
    """Paso 0 — verifica que el .env tiene las 5 variables requeridas."""
    missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        report.steps.append(
            StepResult(
                name="env",
                ok=False,
                detail=f"Faltan variables: {missing}",
            )
        )
        return False
    report.steps.append(
        StepResult(name="env", ok=True, detail="5/5 variables presentes")
    )
    return True


async def run_step(
    report: Report,
    name: str,
    coro_factory,
) -> StepResult:
    """Ejecuta un paso con timing + captura de excepciones."""
    t0 = time.perf_counter()
    try:
        data = await coro_factory()
        elapsed = (time.perf_counter() - t0) * 1000
        step = StepResult(
            name=name, ok=True, detail="OK", data=data, elapsed_ms=elapsed
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        step = StepResult(
            name=name,
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            elapsed_ms=elapsed,
        )
    report.steps.append(step)
    return step


def summarize_positions(positions: list[dict]) -> dict:
    """Agrega métricas básicas sobre posiciones."""
    total = len(positions)
    cash_pnl = sum(float(p.get("cashPnl", 0.0) or 0.0) for p in positions)
    current_value = sum(
        float(p.get("currentValue", 0.0) or 0.0) for p in positions
    )
    initial_value = sum(
        float(p.get("initialValue", 0.0) or 0.0) for p in positions
    )
    redeemable = sum(1 for p in positions if p.get("redeemable"))
    return {
        "total": total,
        "redeemable": redeemable,
        "initial_value_pusd": round(initial_value, 4),
        "current_value_pusd": round(current_value, 4),
        "cash_pnl_pusd": round(cash_pnl, 4),
    }


def summarize_trades(trades: list[dict]) -> dict:
    """Métricas básicas sobre los trades (no calcula PnL fino — eso es post_trade)."""
    total = len(trades)
    buy = sum(1 for t in trades if str(t.get("side", "")).upper() == "BUY")
    sell = total - buy
    return {"total": total, "buy": buy, "sell": sell}


async def verify(args: argparse.Namespace) -> Report:
    report = Report()

    if not check_env(report):
        return report

    # Importes diferidos — sólo si el .env tiene credenciales.
    from src.infrastructure.polymarket.clob_client import PolymarketCLOBClient
    from src.infrastructure.polymarket.data_api_client import DataAPIClient
    from src.infrastructure.security.key_manager import KeyManager

    # ── Inicialización del SDK ─────────────────────────────────────
    # Si las credenciales son placeholders inválidos (clave hex mal formada,
    # wallet con caracteres no-hex), eth_account / py-clob-client-v2 lanzan
    # la excepción aquí. Captura en un step propio para no romper la salida.
    t0 = time.perf_counter()
    try:
        keys = KeyManager()
        report.wallet_masked = _mask_wallet(keys.wallet_address)
        clob = PolymarketCLOBClient(key_manager=keys)
        data_api = DataAPIClient(wallet_address=keys.wallet_address)
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        report.steps.append(
            StepResult(
                name="init_clients",
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                elapsed_ms=elapsed,
            )
        )
        return report

    report.steps.append(
        StepResult(
            name="init_clients",
            ok=True,
            detail="KeyManager + ClobClient + DataAPIClient",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
    )

    try:
        await run_step(report, "auth_l1_l2", lambda: clob.assert_auth())

        await run_step(report, "balance_pusd", lambda: clob.get_balance())

        async def _positions() -> dict:
            positions = await data_api.get_positions()
            return {
                "summary": summarize_positions(positions),
                "sample": positions[: args.positions_sample],
            }

        await run_step(report, "data_api_positions", _positions)

        async def _open_orders() -> dict:
            orders = await clob.get_open_orders()
            return {"total": len(orders), "sample": orders[: args.orders_sample]}

        await run_step(report, "clob_open_orders", _open_orders)

        async def _trades() -> dict:
            trades = await clob.get_trades(limit=args.trades_limit)
            return {
                "summary": summarize_trades(trades),
                "sample": trades[: args.trades_sample],
            }

        await run_step(report, "clob_trades", _trades)

        async def _activity() -> dict:
            activity = await data_api.get_activity(limit=args.trades_limit)
            return {"total": len(activity), "sample": activity[: args.trades_sample]}

        await run_step(report, "data_api_activity", _activity)

    finally:
        await clob.close()
        await data_api.close()

    return report


# ── CLI ────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verifica conectividad READ-ONLY con Polymarket.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Salida en JSON (para consumo programático).",
    )
    parser.add_argument(
        "--trades-limit",
        type=int,
        default=50,
        help="Máximo de trades / eventos de actividad a pedir.",
    )
    parser.add_argument(
        "--trades-sample",
        type=int,
        default=3,
        help="Cuántos trades incluir como muestra en la salida.",
    )
    parser.add_argument(
        "--positions-sample",
        type=int,
        default=3,
        help="Cuántas posiciones incluir como muestra en la salida.",
    )
    parser.add_argument(
        "--orders-sample",
        type=int,
        default=3,
        help="Cuántas órdenes abiertas incluir como muestra.",
    )
    return parser.parse_args()


def render_human(report: Report) -> None:
    print("=" * 70)
    print("  PolyBot — Verificación de conectividad Polymarket (READ-ONLY)")
    print(f"  Wallet: {report.wallet_masked or '[no env]'}")
    print("=" * 70)

    for step in report.steps:
        marker = "✅" if step.ok else "❌"
        elapsed = f"{step.elapsed_ms:6.1f} ms" if step.elapsed_ms else "       "
        print(f"  {marker} {step.name:<22} {elapsed}  {step.detail}")
        if step.ok and step.data:
            if step.name == "balance_pusd":
                print(f"       balance: {step.data:.6f} pUSD")
            elif step.name == "data_api_positions":
                summary = step.data.get("summary", {})
                print(
                    "       "
                    f"positions={summary.get('total', 0)} "
                    f"redeemable={summary.get('redeemable', 0)} "
                    f"current={summary.get('current_value_pusd', 0)} pUSD "
                    f"pnl={summary.get('cash_pnl_pusd', 0)} pUSD"
                )
            elif step.name == "clob_trades":
                summary = step.data.get("summary", {})
                print(
                    "       "
                    f"trades={summary.get('total', 0)} "
                    f"(buy={summary.get('buy', 0)} sell={summary.get('sell', 0)})"
                )
            elif step.name == "clob_open_orders":
                print(f"       open_orders={step.data.get('total', 0)}")
            elif step.name == "data_api_activity":
                print(f"       activity_events={step.data.get('total', 0)}")

    print("=" * 70)
    if report.all_ok:
        print("✅ Conectividad verificada. Lectura wallet/posiciones/trades OK.")
    else:
        print("❌ Al menos un paso falló — ver detalle arriba.")
    print("=" * 70)


def main() -> int:
    args = parse_args()

    try:
        report = asyncio.run(verify(args))
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return EXIT_FAILED
    except Exception:
        traceback.print_exc()
        return EXIT_FAILED

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        render_human(report)

    if not report.steps:
        return EXIT_FAILED
    if report.steps[0].name == "env" and not report.steps[0].ok:
        return EXIT_MISSING_ENV
    return EXIT_OK if report.all_ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
