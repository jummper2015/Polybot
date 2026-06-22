# tests/unit/test_docker_compose_app_command_r2_2.py

"""
R2.2-paper-verify — Fix #1.

Asegura que el container `app` arranca el bootstrap completo, NO solo uvicorn:
- `docker-compose.yml` ejecuta `python main.py` (que entra a
  src.core.bootstrap.bootstrap y lanza FastAPI + Telegram + TradingService
  en paralelo via asyncio.create_task()).
- `main.py` existe en la raíz, es ejecutable, y llama al bootstrap.
"""

import os
import re
from pathlib import Path

import yaml

ROOT          = Path(__file__).resolve().parents[2]
COMPOSE_FILE  = ROOT / "docker-compose.yml"
MAIN_FILE     = ROOT / "main.py"
BOOTSTRAP_M   = ROOT / "src" / "core" / "bootstrap.py"


def _load_compose() -> dict:
    assert COMPOSE_FILE.is_file(), (
        f"docker-compose.yml no existe en {ROOT}"
    )
    with COMPOSE_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_compose_app_command_runs_main_py() -> None:
    """
    El comando de `services.app.command` DEBE invocar `python main.py`
    para que el loop de TradingService arranque. Antes de R2.2 se hacía
    `python -m uvicorn src.interfaces.api.app:create_app --factory ...`
    que sólo levanta el API; el trading jamás iniciaba.
    """
    compose = _load_compose()
    cmd = compose["services"]["app"]["command"]
    cmd_str = " ".join(cmd).strip() if isinstance(cmd, list) else str(cmd)

    # Tolerancia: espacios / newlines entre tokens. Concatenado debe contener.
    flat = re.sub(r"\s+", " ", cmd_str)
    assert "python main.py" in flat, (
        f"docker-compose app.command no arranca main.py; "
        f"observado: {cmd_str!r}"
    )
    assert "uvicorn" not in flat, (
        "docker-compose aún referencia uvicorn (Fix #1 no aplicado)."
    )


def test_main_py_exists_and_calls_bootstrap() -> None:
    """
    `main.py` debe existir en la raíz, contener `if __name__ == "__main__":`
    y referenciar `bootstrap` para arrancar el sistema completo.
    """
    assert MAIN_FILE.is_file(), "main.py no encontrado en raíz"
    text = MAIN_FILE.read_text(encoding="utf-8")
    assert "__name__" in text and "__main__" in text, (
        "main.py no contiene entry point estándar."
    )
    assert "bootstrap" in text, (
        "main.py no llama a bootstrap (sin esto no se levanta el trading)."
    )


def test_bootstrap_launches_trading_service_in_parallel() -> None:
    """
    `src/core/bootstrap.py` DEBE crear tareas asyncio en paralelo para:
    FastAPI, TradingService, Telegram. Sin la tarea `trading` el bot
    corre solo como API y nunca opera.
    """
    text = BOOTSTRAP_M.read_text(encoding="utf-8")
    assert "asyncio.create_task(run_trading" in text, (
        "bootstrap.py no invoca asyncio.create_task(run_trading(...)); "
        "sin esto el bot no opera mercados."
    )
    assert "asyncio.create_task(run_fastapi" in text, (
        "bootstrap.py no lanza el servidor FastAPI en paralelo."
    )


def test_bootstrap_uses_uvloop() -> None:
    """
    `main.py` instala `uvloop` (alta performance). Si uvloop no está
    disponible, el sistema cae a asyncio estándar. Smoke check: el
    código debe intentar importarlo.
    """
    text = MAIN_FILE.read_text(encoding="utf-8")
    assert "uvloop" in text, (
        "main.py no referencia uvloop (degradación esperada pero debe estar)."
    )
