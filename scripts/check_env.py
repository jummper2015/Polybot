# scripts/check_env.py
# Ejecutar ANTES de arrancar: python scripts/check_env.py [--phase <N>]
#
# Fases soportadas:
#   --phase 8    Data Recording (NO requiere credenciales Polymarket)
#   --phase 9    Real Trading (SI requiere credenciales)
#   --phase real Equivalente a --phase 9
#   (sin flag)   Verifica segun TRADING_MODE actual

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(override=True)

REQUIRED_ALWAYS = [
    "DATABASE_URL",
    "REDIS_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TRADING_MODE",
]

REQUIRED_REAL = [
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
    "POLYMARKET_WALLET_ADDRESS",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Verifica las variables de entorno requeridas por PolyBot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/check_env.py               # Verifica segun TRADING_MODE actual
  python scripts/check_env.py --phase 8     # Verifica para Fase 8 (recording)
  python scripts/check_env.py --phase 9     # Verifica para Fase 9+ (real trading)
  python scripts/check_env.py --phase real  # Equivalente a --phase 9
        """,
    )
    parser.add_argument(
        "--phase",
        choices=["8", "9", "real"],
        help="Fase a verificar: 8=Recording (sin creds), 9/real=Trading real (con creds)",
    )
    return parser.parse_args()


args = parse_args()

# Determinar si verificamos para real trading
if args.phase in ("9", "real"):
    check_real = True
    phase_label = "Fase 9+ (Real Trading)"
elif args.phase == "8":
    check_real = False
    phase_label = "Fase 8 (Data Recording)"
else:
    mode = os.environ.get("TRADING_MODE", "paper")
    check_real = mode == "real"
    phase_label = f"Modo actual: {mode.upper()}"

print("=" * 65)
print(f"  PolyBot — Environment Check — {phase_label}")
print("=" * 65)

all_ok = True

# ── Variables siempre requeridas ────────────────────────────────────────
print("\n📋 Variables siempre requeridas:")
for var in REQUIRED_ALWAYS:
    val    = os.environ.get(var)
    status = "✅" if val else "❌"
    # No muestra el valor de variables sensibles
    display = (
        "[SET]" if var in {"TELEGRAM_BOT_TOKEN", "DATABASE_URL"}
        else (val or "[MISSING]")
    )
    print(f"  {status} {var}: {display}")
    if not val:
        all_ok = False

# ── Variables de real trading ───────────────────────────────────────────
if check_real:
    print("\n🔴 Variables de Real Trading (Polymarket API):")
    for var in REQUIRED_REAL:
        val    = os.environ.get(var)
        status = "✅" if val else "❌"
        print(f"  {status} {var}: {'[SET]' if val else '[MISSING]'}")
        if not val:
            all_ok = False
else:
    print(f"\n📋 {phase_label} — NO requiere credenciales Polymarket API")
    print("   El WebSocket y Gamma API son publicos.")
    print("   Las credenciales solo se necesitan para trading real (Fase 9+).")

    # Mostrar balance paper si aplica
    bal = os.environ.get("PAPER_INITIAL_BALANCE", "1000.0")
    print(f"\n   💰 Paper trading balance inicial: {bal} USDC")

# ── Resultado ───────────────────────────────────────────────────────────
print("\n" + "=" * 65)
if all_ok:
    if check_real:
        print("✅ Todo OK — puedes arrancar el bot en modo REAL")
    else:
        print("✅ Todo OK — puedes ejecutar recording y paper trading")
    print("   Siguiente paso:")
    if not check_real:
        print("   • Grabar datos: python scripts/record_live_data.py --all --duration-hours 24")
        print("   • Cuando tengas credenciales: python scripts/check_env.py --phase real")
    else:
        print("   • Verifica el checklist pre-real-trading en WORKFLOW.md")
    sys.exit(0)
else:
    print("❌ Faltan variables de entorno")
    print("   • Revisa tu archivo .env (copia desde .env.example si no existe)")
    if not check_real:
        missing_real = [v for v in REQUIRED_REAL if not os.environ.get(v)]
        if 0 < len(missing_real) < 5:
            print(f"   • Para real trading faltan: {missing_real}")
            print("   • Pero puedes ejecutar Fase 8 (recording) sin ellas.")
    sys.exit(1)