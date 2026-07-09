"""
Property-based tests (Hypothesis) para CTFRedeemer invariants (R2.0-redeem-impl F1).

Regla dura #5: cambios en src/infrastructure/polymarket/ requieren property tests.

Invariantes cubiertas:
  - TestComputeIndexSetsInvariant:
      ∀ shares_yes, shares_no ∈ [0, 10^6]:
        - Si ambos == 0 → raises CTFRedeemError
        - Si solo uno > 0 → índice único correcto (1 para YES, 2 para NO)
        - Si ambos > 0 → (1, 2)
        - result es tuple de ints en {1, 2}
        - Determinístico (mismo input → mismo output)

  - TestNormalizeConditionIdInvariant:
      ∀ s ∈ hex strings de exactamente 64 chars:
        result es bytes de longitud 32
      ∀ cualquier string inválido (longitud != 64 o sin 0x):
        raises CTFRedeemError
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from src.domain.exceptions.ctf_exceptions import CTFRedeemError
from unittest.mock import AsyncMock

from src.infrastructure.polymarket.ctf_redeemer import CTFRedeemer

# ── Hypothesis strategies ────────────────────────────────────────────────

# uint256-like para shares (cap en 10^6 para el test; CTF acepta hasta uint256)
shares_strategy = st.integers(min_value=0, max_value=10**6)

# hex string de longitud exacta 64 (sin prefijo 0x, hex puro)
hex64_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=64, max_size=64,
)


# ══════════════════════════════════════════════════════════════════════
# TestComputeIndexSetsInvariant
# ══════════════════════════════════════════════════════════════════════

class TestComputeIndexSetsInvariant:

    @given(shares_yes=shares_strategy, shares_no=shares_strategy)
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_index_sets_consistency(self, shares_yes: int, shares_no: int) -> None:
        """∀ (yes, no) ∈ [0, 10^6]²: index_sets consistency invariants."""
        if shares_yes == 0 and shares_no == 0:
            with pytest.raises(CTFRedeemError):
                CTFRedeemer.compute_index_sets(shares_yes, shares_no)
        else:
            result = CTFRedeemer.compute_index_sets(shares_yes, shares_no)
            # ── Invariant 1: result is non-empty tuple of {1, 2} ─────
            assert isinstance(result, tuple)
            assert 1 <= len(result) <= 2
            assert all(i in (1, 2) for i in result)
            # ── Invariant 2: deterministic ───────────────────────────
            result2 = CTFRedeemer.compute_index_sets(shares_yes, shares_no)
            assert result == result2
            # ── Invariant 3: reflects input shares correctly ──────────
            if shares_yes > 0 and shares_no == 0:
                assert result == (1,)
            elif shares_no > 0 and shares_yes == 0:
                assert result == (2,)
            else:  # ambos > 0 (hedging residual)
                assert result == (1, 2)

    @given(shares_yes=st.integers(min_value=1, max_value=10**6))
    @settings(max_examples=50, deadline=None)
    def test_only_yes_always_returns_unit_index_1(self, shares_yes: int) -> None:
        """∀ shares_yes > 0, shares_no = 0 → (1,)."""
        assert CTFRedeemer.compute_index_sets(shares_yes, 0) == (1,)

    @given(shares_no=st.integers(min_value=1, max_value=10**6))
    @settings(max_examples=50, deadline=None)
    def test_only_no_always_returns_unit_index_2(self, shares_no: int) -> None:
        """∀ shares_no > 0, shares_yes = 0 → (2,)."""
        assert CTFRedeemer.compute_index_sets(0, shares_no) == (2,)

    @given(
        shares_yes=st.integers(min_value=1, max_value=10**6),
        shares_no=st.integers(min_value=1, max_value=10**6),
    )
    @settings(max_examples=50, deadline=None)
    def test_hedging_always_returns_pair(self, shares_yes: int, shares_no: int) -> None:
        """∀ shares_yes, shares_no > 0 → (1, 2)."""
        assert CTFRedeemer.compute_index_sets(shares_yes, shares_no) == (1, 2)


# ══════════════════════════════════════════════════════════════════════
# TestNormalizeConditionIdInvariant
# ══════════════════════════════════════════════════════════════════════

class TestNormalizeConditionIdInvariant:

    @given(hex_chars=hex64_strategy)
    @settings(max_examples=50, deadline=None)
    def test_valid_hex64_prefix_0x_returns_32_bytes(self, hex_chars: str) -> None:
        """∀ hex string de 64 chars con prefijo 0x → bytes(32)."""
        result = CTFRedeemer._normalize_condition_id("0x" + hex_chars)
        assert isinstance(result, bytes)
        assert len(result) == 32

    @given(hex_chars=hex64_strategy)
    @settings(max_examples=50, deadline=None)
    def test_valid_hex64_bytes_input_roundtrip(self, hex_chars: str) -> None:
        """Si input son bytes de longitud 32, debe devolver los mismos bytes."""
        raw_bytes = bytes.fromhex(hex_chars)
        result = CTFRedeemer._normalize_condition_id(raw_bytes)
        assert result == raw_bytes

    @given(
        hex_chars=st.text(
            alphabet="0123456789abcdef",
            min_size=1, max_size=63,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_short_hex_string_raises(self, hex_chars: str) -> None:
        """Cualquier hex string con longitud < 64 chars → raises."""
        bad_id = "0x" + hex_chars
        with pytest.raises(CTFRedeemError):
            CTFRedeemer._normalize_condition_id(bad_id)

    @given(
        length=st.integers(min_value=65, max_value=130),
    )
    @settings(max_examples=30, deadline=None)
    def test_long_hex_string_raises(self, length: int) -> None:
        """Cualquier hex string con longitud > 64 → raises."""
        hex_chars = "ab" * length
        with pytest.raises(CTFRedeemError):
            CTFRedeemer._normalize_condition_id("0x" + hex_chars)


# ── Helper ──────────────────────────────────────────────────────────────

def scores_safe_strategy():
    """Estrategia Hypothesis que produce shares_no ∈ [0, 10^6] compatibles con el loop."""
    return st.integers(min_value=0, max_value=10**6)
