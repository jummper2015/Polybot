# tests/unit/test_pin_gate.py
"""Ola 2.2: tests para PinGate (Capa 2 real trading confirmation)."""

import hashlib

import pytest

from src.interfaces.telegram.pin_gate import (
    MAX_FAILED_ATTEMPTS,
    PinGate,
    PinResult,
)


def _hash(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()


class TestPinGateBasics:

    def test_not_configured_returns_not_configured(self):
        """Sin hash cargado, verify siempre retorna NOT_CONFIGURED."""
        gate = PinGate(_expected_hash="")
        assert gate.verify(chat_id=1, pin="123456") == PinResult.NOT_CONFIGURED
        assert gate.is_configured() is False

    def test_correct_pin_returns_ok(self):
        gate = PinGate(_expected_hash=_hash("246810"))
        assert gate.verify(chat_id=1, pin="246810") == PinResult.OK

    def test_wrong_pin_returns_wrong(self):
        gate = PinGate(_expected_hash=_hash("246810"))
        assert gate.verify(chat_id=1, pin="000000") == PinResult.WRONG

    @pytest.mark.parametrize("bad_pin", [
        "12345",      # 5 dígitos
        "1234567",    # 7 dígitos
        "12345a",     # con letra
        "abcdef",     # todo letras
        "",           # vacío
        "12 456",     # con espacio
    ])
    def test_invalid_format_returns_invalid_format(self, bad_pin):
        gate = PinGate(_expected_hash=_hash("246810"))
        assert gate.verify(chat_id=1, pin=bad_pin) == PinResult.INVALID_FORMAT

    def test_invalid_format_does_not_count_as_attempt(self):
        """
        Formatos malformados no incrementan el contador — evita que un
        typo obvio del usuario gaste un slot de rate limit.
        """
        gate = PinGate(_expected_hash=_hash("246810"))
        for _ in range(10):
            gate.verify(chat_id=1, pin="abc")
        # Después de 10 intentos malformados, aún tenemos 3 slots.
        for i in range(MAX_FAILED_ATTEMPTS - 1):
            assert gate.verify(chat_id=1, pin="000000") == PinResult.WRONG


class TestPinGateRateLimit:

    def test_three_wrong_pins_triggers_lockout(self):
        gate = PinGate(_expected_hash=_hash("246810"))
        for _ in range(MAX_FAILED_ATTEMPTS):
            assert gate.verify(chat_id=42, pin="000000") == PinResult.WRONG
        # 4to intento (aunque sea correcto) debe rechazar por lockout.
        assert gate.verify(chat_id=42, pin="246810") == PinResult.LOCKED_OUT

    def test_lockout_is_per_chat_id(self):
        gate = PinGate(_expected_hash=_hash("246810"))
        for _ in range(MAX_FAILED_ATTEMPTS):
            gate.verify(chat_id=1, pin="wrongo")  # invalid format actually
            gate.verify(chat_id=1, pin="000000")
        assert gate.verify(chat_id=1, pin="246810") == PinResult.LOCKED_OUT
        # Otro chat_id, sin fallos, no está bloqueado.
        assert gate.verify(chat_id=2, pin="246810") == PinResult.OK

    def test_correct_pin_resets_failed_count(self):
        """
        Un PIN correcto resetea el contador — el usuario no acumula
        intentos "olvidados" tras logins exitosos.
        """
        gate = PinGate(_expected_hash=_hash("246810"))
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            gate.verify(chat_id=1, pin="000000")
        # 2 fallos acumulados, ahora un OK
        assert gate.verify(chat_id=1, pin="246810") == PinResult.OK
        # Tras el reset, otros 2 fallos no deben triggerar lockout.
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            assert gate.verify(chat_id=1, pin="000000") == PinResult.WRONG

    def test_lockout_expires_after_lockout_seconds(self):
        """
        Tras `_lockout_seconds` segundos, el gate desbloquea y resetea
        el contador de fallos.
        """
        gate = PinGate(
            _expected_hash=_hash("246810"),
            _lockout_seconds=60,
        )
        for _ in range(MAX_FAILED_ATTEMPTS):
            gate.verify(chat_id=1, pin="000000", now=1000.0)
        assert gate.verify(chat_id=1, pin="246810", now=1000.5) == PinResult.LOCKED_OUT

        # Después del lockout (t + 61) desbloquea y acepta el correcto.
        assert gate.verify(chat_id=1, pin="246810", now=1061.0) == PinResult.OK

    def test_seconds_until_unlock_reports_correctly(self):
        gate = PinGate(
            _expected_hash=_hash("246810"),
            _lockout_seconds=600,
        )
        for _ in range(MAX_FAILED_ATTEMPTS):
            gate.verify(chat_id=1, pin="000000", now=1000.0)
        assert gate.seconds_until_unlock(1, now=1000.0) == 600
        assert gate.seconds_until_unlock(1, now=1300.0) == 300
        # Nunca bloqueado
        assert gate.seconds_until_unlock(2, now=1000.0) == 0


class TestPinGateFromEnv:

    def test_from_env_prefers_hash(self, monkeypatch):
        """Si REAL_MODE_PIN_HASH está, se usa directamente."""
        expected = _hash("999888")
        monkeypatch.setenv("REAL_MODE_PIN_HASH", expected)
        monkeypatch.delenv("REAL_MODE_PIN", raising=False)
        gate = PinGate.from_env()
        assert gate.is_configured()
        assert gate.verify(chat_id=1, pin="999888") == PinResult.OK

    def test_from_env_falls_back_to_plain(self, monkeypatch):
        monkeypatch.delenv("REAL_MODE_PIN_HASH", raising=False)
        monkeypatch.setenv("REAL_MODE_PIN", "111222")
        gate = PinGate.from_env()
        assert gate.is_configured()
        assert gate.verify(chat_id=1, pin="111222") == PinResult.OK

    def test_from_env_rejects_malformed_hash(self, monkeypatch):
        monkeypatch.setenv("REAL_MODE_PIN_HASH", "not-a-hex-hash")
        monkeypatch.delenv("REAL_MODE_PIN", raising=False)
        gate = PinGate.from_env()
        assert not gate.is_configured()

    def test_from_env_rejects_malformed_plain(self, monkeypatch):
        monkeypatch.delenv("REAL_MODE_PIN_HASH", raising=False)
        monkeypatch.setenv("REAL_MODE_PIN", "abc")
        gate = PinGate.from_env()
        assert not gate.is_configured()

    def test_from_env_neither_set(self, monkeypatch):
        monkeypatch.delenv("REAL_MODE_PIN_HASH", raising=False)
        monkeypatch.delenv("REAL_MODE_PIN", raising=False)
        gate = PinGate.from_env()
        assert not gate.is_configured()
        assert gate.verify(chat_id=1, pin="123456") == PinResult.NOT_CONFIGURED
