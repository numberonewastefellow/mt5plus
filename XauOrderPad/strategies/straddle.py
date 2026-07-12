"""Volume-Spike Straddle — DEMO-ONLY.

Design (validated in ../../analysis/): a rare M1 volume spike reliably marks a BIG
move but NOT its direction. So we trade BOTH ways at once (a straddle): on a spike
we open a long AND a short, each with a tight ATR stop and a larger target. One
leg stops out small; the other is meant to run. This sidesteps the direction
problem entirely — it is a volatility bet, not a hedge.

Because the two legs are opposite positions on the same symbol, this REQUIRES a
hedging account (netting nets them to zero).

See StrategyBase for the MT5-isolation and safety-gate contract.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import numpy as np

import config
from .base import StrategyBase

log = logging.getLogger("XauOrderPad.strategy")


class VolumeSpikeStraddle(StrategyBase):
    ID = "straddle"
    NAME = "Volume-Spike Straddle"
    needs_hedging = True          # two opposite legs on one symbol

    def __init__(self) -> None:
        super().__init__()
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
        self._signals_day = None
        self._last_rvol = 0.0
        self._last_atr = 0.0

    # ---- params -----------------------------------------------------------
    def _defaults(self) -> dict:
        return dict(config.STRATEGY_DEFAULTS)

    def _params(self) -> dict:
        return {"volume": self.volume, "rvol_window": self.rvol_window,
                "rvol_threshold": self.rvol_threshold, "atr_period": self.atr_period,
                "sl_atr_mult": self.sl_atr_mult, "tp_r": self.tp_r,
                "max_hold_min": self.max_hold_min, "cooldown_min": self.cooldown_min,
                "max_concurrent": self.max_concurrent,
                "max_daily_loss": self.max_daily_loss, "vol_filter": self.vol_filter}

    def _apply(self, params: dict) -> None:
        for k in ("volume", "rvol_threshold", "sl_atr_mult", "tp_r",
                  "max_hold_min", "cooldown_min", "max_daily_loss"):
            if params.get(k) is not None:
                setattr(self, k, float(params[k]))
        for k in ("rvol_window", "atr_period", "max_concurrent"):
            if params.get(k) is not None:
                setattr(self, k, int(params[k]))
        if params.get("vol_filter") is not None:
            self.vol_filter = bool(params["vol_filter"])

    def _extra_status(self) -> dict:
        cd_left = max(0.0, self.cooldown_min * 60 - (time.time() - self._last_signal_ts)) \
            if self._last_signal_ts else 0.0
        return {
            "active_straddles": len(self._straddles),
            "signals_today": self._signals_today,
            "last_rvol": round(self._last_rvol, 2),
            "last_atr": round(self._last_atr, 3),
            "cooldown_left_s": int(cd_left),
        }

    def _on_killed(self) -> None:
        self._straddles.clear()

    # ---- main loop --------------------------------------------------------
    def _tick(self, worker, st: dict) -> None:
        """Cheap position management runs each tick; new-signal detection runs
        only once per freshly CLOSED M1 bar."""
        self._manage(worker)
        self._maybe_signal(worker)
        self._state = "active" if self._straddles else "armed"

    def _open_leg_tickets(self, worker) -> set[int]:
        try:
            return {int(p.ticket) for p in self.positions(worker)}
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
                    r = worker.strategy_close_ticket(self.MAGIC, t)
                    log.info("strategy time-stop close",
                             extra={"event": "strategy_time_stop", "strategy": self.ID,
                                    "ticket": t, "ok": bool(r.get("ok"))})
                continue
            survivors.append(s)
        self._straddles = survivors

    def _maybe_signal(self, worker) -> None:
        need = self.rvol_window + self.atr_period + 3
        bars = worker.recent_m1(need)
        if bars is None or len(bars) < need:
            return
        sig = len(bars) - 2                     # last CLOSED bar (-1 is still forming)
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
        buy = worker.strategy_place(self.MAGIC, "buy", vol, sl_dist, tp_dist,
                                    comment="XauStraddle")
        if not buy.get("ok"):
            self._error = f"straddle abort: buy leg failed ({buy.get('error')})"
            log.warning("straddle buy leg failed",
                        extra={"event": "strategy_leg_failed", "strategy": self.ID,
                               "leg": "buy", "error": buy.get("error")})
            return
        sell = worker.strategy_place(self.MAGIC, "sell", vol, sl_dist, tp_dist,
                                     comment="XauStraddle")
        if not sell.get("ok"):
            # abort cleanly: never leave a one-sided naked position
            worker.strategy_close_ticket(self.MAGIC, int(buy["ticket"]))
            self._error = f"straddle abort: sell leg failed ({sell.get('error')}); buy leg closed"
            log.warning("straddle sell leg failed — buy leg closed",
                        extra={"event": "strategy_leg_failed", "strategy": self.ID,
                               "leg": "sell", "error": sell.get("error"),
                               "buy_ticket": buy.get("ticket")})
            return
        self._straddles.append({"open_ts": time.time(),
                                "legs": [int(buy["ticket"]), int(sell["ticket"])]})
        self._last_signal_ts = time.time()
        self._signals_today += 1
        self._error = None
        log.info("straddle opened",
                 extra={"event": "strategy_straddle_opened", "strategy": self.ID,
                        "rvol": round(rvol, 2), "atr": round(atr, 3),
                        "sl_dist": round(sl_dist, 3), "tp_dist": round(tp_dist, 3),
                        "volume": vol, "buy_ticket": buy.get("ticket"),
                        "sell_ticket": sell.get("ticket")})
