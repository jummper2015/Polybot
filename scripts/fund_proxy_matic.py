# scripts/fund_proxy_matic.py
"""
Helper para fondear MATIC al POLY_PROXY (RFC §13.Q1 F1.5).

Modelo híbrido según decisión operativa Q1 del RFC:
  1. Este script arma la tx (calldata, nonce, gas EIP-1559) — dry-run por default
  2. Imprime la tx para inspección visual (from, to, value, gas, nonce)
  3. El operador la firma y envía manualmente desde MetaMask
  4. --reconcile-hash <tx_hash> permite verificar que la tx llegó, polleando
     eth_getTransactionByHash 30s hasta encontrar receipt

Sin broadcast automático → sin firma con private key en producción → sin
riesgo de que un bug del bot vacíe la wallet fondos.

Uso:
  # 1. Armar tx (dry-run, imprime data para MetaMask)
  python scripts/fund_proxy_matic.py --amount-matic 1.0

  # 2. Enviar manualmente desde MetaMask con los datos impresos

  # 3. Reconciliar cuando ya envió
  python scripts/fund_proxy_matic.py --reconcile-hash 0xabc...

Salida:
  exit 0 → tx armada / reconciliada exitosamente.
  exit 1 → env vars faltantes o args inválidos.
  exit 2 → error RPC / tx no encontrada.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


def _load_env_from_dotenv() -> None:
    """Carga .env si existe (best-effort)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _validate_env() -> dict:
    """Valida env vars requeridas para armar tx."""
    required = {
        "POLYGON_RPC_URL":          os.environ.get("POLYGON_RPC_URL", "").strip(),
        "POLYMARKET_PROXY_ADDRESS": os.environ.get("POLYMARKET_PROXY_ADDRESS", "").strip(),
        "POLYMARKET_WALLET_ADDRESS": os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip(),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(f"env vars faltantes: {missing}")
    return required


async def build_funding_tx(
    amount_matic: float,
    verbose:      bool = True,
) -> dict:
    """
    Arma tx EIP-1559 para transferir MATIC del EOA al POLY_PROXY.
    NO firma, NO envía. Solo genera calldata + nonce + gas params.
    """
    from web3 import AsyncHTTPProvider, AsyncWeb3

    env = _validate_env()

    w3 = AsyncWeb3(AsyncHTTPProvider(env["POLYGON_RPC_URL"]))

    eoa = w3.to_checksum_address(env["POLYMARKET_WALLET_ADDRESS"])
    proxy = w3.to_checksum_address(env["POLYMARKET_PROXY_ADDRESS"])
    amount_wei = int(amount_matic * 10**18)

    # ── Balance check EOA ────────────────────────────────────────
    if verbose:
        print(f"[1/4] Verificando balance EOA {eoa[:10]}...")
    eoa_balance = await w3.eth.get_balance(eoa, "latest")
    if verbose:
        print(f"      EOA balance: {eoa_balance / 1e18:.4f} MATIC")

    if eoa_balance < amount_wei:
        raise ValueError(
            f"EOA balance {eoa_balance / 1e18:.4f} MATIC < "
            f"requerido {amount_matic:.4f} MATIC"
        )

    # ── Nonce del EOA ────────────────────────────────────────────
    if verbose:
        print(f"[2/4] Consultando nonce del EOA...")
    nonce = await w3.eth.get_transaction_count(eoa, "latest")

    # ── Gas EIP-1559 ─────────────────────────────────────────────
    if verbose:
        print(f"[3/4] Estimando gas EIP-1559...")
    block = await w3.eth.get_block("latest")
    base_fee = block.get("baseFeePerGas", int(50e9))
    max_priority = int(1.5e9)
    try:
        max_priority = await w3.eth.max_priority_fee
    except Exception:
        pass
    max_fee = int(base_fee * 2 + max_priority)

    chain_id = await w3.eth.chain_id

    tx = {
        "from":                 eoa,
        "to":                   proxy,
        "value":                amount_wei,
        "gas":                  21_000,  # transferencia simple MATIC nativo
        "maxFeePerGas":         max_fee,
        "maxPriorityFeePerGas": max_priority,
        "nonce":                nonce,
        "chainId":              chain_id,
    }

    # ── Cost estimation ──────────────────────────────────────────
    max_gas_cost = tx["gas"] * max_fee
    if verbose:
        print(f"[4/4] Tx armada:")
        print(f"      max gas cost:   {max_gas_cost / 1e18:.6f} MATIC")

    return {
        "tx":                tx,
        "eoa_balance_wei":   eoa_balance,
        "amount_wei":        amount_wei,
        "max_gas_cost_wei":  max_gas_cost,
        "chain_id":          chain_id,
    }


async def reconcile_by_hash(tx_hash: str, poll_seconds: int = 30) -> dict:
    """
    Polls eth_getTransactionByHash hasta que la tx aparezca (o timeout).
    Reporta status: mined/reverted, gas_used, block_number.
    """
    from web3 import AsyncHTTPProvider, AsyncWeb3

    env = _validate_env()
    w3 = AsyncWeb3(AsyncHTTPProvider(env["POLYGON_RPC_URL"]))

    print(f"[reconcile] Polleando tx_hash={tx_hash} (max {poll_seconds}s)...")

    result: dict = {"tx_hash": tx_hash, "status": "pending", "receipt": None}

    end = asyncio.get_event_loop().time() + poll_seconds
    while asyncio.get_event_loop().time() < end:
        try:
            receipt = await w3.eth.get_transaction_receipt(tx_hash)
            if receipt and receipt.get("blockNumber") is not None:
                result["receipt"] = {
                    "block_number": receipt["blockNumber"],
                    "gas_used":     receipt["gasUsed"],
                    "status":       receipt["status"],
                }
                result["status"] = "mined" if receipt["status"] == 1 else "reverted"
                print(f"[reconcile] Tx mined en block={receipt['blockNumber']}, "
                      f"gas_used={receipt['gasUsed']}, status={receipt['status']}")
                return result
        except Exception:
            pass
        await asyncio.sleep(2.0)

    result["status"] = "timeout"
    print(f"[reconcile] Tx no encontrada en {poll_seconds}s — posiblemente aún en mempool")
    return result


async def main_async(args: argparse.Namespace) -> int:
    _load_env_from_dotenv()

    try:
        _validate_env()
    except EnvironmentError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    # ── Modo reconcile ───────────────────────────────────────────
    if args.reconcile_hash:
        result = await reconcile_by_hash(
            tx_hash=args.reconcile_hash,
            poll_seconds=args.poll_seconds,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        return 0 if result["status"] == "mined" else 2

    # ── Modo build tx ────────────────────────────────────────────
    try:
        built = await build_funding_tx(
            amount_matic=args.amount_matic,
            verbose=not args.json,
        )
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        # tx contiene ints grandes → serializa como str
        tx_serializable = {
            k: (str(v) if isinstance(v, int) and v > 2**32 else v)
            for k, v in built["tx"].items()
        }
        print(json.dumps({"tx": tx_serializable, **{
            k: (str(v) if isinstance(v, int) else v)
            for k, v in built.items() if k != "tx"
        }}, indent=2))
    else:
        tx = built["tx"]
        print()
        print("═══ Tx armada — copia estos valores a MetaMask ═══")
        print(f"  From:                {tx['from']}")
        print(f"  To (POLY_PROXY):     {tx['to']}")
        print(f"  Value:               {tx['value']} wei ({tx['value']/1e18:.4f} MATIC)")
        print(f"  Gas limit:           {tx['gas']}")
        print(f"  Max fee per gas:     {tx['maxFeePerGas']} wei ({tx['maxFeePerGas']/1e9:.2f} gwei)")
        print(f"  Max priority fee:    {tx['maxPriorityFeePerGas']} wei ({tx['maxPriorityFeePerGas']/1e9:.2f} gwei)")
        print(f"  Nonce:               {tx['nonce']}")
        print(f"  Chain ID:            {tx['chainId']} (Polygon Mainnet)")
        print()
        print(f"  Max gas cost:        {built['max_gas_cost_wei']/1e18:.6f} MATIC")
        print(f"  Total cost:          {(built['amount_wei'] + built['max_gas_cost_wei'])/1e18:.6f} MATIC")
        print()
        print("═══ Instrucciones ═══")
        print("  1. Abre MetaMask, cambia a Polygon Mainnet.")
        print("  2. Send → introduce los valores de arriba.")
        print("  3. Cuando envíes, guarda el tx_hash y ejecuta:")
        print(f"     python scripts/fund_proxy_matic.py --reconcile-hash 0x...")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fondear MATIC al POLY_PROXY (RFC §13.Q1)"
    )
    parser.add_argument("--amount-matic", type=float, default=0.5,
                        help="Cantidad de MATIC a transferir (default 0.5)")
    parser.add_argument("--reconcile-hash", type=str, default=None,
                        help="Reconcile por tx_hash en vez de armar nueva tx")
    parser.add_argument("--poll-seconds", type=int, default=30,
                        help="Segundos a pollear en reconcile (default 30)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON en vez de texto")
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
