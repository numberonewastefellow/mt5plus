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
              lot_min: float = 0.01, lot_max: float = 1.0) -> float:
    """Fixed-fractional (Kelly-small): risk `risk_frac` of equity on the stop.
    lot = risk_frac*equity / (sl$ * contract). Floored at the broker minimum."""
    raw = cfg.risk_frac * equity / (cfg.sl * contract)
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

        # 1) fill a pending thrust at THIS bar's open
        if self.pending_dir != 0 and not self.in_pos:
            d = self.pending_dir; self.pending_dir = 0
            lot = kelly_lot(equity, cfg)
            self.in_pos = True; self.dirn = d; self.side = "buy" if d > 0 else "sell"
            self.entry = o; self.best = o; self.bars_held = 0; self.n_trades += 1
            self.stop = o - d * cfg.sl; self.tp = o + d * cfg.tp
            acts.append(Suggestion("enter", side=self.side, lot=lot, entry_ref=o,
                                   sl=self.stop, tp=self.tp,
                                   reason=f"{self.side.upper()} thrust in high-vol regime",
                                   components={"stop_dist": cfg.sl, "trail": cfg.trail}))

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
                    if cfg.trail > 0:
                        self.stop = max(self.stop, self.best - cfg.trail)
            else:
                if h >= self.stop:
                    acts.append(self._close("stop", self.stop)); exited = True
                elif l <= self.tp:
                    acts.append(self._close("target", self.tp)); exited = True
                else:
                    self.best = min(self.best, l)
                    if cfg.trail > 0:
                        self.stop = min(self.stop, self.best + cfg.trail)
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
