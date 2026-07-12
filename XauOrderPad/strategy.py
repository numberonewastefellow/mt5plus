"""Volume-Spike Straddle — the app's one automated, DEMO-ONLY strategy.

Design (validated in ../analysis/): a rare M1 volume spike reliably marks a BIG
move but NOT its direction. So we trade BOTH ways at once (a straddle): on a spike
we open a long AND a short, each with a tight ATR stop and a larger target. One
leg stops out small; the other is meant to run. This sidesteps the direction
problem entirely — it is a volatility bet, not a hedge.

Because the two legs are opposite positions on the same symbol, this REQUIRES a
hedging account (netting nets them to zero). It is also gated to DEMO accounts.

This module owns ZERO MetaTrader5 calls of its own. Everything that touches MT5
goes through the Mt5Worker helper methods (recent_m1 / strategy_place /
strategy_positions / strategy_close_ticket / strategy_daily_realized), so the
"single thread owns the terminal" invariant is preserved — `evaluate()` is only
ever called from the worker thread.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any

import numpy as np

import config

log = logging.getLogger("XauOrderPad.strategy")


class VolumeSpikeStraddle:
    NAME = "Volume-Spike Straddle"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled = False
        # tunables (seeded from config; overridable live from the UI)
        p = dict(config.STRATEGY_DEFAULTS)
        self.volume = float(p["volume"])
        self.rvol_window = int(p["rvol_window"])
        self.rvol_threshold = float(p["rvol_threshold"])
        self.atr_period = int(p["atr_period"])
        self.sl_atr_mult = float(p["sl_atr_mult"])
        self.tp_r = float(p["tp_r"])
        self.max_hold_min = float(p["max_hold_min"])
        self.cooldown_min = float(p["cooldown_min"])
        self.max_concurrent = int(p["max_concurrent"])
        self.max_daily_loss = float(p["max_daily_loss"])
        self.vol_filter = bool(p["vol_filter"])
        # runtime state
        self._straddles: list[dict] = []      # [{open_ts, legs:[t1,t2]}]
        self._last_bar_ts = 0                  # last CLOSED M1 bar we evaluated
        self._last_signal_ts = 0.0
        self._signals_today = 0
        self._signals_day = None               # date for the daily counter reset
        self._last_rvol = 0.0                  # last evaluated bar's RVOL (UI feedback)
        self._last_atr = 0.0
        self._state = "disabled"               # disabled|armed|waiting|active|killed
        self._error: str | None = None
        self._killed = False                   # daily-loss kill-switch latched

    # ---- control (called from the worker thread via a queued command) ----
    def update(self, params: dict | None, enabled: bool | None) -> dict:
        with self._lock:
            if params:
                for k in ("volume", "rvol_threshold", "sl_atr_mult", "tp_r",
                          "max_hold_min", "cooldown_min", "max_daily_loss"):
                    if params.get(k) is not None:
                        setattr(self, k, float(params[k]))
                for k in ("rvol_window", "atr_period", "max_concurrent"):
                    if params.get(k) is not None:
                        setattr(self, k, int(params[k]))
                if params.get("vol_filter") is not None:
                    self.vol_filter = bool(params["vol_filter"])
            if enabled is not None:
                self.enabled = bool(enabled)
                if self.enabled:
                    self._killed = False           # re-arming clears a prior kill
                    self._error = None
                    self._state = "armed"
                else:
                    self._state = "disabled"
                log.info("strategy %s", "enabled" if self.enabled else "disabled",
                         extra={"event": "strategy_toggle", "enabled": self.enabled,
                                "params": self._params()})
        return self.status()

    def _params(self) -> dict:
        return {"volume": self.volume, "rvol_window": self.rvol_window,
                "rvol_threshold": self.rvol_threshold, "atr_period": self.atr_period,
                "sl_atr_mult": self.sl_atr_mult, "tp_r": self.tp_r,
                "max_hold_min": self.max_hold_min, "cooldown_min": self.cooldown_min,
                "max_concurrent": self.max_concurrent,
                "max_daily_loss": self.max_daily_loss, "vol_filter": self.vol_filter}

    def status(self) -> dict:
        cd_left = max(0.0, self.cooldown_min * 60 - (time.time() - self._last_signal_ts)) \
            if self._last_signal_ts else 0.0
        return {
            "name": self.NAME,
            "enabled": self.enabled,
            "state": self._state,
            "error": self._error,
            "params": self._params(),
            "active_straddles": len(self._straddles),
            "signals_today": self._signals_today,
            "last_rvol": round(self._last_rvol, 2),
            "last_atr": round(self._last_atr, 3),
            "cooldown_left_s": int(cd_left),
            "killed": self._killed,
        }

    # ---- main loop hook (worker thread only) -----------------------------
    def evaluate(self, worker, st: dict) -> None:
        """Called every poll. Cheap position management runs each tick; new-signal
        detection runs only once per freshly CLOSED M1 bar."""
        if not self.enabled:
            self._state = "disabled"
            return

        acc = st.get("account") or {}
        # Hard safety gates — refuse and auto-disable on anything but demo+hedging.
        if not acc.get("is_demo", False):
            self._disable_with("refused: connected account is NOT a demo account")
            return
        if int(acc.get("margin_mode", -1)) != 2:
            self._disable_with("refused: account is netting — straddle needs a hedging account")
            return
        if self._killed:
            self._state = "killed"
            return
        if not st.get("healthy"):
            self._state = "waiting: terminal not healthy / market closed"
            return

        # 1) manage open straddles (time-stop + prune closed) — every tick
        self._manage(worker)
        # 2) daily-loss kill-switch — every tick
        if self._check_kill(worker):
            return
        # 3) new-signal detection — once per newly closed M1 bar
        self._maybe_signal(worker)
        self._state = "active" if self._straddles else "armed"

    # ---- internals -------------------------------------------------------
    def _disable_with(self, msg: str) -> None:
        if self.enabled or self._error != msg:
            log.warning("strategy auto-disabled: %s", msg,
                        extra={"event": "strategy_auto_disabled", "reason": msg})
        self.enabled = False
        self._error = msg
        self._state = "disabled"

    def _open_leg_tickets(self, worker) -> set[int]:
        try:
            return {int(p.ticket) for p in worker.strategy_positions()}
        except Exception:
            return set()

    def _manage(self, worker) -> None:
        open_tickets = self._open_leg_tickets(worker)
        now = time.time()
        survivors = []
        for s in self._straddles:
            remaining = [t for t in s["legs"] if t in open_tickets]
            if not remaining:
                continue                       # both legs closed by SL/TP -> done
            if now - s["open_ts"] >= self.max_hold_min * 60:
                for t in remaining:            # time-stop: flatten what's left
                    r = worker.strategy_close_ticket(t)
                    log.info("strategy time-stop close",
                             extra={"event": "strategy_time_stop", "ticket": t,
                                    "ok": bool(r.get("ok"))})
                continue
            survivors.append(s)
        self._straddles = survivors

    def _check_kill(self, worker) -> bool:
        try:
            realized = float(worker.strategy_daily_realized())
            floating = sum(float(getattr(p, "profit", 0.0))
                           for p in worker.strategy_positions())
        except Exception:
            return False
        if (realized + floating) <= -abs(self.max_daily_loss):
            log.error("strategy daily-loss kill-switch tripped",
                      extra={"event": "strategy_kill_switch",
                             "realized": realized, "floating": floating,
                             "limit": self.max_daily_loss})
            for p in list(worker.strategy_positions()):
                worker.strategy_close_ticket(int(p.ticket))
            self._straddles.clear()
            self._killed = True
            self.enabled = False
            self._state = "killed"
            self._error = (f"daily loss {realized + floating:.2f} <= "
                           f"-{self.max_daily_loss:.0f} — auto-disabled")
            return True
        return False

    def _maybe_signal(self, worker) -> None:
        need = self.rvol_window + self.atr_period + 3
        bars = worker.recent_m1(need)
        if bars is None or len(bars) < need:
            return
        sig = len(bars) - 2                     # last CLOSED bar (‑1 is still forming)
        bar_ts = int(bars["time"][sig])
        if bar_ts == self._last_bar_ts:
            return                              # already evaluated this bar
        self._last_bar_ts = bar_ts

        v = bars["tick_volume"].astype(float)
        h = bars["high"].astype(float)
        l = bars["low"].astype(float)
        c = bars["close"].astype(float)
        base = np.median(v[sig - self.rvol_window:sig])
        rvol = float(v[sig] / base) if base > 0 else 0.0
        tr = [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(c[k - 1] - l[k]))
              for k in range(sig - self.atr_period + 1, sig + 1)]
        atr = float(np.mean(tr)) if tr else 0.0
        self._last_rvol, self._last_atr = rvol, atr

        # daily signal counter reset
        today = dt.date.today()
        if self._signals_day != today:
            self._signals_day, self._signals_today = today, 0

        if rvol < self.rvol_threshold or atr <= 0:
            return
        # volatility regime filter: only straddle when ATR is expanding
        if self.vol_filter:
            ref = [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(c[k - 1] - l[k]))
                   for k in range(max(1, sig - 60), sig + 1)]
            if ref and atr < float(np.mean(ref)):
                return
        if len(self._straddles) >= self.max_concurrent:
            return
        if self._last_signal_ts and (time.time() - self._last_signal_ts) < self.cooldown_min * 60:
            return
        self._open_straddle(worker, rvol, atr)

    def _open_straddle(self, worker, rvol: float, atr: float) -> None:
        sl_dist = self.sl_atr_mult * atr
        tp_dist = self.tp_r * sl_dist
        vol = round(self.volume, 2)
        buy = worker.strategy_place("buy", vol, sl_dist, tp_dist)
        if not buy.get("ok"):
            self._error = f"straddle abort: buy leg failed ({buy.get('error')})"
            log.warning("straddle buy leg failed",
                        extra={"event": "strategy_leg_failed", "leg": "buy",
                               "error": buy.get("error")})
            return
        sell = worker.strategy_place("sell", vol, sl_dist, tp_dist)
        if not sell.get("ok"):
            # abort cleanly: never leave a one-sided naked position
            worker.strategy_close_ticket(int(buy["ticket"]))
            self._error = f"straddle abort: sell leg failed ({sell.get('error')}); buy leg closed"
            log.warning("straddle sell leg failed — buy leg closed",
                        extra={"event": "strategy_leg_failed", "leg": "sell",
                               "error": sell.get("error"), "buy_ticket": buy.get("ticket")})
            return
        self._straddles.append({"open_ts": time.time(),
                                "legs": [int(buy["ticket"]), int(sell["ticket"])]})
        self._last_signal_ts = time.time()
        self._signals_today += 1
        self._error = None
        log.info("straddle opened",
                 extra={"event": "strategy_straddle_opened",
                        "rvol": round(rvol, 2), "atr": round(atr, 3),
                        "sl_dist": round(sl_dist, 3), "tp_dist": round(tp_dist, 3),
                        "volume": vol, "buy_ticket": buy.get("ticket"),
                        "sell_ticket": sell.get("ticket")})
