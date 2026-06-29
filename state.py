"""
Persistencia de estado en JSON.

Maquina de estados:
  idle    -> no hay orden ni posicion abierta
  pending -> orden limite colocada, esperando fill
  open    -> posicion abierta

Campos del estado:
  phase:           "idle" | "pending" | "open"
  pending_price:   float   (precio de la orden limite)
  pending_order_id: str
  pending_expiry:  int     (bar index o timestamp relativo — guardamos la vela de expiracion)
  pending_bars:    int     (barras transcurridas desde que se coloco la orden)
  entry_price:     float
  entry_bar_time:  int     (epoch ms de la vela de entrada)
  sl_price:        float
  entry_qty:       float
  bars_held:       int
  time_exit_at_bar: int    (bars_held == HOLD_BARS => cerrar)
  capital:         float
  daily_pnl:       float   (reseteado cada dia UTC)
  daily_date:      str     ("YYYY-MM-DD")
"""

import json
from pathlib import Path

from config import CAPITAL_USD, STATE_FILE


_DEFAULT: dict = {
    "phase":             "idle",
    "pending_price":     0.0,
    "pending_order_id":  "",
    "pending_bars":      0,
    "entry_price":       0.0,
    "entry_bar_time":    0,
    "sl_price":          0.0,
    "entry_qty":         0.0,
    "bars_held":         0,
    "capital":           CAPITAL_USD,
    "daily_pnl":         0.0,
    "daily_date":        "",
}


def load() -> dict:
    path = Path(STATE_FILE)
    if path.exists():
        with open(path) as f:
            data = json.load(f)
        # Compatibilidad: agregar campos nuevos que no existan
        for k, v in _DEFAULT.items():
            data.setdefault(k, v)
        return data
    return dict(_DEFAULT)


def save(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def reset_to_idle(state: dict) -> dict:
    state.update({
        "phase":            "idle",
        "pending_price":    0.0,
        "pending_order_id": "",
        "pending_bars":     0,
        "entry_price":      0.0,
        "entry_bar_time":   0,
        "sl_price":         0.0,
        "entry_qty":        0.0,
        "bars_held":        0,
    })
    return state
