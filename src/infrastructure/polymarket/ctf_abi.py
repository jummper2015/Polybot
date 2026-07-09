"""
ABIs mínimos para interaccionar con CTF + Collateral Onramp + Gnosis Safe.

Cada fragmento contiene SOLO las funciones que CTFRedeemer necesita.
NO se incluye el ABI completo del CTF ni del Safe para mantener
el footprint de web3.py mínimo y los tests enfocados.

Fuentes documentadas en RFC §3.
"""

from __future__ import annotations

# ── Conditional Tokens Framework (CTF) ───────────────────────────────────
# doc: https://docs.polymarket.com/trading/ctf/redeem
# Address: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 (Polygon Mainnet)
#
# Función: redeemPositions(address, bytes32, bytes32, uint256[])
#   - collateralToken: dirección del colateral (USDC.e o pUSD adapter)
#   - parentCollectionId: 0x0000... para markets Pollen de Polymarket
#   - conditionId: ID del mercado
#   - indexSets: [1, 2] cubre ambos outcomes; el perdedor se quema por 0
CTF_REDEEM_POSITIONS_ABI: list[dict] = [
    {
        "inputs": [
            {"name": "collateralToken",    "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId",        "type": "bytes32"},
            {"name": "indexSets",          "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# ── Thin Collateral Adapter / Onramp ────────────────────────────────────
# doc: https://docs.polymarket.com/trading/ctf/redeem
# Address: 0x93070a847efEf7F70739046A929D47a521F5B8ee (Polygon Mainnet)
#
# Función: redeemAndWrap(bytes32 conditionId, uint256[] indexSets)
#   - Atómico: burn outcome tokens → USDC.e unwrap → pUSD wrap → credit al caller.
#   - Asume firma inferida de docs (idéntica a ConditionalTokens.redeemPositions
#     seguido de wrap; aquí simplificada). Si cambia, abrir RFC.
COLLATERAL_ADAPTER_REDEEM_ABI: list[dict] = [
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets",   "type": "uint256[]"},
        ],
        "name": "redeemAndWrap",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# ── Gnosis Safe v1.3.0 — execTransaction (subset) ───────────────────────
# doc: https://github.com/safe-global/safe-contracts
# Firmado por EIP-712 SafeTx struct; el EOA del operador firma con su private key
# y la tx se envía desde el EOA → el POLY_PROXY (Gnosis Safe) → el Adapter.
SAFE_V130_EXEC_TRANSACTION_ABI: list[dict] = [
    {
        "inputs": [
            {"name": "to",             "type": "address"},
            {"name": "value",          "type": "uint256"},
            {"name": "data",           "type": "bytes"},
            {"name": "operation",      "type": "uint8"},
            {"name": "safeTxGas",      "type": "uint256"},
            {"name": "baseGas",        "type": "uint256"},
            {"name": "gasPrice",       "type": "uint256"},
            {"name": "gasToken",       "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures",     "type": "bytes"},
        ],
        "name": "execTransaction",
        "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ── ERC-20 (pUSD) — lectura de balance + decimals ────────────────────────
# Address pUSD: 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB (Polygon)
# Usado para leer el balance pUSD del POLY_PROXY post-redeem y calcular
# pusd_received como delta.
ERC20_BALANCE_AND_DECIMALS_ABI: list[dict] = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]
