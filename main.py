# main.py  (raíz del proyecto)

import asyncio
import sys
import os

# ── uvloop: reemplazo de alto rendimiento para el event loop asyncio ─
try:
    import uvloop
    uvloop.install()
except ImportError:
    print("[main] uvloop not available, using standard asyncio event loop")

# Añade src al path para imports absolutos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def main():
    """
    Punto de entrada del sistema.
    Configura el event loop y lanza el bootstrap.
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[main] Interrupted by user")
    except SystemExit as e:
        sys.exit(e.code)


async def run():
    """Función async principal — separada para facilitar testing."""
    from src.core.bootstrap import bootstrap
    await bootstrap()


if __name__ == "__main__":
    main()