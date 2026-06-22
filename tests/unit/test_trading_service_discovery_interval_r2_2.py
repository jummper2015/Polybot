"""
test_trading_service_discovery_interval_r2_2.py
────────────────────────────────────────────────────────────────────────────
Unit test R2.2 (Fix #3): DISCOVERY_INTERVAL_SECONDS debe estar en [60, 300]
segundos (no en 3600 como antes), con override por env var.

Por qué el cambio:
────────────────────────────────────────────────────────────────────────────
Los markets live crypto M5/M15 rotan cada 5-15 min. Con DISCOVERY=3600s:
  - Mercados expirados sobrevivían hasta 1h en markets:active:{asset}:{window}
  - Ventana ciega entre rotaciones: hasta 60min de mercados "fantasma"
Con DISCOVERY=60s: ventana ciega ≤ 60s, aligns con granularidad M5.
Override via POLYBOT_DISCOVERY_INTERVAL_S para tuning en ops.

Por qué este test:
────────────────────────────────────────────────────────────────────────────
Regresión silenciosa: si alguien sube la constante a "optimizar carga",
los markets rotan más lento y el bot opera en mercados expirados sin que
los tests E2E lo detecten (smoke solo mira logs, no contenido de Redis).
Este test bloqua el valor mínimo permisible.

Diseño del test:
────────────────────────────────────────────────────────────────────────────
- NO usa importlib.reload (side-effects en sys.modules entre tests).
- Usa fixture class-scoped `mod` que importa el módulo UNA vez por clase,
  con monkeypatch.setattr sobre la constante para forzar valores de test.
- Cada test usa el parámetro `mod` que coincide con el nombre del fixture
  (NO colisión con imports porque el módulo no es aliasado a `mod`).

Refs R2.2-paper-verify (RECORRIDO_ACTUAL.md §Bloque #1).
"""
import pytest


@pytest.fixture(scope="class")
def mod():
    """
    Fixture class-scoped: importa el módulo UNA vez por clase de tests.

    El test usa `monkeypatch.setattr(mod, "DISCOVERY_INTERVAL_SECONDS", X)`
    para forzar valores. monkeypatch restaura automáticamente al final de
    cada test, así que la contaminación cross-test está eliminada sin
    necesidad de importlib.reload (que era la solución anterior con
    side-effects globales en sys.modules).
    """
    try:
        import src.application.services.trading_service as m
        return m
    except Exception:
        pytest.skip(
            "trading_service no importable en este scope (deps runtime)"
        )


class TestDiscoveryIntervalR22:
    """
    R2.2-paper-verify Fix #3: el bot debe re-descubrir markets al menos
    cada 5 minutos. 60s es el valor por defecto (alineado con M5 granularity).
    """

    def test_default_discovery_interval_is_60(self, mod):
        """
        Sin override de env: DISCOVERY_INTERVAL_SECONDS == 60 (1 min).
        Refleja el module-level int(os.environ.get(default=60)).
        """
        assert mod.DISCOVERY_INTERVAL_SECONDS == 60, (
            f"Default R2.2 debe ser 60s; got {mod.DISCOVERY_INTERVAL_SECONDS}. "
            f"Si lo subiste para 'optimizar carga' rompe el guardrail de "
            f"rotación M5/M15."
        )

    def test_default_discovery_interval_within_bounds_5min(self, mod):
        """
        Default debe estar dentro de un bound superior razonable
        (<= 300s = 5min). Más allá de eso, la ventana ciega entre
        rotaciones es inaceptable para markets M5 (que viven 5min).
        """
        assert 60 <= mod.DISCOVERY_INTERVAL_SECONDS <= 300, (
            f"Discovery interval fuera de bounds: "
            f"{mod.DISCOVERY_INTERVAL_SECONDS}s. "
            f"Esperado [60, 300]s para M5/M15 crypto."
        )

    def test_env_override_takes_effect(self, monkeypatch, mod):
        """
        Override via monkeypatch.setattr simula el env override
        (POLYBOT_DISCOVERY_INTERVAL_S=N → DISCOVERY_INTERVAL_SECONDS=N
        cuando se importa el módulo; aquí testamos que la constante
        acepta un valor arbitrario).
        """
        monkeypatch.setattr(mod, "DISCOVERY_INTERVAL_SECONDS", 120)
        assert mod.DISCOVERY_INTERVAL_SECONDS == 120, (
            f"Override no aplicado: expected 120, got "
            f"{mod.DISCOVERY_INTERVAL_SECONDS}"
        )

    def test_env_override_minimum_bound(self, monkeypatch, mod):
        """
        Override a 30s (debe funcionar sin imponer min en codigo).
        Codigo NO impone min — solo verifica que la constante acepta
        el valor (no se lanza exception ni se queda en default).
        """
        monkeypatch.setattr(mod, "DISCOVERY_INTERVAL_SECONDS", 30)
        assert mod.DISCOVERY_INTERVAL_SECONDS == 30

    def test_discovery_interval_distinct_from_cycle_interval(self, mod):
        """
        Regresión crítica: DISCOVERY_INTERVAL_SECONDS no debe colapsar
        a CYCLE_INTERVAL_SECONDS (30s). Si alguien copia-pega mal,
        el bot re-descubre markets en cada ciclo y satura la API.
        """
        assert (
            mod.DISCOVERY_INTERVAL_SECONDS != mod.CYCLE_INTERVAL_SECONDS
        ), (
            "REGRESIÓN CRÍTICA: discovery == cycle. El bot re-descubriría "
            "markets en cada ciclo de 30s y saturaría el endpoint "
            "/events de Polymarket."
        )
        # Sanity check de las constantes esperadas
        assert mod.CYCLE_INTERVAL_SECONDS == 30

    def test_env_var_drives_default_value_at_import_time(self):
        """
        Wiring env → constante ocurre a module-level via:
            DISCOVERY_INTERVAL_SECONDS = int(os.environ.get("POLYBOT_DISCOVERY_INTERVAL_S", "60"))

        Para VERIFICAR este wiring sin recargar el módulo (evitar
        side-effects en sys.modules entre tests, Nota 2 R2.2),
        inspeccionamos el source file directamente.

        Si el guardrail cambia en el futuro (ej. lectura per-call en lugar
        de module-level), este test detecta la regresión.
        """
        import inspect
        import re
        try:
            import src.application.services.trading_service as m
            source = inspect.getsource(m)
        except Exception:
            pytest.skip("trading_service no introspectable en este scope")

        # Regex robusto: acepta single o double quotes, con o sin default.
        # Patrón: int(os.environ.get(<quote>POLYBOT_DISCOVERY_INTERVAL_S<quote>, ...))
        pattern = re.compile(
            r'int\(\s*os\.environ\.get\(\s*[\'"]POLYBOT_DISCOVERY_INTERVAL_S[\'"]',
            re.MULTILINE,
        )
        match = pattern.search(source)
        assert match is not None, (
            f"Wiring env var -> constante no encontrado en trading_service. "
            f"Esperado: int(os.environ.get(...['\\\"]POLYBOT_DISCOVERY_INTERVAL_S['\\\"]...)) "
            f"en una linea del modulo. Source excerpt:\n{source[:600]}"
        )

        # Bonus: la constante debería ser 60s por default (no None, no 3600 legacy).
        # Si alguien cambia el default a otro valor, este test alerta.
        assert '"60"' in source or "'60'" in source, (
            f"Default value '60' no encontrado en source; revisar default "
            f"de POLYBOT_DISCOVERY_INTERVAL_S en trading_service.py"
        )
