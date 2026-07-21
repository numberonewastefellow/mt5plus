"""Vol-Regime Trend-Rider — DEMO by default, REAL only behind an explicit toggle.

It computes the verified M5 thrust-follow + trailing-stop signal (see
analysis/eda/RIDER.md and INVESTIGATION_JOURNEY.md) and then, depending on two
switches the operator sets per account class:

  * OFF  -> surfaces a "trade now" CARD (side, lot, entry, SL, TP, reason) for the
            operator to place with ONE TAP through the normal order path, and
  * ON   -> places the trade itself, on that account class only.

`auto_demo` and `auto_real` are separate on purpose. They are re-read on every
poll, so switching MT5 from a demo to a real account mid-session changes what this
engine may do immediately, with no restart and no re-arm. `auto_real` additionally
never survives a restart (NEVER_RESTORE) -- real money resumes only when a human
says so again.

A running PAPER P&L is kept in ALL modes: it is the forward-test record of a
(marginal, regime-dependent) edge whose confidence interval includes zero. Watch it
before trusting it with anything.

The decision logic lives in `rider_core.RiderState` (numpy-only, MT5-free) so the
research backtest runs the EXACT code that trades here.

Stops, and why they are in two places:
  * The BROKER holds a static stop at entry -/+ cfg.sl, attached by strategy_place.
    That is the backstop that survives this process dying.
  * The TRAIL runs here, against live bid/ask on every poll, and exits at market.
    The backtest trailed on bar extremes; checking each tick is the same test, only
    sooner, so live is very slightly tighter than the numbers in RIDER.md.
"""
from __future__ import annotations

import logging
import time

import numpy as np

import config
from .base import StrategyBase
from .rider_core import RiderConfig, RiderState, kelly_lot

log = logging.getLogger("XauOrderPad.strategy")

_STRUCT = ("thrust_mult", "sl", "trail", "tp", "max_hold", "atr_win", "use_ny_hours")

_M5 = 300           # seconds per M5 bar — the time-stop counts these, not polls
_CONTRACT = 100.0   # oz per lot (XAUUSD) — turns $/oz into account dollars


class VolRegimeRider(StrategyBase):
    ID = "rider"
    NAME = "Vol-Regime Rider"
    needs_hedging = False

    # This engine has been built for real-account execution (broker-side stop attached
    # at entry, tick-level trail, book rebuilt from the broker on restart). It still
    # trades nothing on a real account unless `auto_real` is also on -- see
    # StrategyBase.evaluate, which requires BOTH.
    allows_real = True

    # Saved, never restored. A crash-restart must not resume real-money trading.
    NEVER_RESTORE = frozenset({"auto_real"})

    def __init__(self) -> None:
        super().__init__()
        p = dict(config.RIDER_DEFAULTS)
        self.thrust_mult = float(p["thrust_mult"])
        self.sl = float(p["sl"])
        self.trail = float(p["trail"])
        self.tp = float(p["tp"])
        self.max_hold = int(p["max_hold"])
        self.atr_win = int(p["atr_win"])
        self.use_ny_hours = bool(p["use_ny_hours"])
        self.risk_frac = float(p["risk_frac"])
        self.max_daily_loss = float(p["max_daily_loss"])
        self.auto_demo = bool(p["auto_demo"])
        self.auto_real = bool(p["auto_real"])
        self._sm = RiderState(self._cfg())
        self._last_bar_ts = 0
        self._card: dict = {"kind": "flat", "reason": "not started"}
        self._paper_pnl = 0.0        # $/oz cumulative
        self._paper_pnl_usd = 0.0    # account dollars, accrued at each close
        self._paper_trades = 0
        self._paper_entry: tuple | None = None   # (dir, entry_px, lot)
        # The REAL position this engine is riding, or None. Rebuilt from the broker on
        # restart (_adopt), never trusted from disk.
        self._live: dict | None = None
        # What the last evaluate() saw, for the status the UIs render.
        self._auto_now = False
        self._demo_now = True

    # ---- config plumbing --------------------------------------------------
    def _cfg(self) -> RiderConfig:
        return RiderConfig(thrust_mult=self.thrust_mult, sl=self.sl, trail=self.trail,
                           tp=self.tp, max_hold=self.max_hold, atr_win=self.atr_win,
                           use_ny_hours=self.use_ny_hours, risk_frac=self.risk_frac)

    @property
    def wants_real(self) -> bool:
        """The operator's live choice, read by StrategyBase.evaluate on every poll."""
        return self.auto_real

    def update(self, params: dict | None, enabled: bool | None) -> dict:
        """Turning the engine OFF must not abandon an OPEN position.

        `evaluate()` returns early on `not (enabled or _managing)`, so without this a
        disable would stop the trail dead and leave the trade with nothing but the
        broker's original static stop -- the widest it ever was, and the operator would
        have no signal that the trailing they were relying on had stopped. So: switch to
        MANAGING instead. `can_enter` is already False in that mode, so it works the exit
        and opens nothing new -- exactly what reconcile() does for an adopted book.
        Closing it instead is not on the table: nothing here closes a human's trade
        because a switch moved."""
        out = super().update(params, enabled)
        if enabled is False and self._live:
            self._managing = True
            self._state = "managing"
            self._error = ("disabled, but still working the exit on live ticket "
                           f"{self._live['ticket']} — close it yourself to stop entirely")
            log.warning("rider disabled while holding ticket %s — still managing the exit",
                        self._live["ticket"],
                        extra={"event": "rider_disabled_while_live", "strategy": self.ID,
                               "ticket": self._live["ticket"]})
            return self.status()
        return out

    def _defaults(self) -> dict:
        return dict(config.RIDER_DEFAULTS)

    def _params(self) -> dict:
        return {"thrust_mult": self.thrust_mult, "sl": self.sl, "trail": self.trail,
                "tp": self.tp, "max_hold": self.max_hold, "atr_win": self.atr_win,
                "use_ny_hours": self.use_ny_hours, "risk_frac": self.risk_frac,
                "max_daily_loss": self.max_daily_loss,
                "auto_demo": self.auto_demo, "auto_real": self.auto_real}

    def _apply(self, params: dict) -> None:
        before = {k: getattr(self, k) for k in _STRUCT}
        for k, cast in (("thrust_mult", float), ("sl", float), ("trail", float),
                        ("tp", float), ("max_hold", int), ("atr_win", int),
                        ("use_ny_hours", bool), ("risk_frac", float),
                        ("max_daily_loss", float),
                        ("auto_demo", bool), ("auto_real", bool)):
            if k in params and params[k] is not None:
                setattr(self, k, cast(params[k]))
        # Rebuild the state machine only when a STRUCTURAL param moved (that resets the
        # paper run, so it must not happen for a risk tweak) -- but ALWAYS re-hand it the
        # config. It captured a RiderConfig at construction, so without this line a
        # `risk_frac` change was reported back to the UI and then ignored by the sizing:
        # the panel said 5% while every card was still sized at 1%.
        if any(before[k] != getattr(self, k) for k in _STRUCT):
            self._sm = RiderState(self._cfg())
            self._last_bar_ts = 0
            self._paper_entry = None
        else:
            self._sm.cfg = self._cfg()

    # ---- indicators over the recent M5 window (numpy-only) ----------------
    @staticmethod
    def _atr_series(h, l, c, n=14):
        prevc = np.concatenate(([c[0]], c[:-1]))
        tr = np.maximum.reduce([h - l, np.abs(h - prevc), np.abs(prevc - l)])
        atr = np.full(len(c), np.nan)
        for i in range(n, len(c)):
            atr[i] = tr[i - n + 1:i + 1].mean()
        return atr

    # ---- main hook (worker thread; called only when enabled + allowed + healthy) ----
    def _tick(self, worker, st: dict) -> None:
        acc = st.get("account") or {}
        self._demo_now = bool(acc.get("is_demo", False))
        # Which switch governs THIS account, right now. Re-read every poll so an account
        # switch takes effect without a restart; `can_enter` keeps a managing-only engine
        # (adopted a book but not re-armed) from opening anything new.
        self._auto_now = bool((self.auto_demo if self._demo_now else self.auto_real)
                              and self.can_enter)

        # 1) MANAGE the live position first, and on EVERY poll. Prices come from the poll
        #    snapshot that was just taken, so this costs no MT5 IPC at all. Exits before
        #    entries, always: never look for a new trade while an old one needs closing.
        self._manage_live(worker, st)

        # 2) SIGNAL, only when a new M5 bar has closed. recent_m5 is cached by the worker
        #    to the 5-minute boundary, so this is one IPC per bar, not one per poll.
        need = self.atr_win + 40
        bars = worker.recent_m5(need)
        if bars is None or len(bars) < self.atr_win + 20:
            self._state = "waiting: warming up M5 history"
            return
        t = bars["time"].astype("int64")
        # Nothing has closed since we last advanced -> no decision to make.
        n = len(t)
        if n < 2 or int(t[n - 2]) <= self._last_bar_ts:
            self._state = self._state_label()
            return

        o = bars["open"].astype(float); h = bars["high"].astype(float)
        l = bars["low"].astype(float); c = bars["close"].astype(float)
        atr = self._atr_series(h, l, c)
        equity = float(acc.get("equity", 0.0)) or 500.0

        # feed every bar that has closed since we last advanced, in order
        for i in range(1, n - 1):
            if t[i] <= self._last_bar_ts:
                continue
            lo = max(0, i - self.atr_win + 1)
            window = atr[lo:i + 1]
            atr_med = float(np.nanmedian(window)) if np.isfinite(window).any() else np.nan
            hour = int(np.datetime64(int(t[i]), "s").astype("datetime64[h]").astype(int) % 24)
            for act in self._sm.on_bar(o[i], h[i], l[i], c[i], atr[i], atr_med, hour, equity):
                self._record(act, int(t[i]))
                self._maybe_place(worker, st, act)
            self._last_bar_ts = int(t[i])

        self._state = self._state_label()

    def _state_label(self) -> str:
        if self._live:
            return "active (riding a live position)"
        if self._sm.in_pos:
            return "active (in paper position)"
        return "active"

    # ---- paper bookkeeping -------------------------------------------------
    def _record(self, act, ts: int) -> None:
        """Update the operator card + the paper record from a state-machine action.
        This is the FORWARD TEST and is kept in every mode, including live: it is the
        only clean read on the edge, unpolluted by slippage and fill luck."""
        if act.kind == "enter":
            d = 1 if act.side == "buy" else -1
            self._paper_entry = (d, act.entry_ref, act.lot)
            self._card = {"kind": "enter", "side": act.side, "lot": act.lot,
                          "entry": round(act.entry_ref, 2), "sl": round(act.sl, 2),
                          "tp": round(act.tp, 2), "reason": act.reason, "bar_ts": ts}
        elif act.kind == "close" and self._paper_entry is not None:
            d, entry_px, lot = self._paper_entry
            pnl_oz = d * (act.entry_ref - entry_px)
            self._paper_pnl += pnl_oz
            # Accrue dollars at the lot this trade was actually SIZED at. The old field
            # multiplied the running $/oz total by whatever lot the CURRENT card happened
            # to carry (0.01 on a close card, which has no lot at all), so it moved
            # whenever the next signal did and was never anyone's P&L.
            self._paper_pnl_usd += pnl_oz * _CONTRACT * float(lot)
            self._paper_trades += 1
            self._paper_entry = None
            self._card = {"kind": "close", "reason": act.reason,
                          "pnl_oz": round(pnl_oz, 2), "bar_ts": ts}
        elif act.kind in ("hold", "flat"):
            # Keep the last actionable card; refresh only a lightweight status.
            # `bar_ts` is deliberately NOT touched: it records the bar the card was
            # ISSUED on, and _actionable() compares it against the newest closed bar to
            # decide whether the suggestion is still live. Stamping it here would refresh
            # the card's apparent age every 5 minutes and make it immortal -- which is
            # precisely the bug this is fixing. `status_ts` carries "as of when".
            self._card = {**self._card, "status": act.kind, "note": act.reason,
                          "status_ts": ts}

    # ---- live execution ----------------------------------------------------
    def _maybe_place(self, worker, st: dict, act) -> None:
        """Turn an 'enter' action into a REAL order -- but only when the switch for the
        connected account class is on. With both switches off this method does nothing
        at all and the card is the whole product."""
        if act.kind != "enter" or not self._auto_now or self._live is not None:
            return
        lot = float(act.lot)
        # sl/tp go in as $/oz DISTANCES: strategy_place converts them against the fill
        # price itself (mt5_worker._strategy_place), which is the same unit RiderConfig
        # is written in. No 10^digits anywhere on this path, so no unit trap.
        res = worker.strategy_place(self.MAGIC, act.side, round(lot, 2),
                                    float(self.sl), float(self.tp), comment="XauRider")
        if not res.get("ok"):
            self._error = f"order rejected: {res.get('error') or res.get('retcode')}"
            log.error("rider entry rejected", extra={"event": "rider_entry_rejected",
                                                     "strategy": self.ID, "result": res})
            return
        fill = float(res.get("price") or act.entry_ref)
        d = 1 if act.side == "buy" else -1
        self._live = {
            "ticket": int(res.get("ticket") or 0),
            "dir": d, "side": act.side, "lot": lot, "entry": fill,
            # Engine-side trail, seeded at the same level the broker is holding.
            "stop": fill - d * self.sl,
            "tp": fill + d * self.tp,
            "best": fill,
            "opened": int(time.time()),
            "basis": "fill",
        }
        log.warning("rider ENTERED a live position (%s account)",
                    "demo" if self._demo_now else "REAL",
                    extra={"event": "rider_entry", "strategy": self.ID,
                           "is_demo": self._demo_now, "ticket": self._live["ticket"],
                           "side": act.side, "lot": lot, "fill": fill,
                           "broker_stop": self._live["stop"], "reason": act.reason})

    def _manage_live(self, worker, st: dict) -> None:
        """Trail and exit the live position. Runs on EVERY poll, off the snapshot's
        bid/ask -- no MT5 call is made here unless we actually decide to close."""
        if not self._live:
            return

        # Has it gone? The broker's own stop may have filled, or the operator may have
        # closed it by hand from either client. `st["positions"]` was just fetched by the
        # poll, so this reconciliation is free.
        tick = self._live["ticket"]
        alive = any(int(p.get("ticket", 0)) == tick
                    for p in (st.get("positions") or []))
        if not alive:
            log.info("rider position %s closed outside the engine", tick,
                     extra={"event": "rider_position_gone", "strategy": self.ID,
                            "ticket": tick})
            self._live = None
            # Managing existed only to see this position out. With the book flat there is
            # nothing left to manage, so drop back to a clean disabled state rather than
            # sitting in "managing" forever.
            if self._managing:
                self._managing = False
                self._state = "disabled"
                self._error = None
            return

        bid, ask = st.get("bid"), st.get("ask")
        if bid is None or ask is None:
            return
        d = self._live["dir"]
        # Mark against the side we would EXIT at, not the mid: a long is closed at bid.
        mark = float(bid) if d > 0 else float(ask)

        # Conservative order, matching rider_core.on_bar: stop, then target, then trail.
        # Checking the trail first would let the same tick both raise the stop and be
        # judged against it -- the intrabar-optimism bug the backtest already had once.
        if (d > 0 and mark <= self._live["stop"]) or (d < 0 and mark >= self._live["stop"]):
            self._close_live(worker, "stop", mark)
            return
        if (d > 0 and mark >= self._live["tp"]) or (d < 0 and mark <= self._live["tp"]):
            self._close_live(worker, "target", mark)
            return
        if (time.time() - self._live["opened"]) >= self.max_hold * _M5:
            self._close_live(worker, "time", mark)
            return

        if self.trail > 0:
            if d > 0:
                self._live["best"] = max(self._live["best"], mark)
                self._live["stop"] = max(self._live["stop"], self._live["best"] - self.trail)
            else:
                self._live["best"] = min(self._live["best"], mark)
                self._live["stop"] = min(self._live["stop"], self._live["best"] + self.trail)

    def _close_live(self, worker, why: str, mark: float) -> None:
        live = self._live
        r = worker.strategy_close_ticket(self.MAGIC, int(live["ticket"]))
        pnl_oz = live["dir"] * (mark - live["entry"])
        log.warning("rider EXIT (%s) %s", why, "ok" if r.get("ok") else "FAILED",
                    extra={"event": "rider_exit", "strategy": self.ID, "why": why,
                           "ticket": live["ticket"], "mark": mark,
                           "pnl_per_oz": round(pnl_oz, 2), "result": r})
        # If the close FAILED we KEEP _live, so the next poll tries again rather than
        # forgetting a position that is still open and still carrying risk. The one
        # exception is "the ticket is not there any more", which is not a failure to
        # close -- it is already closed, and retrying would just log an error forever.
        if r.get("ok") or "not found" in str(r.get("error", "")).lower():
            self._live = None

    def _on_killed(self) -> None:
        """The kill-switch already flattened this engine's book; drop the local handle so
        we do not keep trying to close a ticket that is gone."""
        self._live = None

    # ---- crash recovery ----------------------------------------------------
    def _adopt(self, worker, positions: list) -> int:
        """Rebuild the live position from the BROKER after a restart.

        Everything here comes off the position except `best` -- the high-water mark the
        trail measures from -- which the broker does not store and which is exactly what
        decides where the stop sits. Reconstruct it from tick history (the extreme the
        price actually reached since the position opened); if the ticks are unavailable
        (weekend, gap, broker returns nothing) fall back to the ENTRY price, which is the
        conservative answer -- it puts the stop back at its widest, never tighter than
        reality justifies -- and record in the log that the basis is approximate.
        Never silently invent a stop basis."""
        if not positions:
            return 0
        p = positions[0]
        d = 1 if int(p.type) == 0 else -1          # MT5: 0=BUY, 1=SELL
        entry = float(p.price_open)
        best, basis = entry, "entry (approximate — no ticks)"
        try:
            ticks = worker.ticks_since(int(p.time))
            if ticks is not None and len(ticks):
                best = float(max(ticks["bid"])) if d > 0 else float(min(ticks["ask"]))
                basis = "ticks"
        except Exception:
            log.exception("rider adopt: tick history unavailable",
                          extra={"event": "rider_adopt_ticks_failed", "strategy": self.ID})

        stop = (best - self.trail) if d > 0 else (best + self.trail)
        # Never looser than the original hard stop.
        stop = max(stop, entry - self.sl) if d > 0 else min(stop, entry + self.sl)
        self._live = {
            "ticket": int(p.ticket), "dir": d, "side": "buy" if d > 0 else "sell",
            "lot": float(p.volume), "entry": entry, "stop": stop,
            "tp": entry + d * self.tp, "best": best,
            "opened": int(p.time), "basis": basis,
        }
        log.warning("rider adopted a live position from the broker",
                    extra={"event": "rider_adopted", "strategy": self.ID,
                           "ticket": int(p.ticket), "entry": entry, "best": best,
                           "stop": stop, "basis": basis})
        return len(positions)

    def _adopt_detail(self) -> dict:
        return {"live": self._live}

    # ---- status ------------------------------------------------------------
    def _execution(self) -> str:
        """ONE label, derived here, rendered verbatim by both clients so they cannot
        describe the same engine differently."""
        if not (self.enabled or self._managing):
            return "off"
        if not self._auto_now:
            return "suggest"
        return "auto-demo" if self._demo_now else "AUTO-REAL"

    def _actionable(self) -> bool:
        """May the operator usefully tap PLACE on the current card?

        Three conditions, and the bar_ts one is the reason this exists: `_record` keeps
        the last actionable card while the position is held, so `kind == "enter"` stays
        true for up to max_hold bars (~2 h). Both clients used to key the PLACE button
        off that alone and would happily offer a two-hour-old entry price. A suggestion
        is only worth acting on for the bar it was computed on."""
        c = self._card
        return bool(c.get("kind") == "enter"
                    and self.enabled
                    and not self._auto_now          # it already placed it for you
                    and c.get("bar_ts") == self._last_bar_ts)

    def _extra_status(self) -> dict:
        return {
            # NOT called "paper": the Android strategy page used to key its LADDER-specific
            # UI off `paper != null`, so emitting `paper` here made the rider render the
            # ladder's trigger + a dead LIVE-ORDERS switch. This engine has no paper/live
            # toggle -- it has two account-scoped execution switches instead.
            "suggestion_only": not self._auto_now,
            "execution": self._execution(),      # off | suggest | auto-demo | AUTO-REAL
            "card": self._card,
            "actionable": self._actionable(),    # both clients gate PLACE on THIS
            "paper_pnl_per_oz": round(self._paper_pnl, 2),
            "paper_pnl_usd": round(self._paper_pnl_usd, 2),
            "paper_trades": self._paper_trades,
            "in_paper_position": self._sm.in_pos,
            "live_ticket": (self._live or {}).get("ticket"),
            "live_stop": round((self._live or {}).get("stop", 0.0), 2) if self._live else None,
            "note": ("Places automatically on this account — the card is informational."
                     if self._auto_now else
                     "SUGGESTION ONLY — tap Place to execute. Marginal edge; forward-test first."),
        }
