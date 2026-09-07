"""Straddle-Ladder — DEMO-ONLY, HEDGING account required.

A SEPARATE engine from `straddle` and `ladder`. It owns magic 532030 and never touches either of
theirs.

── The model ──

You set a **level** by hand (the ladder's UX). The engine opens a **straddle** there -- one long and
one short, both with the SAME bracket, sl/tp $/oz each way -- purely to DETECT direction. With
sl == tp the first tp-sized move resolves it at one price:

    level 4050, sl 2, tp 2:
      long  4050  ->  sl 4048 / tp 4052
      short 4050  ->  sl 4052 / tp 4048
    price 4052 -> long tp (+2) AND short sl (-2)  => UP
    price 4048 -> short tp (+2) AND long  sl (-2) => DOWN

What happens AFTER that first straddle depends on ONE toggle, `always_straddle`:

**`always_straddle = False` (DEFAULT) -- SINGLE-LEG trend continuation.** From the 2nd order on it
takes ONE leg on the trend side (no straddle). Each entry arms at the last SUCCESSFUL take-profit
+/- gap. A **TP advances** the level; an **SL retries the SAME level** (it does not step off the
stop -- it waits for price to come back to the last winning level):

    UP, last TP 4052:
      order2 long 4053 (=4052+gap) -> tp 4055  (last TP 4055)
      order3 long 4056 (=4055+gap) -> tp 4058  (last TP 4058)
      order4 long 4059 (=4058+gap) -> SL 4057  (last TP still 4058)
      order5 long 4059 (=4058+gap, RETRY same) -> tp 4061  (last TP 4061)
      order6 long 4062 (=4061+gap) -> ...

This rides a trend (+tp per order) and, because an entry only fires when price CROSSES the level, a
sustained reversal simply walks price away and it idles -- no bleed.

**`always_straddle = True` -- WALKING STRADDLE GRID.** EVERY entry is a straddle, stepping gap past
each winner's TP; direction is re-detected every step (no locked direction), but each step nets ~0.

Units are $/oz, like the ladder's `target` (2 = $2.00), never MT5 points. `strategy_place` converts
the distance to a price internally.

── Caps / gates ──

`max_legs` caps ENTRIES per run (the initial straddle counts as one, each order as one); at the cap
the engine PARKS + chimes. `max_lots` caps total open lots (a straddle is 2*volume). `max_daily_loss`
is the inherited kill-switch. `allows_real = False` (auto-disables off a demo account, every poll)
and `needs_hedging = True` (the straddle holds both legs; a netting account is refused).

── Pure decision core ──

Every branch lives in `StraddleGridState`, free of MT5/IO/clock, so `analysis/sladder_replay.py`
replays the exact code. The engine (`StraddleLadder`) only does IPC. Each leg carries a real
broker-side SL and TP (like `straddle.py`), so a dead process still has its stops enforced by the
broker. Full model + worked cases: analysis/STRADDLE_LADDER_STRATEGY.md.
"""

from __future__ import annotations

import logging
import time

import config
from .base import StrategyBase

log = logging.getLogger("XauOrderPad.strategy")


class StraddleGridState:
    """Pure decision logic: (bid, ask, open-ticket-set) in, actions out. No MT5, no clock.

    Actions the engine executes:
        ("straddle",)     -> place BOTH legs, then register_straddle(...)
        ("place", side)   -> place ONE continuation leg, then register_cont(...)   (single-leg mode)
        ("close", ticket) -> close that ticket (the losing straddle leg on resolution)

    A tracked leg missing from `open_ids` was CLOSED by the broker (its SL or TP fired) -- that is how
    the state detects resolutions and continuation exits without ever calling MT5.

    Phases: "armed" (waiting for price to reach the level) -> "straddle" (a straddle is open,
    resolving) -> "continuation" (single-leg mode) OR back to "armed" (walking-grid mode) -> "parked"
    (max_legs hit). A leg record is {"role","side","entry","tp_px","sl_px"}; role is "straddle" or "cont".
    """

    def __init__(self, level: float, sl: float, tp: float, gap: float, max_legs: int,
                 max_lots: float, volume: float, always_straddle: bool = False) -> None:
        self.level = float(level)
        self.sl = float(sl)
        self.tp = float(tp)
        self.gap = float(gap)
        self.max_legs = int(max_legs)
        self.max_lots = float(max_lots)
        self.volume = float(volume)
        self.always_straddle = bool(always_straddle)

        self.phase = "armed"
        self.direction: str | None = None
        self.fire_side = "touch"               # how a straddle arms: touch (initial) | up | down (walking)
        self.trigger = 0.0                     # single-leg continuation arm level
        self.n_entries = 0                     # entry events (straddle=1, cont leg=1) vs max_legs
        self.park_reason: str | None = None
        self.legs: dict[int, dict] = {}
        self._ref = float(level)               # the level the OPEN straddle resolves against
        self._rearm_ok = True                  # single-leg crossing latch

    # Wire-compatible: the clients read `legs_taken`; here a "leg" is an entry event.
    @property
    def legs_taken(self) -> int:
        return self.n_entries

    # ---- the tick ---------------------------------------------------------
    def on_tick(self, bid: float, ask: float, open_ids: set[int]) -> list[tuple]:
        acts: list[tuple] = []
        for t in [t for t in self.legs if t not in open_ids]:
            leg = self.legs.pop(t)
            self._on_closed(leg, bid, ask, open_ids, acts)

        if self.phase == "armed":
            self._arm_straddle(bid, ask, acts)
        elif self.phase == "continuation":
            self._continuation(bid, ask, acts)
        return acts

    def _park(self, reason: str) -> None:
        self.phase = "parked"
        self.park_reason = reason

    def _reached(self, bid: float, ask: float) -> bool:
        if self.fire_side == "up":
            return ask >= self.level
        if self.fire_side == "down":
            return bid <= self.level
        return bid <= self.level <= ask        # initial: fire when price touches the level

    def _arm_straddle(self, bid: float, ask: float, acts: list[tuple]) -> None:
        if self.legs or self.level <= 0 or not self._reached(bid, ask):
            return
        if self.max_legs > 0 and self.n_entries >= self.max_legs:
            self._park(f"max_legs {self.max_legs} reached -- set a new level to run the next move")
        elif self.max_lots > 0 and (2.0 * self.volume) > self.max_lots + 1e-9:
            pass                               # a straddle (2 legs) already breaches the lot cap
        else:
            acts.append(("straddle",))

    def _continuation(self, bid: float, ask: float, acts: list[tuple]) -> None:
        if self.legs:
            return                             # SEQUENTIAL: one order at a time
        if self.max_legs > 0 and self.n_entries >= self.max_legs:
            self._park(f"max_legs {self.max_legs} reached -- set a new level to run the next move")
            return
        buy = self.direction == "buy"
        mark = ask if buy else bid             # enter a long at the ask, a short at the bid
        near = (mark < self.trigger) if buy else (mark > self.trigger)
        if not self._rearm_ok and near:
            self._rearm_ok = True
        crossed = (mark >= self.trigger) if buy else (mark <= self.trigger)
        if self._rearm_ok and crossed:
            if self.max_lots > 0 and self.volume > self.max_lots + 1e-9:
                return
            acts.append(("place", self.direction))

    def _on_closed(self, leg: dict, bid: float, ask: float,
                   open_ids: set[int], acts: list[tuple]) -> None:
        if leg["role"] == "straddle":
            if self.phase != "straddle":
                return                         # the winner leg closing after resolution, or a stray
            mark = (bid + ask) / 2.0
            up = mark >= self._ref
            self.direction = "buy" if up else "sell"
            win_tp = (self._ref + self.tp) if up else (self._ref - self.tp)
            # Flatten the LOSING leg (opposite the resolved direction) if still open. The WINNER rides
            # to its own broker TP (that IS its +tp) and its later close is harmlessly ignored.
            for t, other in list(self.legs.items()):
                if other["side"] != self.direction and t in open_ids:
                    acts.append(("close", t))
            if self.always_straddle:
                self.level = (win_tp + self.gap) if up else (win_tp - self.gap)
                self.fire_side = "up" if up else "down"
                self.phase = "armed"
            else:
                self.trigger = (win_tp + self.gap) if up else (win_tp - self.gap)
                self._rearm_ok = True
                self.phase = "continuation"
            return
        # A single-leg (continuation) order closed.
        buy = self.direction == "buy"
        mark = ask if buy else bid
        was_tp = (mark >= leg["entry"]) if buy else (mark <= leg["entry"])
        if was_tp:
            # Advance: arm gap past THIS take-profit -- the new "last successful TP".
            self.trigger = (leg["tp_px"] + self.gap) if buy else (leg["tp_px"] - self.gap)
        else:
            # Retry the SAME level: `entry` == last-successful-TP +/- gap, so we do not step off the
            # stop -- we wait for price to return to the last winning level.
            self.trigger = leg["entry"]
        self._rearm_ok = False

    # ---- engine callbacks (confirmed fills) -------------------------------
    def register_straddle(self, buy_t: int, buy_fill: float,
                          sell_t: int, sell_fill: float) -> None:
        self._ref = self.level
        self.legs[int(buy_t)] = {"role": "straddle", "side": "buy", "entry": float(buy_fill),
                                 "tp_px": buy_fill + self.tp, "sl_px": buy_fill - self.sl}
        self.legs[int(sell_t)] = {"role": "straddle", "side": "sell", "entry": float(sell_fill),
                                  "tp_px": sell_fill - self.tp, "sl_px": sell_fill + self.sl}
        self.phase = "straddle"
        self.n_entries += 1

    def register_cont(self, ticket: int, fill: float) -> None:
        buy = self.direction == "buy"
        self.legs[int(ticket)] = {
            "role": "cont", "side": self.direction, "entry": float(fill),
            "tp_px": fill + self.tp if buy else fill - self.tp,
            "sl_px": fill - self.sl if buy else fill + self.sl,
        }
        self.n_entries += 1
        self._rearm_ok = False

    # ---- display ----------------------------------------------------------
    @property
    def open_lots(self) -> float:
        return round(len(self.legs) * self.volume, 8)

    @property
    def next_level(self) -> float:
        """Where the next entry arms: the continuation trigger, else the (walking/initial) level."""
        return self.trigger if (self.phase == "continuation" and self.trigger) else self.level

    def describe(self) -> str:
        if self.phase == "parked":
            return self.park_reason or "parked: set a new level"
        if self.phase == "straddle":
            return "straddle open, resolving direction"
        if self.phase == "continuation":
            d = (self.direction or "").upper()
            return (f"riding {d}: order {self.n_entries} open" if self.legs
                    else f"riding {d}: {self.n_entries} taken, next {d} at {self.trigger:g}")
        # armed
        if self.n_entries == 0:
            return f"armed: straddle fires when price reaches {self.level:g}"
        d = (self.direction or "").upper()
        return f"walking {d}: {self.n_entries} straddle(s), next at {self.level:g}"


class StraddleLadder(StrategyBase):
    ID = "sladder"
    NAME = "Straddle Ladder"
    needs_hedging = True                        # the opening straddle holds both legs at once

    def __init__(self) -> None:
        super().__init__()
        p = dict(config.STRADDLE_LADDER_DEFAULTS)
        self.level = float(p["level"])
        self.sl = float(p["sl"])
        self.tp = float(p["tp"])
        self.gap = float(p["gap"])
        self.volume = float(p["volume"])
        self.max_legs = int(p["max_legs"])
        self.max_lots = float(p["max_lots"])
        self.cooldown_s = float(p["cooldown_s"])
        self.max_daily_loss = float(p["max_daily_loss"])
        self.always_straddle = bool(p["always_straddle"])
        # runtime
        self._grid: StraddleGridState | None = None
        self._last_spread = 0.0
        self._last_bid: float | None = None
        self._last_ask: float | None = None
        self._last_place_ts = 0.0
        self._needs_attention = False
        self._attention_reason: str | None = None
        self._managing_reason: str | None = None

    # ---- params -----------------------------------------------------------
    def _defaults(self) -> dict:
        return dict(config.STRADDLE_LADDER_DEFAULTS)

    def _params(self) -> dict:
        return {"level": self.level, "sl": self.sl, "tp": self.tp, "gap": self.gap,
                "volume": self.volume, "max_legs": self.max_legs, "max_lots": self.max_lots,
                "cooldown_s": self.cooldown_s, "max_daily_loss": self.max_daily_loss,
                "always_straddle": self.always_straddle}

    def _apply(self, params: dict) -> None:
        for k in ("level", "sl", "tp", "gap", "volume", "max_lots", "cooldown_s",
                  "max_daily_loss"):
            if params.get(k) is not None:
                setattr(self, k, float(params[k]))
        if params.get("max_legs") is not None:
            self.max_legs = int(params["max_legs"])
        if params.get("always_straddle") is not None:
            self.always_straddle = bool(params["always_straddle"])
        # A new run starts fresh. Open legs carry broker SL/TP, so they are handed to managing
        # (finished on their own brackets, no new entries) rather than abandoned.
        if self._grid is not None and self._grid.legs:
            self._managing = True
            self._managing_reason = "params_changed"
            log.info("sladder params changed mid-run -- managing the open leg(s) on their broker "
                     "SL/TP, no new entries", extra={"event": "sladder_params_handover",
                                                     "strategy": self.ID,
                                                     "open": len(self._grid.legs)})
        else:
            self._grid = None

    # ---- status -----------------------------------------------------------
    def _extra_status(self) -> dict:
        guard_ok, guard_reason = self._guard_check(self._last_spread)
        g = self._grid
        phase = g.phase if g else ("disabled" if not self.enabled else "armed")
        would_fire = False
        if self.level > 0 and self._last_bid is not None and self._last_ask is not None:
            would_fire = self._last_bid <= self.level <= self._last_ask
        nxt = g.next_level if g else self.level
        return {
            "phase": phase,
            "direction": (g.direction if g else None),
            "trigger": round(nxt, 3) if nxt else 0.0,
            "next_entry": round(nxt, 3) if nxt else (round(self.level, 3) if self.level else 0.0),
            "legs_taken": (g.n_entries if g else 0),
            "open_legs": (len(g.legs) if g else 0),
            "open_lots": (g.open_lots if g else 0.0),
            "spread": round(self._last_spread, 3),
            "min_stop": round(self._last_spread, 3),
            "guard_ok": guard_ok,
            "guard_reason": guard_reason,
            "would_fire_now": would_fire,
            "managing_reason": (self._managing_reason if self._managing else None),
            "needs_attention": bool(self._needs_attention and self.enabled),
            "attention_reason": (self._attention_reason
                                 if (self._needs_attention and self.enabled) else None),
        }

    def _on_killed(self) -> None:
        self._grid = None

    def _on_flat(self) -> None:
        self._grid = None

    # ---- crash recovery ---------------------------------------------------
    def _adopt(self, worker, positions: list) -> int:
        """Take ownership so adopted legs are MANAGED. Each carries its own broker SL/TP, so the book
        flattens itself; the engine boots DISABLED and opens nothing until the operator arms a fresh
        run. A precise grid cannot be reconstructed from open positions and is not needed."""
        self._managing_reason = "adopted"
        log.warning("sladder adopted %d open position(s) from the broker -- managing exits only",
                    len(positions), extra={"event": "sladder_adopted", "strategy": self.ID,
                                           "count": len(positions)})
        return len(positions)

    def _adopt_detail(self) -> dict:
        return {"open": len(self._grid.legs) if self._grid else 0}

    # ---- control ----------------------------------------------------------
    def update(self, params: dict | None, enabled: bool | None) -> dict:
        if enabled is not None:
            self._needs_attention = False
            self._attention_reason = None
        if params is not None and params.get("level") is not None:
            self._needs_attention = False
            self._attention_reason = None
        res = super().update(params, enabled)
        if self.enabled and not self._param_guard(self._last_spread):
            return self.status()
        return res

    # ---- main loop --------------------------------------------------------
    def evaluate(self, worker, st: dict) -> None:
        bid, ask = st.get("bid"), st.get("ask")
        if bid and ask:
            self._last_spread = float(ask) - float(bid)
            self._last_bid = float(bid)
            self._last_ask = float(ask)
        super().evaluate(worker, st)

    def _tick(self, worker, st: dict) -> None:
        bid, ask = st.get("bid"), st.get("ask")
        if not bid or not ask:
            self._state = "waiting: no quote"
            return
        bid, ask = float(bid), float(ask)
        self._last_spread = ask - bid

        if self._managing:
            self._clear_managing_if_flat(worker)
            if self._managing:
                self._state = ("finishing open leg(s) on the old rules; new level queued"
                               if self._managing_reason == "params_changed"
                               else "managing adopted position(s)")
                return
            self._grid = None
        if not self.enabled:
            self._state = "disabled"
            return

        if self.level <= 0:
            self._state = "waiting: no level set"
            return
        if not self._param_guard(ask - bid):
            return

        if self._grid is None:
            self._grid = StraddleGridState(self.level, self.sl, self.tp, self.gap, self.max_legs,
                                           self.max_lots, self.volume, self.always_straddle)

        open_ids: set[int] = set()
        if self._grid.legs:
            open_ids = {int(p.ticket) for p in self.positions(worker)}

        for a in self._grid.on_tick(bid, ask, open_ids):
            self._do(worker, a)

        if self._grid.phase == "parked" and not self._needs_attention:
            self._needs_attention = True
            self._attention_reason = self._grid.park_reason
            log.info("sladder parked -- awaiting a new level",
                     extra={"event": "sladder_needs_attention", "strategy": self.ID,
                            "reason": self._grid.park_reason})
        self._state = self._grid.describe()

    # ---- execution --------------------------------------------------------
    def _do(self, worker, act: tuple) -> None:
        kind = act[0]
        if kind == "straddle":
            self._place_straddle(worker)
        elif kind == "place":
            self._place_cont(worker, act[1])
        elif kind == "close":
            self._close(worker, int(act[1]), "resolve-loser")

    def _cooldown_left(self) -> float:
        """Seconds still to wait before the next RE-ENTRY (the first entry of a run never waits)."""
        if self._grid.n_entries > 0 and self.cooldown_s > 0 and self._last_place_ts:
            return self.cooldown_s - (time.time() - self._last_place_ts)
        return 0.0

    def _place_straddle(self, worker) -> None:
        left = self._cooldown_left()
        if left > 0:
            self._state = f"cooldown: {left:.0f}s"
            return
        vol = round(self.volume, 2)
        buy = worker.strategy_place(self.MAGIC, "buy", vol, self.sl, self.tp, comment="XauSLadder")
        if not buy.get("ok"):
            self._error = f"straddle abort: buy leg failed ({buy.get('error')})"
            log.warning("sladder buy leg failed",
                        extra={"event": "sladder_leg_failed", "strategy": self.ID,
                               "leg": "buy", "error": buy.get("error")})
            return
        sell = worker.strategy_place(self.MAGIC, "sell", vol, self.sl, self.tp, comment="XauSLadder")
        if not sell.get("ok"):
            worker.strategy_close_ticket(self.MAGIC, int(buy["ticket"]))
            self._error = f"straddle abort: sell leg failed ({sell.get('error')}); buy leg closed"
            log.warning("sladder sell leg failed -- buy leg closed",
                        extra={"event": "sladder_leg_failed", "strategy": self.ID,
                               "leg": "sell", "error": sell.get("error"),
                               "buy_ticket": buy.get("ticket")})
            return
        self._grid.register_straddle(int(buy["ticket"]), float(buy.get("price") or self.level),
                                     int(sell["ticket"]), float(sell.get("price") or self.level))
        self._last_place_ts = time.time()
        self._needs_attention = False
        self._attention_reason = None
        self._error = None
        log.info("sladder straddle opened",
                 extra={"event": "sladder_straddle_opened", "strategy": self.ID,
                        "n": self._grid.n_entries, "level": round(self._grid.level, 3),
                        "sl": self.sl, "tp": self.tp,
                        "buy_ticket": buy.get("ticket"), "sell_ticket": sell.get("ticket")})

    def _place_cont(self, worker, side: str) -> None:
        left = self._cooldown_left()
        if left > 0:
            self._state = f"cooldown: {left:.0f}s"
            return
        vol = round(self.volume, 2)
        r = worker.strategy_place(self.MAGIC, side, vol, self.sl, self.tp, comment="XauSLadder")
        if not r.get("ok"):
            self._error = f"continuation entry failed: {r.get('error')}"
            log.warning("sladder continuation entry failed",
                        extra={"event": "sladder_entry_failed", "strategy": self.ID,
                               "side": side, "error": r.get("error")})
            return
        fill = float(r.get("price") or self._grid.trigger)
        self._grid.register_cont(int(r["ticket"]), fill)
        self._last_place_ts = time.time()
        self._needs_attention = False
        self._attention_reason = None
        self._error = None
        log.info("sladder continuation entry",
                 extra={"event": "sladder_entry", "strategy": self.ID, "side": side,
                        "ticket": r.get("ticket"), "fill": round(fill, 3),
                        "n": self._grid.n_entries, "volume": vol})

    def _close(self, worker, ticket: int, why: str) -> None:
        r = worker.strategy_close_ticket(self.MAGIC, ticket)
        ok = bool(r.get("ok"))
        err = (r.get("error") or "")
        gone = ok or "not found" in err.lower()
        log.info("sladder close",
                 extra={"event": "sladder_close", "strategy": self.ID, "why": why,
                        "ticket": ticket, "ok": ok, "error": r.get("error"),
                        "already_closed": (not ok) and gone})

    # ---- guards -----------------------------------------------------------
    def _guard_check(self, spread: float) -> tuple[bool, str | None]:
        """Would the current bracket be REFUSED at this spread? Pure -- mutates nothing. Same shape
        and one-source-of-truth reason as the ladder's `_guard_check`, so both clients grey out ARM
        with the exact sentence the engine would raise, before posting."""
        if self.tp <= spread:
            return False, (
                f"refused: tp {self.tp:.2f}/oz is inside the live spread {spread:.2f}/oz -- a "
                f"winning leg would still net {self.tp - spread:+.2f}/oz. Raise tp above the spread.")
        if self.sl <= spread:
            return False, (
                f"refused: sl {self.sl:.2f}/oz is inside the live spread {spread:.2f}/oz -- every "
                f"leg would stop out on the first tick. Raise sl above the spread.")
        return True, None

    def _param_guard(self, spread: float) -> bool:
        """The side-effecting half: disable + record the reason. Re-checked EVERY poll so a spread
        that widens past a dialled bracket disarms a RUNNING grid, not only blocks a fresh arm."""
        ok, reason = self._guard_check(spread)
        if not ok:
            self._disable_with(reason)
        return ok
