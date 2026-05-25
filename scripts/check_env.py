# scripts/check_env.py
# Ejecutar ANTES de arrancar: python scripts/check_env.py

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

print("=" * 50)
print("  Polymarket Bot — Environment Check")
print("=" * 50)

all_ok = True

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

mode = os.environ.get("TRADING_MODE", "paper")
print(f"\n🔧 Modo de trading: {mode.upper()}")

if mode == "real":
    print("\n🔴 Variables de Real Trading:")
    for var in REQUIRED_REAL:
        val    = os.environ.get(var)
        status = "✅" if val else "❌"
        print(f"  {status} {var}: {'[SET]' if val else '[MISSING]'}")
        if not val:
            all_ok = False
else:
    print("\n📋 Modo paper — variables de real trading no requeridas")
    bal = os.environ.get("PAPER_INITIAL_BALANCE", "1000.0")
    print(f"  💰 Balance inicial: {bal} USDC")

print("\n" + "=" * 50)
if all_ok:
    print("✅ Todo OK — puedes arrancar el bot")
    sys.exit(0)
else:
    print("❌ Faltan variables — revisa tu .env")
    sys.exit(1)