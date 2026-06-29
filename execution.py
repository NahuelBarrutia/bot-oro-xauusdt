"""
Capa de ejecucion sobre Bybit (pybit v5).

Responsabilidades:
  - Conectar al exchange correcto (testnet / mainnet)
  - Consultar balance, posicion abierta, estado de ordenes
  - Colocar orden limite con SL
  - Cancelar ordenes pendientes
  - Cerrar posicion con orden de mercado
  - Obtener klines H1 (con fuente configurable)

Bybit Testnet: https://testnet.bybit.com -> API Management
"""

import time
import math
from pybit.unified_trading import HTTP

from config import (
    SYMBOL, CATEGORY, INTERVAL, LEVERAGE,
    API_KEY, API_SECRET, ORDER_TARGET, SIGNAL_SOURCE,
    DRY_RUN, COMMISSION_PCT,
)


# ── Sesiones HTTP ─────────────────────────────────────────────────────────────

def _make_session(target: str, auth: bool = False) -> HTTP:
    is_testnet = (target == "testnet")
    if auth:
        return HTTP(testnet=is_testnet, api_key=API_KEY, api_secret=API_SECRET)
    return HTTP(testnet=is_testnet)


# Sesion autenticada para ordenes (testnet)
_order_session:  HTTP | None = None
# Sesion publica para klines (mainnet — datos reales)
_kline_session:  HTTP | None = None


def init_sessions() -> None:
    global _order_session, _kline_session
    _order_session = _make_session(ORDER_TARGET, auth=True)
    _kline_session = _make_session(SIGNAL_SOURCE, auth=False)


def order_session() -> HTTP:
    if _order_session is None:
        raise RuntimeError("Sesiones no inicializadas. Llamar init_sessions() primero.")
    return _order_session


def kline_session() -> HTTP:
    if _kline_session is None:
        raise RuntimeError("Sesiones no inicializadas.")
    return _kline_session


# ── Instrumento ───────────────────────────────────────────────────────────────

_instrument: dict | None = None

def get_instrument_info() -> dict:
    global _instrument
    if _instrument is not None:
        return _instrument
    resp = kline_session().get_instruments_info(category=CATEGORY, symbol=SYMBOL)
    _check(resp, "get_instruments_info")
    _instrument = resp["result"]["list"][0]
    return _instrument


def min_qty() -> float:
    return float(get_instrument_info()["lotSizeFilter"]["minOrderQty"])

def qty_step() -> float:
    return float(get_instrument_info()["lotSizeFilter"]["qtyStep"])

def price_tick() -> float:
    return float(get_instrument_info()["priceFilter"]["tickSize"])

def round_price(p: float) -> float:
    tick = price_tick()
    return round(round(p / tick) * tick, 10)

def round_qty(q: float) -> float:
    step = qty_step()
    floored = math.floor(q / step) * step
    return round(max(floored, min_qty()), 10)


# ── Klines (señales) ──────────────────────────────────────────────────────────

def get_h1_candles(limit: int = 60) -> list[dict]:
    """
    Obtiene las ultimas `limit` velas H1 cerradas desde la fuente de señales.
    Retorna lista de dicts [{time, open, high, low, close, volume}, ...] ASC.
    """
    resp = kline_session().get_kline(
        category=CATEGORY,
        symbol=SYMBOL,
        interval=INTERVAL,
        limit=limit + 1,   # +1 porque la ultima puede estar abierta
    )
    _check(resp, "get_kline")
    rows = resp["result"]["list"]   # Bybit devuelve DESC (newest first)

    # Descartar la vela mas reciente si aun esta abierta
    now_ms = int(time.time() * 1000)
    bars = []
    for row in rows:
        open_ms = int(row[0])
        # La vela H1 cierra 3600000 ms despues de su apertura
        close_ms = open_ms + 3_600_000
        if close_ms <= now_ms:
            bars.append({
                "time":   open_ms,
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
            })

    bars.reverse()   # ASC cronologico
    return bars[-limit:]


# ── Balance y posicion ────────────────────────────────────────────────────────

def get_usdt_balance() -> float:
    resp = order_session().get_wallet_balance(accountType="UNIFIED")
    _check(resp, "get_wallet_balance")
    for coin in resp["result"]["list"][0]["coin"]:
        if coin["coin"] == "USDT":
            return float(coin["walletBalance"])
    return 0.0


def get_open_position() -> dict | None:
    """Retorna la posicion abierta en SYMBOL o None si no hay."""
    resp = order_session().get_positions(category=CATEGORY, symbol=SYMBOL)
    _check(resp, "get_positions")
    for pos in resp["result"]["list"]:
        if float(pos["size"]) > 0:
            return pos
    return None


# ── Calcular qty ──────────────────────────────────────────────────────────────

def calc_qty(entry: float, sl: float, risk_usd: float) -> float:
    """
    Qty = risk_usd / SL_distance, redondeado al step del instrumento.
    Minimo: min_qty().
    """
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    return round_qty(risk_usd / sl_dist)


# ── Ordenes ───────────────────────────────────────────────────────────────────

def set_leverage() -> None:
    if DRY_RUN:
        print(f"  [DRY] set_leverage {LEVERAGE}x")
        return
    try:
        order_session().set_leverage(
            category=CATEGORY,
            symbol=SYMBOL,
            buyLeverage=str(LEVERAGE),
            sellLeverage=str(LEVERAGE),
        )
    except Exception:
        pass  # ya estaba configurado


def place_limit_buy(limit_price: float, qty: float, sl_price: float) -> str | None:
    """
    Coloca orden limite BUY con SL adjunto.
    Retorna order_id o None si DRY_RUN.
    """
    lp = round_price(limit_price)
    sp = round_price(sl_price)
    q  = str(round_qty(qty))

    if DRY_RUN:
        fake_id = f"DRY-{int(time.time())}"
        print(f"  [DRY] LIMIT BUY  qty={q}  price={lp}  sl={sp}  -> id={fake_id}")
        return fake_id

    resp = order_session().place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side="Buy",
        orderType="Limit",
        qty=q,
        price=str(lp),
        timeInForce="GTC",
        stopLoss=str(sp),
        slTriggerBy="LastPrice",
        positionIdx=0,   # one-way mode
    )
    _check(resp, "place_limit_buy")
    order_id = resp["result"]["orderId"]
    print(f"  [ORDER] LIMIT BUY colocada  qty={q}  price={lp}  sl={sp}  id={order_id}")
    return order_id


def cancel_order(order_id: str) -> bool:
    if DRY_RUN:
        print(f"  [DRY] cancel_order {order_id}")
        return True
    try:
        resp = order_session().cancel_order(
            category=CATEGORY, symbol=SYMBOL, orderId=order_id
        )
        ok = resp.get("retCode") == 0
        print(f"  [ORDER] Cancel {'OK' if ok else 'FAIL'}  id={order_id}")
        return ok
    except Exception as e:
        print(f"  [WARN] cancel_order error: {e}")
        return False


def get_order_status(order_id: str) -> str:
    """
    Retorna el status de la orden:
      "Filled" | "Cancelled" | "New" | "PartiallyFilled" | "Unknown"
    """
    if DRY_RUN:
        return "New"   # simulado: siempre pendiente
    try:
        resp = order_session().get_order_history(
            category=CATEGORY, symbol=SYMBOL, orderId=order_id, limit=1
        )
        _check(resp, "get_order_history")
        lst = resp["result"]["list"]
        if lst:
            return lst[0]["orderStatus"]
        return "Unknown"
    except Exception as e:
        print(f"  [WARN] get_order_status: {e}")
        return "Unknown"


def get_filled_entry(order_id: str) -> float | None:
    """Retorna el precio de ejecucion medio si la orden fue llenada."""
    if DRY_RUN:
        return None
    try:
        resp = order_session().get_order_history(
            category=CATEGORY, symbol=SYMBOL, orderId=order_id, limit=1
        )
        lst = resp["result"]["list"]
        if lst and lst[0]["orderStatus"] == "Filled":
            return float(lst[0]["avgPrice"])
        return None
    except Exception:
        return None


def close_position_market(qty: float) -> bool:
    """Cierra la posicion con orden market."""
    q = str(round_qty(qty))
    if DRY_RUN:
        print(f"  [DRY] MARKET SELL (close)  qty={q}")
        return True

    resp = order_session().place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side="Sell",
        orderType="Market",
        qty=q,
        timeInForce="IOC",
        reduceOnly=True,
        positionIdx=0,
    )
    ok = resp.get("retCode") == 0
    print(f"  [ORDER] Market CLOSE {'OK' if ok else 'FAIL'}  qty={q}")
    return ok


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check(resp: dict, ctx: str) -> None:
    if resp.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error en {ctx}: {resp.get('retMsg')} (code {resp.get('retCode')})")
