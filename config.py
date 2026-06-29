"""
Configuracion central del bot DailyHigh_SAR_Breakout.

IMPORTANTE — dos fuentes de datos separadas:
  SIGNAL_SOURCE = "mainnet"  → klines publicas del mainnet (precios reales)
  ORDER_TARGET  = "testnet"  → ordenes se ejecutan en Testnet (capital virtual)

Esto resuelve que el testnet de Bybit tiene precios estaticos en XAUUSDT.
Cuando estes listo para live, cambiar ORDER_TARGET = "mainnet".
"""

import os
import pathlib

def _load_keys() -> tuple:
    k = os.getenv("BYBIT_TESTNET_API_KEY") or os.getenv("API_KEY", "")
    s = os.getenv("BYBIT_TESTNET_API_SECRET") or os.getenv("SECRET_KEY", "")
    if not k or not s:
        env_path = pathlib.Path(__file__).parent.parent.parent / ".env"
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        name, val = line.split("=", 1)
                        name, val = name.strip(), val.strip()
                        if name in ("BYBIT_TESTNET_API_KEY", "API_KEY") and not k:
                            k = val
                        if name in ("BYBIT_TESTNET_API_SECRET", "SECRET_KEY") and not s:
                            s = val
        except FileNotFoundError:
            pass
    return k, s

# ── Exchange ───────────────────────────────────────────────────────────────────

SYMBOL        = "XAUUSDT"
CATEGORY      = "linear"          # perpetuo lineal
INTERVAL      = "60"              # H1 en formato Bybit (minutos como string)
LEVERAGE      = 2

# Fuente de klines para señales (mainnet siempre tiene precios reales)
SIGNAL_SOURCE = "mainnet"         # "mainnet" | "testnet"

# Destino de ordenes
ORDER_TARGET  = "testnet"         # "testnet" | "mainnet"

# Modo sin ordenes reales — configurable por env var (Railway: DRY_RUN=false)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

API_KEY, API_SECRET = _load_keys()

# ── Capital y riesgo ───────────────────────────────────────────────────────────

CAPITAL_USD     = 5_000.0   # capital inicial virtual
RISK_PCT        = 0.02      # 2% del capital por trade
DAILY_STOP_USD  = 500.0     # stop diario: -$500 → bot se apaga

# ── Estrategia ─────────────────────────────────────────────────────────────────

SAR_STEP     = 0.02
SAR_MAX      = 0.20
SAR_CONFIRM  = 3            # barras consecutivas de SAR debajo del precio
DAILY_BARS   = 24           # ventana del Daily High (H1 x 24 = 1 dia)
PENDING_BARS = 5            # velas hasta que expira la orden limitde
HOLD_BARS    = 9            # velas hasta el cierre temporal

# ── Comision Bybit Testnet (maker limit) ──────────────────────────────────────

COMMISSION_PCT = 0.0002     # 0.02% por lado

# ── Rutas ──────────────────────────────────────────────────────────────────────

BOT_DIR    = pathlib.Path(__file__).parent
STATE_FILE = BOT_DIR / "state.json"
LOG_FILE   = BOT_DIR / "trades.csv"
