"""
RiderState — pure decision logic for the vol-regime trend-rider. NUMPY ONLY, no
MT5, no pandas, no I/O, no clock (mirrors LadderState in ladder.py). This is the
ONE copy: the server engine (strategies/rider.py) and the research backtest
(analysis/eda/rider_state.py) both import it, so what is validated is what runs.

Strategy (verified — see analysis/eda/INVESTIGATION_JOURNEY.md):
  * REGIME gate  — act only when volatility is present (ATR above its rolling
    median, optionally also NY hours). Direction is ~random; we only participate.
  * ENTRY        — M5 thrust-follow: |body| > mult*ATR → trade the candle's way.
  * EXIT         — trailing stop: cut ~sl, trail ~trail, run to ~tp, time-stop.
  * SIZING       — fixed-fractional (Kelly-small): risk ~risk_frac of equity.

Honest by construction: entries fill at the NEXT bar's open; one full spread per
round trip; one position at a time. The live engine is PAPER/SUGGESTION-ONLY —
it computes these actions and surfaces them; it never places an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

NY_HOURS = (13, 14, 15, 16)      # UTC = NY session / US-data hours (big-move window)


@dataclass
class RiderConfig:
    thrust_mult: float = 1.5
    sl: float = 6.0
    trail: float = 6.0
    tp: float = 50.0
    max_hold: int = 24
    atr_win: int = 100
    use_ny_hours: bool = False
    risk_frac: float = 0.01
    # How `sl` and `trail` are read:
    #   "fixed" -- as $/oz, exactly as written. The validated default.
    #   "atr"   -- as MULTIPLES of the bar's ATR(14), i.e. sl=1.5 means 1.5 x ATR.
    #
    # Why the option exists: measured on 6.5 months of M5, a FIXED $6 trail sits below
    # the instrument's own noise in an active session (median per-bar pullback from the
    # running high was $6.45), so every trade exits on the first real retrace -- six for
    # six on 2026-07-22, none reaching the target or the time stop. ATR-scaling makes the
    # exit scale-invariant instead of pinned to one dollar figure.
    #
    # It is NOT the default, and must not become one on backtest alone: 12 variants over
    # the same history put train and holdout in conflicting order, which is what noise
    # looks like. Only sl/trail at 1.5x ATR improved on BOTH halves (train +1.466 vs
    # +1.028, holdout +1.334 vs +0.813) and beat the random-direction control by 2x the
    # baseline's margin. Promote on forward-test evidence, not on this comment.
    stop_units: str = "fixed"

    def stop_dist(self, atr: float) -> float:
        """Initial stop distance in $/oz for a bar whose ATR is `atr`."""
        if self.stop_units == "atr" and isfinite(atr):
            return max(0.5, self.sl * atr)
        return self.sl

    def trail_dist(self, atr: float) -> float:
        """Trailing distance in $/oz. 0 disables the trail in both modes."""
        if self.trail <= 0:
            return 0.0
        if self.stop_units == "atr" and isfinite(atr):
            return max(0.5, self.trail * atr)
        return self.trail


@dataclass
class Suggestion:
    kind: str                    # "enter" | "close" | "flat" | "hold"
    side: str = ""
    lot: float = 0.0
    entry_ref: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    reason: str = ""
    components: dict = field(default_factory=dict)


def kelly_lot(equity: float, cfg: RiderConfig, contract: float = 100.0,
              lot_min: float = 0.01, lot_max: float = 1.0,
              stop_dist: float | None = None) -> float:
    """Fixed-fractional (Kelly-small): risk `risk_frac` of equity on the stop.
    lot = risk_frac*equity / (stop$ * contract). Floored at the broker minimum.

    `stop_dist` is the stop in DOLLARS PER OUNCE and must be passed whenever
    stop_units is "atr" -- there `cfg.sl` is a multiplier (e.g. 1.5), and dividing by
    1.5 instead of by 1.5xATR would oversize the position by roughly the ATR itself."""
    sd = cfg.sl if stop_dist is None else float(stop_dist)
    if sd <= 0:
        return lot_min
    raw = cfg.risk_frac * equity / (sd * contract)
    return float(min(lot_max, max(lot_min, round(raw, 2))))


class RiderState:
    """Feed CLOSED M5 bars in order; get a list of actions per bar (an "enter"
    and/or a "close" can both fire on one bar). Exits use the conservative
    intrabar order (check stop against the prior level, THEN trail)."""

    def __init__(self, cfg: RiderConfig) -> None:
        self.cfg = cfg
        self.in_pos = False
        self.side = ""
        self.dirn = 0
        self.entry = 0.0
        self.stop = 0.0
        self.tp = 0.0
        self.best = 0.0
        self.bars_held = 0
        self.n_trades = 0
        self.pending_dir = 0

    def on_bar(self, o: float, h: float, l: float, c: float,
               atr: float, atr_med: float, hour: int, equity: float) -> list[Suggestion]:
        cfg = self.cfg
        acts: list[Suggestion] = []

        # Distances for THIS bar. In "fixed" mode these are just cfg.sl / cfg.trail; in
        # "atr" mode they scale with the bar's own volatility. Computed once here so the
        # fill and the trail below cannot disagree about which mode is in force.
        sd = cfg.stop_dist(atr)
        td = cfg.trail_dist(atr)

        # 1) fill a pending thrust at THIS bar's open
        if self.pending_dir != 0 and not self.in_pos:
            d = self.pending_dir; self.pending_dir = 0
            lot = kelly_lot(equity, cfg, stop_dist=sd)
            self.in_pos = True; self.dirn = d; self.side = "buy" if d > 0 else "sell"
            self.entry = o; self.best = o; self.bars_held = 0; self.n_trades += 1
            self.stop = o - d * sd; self.tp = o + d * cfg.tp
            acts.append(Suggestion("enter", side=self.side, lot=lot, entry_ref=o,
                                   sl=self.stop, tp=self.tp,
                                   reason=f"{self.side.upper()} thrust in high-vol regime",
                                   components={"stop_dist": round(sd, 2),
                                               "trail": round(td, 2),
                                               "stop_units": cfg.stop_units}))

        # 2) manage the open position on THIS bar (incl. the fill bar)
        exited = False
        if self.in_pos:
            self.bars_held += 1
            if self.dirn > 0:
                if l <= self.stop:
                    acts.append(self._close("stop", self.stop)); exited = True
                elif h >= self.tp:
                    acts.append(self._close("target", self.tp)); exited = True
                else:
                    self.best = max(self.best, h)
                    if td > 0:
                        self.stop = max(self.stop, self.best - td)
            else:
                if h >= self.stop:
                    acts.append(self._close("stop", self.stop)); exited = True
                elif l <= self.tp:
                    acts.append(self._close("target", self.tp)); exited = True
                else:
                    self.best = min(self.best, l)
                    if td > 0:
                        self.stop = min(self.stop, self.best + td)
            if not exited and self.bars_held >= cfg.max_hold:
                acts.append(self._close("time", c)); exited = True

        # 3) flat & idle: arm a new thrust for next bar (not on an exit bar)
        if not self.in_pos and self.pending_dir == 0 and not exited:
            high_vol = isfinite(atr_med) and atr > atr_med
            in_ny = (hour in NY_HOURS) if cfg.use_ny_hours else True
            body = c - o
            is_thrust = isfinite(atr) and abs(body) > cfg.thrust_mult * atr
            if high_vol and in_ny and is_thrust and body != 0:
                self.pending_dir = 1 if body > 0 else -1

        if not acts:
            acts.append(Suggestion("hold" if self.in_pos else "flat",
                                   side=self.side, entry_ref=self.entry, sl=self.stop,
                                   reason=(f"in {self.side}, stop@{self.stop:.2f}"
                                           if self.in_pos else "flat (no signal)")))
        return acts

    def _close(self, why: str, px: float) -> Suggestion:
        side = self.side
        pnl = self.dirn * (px - self.entry)
        self.in_pos = False; self.dirn = 0; self.side = ""; self.bars_held = 0
        return Suggestion("close", side=side, entry_ref=px, reason=f"exit ({why})",
                          components={"pnl_per_oz": round(pnl, 2)})
