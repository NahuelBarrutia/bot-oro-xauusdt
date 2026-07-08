"""
Logger CSV de trades.

Columnas:
  timestamp, symbol, entry, sl, exit_price, exit_reason,
  velas_abiertas, resultado_R, pnl_usd, capital_acumulado
"""

import csv
import os
from datetime import datetime, timezone

from config import LOG_FILE, SYMBOL

COLUMNS = [
    "timestamp", "symbol",
    "entry", "sl", "sl_dist",
    "exit_price", "exit_reason",
    "velas_abiertas",
    "resultado_R", "pnl_usd",
    "capital_acumulado",
    "order_id",
]


def _ensure_header() -> None:
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0:
        with open(LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(COLUMNS)


def log_trade(
    entry:        float,
    sl:           float,
    exit_price:   float,
    exit_reason:  str,    # "SL" | "TIME" | "DAILY_STOP"
    bars_held:    int,
    pnl_usd:      float,
    capital:      float,
    order_id:     str = "",
) -> None:
    _ensure_header()

    sl_dist    = entry - sl
    r          = (exit_price - entry) / sl_dist if sl_dist != 0 else 0.0

    row = [
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        SYMBOL,
        round(entry,      2),
        round(sl,         2),
        round(sl_dist,    2),
        round(exit_price, 2),
        exit_reason,
        bars_held,
        round(r,          4),
        round(pnl_usd,    2),
        round(capital,    2),
        order_id,
    ]

    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow(row)

    print(f"  [LOG] {exit_reason:5s}  entry={entry:.2f}  exit={exit_price:.2f}  "
          f"R={r:+.3f}  pnl=${pnl_usd:+.2f}  capital=${capital:,.2f}")


def get_running_stats() -> dict:
    """Retorna stats acumuladas del CSV para mostrar en cada iteracion."""
    if not os.path.exists(LOG_FILE):
        return {"n": 0, "wins": 0, "wr": 0.0, "r_avg": 0.0, "streak": 0, "streak_type": ""}

    trades = []
    with open(LOG_FILE, newline="") as f:
        for row in csv.DictReader(f):
            trades.append(row)

    if not trades:
        return {"n": 0, "wins": 0, "wr": 0.0, "r_avg": 0.0, "streak": 0, "streak_type": ""}

    n     = len(trades)
    wins  = sum(1 for t in trades if float(t["pnl_usd"]) > 0)
    r_avg = sum(float(t["resultado_R"]) for t in trades) / n

    # Racha actual (ultimos trades consecutivos del mismo tipo)
    streak = 0
    streak_type = ""
    last = None
    for t in reversed(trades):
        won = float(t["pnl_usd"]) > 0
        if last is None:
            last = won
            streak_type = "W" if won else "L"
        if won == last:
            streak += 1
        else:
            break

    return {
        "n":           n,
        "wins":        wins,
        "wr":          wins / n * 100,
        "r_avg":       r_avg,
        "streak":      streak,
        "streak_type": streak_type,
    }


def print_summary() -> None:
    """Imprime resumen de trades del CSV."""
    if not os.path.exists(LOG_FILE):
        print("  Sin trades registrados aun.")
        return

    trades = []
    with open(LOG_FILE, newline="") as f:
        for row in csv.DictReader(f):
            trades.append(row)

    if not trades:
        print("  Sin trades registrados aun.")
        return

    n      = len(trades)
    wins   = sum(1 for t in trades if float(t["pnl_usd"]) > 0)
    pnl    = sum(float(t["pnl_usd"]) for t in trades)
    r_avg  = sum(float(t["resultado_R"]) for t in trades) / n
    cap    = float(trades[-1]["capital_acumulado"])

    print(f"\n  Resumen ({n} trades):")
    print(f"    WR:     {wins/n*100:.1f}%")
    print(f"    R avg:  {r_avg:+.3f}")
    print(f"    PnL:    ${pnl:+,.2f}")
    print(f"    Capital: ${cap:,.2f}")
    print(f"    Log:    {LOG_FILE}")
