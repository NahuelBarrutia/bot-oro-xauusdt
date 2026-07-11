"""
Configuracion central del bot DailyHigh_SAR_Breakout — Binance Futures.

Binance no tiene el problema de klines estaticas en testnet para XAUUSDT,
y no bloquea IPs de Argentina en la API publica. Una sola sesion para todo.

Para testnet de Binance Futures: https://testnet.binancefuture.com
  Crear keys en: https://testnet.binancefuture.com -> API Management
"""

import os
import pathlib


def _load_keys() -> tuple:
    k = os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY", "")
    s = os.getenv("BINANCE_API_SECRET") or os.getenv("SECRET_KEY", "")
    if not k or not s:
        for env_file in ["binance.env", ".env"]:
            env_path = pathlib.Path(__file__).parent / env_file
            if not env_path.exists():
                env_path = pathlib.Path(__file__).parent.parent / env_file
            try:
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            name, val = line.split("=", 1)
                            name, val = name.strip(), val.strip()
                            if name in ("BINANCE_API_KEY", "API_KEY") and not k:
                                k = val
                            if name in ("BINANCE_API_SECRET", "SECRET_KEY") and not s:
                                s = val
                if k and s:
                    break
            except FileNotFoundError:
                pass
    return k, s


# ── Exchange ───────────────────────────────────────────────────────────────────

SYMBOL   = "XAUUSDT"
INTERVAL = "1h"       # formato Binance
LEVERAGE = 10         # suficiente margen para qty calculado por riesgo (ver MARGIN_BUFFER)
TESTNET  = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

# Tope de seguridad: nunca usar mas que este % del capital como margen de una posicion
MARGIN_BUFFER = 0.90

# Modo sin ordenes reales (Railway: DRY_RUN=false)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

API_KEY, API_SECRET = _load_keys()

# ── Capital y riesgo ───────────────────────────────────────────────────────────

CAPITAL_USD    = 5_000.0
RISK_PCT       = 0.02       # 2% del capital por trade
DAILY_STOP_USD = 500.0      # stop diario: -$500

# ── Estrategia ─────────────────────────────────────────────────────────────────

SAR_STEP     = 0.02
SAR_MAX      = 0.20
SAR_CONFIRM  = 3
DAILY_BARS   = 24
PENDING_BARS = 5
HOLD_BARS    = 9
# SL minimo en precio: rechaza entradas donde low_previo esta demasiado cerca
# del entry. Para XAUUSD H1, una vela tipica mueve $5-20; por debajo de $5
# el SL es practicamente un fill garantizado y el sizing queda degenerado.
MIN_SL_DIST  = 5.0
# Intervalo del fast poll (segundos). Cada 5 min chequea fills/posicion;
# la logica de señal y bars_held solo se ejecuta cuando hay nueva vela H1.
POLL_INTERVAL = 300

# ── Comision Binance Futures maker (limit order) ───────────────────────────────

COMMISSION_PCT = 0.0002     # 0.02% por lado

# ── Rutas ──────────────────────────────────────────────────────────────────────
# DATA_DIR apunta al volume persistente de Railway (NO al codigo). Si no esta
# seteado (ej. corriendo local), usa la carpeta del bot como antes.

BOT_DIR    = pathlib.Path(__file__).parent
DATA_DIR   = pathlib.Path(os.getenv("DATA_DIR", str(BOT_DIR)))
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE   = DATA_DIR / "trades.csv"
