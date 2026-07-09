"""
RedeemReceipt — value object inmutable con el resultado de un redeem CTF.

Per RFC R2.0-redeem-impl APPROVED 2026-06-25 §13.
Es la fuente de verdad post-redeem: se persiste en DB, se emite como
AuditAction.CTF_REDEEM_TX_CONFIRMED, y se usa para reconciliación al arrancar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RedeemReceipt:
    """Resultado de una operación de redeem on-chain vía CTF.

    Attributes:
        redeem_op_id:     UUID interna del bot (idempotencia).
        condition_id:     Condition ID del mercado Polymarket (0x + 64 hex).
        tx_hash:          Hash de la transacción on-chain. None sólo si dry_run
                          simula éxito sin tx (status='mined' en dry_run) o si
                          la tx nunca fue enviada.
        index_sets:       Tupla con los indices redimidos, e.g. (1,) / (2,) / (1,2).
        shares_redeemed:  Total de shares quemadas (yes + no).
        pusd_received:    pUSD creditado en el POLY_PROXY al alcanzar CONFIRMED.
        gas_used:         Gas consumido en la tx. None hasta MINED.
        gas_fee_matic:    MATIC pagado en gas. None hasta calculo post-MINED.
        submitted_at:     Timestamp de envío a mempool. None si dry_run.
        mined_at:         Timestamp del bloque de mining. None hasta MINED.
        confirmed_at:     Timestamp al alcanzar N confirmaciones. None hasta CONFIRMED.
        status:           FinalityStatus value (string).
        proxy_address:    POLY_PROXY address que ejecutó la tx.
        adapter_address:  Thin Collateral Adapter address usado para redeemAndWrap.
    """

    redeem_op_id:    str
    condition_id:    str
    tx_hash:         str | None
    index_sets:      tuple[int, ...]
    shares_redeemed: int
    pusd_received:   float
    gas_used:        int | None
    gas_fee_matic:   float | None
    submitted_at:    datetime | None
    mined_at:        datetime | None
    confirmed_at:    datetime | None
    status:          str
    proxy_address:   str
    adapter_address: str

    def is_confirmed(self) -> bool:
        """True si la redeem llegó a finality práctica."""
        return self.status == "confirmed" and self.confirmed_at is not None
