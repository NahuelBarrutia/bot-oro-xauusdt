"""
Bot principal: DailyHigh_SAR_Breakout en Binance Futures (XAUUSDT perpetuo).

Diferencia clave vs Bybit:
  - El SL (STOP_MARKET) se coloca DESPUES del fill de la orden limite,
    no adjunto a ella. Binance no soporta bracket orders en un solo call.

Uso:
  python main.py             # loop continuo
  python main.py --once      # una iteracion (testing)
  python main.py --dry-run   # sin ordenes reales
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return utc_now().strftime("%Y-%m-%d")


def seconds_to_next_h1() -> float:
    now     = time.time()
    elapsed = now % 3600
    return 3600 - elapsed + 5.0


# ── Ciclo principal ────────────────────────────────────────────────────────────

def iterate(state: dict) -> dict:
    now_str = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    stats   = logger.get_running_stats()

    print(f"\n{'='*60}")
    print(f"  Iteracion  {now_str}")
    print(f"  Estado:    {state['phase']}  |  Capital: ${state['capital']:,.2f}")
    print(f"  PnL hoy:   ${state['daily_pnl']:+.2f}  (fecha={state['daily_date']})")
    if stats["n"] > 0:
        racha = f"{stats['streak']}{stats['streak_type']}"
        print(f"  Trades:    {stats['n']} total  |  WR: {stats['wr']:.1f}%  "
              f"|  R avg: {stats['r_avg']:+.3f}  |  Racha: {racha}")

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

    df         = pd.DataFrame(candles)
    last_close = float(df["close"].iloc[-1])
    last_low   = float(df["low"].iloc[-1])
    print(f"  Ultima vela: close={last_close:.2f}  low={last_low:.2f}")

    # ── OPEN: verificar SL tocado o time exit ─────────────────────────────────
    if state["phase"] == "open":
        state["bars_held"] += 1
        print(f"  [OPEN] bars_held={state['bars_held']}/{config.HOLD_BARS}  "
              f"entry={state['entry_price']:.2f}  sl={state['sl_price']:.2f}")

        # Auto-recuperacion: si el SL no quedo confirmado (crash previo en
        # place_sl_order), reintentar/adoptar el SL existente antes de seguir.
        if not state["sl_order_id"] and not config.DRY_RUN:
            print(f"  [WARN] sl_order_id vacio en estado open — reintentando")
            sl_order_id = ex.place_sl_order(state["sl_price"], state["entry_qty"])
            state["sl_order_id"] = sl_order_id or ""
            st.save(state)

            # Si sigue sin SL despues de los reintentos, cerrar la posicion
            # de inmediato — nunca correr una posicion naked sin stop.
            if not state["sl_order_id"]:
                print(f"  [EMERGENCY] SL no confirmado tras reintentos — cerrando posicion")
                ex.cancel_all_open_orders()   # limpiar SL huerfanos antes de cerrar
                ex.close_position_market(state["entry_qty"])
                time.sleep(2)
                pos_check = ex.get_open_position()
                exit_price = float(pos_check["markPrice"]) if pos_check else state["sl_price"]
                pnl = _compute_pnl(state, exit_price)
                state["daily_pnl"] += pnl
                state["capital"]   += pnl
                logger.log_trade(
                    entry       = state["entry_price"],
                    sl          = state["sl_price"],
                    exit_price  = exit_price,
                    exit_reason = "EMERGENCY",
                    bars_held   = state["bars_held"],
                    pnl_usd     = pnl,
                    capital     = state["capital"],
                    order_id    = state["pending_order_id"],
                )
                state = st.reset_to_idle(state)
                st.save(state)
                return state

        pos = ex.get_open_position()

        if pos is None:
            # SL fue tocado — Binance cerro la posicion
            exit_price = state["sl_price"]
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

        if state["bars_held"] >= config.HOLD_BARS:
            # Time exit: cancelar todas las ordenes abiertas, luego cerrar con market
            ex.cancel_all_open_orders()
            ex.close_position_market(state["entry_qty"])
            time.sleep(2)

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

    # ── PENDING: verificar fill o expiracion ──────────────────────────────────
    if state["phase"] == "pending":
        state["pending_bars"] += 1
        order_id = state["pending_order_id"]
        print(f"  [PENDING] bars={state['pending_bars']}/{config.PENDING_BARS}  "
              f"precio={state['pending_price']:.2f}  order_id={order_id}")

        status = ex.get_order_status(order_id)
        print(f"  [PENDING] Status Binance: {status}")

        if status == "FILLED":
            filled_entry = ex.get_filled_entry(order_id) or state["pending_price"]
            state["entry_price"]    = filled_entry
            state["bars_held"]      = 0
            state["entry_bar_time"] = candles[-1]["time"]
            state["phase"]          = "open"
            state["sl_order_id"]    = ""
            st.save(state)   # guardar "open" YA, antes de intentar el SL
            print(f"  [FILL] Orden llenada a {filled_entry:.2f}  sl={state['sl_price']:.2f}")

            # Colocar SL inmediatamente (con retry + deteccion de duplicados)
            sl_order_id = ex.place_sl_order(state["sl_price"], state["entry_qty"])
            state["sl_order_id"] = sl_order_id or ""
            st.save(state)

            # Si el SL no se pudo colocar tras el fill, cerrar ahora mismo
            if not state["sl_order_id"] and not config.DRY_RUN:
                print(f"  [EMERGENCY] SL fallo inmediatamente tras fill — cerrando")
                ex.cancel_all_open_orders()
                ex.close_position_market(state["entry_qty"])
                time.sleep(2)
                pos_check = ex.get_open_position()
                exit_price = float(pos_check["markPrice"]) if pos_check else state["sl_price"]
                pnl = _compute_pnl(state, exit_price)
                state["daily_pnl"] += pnl
                state["capital"]   += pnl
                logger.log_trade(
                    entry       = state["entry_price"],
                    sl          = state["sl_price"],
                    exit_price  = exit_price,
                    exit_reason = "EMERGENCY",
                    bars_held   = state["bars_held"],
                    pnl_usd     = pnl,
                    capital     = state["capital"],
                    order_id    = state["pending_order_id"],
                )
                state = st.reset_to_idle(state)
                st.save(state)
                return state

        elif state["pending_bars"] >= config.PENDING_BARS or status in ("CANCELED", "REJECTED", "EXPIRED"):
            ex.cancel_order(order_id)
            print(f"  [EXPIRE] Orden expirada tras {state['pending_bars']} barras.")
            state = st.reset_to_idle(state)
            st.save(state)

        return state

    # ── IDLE: chequeo defensivo — el estado local pudo perderse en un redeploy
    # o un timeout -1007 en place_limit_buy dejo una orden abierta sin state.
    if not config.DRY_RUN:
        pos = ex.get_open_position()
        if pos is not None:
            qty = abs(float(pos["positionAmt"]))
            sl_order = ex.get_open_sl_order()
            sl_price = float(sl_order["stopPrice"]) if sl_order else last_low
            print(f"  [WARN] Posicion abierta en Binance sin estado local — adoptando "
                  f"(entry={pos['entryPrice']}  qty={qty}  sl={sl_price})")
            state["phase"]          = "open"
            state["entry_price"]    = float(pos["entryPrice"])
            state["entry_qty"]      = qty
            state["sl_price"]       = sl_price
            state["sl_order_id"]    = str(sl_order["orderId"]) if sl_order else ""
            state["bars_held"]      = 0
            st.save(state)
            return state

        limit_order = ex.get_open_limit_buy()
        if limit_order is not None:
            order_price = float(limit_order["price"])
            order_qty   = float(limit_order["origQty"])
            order_id    = str(limit_order["orderId"])
            print(f"  [WARN] Orden LIMIT BUY abierta sin estado local — adoptando "
                  f"(price={order_price:.2f}  qty={order_qty}  id={order_id})")
            state["phase"]            = "pending"
            state["pending_price"]    = order_price
            state["pending_order_id"] = order_id
            state["pending_bars"]     = 0
            state["sl_price"]         = last_low
            state["entry_qty"]        = order_qty
            state["sl_order_id"]      = ""
            st.save(state)
            return state

    # ── IDLE: evaluar señal ───────────────────────────────────────────────────
    sig = strategy.evaluate(df)
    print(f"  [SIGNAL] {sig['reason']}")

    if not sig["signal"]:
        return state

    limit_price = sig["limit_price"]
    sl_price    = last_low

    if sl_price >= limit_price:
        print(f"  [SKIP] SL degenerado: sl={sl_price:.2f} >= limit={limit_price:.2f}")
        return state

    if (limit_price - sl_price) < config.MIN_SL_DIST:
        print(f"  [SKIP] SL demasiado cerca: dist={limit_price - sl_price:.2f} < "
              f"min={config.MIN_SL_DIST:.1f}  (sl={sl_price:.2f}  limit={limit_price:.2f})")
        return state

    risk_usd = state["capital"] * config.RISK_PCT
    qty      = ex.calc_qty(limit_price, sl_price, risk_usd, state["capital"], config.LEVERAGE)

    if qty <= 0:
        print(f"  [SKIP] qty calculada = 0")
        return state

    print(f"  [ORDER] LIMIT BUY  limit={limit_price:.2f}  sl={sl_price:.2f}  "
          f"qty={qty}  risk=${risk_usd:.2f}")

    order_id = ex.place_limit_buy(limit_price, qty)
    if order_id is None and not config.DRY_RUN:
        print(f"  [WARN] No se obtuvo order_id")
        return state

    state["phase"]             = "pending"
    state["pending_price"]     = limit_price
    state["pending_order_id"]  = order_id or ""
    state["pending_bars"]      = 0
    state["sl_price"]          = sl_price
    state["sl_order_id"]       = ""
    state["entry_qty"]         = qty
    st.save(state)

    return state


def _compute_pnl(state: dict, exit_price: float) -> float:
    """
    PnL real tal como lo liquida Binance: precio x qty, sin multiplicar
    de nuevo por leverage (el leverage ya esta reflejado en el margen
    usado para abrir la posicion, no en el PnL por unidad).
    """
    entry = state["entry_price"]
    qty   = state["entry_qty"]
    gross = (exit_price - entry) * qty
    comm  = (entry + exit_price) * qty * config.COMMISSION_PCT
    return round(gross - comm, 2)


# ── Punto de entrada ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="DailyHigh_SAR_Breakout — Binance Futures")
    parser.add_argument("--once",    action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        config.DRY_RUN = True

    env = "TESTNET" if config.TESTNET else "MAINNET"
    print("DailyHigh_SAR_Breakout Bot — Binance Futures")
    print(f"  Symbol:    {config.SYMBOL}")
    print(f"  Exchange:  Binance Futures {env}")
    print(f"  Leverage:  {config.LEVERAGE}x")
    print(f"  Capital:   ${config.CAPITAL_USD:,.2f}")
    print(f"  Risk/trade:{config.RISK_PCT*100:.0f}%")
    print(f"  Daily stop:${config.DAILY_STOP_USD:,.0f}")
    print(f"  DRY_RUN:   {config.DRY_RUN}")

    if not config.API_KEY:
        print("\n[ERROR] Keys no configuradas.")
        print("  Setear: BINANCE_API_KEY y BINANCE_API_SECRET (o API_KEY / SECRET_KEY)")
        if not config.DRY_RUN:
            sys.exit(1)

    print("\nInicializando cliente Binance...")
    ex.init_client()
    ex.set_leverage()

    state = st.load()
    print(f"Estado cargado: phase={state['phase']}  capital=${state['capital']:,.2f}")

    if args.once:
        state = iterate(state)
        logger.print_summary()
        return

    print("\nEntrando al loop H1. Ctrl+C para detener.\n")
    while True:
        try:
            state = iterate(state)
        except KeyboardInterrupt:
            print("\nBot detenido.")
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
