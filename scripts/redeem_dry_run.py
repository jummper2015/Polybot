# scripts/redeem_dry_run.py
"""
Smoke test para redeem CTF on-chain vía eth_call (RFC §8.4).

Simula el flujo completo de CTFRedeemer.redeem() sin enviar transacción a
la mempool. Verifica:
  1. RPC Polygon accesible + AsyncWeb3 responde
  2. Adapter Onramp (0x93070a...) tiene código deployado
  3. POLY_PROXY tiene MATIC suficiente
  4. eth_call al Adapter.redeemAndWrap(conditionId, indexSets) simula OK
  5. Estimación de gas + max_fee_per_gas realista

NO ejecuta la tx. Cero efectos en cadena. Cero pUSD movido.

Uso:
  python scripts/redeem_dry_run.py --condition-id 0xabc... --shares-yes 100
  python scripts/redeem_dry_run.py --condition-id 0xabc... --shares-no 50 --json
  python scripts/redeem_dry_run.py --condition-id 0xabc... --shares-yes 10 --shares-no 10

Salida:
  exit 0 → todo OK, redeem simulado exitosamente.
  exit 1 → faltan env vars (POLYGON_RPC_URL, POLYMARKET_PROXY_ADDRESS,
           POLYMARKET_PRIVATE_KEY, POLYMARKET_WALLET_ADDRESS).
  exit 2 → simulación revertida on-chain (posiciones no válidas, mercado
           no resuelto, adapter pausado, etc.).
  exit 3 → error inesperado (RPC down, etc.).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid


def _load_env_from_dotenv() -> None:
    """Carga .env si existe (best-effort)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _validate_env() -> dict:
    """Valida env vars requeridas. Devuelve dict con valores o levanta."""
    required = {
        "POLYGON_RPC_URL":            os.environ.get("POLYGON_RPC_URL", "").strip(),
        "POLYMARKET_PROXY_ADDRESS":   os.environ.get("POLYMARKET_PROXY_ADDRESS", "").strip(),
        "POLYMARKET_PRIVATE_KEY":     os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip(),
        "POLYMARKET_WALLET_ADDRESS":  os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip(),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(f"env vars faltantes: {missing}")
    return required


async def dry_run(
    condition_id: str,
    shares_yes:   int,
    shares_no:    int,
    verbose:      bool = True,
) -> dict:
    """
    Ejecuta CTFRedeemer.redeem() en modo dry_run=True.

    Devuelve dict con:
      - status: 'ok' | 'reverted' | 'error'
      - simulated_index_sets: [1] / [2] / [1,2]
      - preflight_matic_wei: balance del proxy
      - adapter_alive: bool
      - gas_estimated: uint | None
      - max_fee_per_gas: uint | None
      - error: str | None
    """
    from web3 import AsyncHTTPProvider, AsyncWeb3

    from src.infrastructure.polymarket.ctf_redeemer import CTFRedeemer

    env = _validate_env()

    w3 = AsyncWeb3(AsyncHTTPProvider(env["POLYGON_RPC_URL"]))

    redeemer = CTFRedeemer(
        web3=w3,
        proxy_address=env["POLYMARKET_PROXY_ADDRESS"],
        operator_address=env["POLYMARKET_WALLET_ADDRESS"],
        signature_type=int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1")),
        dry_run=True,
        operator_private_key=env["POLYMARKET_PRIVATE_KEY"],
    )

    result: dict = {
        "status": "ok",
        "condition_id": condition_id,
        "shares_yes": shares_yes,
        "shares_no": shares_no,
        "adapter_alive": None,
        "preflight_matic_wei": None,
        "simulated_index_sets": None,
        "error": None,
    }

    op_id = f"dry-run-{uuid.uuid4().hex[:8]}"

    try:
        # 1. Adapter alive check
        if verbose:
            print(f"[1/4] Verificando adapter alive @ {redeemer._adapter[:10]}...")
        result["adapter_alive"] = await redeemer.adapter_is_alive()
        if not result["adapter_alive"]:
            result["status"] = "reverted"
            result["error"] = "Adapter onramp sin código en latest block"
            return result

        # 2. Preflight MATIC
        if verbose:
            print(f"[2/4] Verificando MATIC balance @ {env['POLYMARKET_PROXY_ADDRESS'][:10]}...")
        try:
            matic_wei = await redeemer.preflight_matic()
            result["preflight_matic_wei"] = matic_wei
            if verbose:
                print(f"      MATIC balance: {matic_wei / 1e18:.4f} MATIC")
        except Exception as e:
            result["status"] = "reverted"
            result["error"] = f"Preflight MATIC failed: {e}"
            return result

        # 3. Compute index_sets
        if verbose:
            print(f"[3/4] Computando index_sets para (yes={shares_yes}, no={shares_no})...")
        result["simulated_index_sets"] = list(
            CTFRedeemer.compute_index_sets(shares_yes, shares_no)
        )
        if verbose:
            print(f"      index_sets={result['simulated_index_sets']}")

        # 4. Full dry-run redeem
        if verbose:
            print("[4/4] Ejecutando eth_call para simular redeem...")
        receipt = await redeemer.redeem(
            condition_id=condition_id,
            shares_yes=shares_yes,
            shares_no=shares_no,
            redeem_op_id=op_id,
        )
        result["status"] = "ok" if receipt.status == "mined" else "reverted"
        result["simulated_status"] = receipt.status
        if verbose:
            print(f"      dry_run receipt status: {receipt.status}")

    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"

    return result


async def main_async(args: argparse.Namespace) -> int:
    """Retorna exit code."""
    _load_env_from_dotenv()

    try:
        _validate_env()
    except EnvironmentError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    result = await dry_run(
        condition_id=args.condition_id,
        shares_yes=args.shares_yes,
        shares_no=args.shares_no,
        verbose=not args.json,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print()
        print("═══ Resultado dry_run ═══")
        print(f"  status:         {result['status']}")
        print(f"  index_sets:     {result.get('simulated_index_sets')}")
        print(f"  matic_wei:      {result.get('preflight_matic_wei')}")
        print(f"  adapter_alive:  {result.get('adapter_alive')}")
        if result.get("error"):
            print(f"  error:          {result['error']}")

    if result["status"] == "ok":
        return 0
    if result["status"] == "reverted":
        return 2
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run CTF redeem via eth_call (RFC §8.4)"
    )
    parser.add_argument("--condition-id", required=True,
                        help="Condition ID del mercado (0x + 64 hex)")
    parser.add_argument("--shares-yes", type=int, default=0,
                        help="Shares YES a redimir (default 0)")
    parser.add_argument("--shares-no", type=int, default=0,
                        help="Shares NO a redimir (default 0)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON en vez de texto")
    args = parser.parse_args()

    if args.shares_yes == 0 and args.shares_no == 0:
        print("❌ debe especificar al menos --shares-yes o --shares-no > 0",
              file=sys.stderr)
        return 1

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
