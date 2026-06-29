"""
Bot principal: DailyHigh_SAR_Breakout en Bybit Testnet (XAUUSDT perpetuo).

Logica H1:
  - Cada hora (al cierre de la vela H1) se evalua la señal.
  - Si estado == idle y hay señal: coloca orden limite BUY en Daily High.
  - Si estado == pending: verifica si se lleno o expiro (PENDING_BARS).
  - Si estado == open: verifica si el SL toco (posicion cerrada por Bybit)
    o si se cumplio el tiempo (HOLD_BARS) -> cierra con market.
  - Daily stop: si pnl del dia <= -DAILY_STOP_USD -> no operar mas hoy.

Uso:
  python main.py             # corre en vivo (loop infinito)
  python main.py --once      # ejecuta UNA iteracion y sale (para testing)
  python main.py --dry-run   # activa DRY_RUN aunque config diga False
"""

import sys
import time
import argparse
from datetime import datetime, timezone

import pandas as pd

import config
import state as st
import strategy
import execution as ex
import logger


# ── Helpers de tiempo ─────────────────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return utc_now().strftime("%Y-%m-%d")


def seconds_to_next_h1() -> float:
    """Segundos hasta el cierre de la proxima vela H1 (+ 5s de margen)."""
    now = time.time()
    h1_sec = 3600
    elapsed = now % h1_sec
    return h1_sec - elapsed + 5.0


# ── Ciclo principal ────────────────────────────────────────────────────────────

def iterate(state: dict) -> dict:
    """
    Ejecuta una iteracion del bot (equivale a una vela H1 cerrada).
    Modifica state en lugar y retorna el estado actualizado.
    """
    now_str = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n{'='*60}")
    print(f"  Iteracion  {now_str}")
    print(f"  Estado:    {state['phase']}  |  Capital: ${state['capital']:,.2f}")
    print(f"  PnL hoy:   ${state['daily_pnl']:+.2f}  (fecha={state['daily_date']})")

    # ── Reset diario ──────────────────────────────────────────────────────────
    today = today_str()
    if state["daily_date"] != today:
        state["daily_pnl"]  = 0.0
        state["daily_date"] = today
        print(f"  [DIARIO] Nuevo dia {today} — PnL reseteado.")
        st.save(state)

    # ── Daily stop ────────────────────────────────────────────────────────────
    if state["daily_pnl"] <= -config.DAILY_STOP_USD:
        print(f"  [STOP] Daily stop alcanzado (${state['daily_pnl']:+.2f}). Sin operaciones hoy.")
        return state

    # ── Obtener velas ─────────────────────────────────────────────────────────
    limit = max(config.DAILY_BARS + config.SAR_CONFIRM + 10, 60)
    try:
        candles = ex.get_h1_candles(limit=limit)
    except Exception as e:
        print(f"  [WARN] Error obteniendo velas: {e}")
        return state

    if len(candles) < config.DAILY_BARS + config.SAR_CONFIRM + 5:
        print(f"  [WARN] Pocas velas: {len(candles)}. Esperando mas datos.")
        return state

    df = pd.DataFrame(candles)

    # ── Precio actual (ultima vela cerrada) ──────────────────────────────────
    last_close = float(df["close"].iloc[-1])
    last_low   = float(df["low"].iloc[-1])
    print(f"  Ultima vela: close={last_close:.2f}  low={last_low:.2f}")

    # ── Maquina de estados ────────────────────────────────────────────────────

    # ESTADO: OPEN → verificar SL tocado o time exit
    if state["phase"] == "open":
        state["bars_held"] += 1
        print(f"  [OPEN] bars_held={state['bars_held']}/{config.HOLD_BARS}  "
              f"entry={state['entry_price']:.2f}  sl={state['sl_price']:.2f}")

        # Verificar si Bybit ya cerro la posicion por SL
        pos = ex.get_open_position()
        if pos is None:
            # SL fue tocado (Bybit cerro la posicion)
            exit_price = state["sl_price"]  # aproximacion conservadora
            pnl = _compute_pnl(state, exit_price)
            state["daily_pnl"] += pnl
            state["capital"]   += pnl
            logger.log_trade(
                entry       = state["entry_price"],
                sl          = state["sl_price"],
                exit_price  = exit_price,
                exit_reason = "SL",
                bars_held   = state["bars_held"],
                pnl_usd     = pnl,
                capital     = state["capital"],
                order_id    = state["pending_order_id"],
            )
            state = st.reset_to_idle(state)
            st.save(state)
            return state

        # Time exit: cerrar si se cumplio HOLD_BARS
        if state["bars_held"] >= config.HOLD_BARS:
            qty = state["entry_qty"]
            ex.close_position_market(qty)
            time.sleep(2)   # dar tiempo al exchange

            # Obtener precio de salida aproximado
            exit_price = last_close
            # Intentar obtener precio exacto de la posicion
            pos2 = ex.get_open_position()
            if pos2 is not None:
                # la orden no se ejecuto aun — usamos close
                exit_price = last_close
            else:
                exit_price = last_close

            pnl = _compute_pnl(state, exit_price)
            state["daily_pnl"] += pnl
            state["capital"]   += pnl
            logger.log_trade(
                entry       = state["entry_price"],
                sl          = state["sl_price"],
                exit_price  = exit_price,
                exit_reason = "TIME",
                bars_held   = state["bars_held"],
                pnl_usd     = pnl,
                capital     = state["capital"],
                order_id    = state["pending_order_id"],
            )
            state = st.reset_to_idle(state)
            st.save(state)

        return state

    # ESTADO: PENDING → verificar fill o expiracion
    if state["phase"] == "pending":
        state["pending_bars"] += 1
        order_id = state["pending_order_id"]
        print(f"  [PENDING] bars={state['pending_bars']}/{config.PENDING_BARS}  "
              f"precio={state['pending_price']:.2f}  order_id={order_id}")

        status = ex.get_order_status(order_id)
        print(f"  [PENDING] Status Bybit: {status}")

        if status == "Filled":
            filled_entry = ex.get_filled_entry(order_id) or state["pending_price"]
            state["entry_price"]    = filled_entry
            state["bars_held"]      = 0
            state["entry_bar_time"] = candles[-1]["time"]
            state["phase"]          = "open"
            print(f"  [FILL] Orden llenada a {filled_entry:.2f}  sl={state['sl_price']:.2f}")
            st.save(state)

        elif state["pending_bars"] >= config.PENDING_BARS or status in ("Cancelled", "Rejected"):
            # Expiro o fue cancelada
            ex.cancel_order(order_id)
            print(f"  [EXPIRE] Orden expirada/cancelada tras {state['pending_bars']} barras.")
            state = st.reset_to_idle(state)
            st.save(state)

        return state

    # ESTADO: IDLE → evaluar señal
    sig = strategy.evaluate(df)
    print(f"  [SIGNAL] {sig['reason']}")

    if not sig["signal"]:
        return state

    # Tenemos señal — calcular SL desde el low de la ultima vela
    limit_price = sig["limit_price"]
    sl_price    = last_low   # low de la barra antes del fill (ultima cerrada)

    if sl_price >= limit_price:
        print(f"  [SKIP] SL degenerado: sl={sl_price:.2f} >= limit={limit_price:.2f}")
        return state

    risk_usd = state["capital"] * config.RISK_PCT
    qty      = ex.calc_qty(limit_price, sl_price, risk_usd)

    if qty <= 0:
        print(f"  [SKIP] qty calculada = 0")
        return state

    print(f"  [ORDER] Colocando LIMIT BUY  limit={limit_price:.2f}  sl={sl_price:.2f}  "
          f"qty={qty}  risk=${risk_usd:.2f}")

    order_id = ex.place_limit_buy(limit_price, qty, sl_price)
    if order_id is None and not config.DRY_RUN:
        print(f"  [WARN] No se obtuvo order_id")
        return state

    state["phase"]             = "pending"
    state["pending_price"]     = limit_price
    state["pending_order_id"]  = order_id or ""
    state["pending_bars"]      = 0
    state["sl_price"]          = sl_price
    state["entry_qty"]         = qty
    st.save(state)

    return state


def _compute_pnl(state: dict, exit_price: float) -> float:
    """PnL en USD considerando leverage y comision."""
    entry    = state["entry_price"]
    sl       = state["sl_price"]
    sl_dist  = entry - sl
    qty      = state["entry_qty"]

    # PnL bruto
    gross = (exit_price - entry) * qty * config.LEVERAGE

    # Comision (entrada + salida)
    comm_entry = entry * qty * config.COMMISSION_PCT
    comm_exit  = exit_price * qty * config.COMMISSION_PCT
    commission = (comm_entry + comm_exit) * config.LEVERAGE

    return round(gross - commission, 2)


# ── Punto de entrada ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="DailyHigh_SAR_Breakout bot")
    parser.add_argument("--once",    action="store_true", help="Ejecutar una sola iteracion")
    parser.add_argument("--dry-run", action="store_true", help="Activar DRY_RUN")
    args = parser.parse_args()

    if args.dry_run:
        config.DRY_RUN = True

    print("DailyHigh_SAR_Breakout Bot")
    print(f"  Symbol:    {config.SYMBOL}")
    print(f"  Leverage:  {config.LEVERAGE}x")
    print(f"  Capital:   ${config.CAPITAL_USD:,.2f}")
    print(f"  Risk/trade:{config.RISK_PCT*100:.0f}%")
    print(f"  Daily stop:${config.DAILY_STOP_USD:,.0f}")
    print(f"  DRY_RUN:   {config.DRY_RUN}")
    print(f"  Signal src:{config.SIGNAL_SOURCE}")
    print(f"  Order dst: {config.ORDER_TARGET}")

    if not config.API_KEY:
        print("\n[ERROR] BYBIT_TESTNET_API_KEY no configurada.")
        print("  Ejecutar: $env:BYBIT_TESTNET_API_KEY = 'tu_key'")
        print("             $env:BYBIT_TESTNET_API_SECRET = 'tu_secret'")
        if not config.DRY_RUN:
            sys.exit(1)

    print("\nInicializando sesiones Bybit...")
    ex.init_sessions()
    ex.set_leverage()

    state = st.load()
    print(f"Estado cargado: phase={state['phase']}  capital=${state['capital']:,.2f}")

    if args.once:
        state = iterate(state)
        logger.print_summary()
        return

    # Loop continuo
    print("\nEntrando al loop H1. Ctrl+C para detener.\n")
    while True:
        try:
            state = iterate(state)
        except KeyboardInterrupt:
            print("\nBot detenido por usuario.")
            logger.print_summary()
            break
        except Exception as e:
            print(f"\n[ERROR] Iteracion fallida: {e}")
            import traceback
            traceback.print_exc()

        wait = seconds_to_next_h1()
        print(f"\n  Proxima iteracion en {wait/60:.1f} min ({wait:.0f}s)...")
        time.sleep(wait)


if __name__ == "__main__":
    main()
