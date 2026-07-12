"""Single-thread owner of the MetaTrader5 connection.

The MetaTrader5 package is blocking and not thread-safe, so EVERY call into it
happens on ONE worker thread. The FastAPI layer talks to this thread through a
command queue and reads an atomically-swapped state snapshot. This guarantees
ticks and orders never race and state can never half-update.
"""

from __future__ import annotations

import datetime
import logging
import queue
import threading
import time
from concurrent.futures import Future
from typing import Any

import MetaTrader5 as mt5

import config
from strategy import VolumeSpikeStraddle


# Logger for every worker-side event. Configured by logger_setup.setup_logging()
# at server boot; if setup wasn't called (e.g. running the worker in isolation
# during tests), Python's default handler safely swallows the calls.
log = logging.getLogger("XauOrderPad.worker")


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
        # When False the user has logged out from the UI: stop attaching to the
        # terminal and report "logged out" until an explicit login command.
        # Start False: on a headless/cloud box the terminal boots with no account
        # logged in, so auto-attaching would call mt5.initialize() with no creds ->
        # a ~65s IPC-timeout that blocks the GIL and freezes uvicorn, so the UI
        # can never load to log in. An explicit /api/login flips this True.
        self._session_active = False
        self._poll_count = 0
        self._stats = {"daily_realized": 0.0, "wins": 0, "losses": 0}
        # The one automated, demo-only strategy. Disabled until enabled from the
        # UI; evaluated on THIS worker thread so it never races ticks/orders.
        self.strategy = VolumeSpikeStraddle()
        # Health-transition tracking: log only when these flip, not on every poll.
        self._last_healthy: bool | None = None
        # Account snapshot throttle: log a snapshot at most once per N polls.
        self._snapshot_every_n_polls = max(1, config.POLL_HZ) * 60  # ~once/minute
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
        """Make sure the MT5 terminal is initialised and connected.

        Emits one `mt5_disconnected` event the first time we notice a drop,
        and one `mt5_connected` event the first time we successfully attach.
        Suppresses spam: only logs on state transitions, not on every poll.
        """
        # Logged out from the UI: do not auto-attach. A `login` command clears
        # this flag. (The terminal itself stays running and logged in; we just
        # refuse to drive it until the user logs in again.)
        if not self._session_active:
            return False
        if self._initialized:
            ti = mt5.terminal_info()
            if ti is not None and ti.connected:
                return True
            # lost connection -> drop and re-init below
            self._initialized = False
            log.warning(
                "MT5 terminal connection lost",
                extra={"event": "mt5_disconnected"},
            )
        kwargs: dict[str, Any] = {}
        if config.MT5_PATH:
            kwargs["path"] = config.MT5_PATH
        if config.MT5_LOGIN:
            kwargs.update(login=int(config.MT5_LOGIN),
                          password=config.MT5_PASSWORD, server=config.MT5_SERVER)
        ok = mt5.initialize(**kwargs)
        if not ok:
            code, msg = mt5.last_error()
            log.warning(
                "MT5 initialize failed",
                extra={"event": "mt5_init_failed",
                       "code": code, "mt5_last_error_msg": msg},
            )
            return False
        self._initialized = True
        self._resolve_symbol()
        # Log the now-current account identity so we can attribute later events.
        acc = mt5.account_info()
        ti = mt5.terminal_info()
        log.info(
            "MT5 terminal connected",
            extra={
                "event": "mt5_connected",
                "login": getattr(acc, "login", None),
                "server": getattr(acc, "server", None),
                "trade_mode": getattr(acc, "trade_mode", None),
                "path": getattr(ti, "path", None) if ti else None,
                "symbol": self._symbol,
            },
        )
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
                if not self._session_active:
                    st.update(connected=False, healthy=False,
                              logged_out=True, error="logged out")
                else:
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
                st["volume"] = int(getattr(tick, "volume", 0) or 0)   # tick volume for live chart
                st["tick_time"] = int(getattr(tick, "time", 0) or 0)  # broker tick timestamp (s)
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
            log.exception("poll_state crashed",
                          extra={"event": "poll_state_exception"})
        # Expose strategy status in the pushed snapshot so the UI can render it.
        st["strategy"] = self.strategy.status()
        self._swap(st)

        # Drive the automated strategy AFTER the swap so any orders it places show
        # up on the next poll. Fully sandboxed: a crash here never kills the worker
        # and never affects manual trading (strategy is a no-op unless enabled).
        try:
            self.strategy.evaluate(self, st)
        except Exception:
            log.exception("strategy evaluate crashed",
                          extra={"event": "strategy_exception"})

        # --- Structured event emission (post-swap, deliberately throttled) ---
        # We poll 15 times/sec; we do NOT want one log line every 67ms. Instead:
        #   1) Health-transition events only fire when the bit flips.
        #   2) Account snapshots fire once per ~minute (POLL_HZ × 60 polls).
        new_healthy = bool(st.get("healthy"))
        if self._last_healthy is None or new_healthy != self._last_healthy:
            log.log(
                logging.INFO if new_healthy else logging.WARNING,
                "health changed",
                extra={"event": "health_changed",
                       "healthy": new_healthy,
                       "connected": bool(st.get("connected")),
                       "trade_allowed": bool(st.get("trade_allowed")),
                       "symbol_ok": bool(st.get("symbol_ok")),
                       "error": st.get("error")},
            )
            self._last_healthy = new_healthy
        if (st.get("account") is not None
                and self._poll_count % self._snapshot_every_n_polls == 0):
            acc = st["account"]
            log.info("account snapshot",
                     extra={"event": "account_snapshot",
                            "login": acc.get("login"),
                            "server": acc.get("server"),
                            "balance": acc.get("balance"),
                            "equity": acc.get("equity"),
                            "currency": acc.get("currency"),
                            "daily_realized": acc.get("daily_realized"),
                            "wins": acc.get("wins"),
                            "losses": acc.get("losses"),
                            "trade_mode": acc.get("trade_mode"),
                            "is_demo": acc.get("is_demo"),
                            "open_positions": len(st.get("positions") or []),
                            "net_lots": st.get("net_lots"),
                            "floating_pl": st.get("floating_pl")})

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
        if action == "close_where":
            return self._close_where(cmd.get("filter") or "all")
        if action == "login":
            return self._login(cmd)
        if action == "logout":
            return self._logout(cmd)
        if action == "strategy":
            return self.strategy.update(cmd.get("params"), cmd.get("enabled"))
        return {"ok": False, "error": f"unknown action {action!r}"}

    # ---- strategy support (worker-thread only; called from strategy.evaluate) ----
    def recent_m1(self, count: int):
        """Last `count` M1 bars for the active symbol (structured np array)."""
        return mt5.copy_rates_from_pos(self._symbol, mt5.TIMEFRAME_M1, 0, int(count))

    def strategy_positions(self) -> list:
        """Open positions belonging to the strategy (STRATEGY_MAGIC only)."""
        poss = mt5.positions_get(symbol=self._symbol) or []
        return [p for p in poss if int(p.magic) == int(config.STRATEGY_MAGIC)]

    def strategy_place(self, side: str, volume: float,
                       sl_dist: float, tp_dist: float) -> dict:
        """Market order for one straddle leg with absolute SL/TP derived from the
        fill side price. Tagged STRATEGY_MAGIC so it is never touched by manual
        close-all and is independently attributable in the log."""
        si = mt5.symbol_info(self._symbol)
        tick = mt5.symbol_info_tick(self._symbol)
        if si is None or tick is None:
            return {"ok": False, "error": "symbol unavailable"}
        is_buy = side == "buy"
        price = tick.ask if is_buy else tick.bid
        sl = (price - sl_dist) if is_buy else (price + sl_dist)
        tp = (price + tp_dist) if is_buy else (price - tp_dist)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self._symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": round(sl, si.digits),
            "tp": round(tp, si.digits),
            "deviation": int(config.DEFAULT_DEVIATION),
            "magic": int(config.STRATEGY_MAGIC),
            "comment": "XauStraddle",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(si),
        }
        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            return {"ok": False, "error": f"{code}: {msg}"}
        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        return {"ok": ok, "ticket": getattr(res, "order", 0),
                "price": getattr(res, "price", price), "retcode": res.retcode,
                "error": None if ok else f"retcode {res.retcode}: {res.comment}"}

    def strategy_close_ticket(self, ticket: int) -> dict:
        """Close one strategy position by ticket (closing deal keeps STRATEGY_MAGIC)."""
        poss = mt5.positions_get(ticket=int(ticket))
        if not poss:
            return {"ok": False, "error": "ticket not found"}
        p = poss[0]
        si = mt5.symbol_info(self._symbol)
        tick = mt5.symbol_info_tick(self._symbol)
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self._symbol,
            "volume": p.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": p.ticket,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": int(config.DEFAULT_DEVIATION),
            "magic": int(config.STRATEGY_MAGIC),
            "comment": "XauStraddle close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(si),
        }
        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            return {"ok": False, "ticket": int(ticket), "error": f"{code}: {msg}"}
        return {"ok": res.retcode == mt5.TRADE_RETCODE_DONE, "ticket": int(ticket)}

    def strategy_daily_realized(self) -> float:
        """Today's realized P/L (profit+swap+commission) for STRATEGY_MAGIC deals."""
        now = datetime.datetime.now()
        start = datetime.datetime(now.year, now.month, now.day)
        deals = mt5.history_deals_get(start, now) or []
        out = (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT, mt5.DEAL_ENTRY_OUT_BY)
        total = 0.0
        for d in deals:
            if int(getattr(d, "magic", 0)) == int(config.STRATEGY_MAGIC) and d.entry in out:
                total += d.profit + d.swap + d.commission
        return round(total, 2)

    # ---- account session (login / logout / switch) ----------------------
    def _open_position_count(self) -> int:
        """Open positions on the CURRENTLY logged-in account (all symbols),
        captured before a switch/logout so the UI can warn what gets left
        behind. Best-effort: returns 0 if we can't read them."""
        try:
            if self._initialized and self._session_active:
                return len(mt5.positions_get() or [])
        except Exception:
            pass
        return 0

    def _log_login_failed(self, stage: str, login, server, code, msg) -> None:
        """Record WHY a login was refused. Without this the only trace of a failed
        switch is a bare `400` in the access log, which is indistinguishable from a
        dead button. The password is deliberately absent from every field here."""
        log.warning("account login failed",
                    extra={"event": "account_login_failed", "stage": stage,
                           "login": login, "server": server,
                           "code": code, "mt5_last_error_msg": msg})

    def _login(self, cmd: dict) -> dict:
        """Log the terminal into the given account (switch if already attached).

        Runs on the worker thread, so it is serialized with ticks and orders.
        `path` falls back to config.MT5_PATH; password is used but never logged.
        """
        login = cmd.get("login")
        password = cmd.get("password")
        server = cmd.get("server")
        path = cmd.get("path") or config.MT5_PATH or None
        if not (login and password and server):
            return {"ok": False, "error": "login, password and server are required"}

        prev_open = self._open_position_count()    # outgoing account's open trades

        if not self._initialized:
            # Terminal not attached yet -> initialize WITH credentials (launches
            # terminal64.exe if needed).
            kwargs: dict[str, Any] = {}
            if path:
                kwargs["path"] = path
            kwargs.update(login=int(login), password=password, server=server)
            if not mt5.initialize(**kwargs):
                code, msg = mt5.last_error()
                self._log_login_failed("initialize", login, server, code, msg)
                return {"ok": False, "prev_open": prev_open,
                        "error": f"initialize/login failed ({code}: {msg})"}
            self._initialized = True
        else:
            # Terminal already attached -> switch account in place.
            if not mt5.login(int(login), password=password, server=server):
                code, msg = mt5.last_error()
                self._log_login_failed("switch", login, server, code, msg)
                return {"ok": False, "prev_open": prev_open,
                        "error": f"login failed ({code}: {msg})"}

        self._session_active = True
        self._resolve_symbol()
        # daily realized / wins / losses belong to the account -> reset on switch.
        self._stats = {"daily_realized": 0.0, "wins": 0, "losses": 0}
        self._poll_count = 0
        self._last_healthy = None

        acc = mt5.account_info()
        tmode = int(acc.trade_mode) if acc is not None else None
        log.info("account login",
                 extra={"event": "account_login",
                        "login": getattr(acc, "login", None),
                        "server": getattr(acc, "server", None),
                        "trade_mode": tmode,
                        "prev_open": prev_open})
        return {
            "ok": True,
            "login": getattr(acc, "login", None),
            "server": getattr(acc, "server", None),
            "trade_mode": tmode,
            "is_demo": (tmode != 2) if tmode is not None else None,
            "prev_open": prev_open,
        }

    def _logout(self, cmd: dict) -> dict:
        """Drop the Python<->terminal link and stop auto-attaching until the
        next login. (MT5 has no real account-logout; the terminal keeps its
        session — we simply refuse to drive it.)"""
        prev_open = self._open_position_count()
        try:
            mt5.shutdown()
        except Exception:
            pass
        self._initialized = False
        self._session_active = False
        self._stats = {"daily_realized": 0.0, "wins": 0, "losses": 0}
        self._last_healthy = None
        log.info("account logout",
                 extra={"event": "account_logout", "prev_open": prev_open})
        return {"ok": True, "prev_open": prev_open}

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
        """Send a market order and return a structured result.

        The result dict ALWAYS contains:
            ok               -- bool
            requested_price  -- the bid/ask we computed at the moment of send
            fill_price       -- the broker's actual fill price (None on failure)
            slippage         -- abs(fill - requested) when both are present
            side             -- "BUY" | "SELL"
            volume           -- lot size sent
            retcode/comment  -- MT5 broker response (when available)
            sl/tp            -- absolute prices we asked for (0 if not set)

        Logs `order_request` before send, then either `order_filled` (with
        slippage) or `order_failed` (with retcode). The `auto_test` flag from
        the inbound command is propagated to every log line so post-hoc
        analysis can filter "auto-test orders only" with one pandas predicate.
        """
        auto_test = bool(cmd.get("auto_test"))
        if not self._ensure_connected():
            log.error("order rejected: terminal not connected",
                      extra={"event": "order_failed",
                             "reason": "terminal_not_connected",
                             "auto_test": auto_test})
            return {"ok": False, "error": "terminal not connected"}
        si = mt5.symbol_info(self._symbol)
        tick = mt5.symbol_info_tick(self._symbol)
        if si is None or tick is None:
            log.error("order rejected: symbol unavailable",
                      extra={"event": "order_failed",
                             "reason": "symbol_unavailable",
                             "symbol": self._symbol,
                             "auto_test": auto_test})
            return {"ok": False, "error": f"symbol {self._symbol} unavailable"}

        is_buy = cmd["action"] == "buy"
        volume = float(cmd.get("volume") or config.DEFAULT_VOLUME)
        requested_price = tick.ask if is_buy else tick.bid
        otype = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

        sl_price, tp_price = self._sl_tp_prices(cmd, si, is_buy, requested_price)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self._symbol,
            "volume": volume,
            "type": otype,
            "price": requested_price,
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

        log.info("order request",
                 extra={"event": "order_request",
                        "symbol": self._symbol,
                        "side": "BUY" if is_buy else "SELL",
                        "volume": volume,
                        "requested_price": requested_price,
                        "sl": sl_price, "tp": tp_price,
                        "magic": int(config.MAGIC),
                        "auto_test": auto_test})

        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            log.error("order_send returned None",
                      extra={"event": "order_failed",
                             "reason": "order_send_returned_none",
                             "mt5_error_code": code,
                             "mt5_last_error_msg": msg,
                             "symbol": self._symbol,
                             "side": "BUY" if is_buy else "SELL",
                             "volume": volume,
                             "requested_price": requested_price,
                             "auto_test": auto_test})
            return {"ok": False, "error": f"order_send failed ({code}: {msg})",
                    "requested_price": requested_price,
                    "fill_price": None, "slippage": None,
                    "side": "BUY" if is_buy else "SELL",
                    "volume": volume}

        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        # Slippage = |actual fill price − requested price|. Only meaningful
        # when the broker actually filled at a price (ok == True).
        fill_price = getattr(res, "price", None) if ok else None
        slippage = (abs(fill_price - requested_price)
                    if fill_price is not None and requested_price is not None
                    else None)
        result = {
            "ok": ok, "retcode": res.retcode, "comment": res.comment,
            "ticket": getattr(res, "order", 0), "deal": getattr(res, "deal", 0),
            "price": fill_price if fill_price is not None else requested_price,
            "requested_price": requested_price,
            "fill_price": fill_price,
            "slippage": slippage,
            "volume": volume,
            "side": "BUY" if is_buy else "SELL",
            "sl": sl_price, "tp": tp_price,
        }
        if ok:
            log.info("order filled",
                     extra={"event": "order_filled",
                            "ticket": result["ticket"],
                            "deal": result["deal"],
                            "symbol": self._symbol,
                            "side": result["side"],
                            "volume": volume,
                            "requested_price": requested_price,
                            "fill_price": fill_price,
                            "slippage": slippage,
                            "retcode": res.retcode,
                            "comment": res.comment,
                            "magic": int(config.MAGIC),
                            "auto_test": auto_test})
        else:
            log.warning("order failed",
                        extra={"event": "order_failed",
                               "reason": "retcode_not_done",
                               "symbol": self._symbol,
                               "side": result["side"],
                               "volume": volume,
                               "requested_price": requested_price,
                               "retcode": res.retcode,
                               "comment": res.comment,
                               "auto_test": auto_test})
        return result

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
        """Flatten every open position for the active symbol. Kept as a thin
        alias so the existing /close_all endpoint (and the web UI's Esc hotkey /
        Auto-Test stop path) behave exactly as before."""
        return self._close_where("all")

    def _select(self, filt: str) -> list:
        """Open positions on the active symbol matching `filt`.

        The profit sign is read from LIVE broker state every time this is called
        -- never from a client-supplied snapshot. A position's sign can flip
        between the browser/phone seeing it and the close landing, so filtering
        here (rather than having the client send a ticket list) is what makes
        close-losing / close-profit correct rather than merely usually-right.
        """
        positions = mt5.positions_get(symbol=self._symbol) or []
        if config.RESTRICT_CLOSE_TO_MAGIC:
            positions = [p for p in positions if p.magic == config.MAGIC]
        if filt == "losing":
            return [p for p in positions if p.profit < 0]
        if filt == "profit":
            return [p for p in positions if p.profit > 0]
        return list(positions)          # "all"

    def _close_where(self, filt: str) -> dict:
        """Close every open position on the active symbol matching `filt`
        ("all" | "losing" | "profit"), optionally restricted to this app's magic.

        Up to 5 retry passes; the filter is re-evaluated against fresh broker
        state on each pass. Logs both the request and the final result so the
        broker round-trip is reconstructable from disk alone.

        Note `remaining` counts only positions still matching the filter -- for
        "losing" a leftover *winner* is not a failure, so `ok` must not consider
        it one. A position that crosses zero mid-flight simply stops matching and
        is left alone, which is the correct behaviour.
        """
        if filt not in ("all", "losing", "profit"):
            return {"ok": False, "error": f"unknown filter {filt!r}"}
        if not self._ensure_connected():
            return {"ok": False, "error": "terminal not connected"}
        log.info("close-where requested",
                 extra={"event": "close_where_request",
                        "symbol": self._symbol,
                        "filter": filt,
                        "restrict_to_magic": bool(config.RESTRICT_CLOSE_TO_MAGIC)})
        results = []
        for _ in range(5):  # retry loop until no position matches
            positions = self._select(filt)
            if not positions:
                break
            for p in positions:
                results.append(self._close_one(p))
            time.sleep(0.05)
        remaining = self._select(filt)
        closed_count = len([r for r in results if r.get("ok")])
        log.info("close-where completed",
                 extra={"event": "close_where_completed",
                        "symbol": self._symbol,
                        "filter": filt,
                        "closed_count": closed_count,
                        "remaining_count": len(remaining),
                        "attempts": len(results),
                        "flat": len(remaining) == 0})
        return {
            "ok": len(remaining) == 0,
            "filter": filt,
            "closed": closed_count,
            "remaining": len(remaining),
            "results": results,
        }

    def _close_one(self, p) -> dict:
        """Close a single open position. Logs `position_closed` (or `position_close_failed`)
        with profit / swap / commission breakdown so realised P&L is reconstructable
        from the log alone."""
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
            log.warning("close failed (order_send None)",
                        extra={"event": "position_close_failed",
                               "ticket": p.ticket,
                               "mt5_error_code": code,
                               "mt5_last_error_msg": msg})
            return {"ok": False, "ticket": p.ticket,
                    "error": f"{code}: {msg}"}
        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        log.info("position closed" if ok else "close retcode not DONE",
                 extra={"event": "position_closed" if ok
                                  else "position_close_failed",
                        "ticket": p.ticket,
                        "side_opened": "BUY" if is_buy else "SELL",
                        "volume": p.volume,
                        "entry_price": p.price_open,
                        "close_price": price,
                        "profit": getattr(p, "profit", None),  # floating at moment of close
                        "swap": getattr(p, "swap", None),
                        "commission": getattr(p, "commission", None),
                        "retcode": res.retcode,
                        "comment": res.comment,
                        "magic": getattr(p, "magic", None)})
        return {"ok": ok, "ticket": p.ticket,
                "retcode": res.retcode, "comment": res.comment}
