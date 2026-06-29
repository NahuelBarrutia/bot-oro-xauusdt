"""
Logica de señales para DailyHigh_SAR_Breakout.

Recibe un DataFrame de velas H1 y devuelve:
  - señal activa (True/False)
  - precio de la orden limite (Daily High)
"""

import numpy as np
import pandas as pd

from config import SAR_STEP, SAR_MAX, SAR_CONFIRM, DAILY_BARS


# ── Parabolic SAR ─────────────────────────────────────────────────────────────

def compute_sar(high: np.ndarray, low: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Calcula SAR causal. Identico al motor de backtest.
    Retorna (sar, trend) con trend=1 uptrend, -1 downtrend.
    """
    n   = len(high)
    sar   = np.empty(n, dtype=float)
    trend = np.empty(n, dtype=int)
    ep    = np.empty(n, dtype=float)
    af    = np.empty(n, dtype=float)

    sar[0] = low[0]; trend[0] = 1; ep[0] = high[0]; af[0] = SAR_STEP

    for i in range(1, n):
        pt = trend[i-1]; ps = sar[i-1]; pe = ep[i-1]; pa = af[i-1]

        if pt == 1:
            ns = ps + pa * (pe - ps)
            ns = min(ns, low[i-1]) if i == 1 else min(ns, low[i-1], low[i-2])
            if low[i] < ns:
                trend[i] = -1; sar[i] = pe; ep[i] = low[i];  af[i] = SAR_STEP
            else:
                trend[i] = 1; sar[i] = ns
                ep[i] = high[i] if high[i] > pe else pe
                af[i] = min(pa + SAR_STEP, SAR_MAX) if high[i] > pe else pa
        else:
            ns = ps - pa * (ps - pe)
            ns = max(ns, high[i-1]) if i == 1 else max(ns, high[i-1], high[i-2])
            if high[i] > ns:
                trend[i] = 1; sar[i] = pe; ep[i] = high[i]; af[i] = SAR_STEP
            else:
                trend[i] = -1; sar[i] = ns
                ep[i] = low[i] if low[i] < pe else pe
                af[i] = min(pa + SAR_STEP, SAR_MAX) if low[i] < pe else pa

    return sar, trend


# ── Señal ─────────────────────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame) -> dict:
    """
    Evalua la señal sobre las ultimas velas de df (cierre de la ultima barra).

    Args:
        df: DataFrame con columnas [time, open, high, low, close], ordenado ASC,
            minimo DAILY_BARS + SAR_CONFIRM + 5 filas.

    Returns:
        {
          "signal":       True/False,
          "limit_price":  float (Daily High),
          "close":        float (precio de cierre actual),
          "sar_last":     float,
          "reason":       str (descripcion del resultado),
        }
    """
    if len(df) < DAILY_BARS + SAR_CONFIRM + 5:
        return {"signal": False, "reason": "insuficientes velas"}

    high  = df["high"].values.astype(float)
    low   = df["low"].values.astype(float)
    close = df["close"].values.astype(float)

    sar, _ = compute_sar(high, low)

    i = len(df) - 1   # ultima vela cerrada

    # Condicion 1: SAR_CONFIRM barras consecutivas con SAR < close
    sar_ok = all(sar[i - k] < close[i - k] for k in range(SAR_CONFIRM))
    if not sar_ok:
        return {
            "signal": False,
            "limit_price": 0.0,
            "close": float(close[i]),
            "sar_last": float(sar[i]),
            "reason": f"SAR no confirm ({SAR_CONFIRM} barras): sar={sar[i]:.2f} close={close[i]:.2f}",
        }

    # Condicion 2: close < Daily High (rolling max de las ultimas DAILY_BARS velas de HIGH)
    daily_high = float(np.max(high[i - DAILY_BARS + 1 : i + 1]))
    if close[i] >= daily_high:
        return {
            "signal": False,
            "limit_price": daily_high,
            "close": float(close[i]),
            "sar_last": float(sar[i]),
            "reason": f"close {close[i]:.2f} >= DailyHigh {daily_high:.2f}",
        }

    return {
        "signal":      True,
        "limit_price": daily_high,
        "close":       float(close[i]),
        "sar_last":    float(sar[i]),
        "reason":      f"SEÑAL: limite={daily_high:.2f} close={close[i]:.2f} sar={sar[i]:.2f}",
    }
