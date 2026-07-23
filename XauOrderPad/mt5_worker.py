"""Single-thread owner of the MetaTrader5 connection.

The MetaTrader5 package is blocking and not thread-safe, so EVERY call into it
happens on ONE worker thread. The FastAPI layer talks to this thread through a
command queue and reads an atomically-swapped state snapshot. This guarantees
ticks and orders never race and state can never half-update.
"""

from __future__ import annotations

import datetime
import logging
import os
import queue
import threading
import time
from concurrent.futures import Future
from typing import Any

import MetaTrader5 as mt5

import config
import instance_lock
import instance_paths
import log_context
import ticklog_state
from strategies import ENGINES


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
        # Exclusive claim on the account this server drives (instance_lock.FileLock).
        # None until a successful /api/login. Held for as long as we drive the account;
        # the OS drops it if we die, so there is no stale state to clean up on boot.
        self._account_lock = None
        # Set by _verify_terminal when we land on a terminal that is not ours, so the
        # UI can report a config fault instead of a bogus "broker disconnected".
        self._wrong_terminal = None
        # Set by _verify_account when the terminal is ours but holds the wrong account.
        self._wrong_account = None
        self._poll_count = 0
        # Bar cache for the strategy engines: {key: (wall_clock_bucket, count, array)}.
        # See recent_bars(). Cleared on login/symbol change -- bars from the previous
        # account's symbol must never be handed to an engine on the new one.
        self._bars_cache: dict[str, tuple] = {}
        self._stats = {"daily_realized": 0.0, "wins": 0, "losses": 0}
        # Account-level P&L guard: when enabled, the whole book auto-closes the moment FLOATING P&L
        # reaches the target (profit >= +target, or loss <= -target). It STAYS enabled and re-arms
        # after the book goes flat, so it keeps protecting until the user switches it off. Evaluated
        # on THIS worker thread in _poll_state, so it never races ticks/orders. Reset on account
        # switch (see _login/_logout) -- it must never carry a target from one account to another.
        # `fired` latches a single close per breach so a 15 Hz poll does not re-close every tick.
        self._guard = {"enabled": False, "target_pl": 0.0, "side": "profit", "fired": False}
        # The automated, demo-only strategy engines, keyed by id. All disabled
        # until enabled from a UI, and all evaluated on THIS worker thread, so
        # they never race ticks/orders -- or each other. Each owns a distinct
        # magic (config.STRATEGY_MAGICS), which is what makes them independent:
        # every MT5 helper filters by it, so no engine can see or close another's
        # positions.
        self.strategies = {e.ID: e() for e in ENGINES}
        # Health-transition tracking: log only when these flip, not on every poll.
        self._last_healthy: bool | None = None
        # Account snapshot throttle: log a snapshot at most once per N polls.
        self._snapshot_every_n_polls = max(1, config.POLL_HZ) * 60  # ~once/minute
        # Opt-in tick logging (config.TICKLOG_ENABLED). A CSV file handle opened
        # lazily on first captured tick, a millisecond watermark to dedupe, and the
        # unix-seconds floor we ask copy_ticks_range from. Read-only; never trades.
        self._tick_fh = None
        self._tick_last_msc = 0
        self._tick_from_ts = 0
        # Runtime on/off for tick logging, authoritative over config.TICKLOG_ENABLED.
        # Seeded from the persisted choice (ticklog.json) if the operator ever set one,
        # else from the env default -- so a restart keeps whatever was last chosen.
        self._ticklog = {
            "enabled": bool(ticklog_state.load().get("enabled", config.TICKLOG_ENABLED)),
            "rows": 0,
        }
        # Machine-wide claim on tick capture. Taken by _apply_ticklog on an explicit
        # enable, or by _claim_ticklog_on_boot when the persisted state says "on" --
        # without the latter, two instances that were both left enabled would resume
        # capturing on restart having never passed through the toggle that checks.
        self._ticklog_lock = None
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
            # Release on a CLEAN shutdown so the account frees up immediately rather
            # than at process exit. A dirty exit is already covered -- the OS drops it.
            self._release_account_lock()
            self._release_ticklog_lock()

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def submit(self, cmd: dict) -> Future:
        """Queue a command; returns a concurrent.futures.Future with the result."""
        fut: Future = Future()
        self._cmd_q.put((cmd, fut))
        return fut

    # ---- worker thread internals ----------------------------------------
    def _claim_ticklog_on_boot(self) -> None:
        """Honour a persisted `ticklog: on` only if this instance can claim the slot.

        On the worker thread, before the first poll. If another instance already
        holds it we turn tick logging OFF for this one and say so -- better than two
        servers quietly capturing because both were enabled when they last ran.
        """
        if not self._ticklog["enabled"]:
            return
        try:
            lock = instance_lock.ticklog_lock()
            lock.acquire({"instance": instance_paths.instance_name(),
                          "port": config.PORT, "what": "ticklog"})
            self._ticklog_lock = lock
        except instance_lock.LockHeld as held:
            self._ticklog["enabled"] = False
            log.warning("tick logging disabled at boot: another instance is capturing",
                        extra={"event": "ticklog_boot_refused", "holder": held.holder})
        except instance_lock.LockUnavailable as exc:
            log.error("ticklog lock unavailable at boot -- capturing anyway",
                      extra={"event": "ticklog_lock_unavailable", "error": str(exc)})

    def _run(self) -> None:
        self._claim_ticklog_on_boot()
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
            self._capture_ticks()

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
                # Re-assert the bindings on EVERY poll, not just at connect time. Both are
                # cheap -- `ti` is already in hand -- and they turn "this was right when we
                # attached" into "this is right now", which is the claim the close-all path
                # actually depends on. The account check matters just as much as the
                # terminal one: someone can switch account inside the MT5 GUI at any moment.
                return self._verify_terminal(ti) and self._verify_account()
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
        # INVARIANT: we are attached to OUR terminal, or we are attached to nothing.
        if not self._verify_terminal():
            return False
        if not self._verify_account():
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
        # The terminal is up and we can see the book, so this is the first moment we
        # can find out whether a previous run left positions behind. Do it here rather
        # than at boot: at boot there is no MT5 connection to ask.
        self.reconcile_strategies()
        return True

    def _verify_terminal(self, ti=None) -> bool:
        """Prove we attached to the terminal config.MT5_PATH names. Fail closed.

        Why this exists: `positions_get()` only ever returns the ATTACHED account's
        book, which is what makes cross-account damage impossible -- but only while
        each server is really on its own terminal. If that binding is ever wrong, a
        close-all flattens someone else's account with no error anywhere. So the
        binding is asserted rather than assumed.

        Comparing `terminal_info().path`, established by measurement during bring-up:

          * `.path` is the DIRECTORY holding terminal64.exe -- not the exe -- so the
            configured path is dirname()'d before comparison.
          * `.path` is the right field and `.data_path` is NOT: for a normal install
            they differ (`C:\\Program Files\\MetaTrader 5` vs
            `%APPDATA%\\MetaQuotes\\Terminal\\<hash>`), and only coincide for a
            /portable one. Comparing data_path would reject every non-portable setup.

        No MT5_PATH configured (single-account mode) -> nothing to verify, and there
        is only one terminal anyway.

        `ti` may be passed in by a caller that already fetched it, so running this on
        every poll costs nothing.
        """
        if not config.MT5_PATH:
            self._wrong_terminal = None
            return True
        if ti is None:
            ti = mt5.terminal_info()
        if ti is None:
            return False
        want = os.path.normcase(os.path.normpath(os.path.dirname(config.MT5_PATH)))
        got = os.path.normcase(os.path.normpath(getattr(ti, "path", "") or ""))
        if want == got:
            self._wrong_terminal = None
            return True

        # Attached to the WRONG terminal: drop the connection rather than drive it.
        # Not retried into a trade path -- _poll_state publishes healthy=False and the
        # order endpoints refuse on that.
        log.error("attached to the WRONG terminal -- refusing to drive it",
                  extra={"event": "wrong_terminal_attached",
                         "expected_path": want, "actual_path": got,
                         "actual_login": getattr(mt5.account_info(), "login", None)})
        try:
            mt5.shutdown()
        except Exception:
            pass
        self._initialized = False
        # Latched so _poll_state can say WHY. Without it the UI would read
        # "terminal not connected", sending the operator to hunt a broker outage
        # instead of the configuration error this actually is.
        self._wrong_terminal = {"expected": want, "actual": got}
        return False

    def _verify_account(self) -> bool:
        """Prove the terminal holds the ONE account this instance is pinned to.

        The terminal guard answers "am I driving my own terminal". This answers the
        question that actually matters to a close-all: "is my terminal on the account
        I think it is". They are different failures -- the terminal can be correct
        while a human switches its account in the MT5 GUI, or logs it into the wrong
        one from the phone -- and `positions_get()` reports the new account's book
        without a word of complaint.

        Unpinned (EXPECT_LOGIN == 0) is the single-account default and always passes.

        Not logged in yet is NOT a mismatch: account_info() is None before login, and
        treating that as a fault would make a freshly booted server unhealthy forever.
        """
        if not config.EXPECT_LOGIN:
            self._wrong_account = None
            return True
        acc = mt5.account_info()
        if acc is None:
            return True                      # logged out; nothing to contradict
        actual = int(getattr(acc, "login", 0) or 0)
        if actual == int(config.EXPECT_LOGIN):
            self._wrong_account = None
            return True

        log.error("terminal is on the WRONG ACCOUNT -- refusing to drive it",
                  extra={"event": "wrong_account_attached",
                         "expected_login": int(config.EXPECT_LOGIN),
                         "actual_login": actual,
                         "actual_server": getattr(acc, "server", None)})
        # Deliberately NOT calling mt5.shutdown(): the terminal is ours and correct, only
        # its account is wrong. Keeping the connection lets the next poll notice the moment
        # it is put back, instead of needing a restart. Returning False is what closes the
        # trade path -- every order helper goes through _ensure_connected.
        self._wrong_account = {"expected": int(config.EXPECT_LOGIN), "actual": actual}
        return False

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
                elif self._wrong_account:
                    st.update(connected=False, healthy=False,
                              wrong_account=self._wrong_account,
                              error=(f"terminal is on account "
                                     f"{self._wrong_account['actual']}, but this instance "
                                     f"is pinned to {self._wrong_account['expected']}. "
                                     f"Trading is blocked. Log it back into "
                                     f"{self._wrong_account['expected']}."))
                elif self._wrong_terminal:
                    # A configuration fault, not a broker one. Say which terminal we
                    # got so the fix is obvious from the banner alone.
                    st.update(connected=False, healthy=False,
                              wrong_terminal=self._wrong_terminal,
                              error=("attached to the WRONG MT5 terminal "
                                     f"({self._wrong_terminal['actual'] or 'unknown'}); "
                                     f"this instance expects {self._wrong_terminal['expected']}. "
                                     "Trading is blocked. Check XAUORDERPAD_MT5_PATH."))
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
                # Stamp every subsequent log line with WHO this server is trading as.
                # Done here, off the account_info() this loop already fetched, rather
                # than only at login: if the account is switched in the terminal by
                # hand, the logs follow it within one poll instead of lying until the
                # next /api/login. set_account() no-ops when nothing changed.
                log_context.set_account(acc.login, acc.server, int(acc.trade_mode) != 2)

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
                net += p.volume if p.type == mt5.POSITION_TYPE_BUY else -p.volume
                pl += p.profit
                pos_list.append(self._shape_position(p))
            st["positions"] = pos_list

            orders = mt5.orders_get(symbol=self._symbol) or []
            st["orders"] = [self._shape_order(o) for o in orders]

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
        st["strategies"] = {sid: s.status() for sid, s in self.strategies.items()}
        # Back-compat: the current web panel still reads st["strategy"].
        st["strategy"] = st["strategies"].get("straddle")
        # Mirror the P&L guard so the phone shows what the SERVER is actually enforcing.
        st["guard"] = dict(self._guard)
        # Mirror tick-logging status so the UI toggle reflects what the server is doing.
        st["ticklog"] = {"enabled": self._ticklog["enabled"],
                         "rows": self._ticklog["rows"],
                         "path": config.TICKLOG_PATH}
        self._swap(st)

        # Drive the engines AFTER the swap so any orders they place show up on the
        # next poll. Fully sandboxed: a crash here never kills the worker and never
        # affects manual trading (an engine is a no-op unless enabled).
        #
        # The try/except is PER ENGINE, deliberately. A shared one would let a crash
        # in the first engine silently skip every engine after it -- including, say,
        # the one holding open positions and waiting to close them.
        for sid, s in self.strategies.items():
            try:
                s.evaluate(self, st)
            except Exception:
                log.exception("strategy evaluate crashed",
                              extra={"event": "strategy_exception", "strategy": sid})

        # Account-level P&L guard, AFTER the swap so its close shows next poll. Own try/except: a
        # guard crash must never take down the worker or manual trading.
        try:
            self._check_guard(st)
        except Exception:
            log.exception("pnl guard crashed", extra={"event": "guard_exception"})

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

    def _check_guard(self, st: dict) -> None:
        """Account-level P&L guard: flatten the whole book when FLOATING P&L hits the target.

        Compares `st["floating_pl"]` -- the SAME number the UI shows as FLOATING -- against the
        armed target. Fires at most once per breach (`fired` latch), then re-arms once the book has
        gone flat (P&L retreats back through zero), so it keeps protecting until switched off. Runs
        on the worker thread, so it may call `_close_where` directly.
        """
        g = self._guard
        if not g.get("enabled"):
            return
        pl = st.get("floating_pl")
        if pl is None:
            return
        target = float(g.get("target_pl") or 0.0)
        if target <= 0:
            return
        side = g.get("side", "profit")
        breach = (pl >= target) if side == "profit" else (pl <= -target)

        if breach and not g.get("fired"):
            res = self._close_where("all")
            g["fired"] = True
            log.warning("pnl guard fired",
                        extra={"event": "guard_fired", "side": side, "target": target,
                               "floating_pl": pl, "remaining": res.get("remaining")})
        elif not breach:
            # Hysteresis: re-arm only once P&L has crossed back through zero (book flat/reversed),
            # not the instant it dips under the target -- otherwise it could chatter around the line.
            retreated = (pl <= 0) if side == "profit" else (pl >= 0)
            if retreated:
                g["fired"] = False

    def _apply_guard(self, cmd: dict) -> dict:
        """Arm/disarm/retune the P&L guard from a client command. Re-arming clears the fired latch."""
        g = self._guard
        if cmd.get("enabled") is not None:
            g["enabled"] = bool(cmd["enabled"])
        side = cmd.get("side")
        if side in ("profit", "loss"):
            g["side"] = side
        tp = cmd.get("target_pl")
        if tp is not None:
            try:
                g["target_pl"] = max(0.0, float(tp))
            except (TypeError, ValueError):
                pass
        # Any (re)arm or param change resets the latch, so a fresh target can fire immediately.
        g["fired"] = False
        return {"ok": True, "guard": dict(g)}

    def _apply_ticklog(self, cmd: dict) -> dict:
        """Turn tick logging on/off at runtime and persist the choice so it survives a restart.

        Disabling flushes and closes the CSV handle so writing truly stops; a later enable
        reopens it lazily in _capture_ticks. Never trades -- this only gates a file writer."""
        if cmd.get("enabled") is not None:
            enabled = bool(cmd["enabled"])
            if enabled and not self._ticklog["enabled"]:
                # ONE capturer per machine. Each instance writes its own CSV, but two
                # instances capturing at once doubles the copy_ticks_range work on a
                # shared terminal host for two files nobody asked for -- and silently,
                # since each one's UI would report success.
                try:
                    lock = instance_lock.ticklog_lock()
                    lock.acquire({"instance": instance_paths.instance_name(),
                                  "port": config.PORT, "what": "ticklog"})
                    self._ticklog_lock = lock
                except instance_lock.LockHeld as held:
                    log.warning("tick logging refused: another instance is capturing",
                                extra={"event": "ticklog_refused_duplicate",
                                       "holder": held.holder})
                    return {"ok": False, "error": f"tick logging is {held.describe()}",
                            "ticklog": {"enabled": False, "rows": self._ticklog["rows"],
                                        "path": config.TICKLOG_PATH}}
                except instance_lock.LockUnavailable as exc:
                    # Same policy as the account lock: a broken lock mechanism must not
                    # block a diagnostic that places no orders.
                    log.error("ticklog lock unavailable -- enabling anyway",
                              extra={"event": "ticklog_lock_unavailable", "error": str(exc)})
            elif not enabled:
                self._release_ticklog_lock()
            self._ticklog["enabled"] = enabled
            ticklog_state.save(enabled)
            if not enabled and self._tick_fh is not None:
                try:
                    self._tick_fh.flush()
                    self._tick_fh.close()
                except Exception:
                    pass
                self._tick_fh = None
            log.info("tick logging %s", "enabled" if enabled else "disabled",
                     extra={"event": "ticklog_toggled", "enabled": enabled,
                            "path": config.TICKLOG_PATH})
        return {"ok": True, "ticklog": {"enabled": self._ticklog["enabled"],
                                        "rows": self._ticklog["rows"],
                                        "path": config.TICKLOG_PATH}}

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
        if action == "guard":
            return self._apply_guard(cmd)
        if action == "ticklog":
            return self._apply_ticklog(cmd)
        if action == "history":
            return self._history(cmd)
        if action == "login":
            return self._login(cmd)
        if action == "logout":
            return self._logout(cmd)
        if action == "strategy":
            sid = cmd.get("id") or "straddle"      # default keeps the old API working
            s = self.strategies.get(sid)
            if s is None:
                return {"ok": False, "error": f"unknown strategy {sid!r}"}
            return s.update(cmd.get("params"), cmd.get("enabled"))
        return {"ok": False, "error": f"unknown action {action!r}"}

    # ---- strategy support (worker-thread only; called from strategy.evaluate) ----
    @property
    def poll_count(self) -> int:
        """Polls since start/login. Engines throttle heavy work off this (see
        `StrategyBase._check_kill`), using the same `% POLL_HZ` idiom `_poll_state`
        uses for the account stats."""
        return self._poll_count

    def recent_bars(self, tf: int, count: int, bucket_s: int, key: str):
        """Last `count` bars, CACHED until the timeframe's next bar can have closed.

        Every engine that wants bars calls this on EVERY poll -- 15x/sec -- but an M5
        bar can only close on a 5-minute wall-clock boundary, so 4499 of every 4500
        of those calls used to fetch a byte-identical array over MT5's IPC and then
        throw it away (straddle.py did exactly that: fetch 107 M1 bars, then discard
        when `_last_bar_ts` was unchanged). The poll loop has a ~66 ms budget that
        already contains ~6 IPC round-trips; this removes two of them.

        Refetch only when the wall-clock bucket rolls, or when a caller asks for MORE
        bars than are cached. Cached per `key` so M1 and M5 do not evict each other.

        The still-forming bar is deliberately NOT kept fresh: every caller here reads
        the last CLOSED bar (index -2) for signals and takes live prices from the poll
        snapshot instead, so a stale final bar changes no decision.
        """
        bucket = int(time.time() // max(1, bucket_s))
        hit = self._bars_cache.get(key)
        if hit is not None and hit[0] == bucket and hit[1] >= int(count):
            return hit[2]
        bars = mt5.copy_rates_from_pos(self._symbol, tf, 0, int(count))
        # Never cache a failed read -- MT5 returns None on a dropped terminal, and
        # caching that would keep the engine blind until the next bucket rolls.
        if bars is not None:
            self._bars_cache[key] = (bucket, int(count), bars)
        return bars

    def recent_m1(self, count: int):
        """Last `count` M1 bars for the active symbol (structured np array)."""
        return self.recent_bars(mt5.TIMEFRAME_M1, count, 60, "m1")

    def recent_m5(self, count: int):
        """Last `count` M5 bars for the active symbol (structured np array)."""
        return self.recent_bars(mt5.TIMEFRAME_M5, count, 300, "m5")

    def ticks_since(self, ts: int):
        """Every tick from unix time `ts` to now, for the active symbol.

        Used by crash recovery to rebuild a ladder's EXTREME (the lowest bid it
        reached), which is what its retrace stop measures from and the one piece of
        state the broker does not keep. Returns None when the broker has nothing --
        the caller must fall back rather than treat that as "no move happened"."""
        start = datetime.datetime.fromtimestamp(int(ts))
        end = datetime.datetime.now() + datetime.timedelta(seconds=1)
        return mt5.copy_ticks_range(self._symbol, start, end, mt5.COPY_TICKS_ALL)

    def _capture_ticks(self) -> None:
        """Append every raw tick since the last poll to a CSV, when enabled.

        Rides the connection the worker already owns (copy_ticks_range via
        ticks_since), so it needs no second IPC and captures EVERY tick between
        polls -- not just the 15 Hz snapshot _poll_state keeps. The whole body is
        wrapped in try/except: a logging failure must never disturb ticks or
        orders. It places NO orders and touches no trade path -- read-only.
        """
        if not self._ticklog["enabled"]:
            return
        if not (self._session_active and self._initialized):
            return
        try:
            now = time.time()
            frm = self._tick_from_ts or (now - 2.0)
            ticks = self.ticks_since(int(frm))
            if ticks is None or len(ticks) == 0:
                return

            if self._tick_fh is None:
                path = config.TICKLOG_PATH
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                fresh = (not os.path.exists(path)) or os.path.getsize(path) == 0
                self._tick_fh = open(path, "a", encoding="utf-8", newline="")
                if fresh:
                    self._tick_fh.write(
                        "time_msc,iso_time,bid,ask,last,volume,flags,spread\n")
                log.info("tick logging started",
                         extra={"event": "ticklog_started", "path": path,
                                "symbol": self._symbol})

            wrote = 0
            last_msc = self._tick_last_msc
            for t in ticks:
                msc = int(t["time_msc"])
                if msc <= self._tick_last_msc:
                    continue          # already written on a previous poll
                bid = float(t["bid"])
                ask = float(t["ask"])
                # UTC, to match the time_msc epoch (MT5 tick times are UTC). A trailing
                # 'Z' makes it unambiguous instead of a naive machine-local string.
                iso = datetime.datetime.fromtimestamp(
                    msc / 1000.0, datetime.timezone.utc
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                self._tick_fh.write(
                    f"{msc},{iso},{bid},{ask},{float(t['last'])},"
                    f"{int(t['volume'])},{int(t['flags'])},{round(ask - bid, 6)}\n")
                last_msc = max(last_msc, msc)
                wrote += 1

            if wrote:
                self._tick_fh.flush()
                self._tick_last_msc = last_msc
                self._ticklog["rows"] += wrote
            # Advance the fetch floor to just under the newest tick, so the next
            # range stays small yet can't skip a tick straddling the boundary
            # (the msc watermark above dedupes the deliberate 1 s overlap).
            self._tick_from_ts = (last_msc / 1000.0 - 1.0) if last_msc else (now - 2.0)
        except Exception as exc:  # never let logging disturb the trading loop
            log.warning("tick logging error (continuing)",
                        extra={"event": "ticklog_error", "err": str(exc)})

    def reconcile_strategies(self) -> None:
        """Rebuild every engine from the broker's open book.

        Called once the terminal is connected, and again after any login/switch: a
        different account means a different book, and an engine still holding the old
        account's tickets would be closing trades that are no longer even visible."""
        for sid, s in self.strategies.items():
            try:
                s.reconcile(self)
            except Exception:
                log.exception("strategy reconcile crashed",
                              extra={"event": "strategy_exception", "strategy": sid})

    def strategy_positions(self, magic: int) -> list:
        """Open positions belonging to ONE engine.

        `magic` is required, not defaulted. Every strategy helper filters on it,
        and that filter is the only thing keeping two engines independent: without
        it, one engine's kill-switch would happily flatten the other's book."""
        poss = mt5.positions_get(symbol=self._symbol) or []
        return [p for p in poss if int(p.magic) == int(magic)]

    def strategy_place(self, magic: int, side: str, volume: float,
                       sl_dist: float, tp_dist: float,
                       comment: str = "XauStrategy") -> dict:
        """Market order with absolute SL/TP derived from the fill-side price.

        Tagged with the calling engine's `magic`, so it is never touched by manual
        close-all, never touched by another engine, and independently attributable
        in the log. `sl_dist`/`tp_dist` of 0 mean "no stop"/"no target"."""
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
            # 0 => omit. MT5 reads 0.0 as "no stop", but sending a rounded 0.0
            # where a price is expected is asking for an INVALID_STOPS retcode.
            "sl": round(sl, si.digits) if sl_dist else 0.0,
            "tp": round(tp, si.digits) if tp_dist else 0.0,
            "deviation": int(config.DEFAULT_DEVIATION),
            "magic": int(magic),
            "comment": comment,
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

    def strategy_close_ticket(self, magic: int, ticket: int) -> dict:
        """Close one position by ticket, but ONLY if it belongs to `magic`.

        The ownership re-check is not paranoia: a ticket list can go stale (the
        broker closes a position on SL while the engine still holds its number),
        and a recycled ticket must never let one engine close another's trade."""
        poss = mt5.positions_get(ticket=int(ticket))
        if not poss:
            return {"ok": False, "error": "ticket not found"}
        p = poss[0]
        if int(p.magic) != int(magic):
            return {"ok": False, "ticket": int(ticket),
                    "error": f"ticket {ticket} belongs to magic {p.magic}, not {magic}"}
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
            "magic": int(magic),
            "comment": "XauStrategy close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _pick_filling(si),
        }
        res = mt5.order_send(request)
        if res is None:
            code, msg = mt5.last_error()
            return {"ok": False, "ticket": int(ticket), "error": f"{code}: {msg}"}
        return {"ok": res.retcode == mt5.TRADE_RETCODE_DONE, "ticket": int(ticket)}

    def strategy_daily_realized(self, magic: int) -> float:
        """Today's realized P/L (profit+swap+commission) for ONE engine's deals."""
        now = datetime.datetime.now()
        start = datetime.datetime(now.year, now.month, now.day)
        deals = mt5.history_deals_get(start, now) or []
        out = (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT, mt5.DEAL_ENTRY_OUT_BY)
        total = 0.0
        for d in deals:
            if int(getattr(d, "magic", 0)) == int(magic) and d.entry in out:
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

    def _release_ticklog_lock(self) -> None:
        """Stop claiming the machine-wide tick-capture slot."""
        lock, self._ticklog_lock = self._ticklog_lock, None
        if lock is not None:
            lock.release()

    def _release_account_lock(self) -> None:
        """Give up exclusivity on the account this server was driving, if any.

        Best-effort by design: release() never raises, and even if this were skipped
        entirely the OS drops the lock when the process exits. That is the property
        that makes the whole scheme safe against a crash.
        """
        lock, self._account_lock = self._account_lock, None
        if lock is not None:
            lock.release()

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

        # ---- the instance is PINNED to one account ----------------------------
        # Checked first, before the lock and before MT5 is touched, so a wrong-account
        # attempt changes precisely nothing -- no half-switch, no lock taken, no terminal
        # relogged. This is the guard that makes an instance NAMED after an account
        # trustworthy: without it the name is a comment, and this is a screen people
        # press CLOSE ALL on.
        if config.EXPECT_LOGIN and int(login) != int(config.EXPECT_LOGIN):
            log.warning("login refused: wrong account for this instance",
                        extra={"event": "account_login_refused_pinned",
                               "requested_login": int(login),
                               "expected_login": int(config.EXPECT_LOGIN),
                               "instance": instance_paths.instance_name()})
            return {"ok": False, "wrong_account": True,
                    "error": (f"this server only drives account "
                              f"{config.EXPECT_LOGIN}; refusing to log in {login}. "
                              f"Use the instance for that account.")}

        prev_open = self._open_position_count()    # outgoing account's open trades

        # ---- exclusivity: one account, one server -----------------------------
        # Taken BEFORE MT5 is touched, so a refused login leaves the current session
        # exactly as it was -- no half-switch where we dropped account A and then
        # could not take B. The old lock is released only after the new one is held
        # AND the broker accepted us (below), for the same reason.
        #
        # This is not belt-and-braces: two processes CAN attach to one terminal and
        # drive one account simultaneously (measured), and that would give the account
        # two close-all paths and two P&L guards that cannot see each other.
        new_lock = instance_lock.account_lock(int(login), server)
        # Re-logging into the account we ALREADY drive must not be refused by our own
        # lock. Byte-range locks are per-HANDLE, not per-process: a second handle onto
        # the same file conflicts even from this very process, so without this check
        # "log in again to the account you are already on" would fail with
        # "already in use", naming ourselves. Same path -> keep the lock we hold.
        if (self._account_lock is not None
                and self._account_lock.path == new_lock.path
                and self._account_lock.held):
            new_lock = self._account_lock
        else:
            try:
                new_lock.acquire({"instance": instance_paths.instance_name(),
                                  "port": config.PORT, "login": int(login),
                                  "server": server})
            except instance_lock.LockHeld as held:
                log.warning("login refused: account already driven by another server",
                            extra={"event": "account_login_refused_duplicate",
                                   "login": login, "server": server,
                                   "holder": held.holder})
                # NOTE for the caller: nothing was touched. MT5 was not contacted, the
                # previous session is intact -- so `already_logged_in` must NOT trigger
                # the session-restore path in server.login().
                return {"ok": False, "prev_open": prev_open, "already_logged_in": True,
                        "error": f"account {login} is {held.describe()}"}
            except instance_lock.LockUnavailable as exc:
                # The lock MECHANISM is broken (disk / permissions / AV), not a genuine
                # duplicate. Fail OPEN and shout: a filesystem fault must not make a
                # single-account desk untradeable. A real duplicate still raises LockHeld.
                log.error("account lock unavailable -- proceeding WITHOUT duplicate protection",
                          extra={"event": "account_lock_unavailable",
                                 "login": login, "server": server, "error": str(exc)})
                new_lock = None

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
                # The broker refused us, so we are NOT driving this account -- give the
                # lock back. Holding it after a failed login would make the account
                # permanently unopenable by any instance until this process exits.
                if new_lock is not None:
                    new_lock.release()
                return {"ok": False, "prev_open": prev_open,
                        "error": f"initialize/login failed ({code}: {msg})"}
            self._initialized = True
        else:
            # Terminal already attached -> switch account in place.
            if not mt5.login(int(login), password=password, server=server):
                code, msg = mt5.last_error()
                self._log_login_failed("switch", login, server, code, msg)
                if new_lock is not None:
                    new_lock.release()
                return {"ok": False, "prev_open": prev_open,
                        "error": f"login failed ({code}: {msg})"}

        # The login path calls mt5.initialize()/login() directly, so it never went
        # through _ensure_connected's check -- assert the binding here too. A login
        # that lands on someone else's terminal must not be reported as success.
        if not self._verify_terminal():
            if new_lock is not None and new_lock is not self._account_lock:
                new_lock.release()
            return {"ok": False, "prev_open": prev_open,
                    "error": ("logged in, but the terminal is NOT this instance's "
                              f"({config.MT5_PATH}). Refusing to drive it.")}

        # Broker accepted us and we hold the new lock -> now, and only now, drop the
        # OUTGOING account's lock so another server may take it over.
        #
        # `is not new_lock` guards the same-account re-login above, where new_lock IS
        # the lock we already hold: releasing then re-storing it would hand our own
        # account away to any instance that asked for it in between.
        if self._account_lock is not new_lock:
            self._release_account_lock()
        self._account_lock = new_lock

        self._session_active = True
        self._resolve_symbol()
        # daily realized / wins / losses belong to the account -> reset on switch.
        self._stats = {"daily_realized": 0.0, "wins": 0, "losses": 0}
        # The P&L guard is per-account too: a "close at +$500" armed on account A must NEVER carry
        # to account B. Disarm on every switch; the user re-arms for the new account if they want.
        self._guard = {"enabled": False, "target_pl": 0.0, "side": "profit", "fired": False}
        self._poll_count = 0
        self._last_healthy = None
        # Bars belong to the account's symbol. _resolve_symbol() above may have picked a
        # different suffix (XAUUSDm vs XAUUSD), so a surviving cache would feed the new
        # account's engines the OLD symbol's prices -- and reconcile_strategies() below
        # runs immediately after.
        self._bars_cache.clear()

        acc = mt5.account_info()
        tmode = int(acc.trade_mode) if acc is not None else None
        # Publish the identity BEFORE anything else logs. _poll_state would pick this
        # up within ~67 ms anyway, but that is too late for the lines below: the
        # `account_login` event itself, and every strategy_reconciled line from
        # reconcile_strategies(), would carry "account": null -- and those are exactly
        # the lines you go looking for when asking "what happened on this account?".
        if acc is not None:
            log_context.set_account(acc.login, acc.server, tmode != 2)

        # The book belongs to the account too. Re-derive every engine from THIS
        # account's positions: an engine still holding the previous account's tickets
        # would be trying to close trades that are no longer even visible.
        self.reconcile_strategies()
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
        self._guard = {"enabled": False, "target_pl": 0.0, "side": "profit", "fired": False}
        self._last_healthy = None
        log.info("account logout",
                 extra={"event": "account_logout", "prev_open": prev_open})
        # Hand the account back so another instance may drive it.
        self._release_account_lock()
        # AFTER the log line above, so the logout itself is still attributed to the
        # account being left. Subsequent lines carry "account": null.
        log_context.clear_account()
        return {"ok": True, "prev_open": prev_open}

    def _place_order(self, cmd: dict) -> dict:
        """Unified entry for the new UI: market or pending(limit)."""
        # Auto-Test live-account guard, AUTHORITATIVE copy. The HTTP handler also checks, but it
        # reads worker.get_state() -- a snapshot up to one poll (~67 ms) stale -- so a demo->REAL
        # switch immediately followed by an auto_test order could pass the guard against the
        # PREVIOUS account. Here we are on the worker thread and can read the LIVE account, which
        # closes that window. trade_mode 2 = REAL; None/unknown is refused too (fail safe).
        if cmd.get("auto_test"):
            acc = mt5.account_info()
            tmode = int(getattr(acc, "trade_mode", 2)) if acc is not None else None
            if tmode != 0 and tmode != 1:      # allow only demo(0) / contest(1)
                log.error("auto_test order refused on non-demo account (live check)", extra={
                    "event": "auto_test_refused_live_account_worker",
                    "trade_mode": tmode,
                    "login": getattr(acc, "login", None),
                })
                return {"ok": False, "auto_test_refused": True,
                        "error": f"auto_test refused: account trade_mode={tmode} is not demo"}

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

    # ---- position / order shaping (shared by the live poll and the history query) ----
    # Both the /ws poll and _history serialize the SAME dict shape; keeping one source stops
    # the two from drifting apart. `symbol` is included so the account-wide history view can
    # label rows -- harmless extra field for the symbol-filtered live poll.
    # Origin letters. Kept here, server-side, so the webapp and the Android app
    # render the SAME label from the SAME rule -- two clients cannot drift.
    _ORIGIN_LETTERS = {"ladder": "L", "straddle": "S", "rider": "R"}

    # Broker comments a client may ask for, by name. A WHITELIST, not free text:
    # the comment is what `_origin_of` reads back, so letting a client write it
    # directly would let a forged request mislabel someone else's trade.
    _ORIGIN_COMMENTS = {"rider": "XauOrderPad rider"}

    @classmethod
    def _order_comment(cls, origin) -> str:
        return cls._ORIGIN_COMMENTS.get(str(origin or "").strip().lower(), "XauOrderPad")

    @classmethod
    def _origin_of(cls, magic: int, comment: str) -> str:
        """Who opened this position: 'L' ladder, 'S' straddle, 'R' rider-suggested,
        '' manual (or a foreign magic).

        Attribution lives on the BROKER's position record (magic + comment), not in
        a local ledger, so it survives app restarts, server restarts and reinstalls,
        and is identical on every device. Costs nothing: both fields are already on
        the TradePosition object `positions_get()` returned -- no extra IPC, no disk.
        """
        for sid, letter in cls._ORIGIN_LETTERS.items():
            if magic == int(config.STRATEGY_MAGICS.get(sid, -1)):
                return letter
        # A rider SUGGESTION is placed by the human through the manual path, so it
        # keeps config.MAGIC (close-all/close-one must still find it) and carries
        # its origin in the broker comment instead.
        if magic == int(config.MAGIC) and "rider" in (comment or "").lower():
            return "R"
        return ""

    @staticmethod
    def _shape_position(p) -> dict:
        return {
            "ticket": p.ticket,
            "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": p.volume, "price_open": p.price_open,
            "sl": p.sl, "tp": p.tp, "profit": p.profit,
            "time": p.time,
            "symbol": getattr(p, "symbol", None),
            "magic": int(p.magic),          # for Auto-Test foreign-magic detection
            # who opened it -- rendered as a badge by BOTH clients (see _origin_of)
            "origin": Mt5Worker._origin_of(int(p.magic), getattr(p, "comment", "") or ""),
        }

    @staticmethod
    def _shape_order(o) -> dict:
        buy_types = (mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_BUY_LIMIT,
                     mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_BUY_STOP_LIMIT)
        return {
            "ticket": o.ticket,
            "side": "BUY" if o.type in buy_types else "SELL",
            "volume": o.volume_current, "price_open": o.price_open,
            "sl": o.sl, "tp": o.tp, "type": "limit", "time": o.time_setup,
            "symbol": getattr(o, "symbol", None),
        }

    def _history(self, cmd: dict) -> dict:
        """Today's CLOSED trades (entry->exit paired), plus current OPEN positions and PENDING
        orders, ACCOUNT-WIDE, with aggregate performance stats. On-demand only (history_deals_get
        is heavy). Uses the SAME local-midnight 'today' boundary as _compute_stats so these numbers
        agree with the daily_realized shown live."""
        try:
            now = datetime.datetime.now()
            start = datetime.datetime(now.year, now.month, now.day)
            deals = mt5.history_deals_get(start, now) or []
            out_entries = (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT,
                           mt5.DEAL_ENTRY_OUT_BY)

            # Pair each closing leg with its opening leg (by position_id) so a closed trade can
            # show entry -> exit. A position OPENED before today has no IN deal in range -> its
            # entry stays null and the UI renders it as "-> exit".
            entries = {}   # position_id -> opening deal
            for d in deals:
                if d.entry == mt5.DEAL_ENTRY_IN:
                    entries.setdefault(d.position_id, d)

            closed = []
            wins = losses = flat = 0
            gross_profit = 0.0
            gross_loss = 0.0
            biggest_win = 0.0
            biggest_loss = 0.0
            for d in deals:
                if d.entry not in out_entries:
                    continue
                pnl = d.profit + d.swap + d.commission
                opening = entries.get(d.position_id)
                if opening is not None:
                    side = "BUY" if opening.type == mt5.DEAL_TYPE_BUY else "SELL"
                else:
                    # The CLOSING deal's type is the OPPOSITE of the position side (a BUY position
                    # is closed by a SELL deal), so invert it to report the position's direction.
                    side = "BUY" if d.type == mt5.DEAL_TYPE_SELL else "SELL"
                closed.append({
                    "position_id": int(d.position_id),
                    "ticket": int(d.ticket),
                    "side": side,
                    "symbol": d.symbol,
                    "volume": d.volume,
                    "entry_price": (opening.price if opening is not None else None),
                    "exit_price": d.price,
                    "entry_time": (int(opening.time) if opening is not None else None),
                    "exit_time": int(d.time),
                    "pnl": round(pnl, 2),
                })
                if d.profit > 0:
                    wins += 1
                    gross_profit += pnl
                    biggest_win = max(biggest_win, pnl)
                elif d.profit < 0:
                    losses += 1
                    gross_loss += pnl
                    biggest_loss = min(biggest_loss, pnl)
                else:
                    flat += 1
            closed.sort(key=lambda r: r["exit_time"], reverse=True)

            net = gross_profit + gross_loss
            closed_count = wins + losses + flat
            stats = {
                "closed_count": closed_count,
                "wins": wins, "losses": losses, "flat": flat,
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "net": round(net, 2),
                "biggest_win": round(biggest_win, 2),
                "biggest_loss": round(biggest_loss, 2),
                "avg_win": round(gross_profit / wins, 2) if wins else 0.0,
                "avg_loss": round(gross_loss / losses, 2) if losses else 0.0,
                # Over TOTAL closed (incl. flats) so 653/807 reads as the 81% the broker shows.
                "win_rate": round(wins / closed_count, 4) if closed_count else 0.0,
            }

            open_positions = [self._shape_position(p)
                              for p in (mt5.positions_get() or [])]
            pending = [self._shape_order(o) for o in (mt5.orders_get() or [])]

            return {
                "ok": True,
                "as_of": int(now.timestamp()),
                "day_start": int(start.timestamp()),
                "stats": stats,
                "closed": closed,
                "open": open_positions,
                "pending": pending,
            }
        except Exception as exc:
            log.exception("history query crashed",
                          extra={"event": "history_exception"})
            return {"ok": False, "error": f"history error: {exc}"}

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
            "comment": self._order_comment(cmd.get("origin")),
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
