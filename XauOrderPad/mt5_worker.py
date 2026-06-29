"""Single-thread owner of the MetaTrader5 connection.

The MetaTrader5 package is blocking and not thread-safe, so EVERY call into it
happens on ONE worker thread. The FastAPI layer talks to this thread through a
command queue and reads an atomically-swapped state snapshot. This guarantees
ticks and orders never race and state can never half-update.
"""

from __future__ import annotations

import datetime
import queue
import threading
import time
from concurrent.futures import Future
from typing import Any

import MetaTrader5 as mt5

import config


# MetaTrader5 symbol filling-mode flags (bitmask on symbol_info.filling_mode)
_SYMBOL_FILLING_FOK = 1
_SYMBOL_FILLING_IOC = 2


def _pick_filling(symbol_info) -> int:
    """Choose an order filling mode the symbol actually supports."""
    fm = getattr(symbol_info, "filling_mode", 0) or 0
    if fm & _SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    if fm & _SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


class Mt5Worker:
    def __init__(self) -> None:
        self._cmd_q: "queue.Queue[tuple[dict, Future]]" = queue.Queue()
        self._state: dict[str, Any] = {"healthy": False, "connected": False,
                                       "error": "starting"}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._symbol = config.SYMBOL
        self._initialized = False
        self._poll_count = 0
        self._stats = {"daily_realized": 0.0, "wins": 0, "losses": 0}
        self._thread = threading.Thread(target=self._run, name="mt5-worker",
                                        daemon=True)

    # ---- public API (called from any thread) ----------------------------
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._thread.join(timeout=3)
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def submit(self, cmd: dict) -> Future:
        """Queue a command; returns a concurrent.futures.Future with the result."""
        fut: Future = Future()
        self._cmd_q.put((cmd, fut))
        return fut

    # ---- worker thread internals ----------------------------------------
    def _run(self) -> None:
        period = 1.0 / max(1, config.POLL_HZ)
        while not self._stop.is_set():
            deadline = time.monotonic() + period
            # Drain and execute any pending commands first (instant order path).
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    cmd, fut = self._cmd_q.get(timeout=remaining)
                except queue.Empty:
                    break
                if fut.set_running_or_notify_cancel():
                    try:
                        fut.set_result(self._handle(cmd))
                    except Exception as exc:  # never kill the worker
                        fut.set_exception(exc)
            self._poll_state()

    def _ensure_connected(self) -> bool:
        if self._initialized:
            ti = mt5.terminal_info()
            if ti is not None and ti.connected:
                return True
            # lost connection -> drop and re-init below
            self._initialized = False
        kwargs: dict[str, Any] = {}
        if config.MT5_PATH:
            kwargs["path"] = config.MT5_PATH
        if config.MT5_LOGIN:
            kwargs.update(login=int(config.MT5_LOGIN),
                          password=config.MT5_PASSWORD, server=config.MT5_SERVER)
        ok = mt5.initialize(**kwargs)
        if not ok:
            return False
        self._initialized = True
        self._resolve_symbol()
        return True

    def _resolve_symbol(self) -> None:
        si = mt5.symbol_info(config.SYMBOL)
        if si is None and config.AUTO_RESOLVE_SYMBOL:
            for s in (mt5.symbols_get(config.SYMBOL_BASE + "*") or []):
                self._symbol = s.name
                break
        else:
            self._symbol = config.SYMBOL
        # make sure it's in Market Watch so ticks flow
        mt5.symbol_select(self._symbol, True)

    def _poll_state(self) -> None:
        st: dict[str, Any] = {"ts": time.time(), "symbol": self._symbol}
        try:
            if not self._ensure_connected():
                code, msg = mt5.last_error()
                st.update(connected=False, healthy=False,
                          error=f"terminal not connected ({code}: {msg})")
                self._swap(st)
                return

            ti = mt5.terminal_info()
            si = mt5.symbol_info(self._symbol)
            tick = mt5.symbol_info_tick(self._symbol)
            acc = mt5.account_info()

            symbol_ok = si is not None and tick is not None
            trade_allowed = bool(ti and ti.trade_allowed) and \
                bool(si and si.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL)

            st["connected"] = bool(ti and ti.connected)
            st["trade_allowed"] = trade_allowed
            st["symbol_ok"] = symbol_ok
            if si is not None:
                st["digits"] = si.digits
                st["point"] = si.point
                st["volume_min"] = si.volume_min
                st["volume_step"] = si.volume_step
            if tick is not None:
                st["bid"] = tick.bid
                st["ask"] = tick.ask
                st["spread"] = round((tick.ask - tick.bid), 6)
            # account stats (daily realized / wins / losses) are heavy -> ~1 Hz
            self._poll_count += 1
            if self._poll_count % max(1, config.POLL_HZ) == 1:
                self._stats = self._compute_stats()
            if acc is not None:
                st["account"] = {"balance": acc.balance, "equity": acc.equity,
                                 "currency": acc.currency, "login": acc.login,
                                 "server": acc.server,
                                 "trade_mode": int(acc.trade_mode),       # 0=demo 1=contest 2=real
                                 "is_demo": int(acc.trade_mode) != 2,
                                 "margin_mode": int(acc.margin_mode),     # 0=netting 2=hedging
                                 **self._stats}

            # `magic` is exposed so the Auto-Test sync verifier can distinguish
            # positions opened by THIS APP (magic == config.MAGIC) from positions
            # opened elsewhere (e.g. user clicking BUY in the MT5 desktop terminal,
            # or another EA). Without this field, the verifier cannot detect
            # foreign interference during an auto-test run.
            positions = mt5.positions_get(symbol=self._symbol) or []
            pos_list = []
            net = 0.0
            pl = 0.0
            for p in positions:
                vol = p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume
                net += vol
                pl += p.profit
                pos_list.append({
                    "ticket": p.ticket,
                    "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "volume": p.volume, "price_open": p.price_open,
                    "sl": p.sl, "tp": p.tp, "profit": p.profit,
                    "time": p.time,
                    "magic": int(p.magic),          # for Auto-Test foreign-magic detection
                })
            st["positions"] = pos_list

            orders = mt5.orders_get(symbol=self._symbol) or []
            buy_types = (mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_BUY_LIMIT,
                         mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_BUY_STOP_LIMIT)
            ord_list = []
            for o in orders:
                ord_list.append({
                    "ticket": o.ticket,
                    "side": "BUY" if o.type in buy_types else "SELL",
                    "volume": o.volume_current, "price_open": o.price_open,
                    "sl": o.sl, "tp": o.tp, "type": "limit", "time": o.time_setup,
                })
            st["orders"] = ord_list

            st["net_lots"] = round(net, 4)
            st["floating_pl"] = round(pl, 2)
            st["healthy"] = bool(st["connected"] and trade_allowed and symbol_ok)
            # Specific, actionable reason when not healthy (shown in the UI banner).
            if st["healthy"]:
                st["error"] = None
            elif not st["connected"]:
                st["error"] = "terminal not connected to broker"
            elif not symbol_ok:
                st["error"] = f"{self._symbol} not available in Market Watch"
            elif ti is not None and not ti.trade_allowed:
                st["error"] = "AutoTrading is OFF in MT5 — press Ctrl+E to enable"
            elif si is not None and si.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
                st["error"] = "market closed / symbol not tradable right now"
            else:
                st["error"] = "trading not allowed"
        except Exception as exc:
            st.update(connected=False, healthy=False, error=f"poll error: {exc}")
        self._swap(st)

    def _swap(self, st: dict) -> None:
        with self._lock:
            self._state = st

    # ---- command handlers (run on worker thread) ------------------------
    def _handle(self, cmd: dict) -> dict:
        action = cmd.get("action")
        if action in ("buy", "sell"):
            return self._market_order(cmd)
        if action == "order":
            return self._place_order(cmd)
        if action == "close":
            return self._close_ticket(cmd.get("ticket"), cmd.get("volume"))
        if action == "close_all":
            return self._close_all()
        return {"ok": False, "error": f"unknown action {action!r}"}

    def _place_order(self, cmd: dict) -> dict:
        """Unified entry for the new UI: market or pending(limit)."""
        typ = (cmd.get("type") or "market").lower()
        side = (cmd.get("side") or "").lower()
        if side not in ("buy", "sell"):
            return {"ok": False, "error": f"bad side {side!r}"}
        if typ == "limit":
            return self._pending_order(cmd, side)
        return self._market_order({
            "action": side, "volume": cmd.get("volume"),
            "sl": cmd.get("sl"), "tp": cmd.get("tp"), "sl_tp_mode": "points",
        })

    def _pending_order(self, cmd: dict, side: str) -> dict:
        if not self._ensure_connected():
            return {"ok": False, "error": "terminal not connected"}
        si = mt5.symbol_info(self._symbol)
        tick = mt5.symbol_info_tick(self._symbol)
        if si is None or tick is None:
            return {"ok": False, "error": f"symbol {self._symbol} unavailable"}
        price = float(cmd.get("price") or 0)
        if price <= 0:
            return {"ok": False, "error": "limit price required"}

        is_buy = side == "buy"
        otype = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
        volume = float(cmd.get("volume") or config.DEFAULT_VOLUME)
        # SL/TP points are measured from the pending (limit) price, not market.
        sl_price, tp_price = self._sl_tp_prices(
            {"sl": cmd.get("sl"), "tp": cmd.get("tp"), "sl_tp_mode": "points"},
            si, is_buy, round(price, si.digits))

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self._symbol,
            "volume": volume,
            "type": otype,
            "price": round(price, si.digits),
            "magic": int(config.MAGIC),
            "comment": "XauOrderPad limit",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        if sl_price:
            request["sl"] = sl_price
        if tp_price:
            request["tp"] = tp_price

        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            return {"ok": False, "error": f"order_send failed ({code}: {msg})"}
        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        return {
            "ok": ok, "retcode": res.retcode, "comment": res.comment,
            "ticket": getattr(res, "order", 0), "price": round(price, si.digits),
            "volume": volume, "side": side.upper(), "state": "pending",
            "sl": sl_price, "tp": tp_price,
        }

    def _close_ticket(self, ticket, volume=None) -> dict:
        if not self._ensure_connected():
            return {"ok": False, "error": "terminal not connected"}
        if ticket is None:
            return {"ok": False, "error": "ticket required"}
        ticket = int(ticket)

        poss = mt5.positions_get(ticket=ticket)
        if poss:
            p = poss[0]
            if volume and 0 < float(volume) < p.volume:
                return self._close_partial(p, float(volume))
            return self._close_one(p)

        # not a position -> maybe a pending order to cancel
        orders = mt5.orders_get(ticket=ticket)
        if orders:
            res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
            if res is None:
                code, msg = mt5.last_error()
                return {"ok": False, "ticket": ticket, "error": f"{code}: {msg}"}
            return {"ok": res.retcode == mt5.TRADE_RETCODE_DONE, "ticket": ticket,
                    "retcode": res.retcode, "comment": res.comment, "cancelled": True}
        return {"ok": False, "ticket": ticket, "error": "ticket not found"}

    def _close_partial(self, p, volume: float) -> dict:
        si = mt5.symbol_info(self._symbol)
        tick = mt5.symbol_info_tick(self._symbol)
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        otype = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self._symbol,
            "volume": round(volume, 2),
            "type": otype,
            "position": p.ticket,
            "price": price,
            "deviation": int(config.DEFAULT_DEVIATION),
            "magic": int(config.MAGIC),
            "comment": "XauOrderPad partial",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(si),
        }
        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            return {"ok": False, "ticket": p.ticket, "error": f"{code}: {msg}"}
        return {"ok": res.retcode == mt5.TRADE_RETCODE_DONE, "ticket": p.ticket,
                "retcode": res.retcode, "comment": res.comment, "volume": volume}

    def _compute_stats(self) -> dict:
        """Today's realized P/L + win/loss counts from closing deals."""
        try:
            now = datetime.datetime.now()
            start = datetime.datetime(now.year, now.month, now.day)
            deals = mt5.history_deals_get(start, now) or []
            out_entries = (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT,
                           mt5.DEAL_ENTRY_OUT_BY)
            realized = 0.0
            wins = losses = 0
            for d in deals:
                if d.entry not in out_entries:
                    continue
                realized += d.profit + d.swap + d.commission
                if d.profit > 0:
                    wins += 1
                elif d.profit < 0:
                    losses += 1
            return {"daily_realized": round(realized, 2), "wins": wins,
                    "losses": losses}
        except Exception:
            return {"daily_realized": 0.0, "wins": 0, "losses": 0}

    def _market_order(self, cmd: dict) -> dict:
        if not self._ensure_connected():
            return {"ok": False, "error": "terminal not connected"}
        si = mt5.symbol_info(self._symbol)
        tick = mt5.symbol_info_tick(self._symbol)
        if si is None or tick is None:
            return {"ok": False, "error": f"symbol {self._symbol} unavailable"}

        is_buy = cmd["action"] == "buy"
        volume = float(cmd.get("volume") or config.DEFAULT_VOLUME)
        price = tick.ask if is_buy else tick.bid
        otype = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

        sl_price, tp_price = self._sl_tp_prices(cmd, si, is_buy, price)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self._symbol,
            "volume": volume,
            "type": otype,
            "price": price,
            "deviation": int(config.DEFAULT_DEVIATION),
            "magic": int(config.MAGIC),
            "comment": "XauOrderPad",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(si),
        }
        if sl_price:
            request["sl"] = sl_price
        if tp_price:
            request["tp"] = tp_price

        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            return {"ok": False, "error": f"order_send failed ({code}: {msg})"}
        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        return {
            "ok": ok, "retcode": res.retcode, "comment": res.comment,
            "ticket": getattr(res, "order", 0), "deal": getattr(res, "deal", 0),
            "price": getattr(res, "price", price), "volume": volume,
            "side": "BUY" if is_buy else "SELL",
            "sl": sl_price, "tp": tp_price,
        }

    def _sl_tp_prices(self, cmd, si, is_buy, ref_price):
        mode = cmd.get("sl_tp_mode") or config.SL_TP_MODE
        sl = cmd.get("sl")
        tp = cmd.get("tp")
        digits = si.digits
        point = si.point

        def to_price(val, is_sl):
            if val in (None, "", 0, 0.0):
                return 0.0
            val = float(val)
            if mode == "price":
                return round(val, digits)
            # points mode: distance from reference price
            dist = val * point
            if is_buy:
                p = ref_price - dist if is_sl else ref_price + dist
            else:
                p = ref_price + dist if is_sl else ref_price - dist
            return round(p, digits)

        return to_price(sl, True), to_price(tp, False)

    def _close_all(self) -> dict:
        if not self._ensure_connected():
            return {"ok": False, "error": "terminal not connected"}
        results = []
        for _ in range(5):  # retry loop until flat
            positions = mt5.positions_get(symbol=self._symbol) or []
            if config.RESTRICT_CLOSE_TO_MAGIC:
                positions = [p for p in positions if p.magic == config.MAGIC]
            if not positions:
                break
            for p in positions:
                results.append(self._close_one(p))
            time.sleep(0.05)
        remaining = mt5.positions_get(symbol=self._symbol) or []
        if config.RESTRICT_CLOSE_TO_MAGIC:
            remaining = [p for p in remaining if p.magic == config.MAGIC]
        return {
            "ok": len(remaining) == 0,
            "closed": len([r for r in results if r.get("ok")]),
            "remaining": len(remaining),
            "results": results,
        }

    def _close_one(self, p) -> dict:
        si = mt5.symbol_info(self._symbol)
        tick = mt5.symbol_info_tick(self._symbol)
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        # close a BUY with a SELL at bid, a SELL with a BUY at ask
        otype = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self._symbol,
            "volume": p.volume,
            "type": otype,
            "position": p.ticket,
            "price": price,
            "deviation": int(config.DEFAULT_DEVIATION),
            "magic": int(config.MAGIC),
            "comment": "XauOrderPad close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(si),
        }
        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            return {"ok": False, "ticket": p.ticket,
                    "error": f"{code}: {msg}"}
        return {"ok": res.retcode == mt5.TRADE_RETCODE_DONE,
                "ticket": p.ticket, "retcode": res.retcode,
                "comment": res.comment}
