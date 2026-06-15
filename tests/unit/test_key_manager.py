# tests/unit/test_key_manager.py
"""Unit tests for KeyManager — focus on signature_type validation (R1.7)."""

import pytest

from src.infrastructure.security.key_manager import (
    DEFAULT_SIGNATURE_TYPE,
    ENV_SIGNATURE_TYPE,
    VALID_SIGNATURE_TYPES,
    KeyManager,
)

# Variables mínimas que KeyManager exige en su validate al instanciar.
REQUIRED_ENV = {
    "POLYMARKET_PRIVATE_KEY":   "0x" + "a" * 64,
    "POLYMARKET_API_KEY":       "test_key",
    "POLYMARKET_API_SECRET":    "test_secret",
    "POLYMARKET_API_PASSPHRASE": "test_pass",
    "POLYMARKET_WALLET_ADDRESS": "0x1234567890abcdef1234567890abcdef12345678",
}


@pytest.fixture
def real_env(monkeypatch):
    """Pone en el entorno las variables necesarias para construir KeyManager."""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(ENV_SIGNATURE_TYPE, raising=False)
    return monkeypatch


class TestSignatureTypeDefault:

    def test_default_is_one_when_env_missing(self, real_env):
        km = KeyManager()
        assert km.signature_type == DEFAULT_SIGNATURE_TYPE == 1

    def test_returns_int_not_str(self, real_env):
        km = KeyManager()
        assert isinstance(km.signature_type, int)


class TestSignatureTypeValid:

    @pytest.mark.parametrize("raw, expected", [
        ("0", 0),  # EOA
        ("1", 1),  # POLY_PROXY
        ("2", 2),  # GNOSIS_SAFE
        ("3", 3),  # POLY_1271
    ])
    def test_accepts_all_valid_values(self, real_env, raw, expected):
        real_env.setenv(ENV_SIGNATURE_TYPE, raw)
        km = KeyManager()
        assert km.signature_type == expected

    def test_strips_whitespace(self, real_env):
        real_env.setenv(ENV_SIGNATURE_TYPE, "  2 ")
        km = KeyManager()
        assert km.signature_type == 2

    def test_all_valid_values_are_in_range(self):
        assert VALID_SIGNATURE_TYPES == {0, 1, 2, 3}


class TestSignatureTypeInvalid:

    def test_rejects_non_integer(self, real_env):
        real_env.setenv(ENV_SIGNATURE_TYPE, "foo")
        km = KeyManager()
        with pytest.raises(EnvironmentError, match="no es un entero"):
            _ = km.signature_type

    def test_rejects_out_of_range_high(self, real_env):
        real_env.setenv(ENV_SIGNATURE_TYPE, "4")
        km = KeyManager()
        with pytest.raises(EnvironmentError, match="fuera de rango"):
            _ = km.signature_type

    def test_rejects_negative(self, real_env):
        real_env.setenv(ENV_SIGNATURE_TYPE, "-1")
        km = KeyManager()
        with pytest.raises(EnvironmentError, match="fuera de rango"):
            _ = km.signature_type

    def test_error_message_lists_valid_values(self, real_env):
        real_env.setenv(ENV_SIGNATURE_TYPE, "9")
        km = KeyManager()
        with pytest.raises(EnvironmentError) as excinfo:
            _ = km.signature_type
        assert "0" in str(excinfo.value)
        assert "POLY_PROXY" in str(excinfo.value)


class TestSignatureTypeCachedProperty:

    def test_caches_value_between_calls(self, real_env):
        real_env.setenv(ENV_SIGNATURE_TYPE, "2")
        km = KeyManager()
        first = km.signature_type
        # Cambiar el entorno DESPUÉS del primer acceso no debe alterar el valor cacheado.
        real_env.setenv(ENV_SIGNATURE_TYPE, "3")
        assert km.signature_type == first == 2
