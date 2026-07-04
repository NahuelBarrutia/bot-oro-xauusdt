"""
Capa de ejecucion sobre Binance Futures (python-binance).

Responsabilidades:
  - Conectar a Binance Futures (testnet o mainnet)
  - Obtener klines H1, balance, posicion abierta
  - Colocar orden limite BUY
  - Colocar STOP_MARKET (SL) despues del fill
  - Cancelar ordenes
  - Cerrar posicion con market

Flujo de SL en Binance (diferente a Bybit):
  - Binance no permite adjuntar SL a la orden limite en un solo call.
  - Se coloca el SL (STOP_MARKET) una vez que la orden limite fue llenada.
  - main.py llama a place_sl_order() inmediatamente despues de detectar el fill.
"""

import time
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config
from config import SYMBOL, INTERVAL, LEVERAGE, TESTNET, API_KEY, API_SECRET


# ── Cliente ───────────────────────────────────────────────────────────────────

_client: Client | None = None


def init_client() -> None:
    global _client
    _client = Client(API_KEY, API_SECRET, testnet=TESTNET)
    env = "TESTNET" if TESTNET else "MAINNET"
    print(f"  [OK] Binance Futures {env} conectado")


def client() -> Client:
    if _client is None:
        raise RuntimeError("Cliente no inicializado. Llamar init_client() primero.")
    return _client


# ── Info del instrumento ──────────────────────────────────────────────────────

_info: dict | None = None


def get_instrument_info() -> dict:
    global _info
    if _info is not None:
        return _info
    resp = client().futures_exchange_info()
    sym  = next((s for s in resp["symbols"] if s["symbol"] == SYMBOL), None)
    if sym is None:
        raise RuntimeError(f"{SYMBOL} no encontrado en Binance Futures")
    _info = sym
    return _info


def price_tick() -> float:
    f = next(f for f in get_instrument_info()["filters"] if f["filterType"] == "PRICE_FILTER")
    return float(f["tickSize"])


def qty_step() -> float:
    f = next(f for f in get_instrument_info()["filters"] if f["filterType"] == "LOT_SIZE")
    return float(f["stepSize"])


def min_qty() -> float:
    f = next(f for f in get_instrument_info()["filters"] if f["filterType"] == "LOT_SIZE")
    return float(f["minQty"])


def round_price(p: float) -> float:
    tick = price_tick()
    return round(round(p / tick) * tick, 8)


def round_qty(q: float) -> float:
    step = qty_step()
    floored = math.floor(q / step) * step
    return round(max(floored, min_qty()), 8)


def _fmt_price(p: float) -> str:
    tick = price_tick()
    decimals = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return f"{round_price(p):.{decimals}f}"


def _fmt_qty(q: float) -> str:
    step = qty_step()
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f"{round_qty(q):.{decimals}f}"


# ── Klines ────────────────────────────────────────────────────────────────────

def get_h1_candles(limit: int = 60) -> list[dict]:
    """
    Obtiene las ultimas `limit` velas H1 cerradas desde Binance Futures.
    Retorna lista de dicts [{time, open, high, low, close, volume}] ASC.
    """
    raw = client().futures_klines(symbol=SYMBOL, interval=INTERVAL, limit=limit + 1)

    now_ms = int(time.time() * 1000)
    bars = []
    for row in raw:
        close_ms = int(row[6])   # Binance provee close_time directamente
        if close_ms < now_ms:
            bars.append({
                "time":   int(row[0]),
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
            })

    return bars[-limit:]


# ── Balance y posicion ────────────────────────────────────────────────────────

def get_usdt_balance() -> float:
    for b in client().futures_account_balance():
        if b["asset"] == "USDT":
            return float(b["balance"])
    return 0.0


def get_open_position() -> dict | None:
    """Retorna la posicion abierta en SYMBOL o None si no hay."""
    for pos in client().futures_position_information(symbol=SYMBOL):
        if float(pos["positionAmt"]) != 0:
            return pos
    return None


# ── Calcular qty ──────────────────────────────────────────────────────────────

def calc_qty(entry: float, sl: float, risk_usd: float, capital: float, leverage: float) -> float:
    """
    Qty por riesgo fijo, recortado al margen disponible.
    Si el riesgo objetivo (risk_usd) requeriria mas margen del que hay,
    se reduce el qty (y por lo tanto el riesgo real de esa trade) en vez
    de fallar la orden por -2019 Margin is insufficient.
    """
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0

    qty_risk = risk_usd / sl_dist

    max_notional = capital * leverage * config.MARGIN_BUFFER
    qty_margin   = max_notional / entry

    qty = min(qty_risk, qty_margin)
    if qty < qty_risk:
        print(f"  [WARN] qty recortado por margen: {qty_risk:.4f} -> {qty:.4f} "
              f"(riesgo real ${qty*sl_dist:.2f} en vez de ${risk_usd:.2f})")

    return round_qty(qty)


# ── Ordenes ───────────────────────────────────────────────────────────────────

def set_leverage() -> None:
    if config.DRY_RUN:
        print(f"  [DRY] set_leverage {LEVERAGE}x")
        return
    try:
        client().futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        print(f"  [OK] Leverage configurado: {LEVERAGE}x")
    except BinanceAPIException as e:
        if "No need to change leverage" in str(e):
            print(f"  [OK] Leverage ya era {LEVERAGE}x")
        else:
            print(f"  [WARN] set_leverage: {e}")


def get_open_limit_buy(price: float | None = None) -> dict | None:
    """
    Busca una orden LIMIT BUY abierta para SYMBOL.
    Si se pasa price, filtra por precio exacto (tolerancia 0.01).
    Usada para idempotencia tras timeout -1007 y para adopcion defensiva en idle.
    """
    try:
        orders = client().futures_get_open_orders(symbol=SYMBOL)
    except BinanceAPIException as e:
        print(f"  [WARN] get_open_limit_buy: {e}")
        return None
    for o in orders:
        if o.get("type") == "LIMIT" and o.get("side") == "BUY":
            if price is None or abs(float(o["price"]) - price) < 0.01:
                return o
    return None


def place_limit_buy(limit_price: float, qty: float) -> str | None:
    """
    Coloca orden limite BUY sin SL adjunto.
    El SL se coloca por separado con place_sl_order() tras el fill.
    En caso de timeout -1007 (estado desconocido), verifica si la orden
    quedo abierta en Binance antes de reportar fallo.
    Retorna order_id o None si DRY_RUN o si fallo sin recuperacion.
    """
    lp = _fmt_price(limit_price)
    q  = _fmt_qty(qty)

    if config.DRY_RUN:
        fake_id = f"DRY-{int(time.time())}"
        print(f"  [DRY] LIMIT BUY  qty={q}  price={lp}  -> id={fake_id}")
        return fake_id

    # Idempotencia previa: si ya existe una orden al mismo precio, reutilizarla
    existing = get_open_limit_buy(limit_price)
    if existing is not None:
        order_id = str(existing["orderId"])
        print(f"  [ORDER] LIMIT BUY ya existe  price={lp}  id={order_id}  (reutilizado)")
        return order_id

    try:
        resp = client().futures_create_order(
            symbol      = SYMBOL,
            side        = "BUY",
            type        = "LIMIT",
            quantity    = q,
            price       = lp,
            timeInForce = "GTC",
        )
        order_id = str(resp["orderId"])
        print(f"  [ORDER] LIMIT BUY  qty={q}  price={lp}  id={order_id}")
        return order_id
    except (BinanceAPIException, KeyError) as e:
        # -1007: timeout — la orden puede haberse creado del lado de Binance
        print(f"  [WARN] place_limit_buy fallo: {e}")
        existing = get_open_limit_buy(limit_price)
        if existing is not None:
            order_id = str(existing["orderId"])
            print(f"  [ORDER] LIMIT BUY recuperado tras timeout  id={order_id}")
            return order_id
        print(f"  [WARN] Sin orden abierta confirmada — no se coloco la orden")
        return None


def get_open_sl_order() -> dict | None:
    """Busca una orden STOP_MARKET SELL ya abierta para SYMBOL (idempotencia)."""
    try:
        orders = client().futures_get_open_orders(symbol=SYMBOL)
    except BinanceAPIException as e:
        print(f"  [WARN] get_open_sl_order: {e}")
        return None
    for o in orders:
        if o.get("type") == "STOP_MARKET" and o.get("side") == "SELL":
            return o
    return None


def place_sl_order(sl_price: float, qty: float, retries: int = 2) -> str | None:
    """
    Coloca STOP_MARKET SELL para el SL. Llamar tras confirmar el fill.
    Antes de colocar, verifica si ya existe un SL abierto (evita duplicados
    si una llamada previa fallo en el parseo de la respuesta pero la orden
    si se creo del lado de Binance).
    Retorna sl_order_id, o None si DRY_RUN o si fallaron todos los intentos.
    """
    if config.DRY_RUN:
        fake_id = f"DRY-SL-{int(time.time())}"
        print(f"  [DRY] STOP_MARKET SL  stopPrice={_fmt_price(sl_price)}  -> id={fake_id}")
        return fake_id

    existing = get_open_sl_order()
    if existing is not None:
        sl_order_id = str(existing["orderId"])
        print(f"  [SL] Ya existe un STOP_MARKET abierto  id={sl_order_id}  "
              f"(se reutiliza, no se duplica)")
        return sl_order_id

    sp = _fmt_price(sl_price)
    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            resp = client().futures_create_order(
                symbol        = SYMBOL,
                side          = "SELL",
                type          = "STOP_MARKET",
                stopPrice     = sp,
                closePosition = "true",
                timeInForce   = "GTE_GTC",
            )
            sl_order_id = str(resp["orderId"])
            print(f"  [ORDER] STOP_MARKET SL  stopPrice={sp}  id={sl_order_id}")
            return sl_order_id
        except (BinanceAPIException, KeyError) as e:
            last_err = e
            print(f"  [WARN] place_sl_order intento {attempt}/{retries} fallo: {e}")
            # La orden puede haberse creado en Binance pese al error de respuesta.
            existing = get_open_sl_order()
            if existing is not None:
                sl_order_id = str(existing["orderId"])
                print(f"  [SL] Recuperado tras fallo de respuesta  id={sl_order_id}")
                return sl_order_id
            time.sleep(2)

    print(f"  [ERROR] No se pudo colocar ni confirmar el SL tras {retries} intentos: {last_err}")
    return None


def cancel_order(order_id: str) -> bool:
    if config.DRY_RUN:
        print(f"  [DRY] cancel_order {order_id}")
        return True
    try:
        client().futures_cancel_order(symbol=SYMBOL, orderId=int(order_id))
        print(f"  [ORDER] Cancel OK  id={order_id}")
        return True
    except BinanceAPIException as e:
        print(f"  [WARN] cancel_order: {e}")
        return False


def get_order_status(order_id: str) -> str:
    """
    Retorna status: "FILLED" | "CANCELED" | "NEW" | "PARTIALLY_FILLED" | "EXPIRED" | "UNKNOWN"
    """
    if config.DRY_RUN:
        return "NEW"
    try:
        resp = client().futures_get_order(symbol=SYMBOL, orderId=int(order_id))
        return resp["status"]
    except BinanceAPIException as e:
        print(f"  [WARN] get_order_status: {e}")
        return "UNKNOWN"


def get_filled_entry(order_id: str) -> float | None:
    """Retorna precio promedio de ejecucion si la orden fue llenada."""
    if config.DRY_RUN:
        return None
    try:
        resp = client().futures_get_order(symbol=SYMBOL, orderId=int(order_id))
        if resp["status"] == "FILLED":
            return float(resp["avgPrice"])
        return None
    except BinanceAPIException:
        return None


def close_position_market(qty: float) -> bool:
    """Cierra la posicion con orden MARKET reduceOnly."""
    q = _fmt_qty(qty)
    if config.DRY_RUN:
        print(f"  [DRY] MARKET SELL (close)  qty={q}")
        return True
    try:
        resp = client().futures_create_order(
            symbol     = SYMBOL,
            side       = "SELL",
            type       = "MARKET",
            quantity   = q,
            reduceOnly = "true",
        )
        print(f"  [ORDER] Market CLOSE OK  qty={q}  id={resp['orderId']}")
        return True
    except BinanceAPIException as e:
        print(f"  [WARN] close_position_market: {e}")
        return False
