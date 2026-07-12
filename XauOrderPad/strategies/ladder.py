"""Trend-Ladder — DEMO-ONLY. Arm a trigger, pyramid into the move, exit on a retrace.

    Arm    : side + trigger price ("SELL if it goes below 4119")
    Enter  : once triggered, add positions while price keeps moving your way
    Target : each position closes at +`target` $/oz
    Stop   : price retraces `retrace` $/oz from the extreme -> close everything

── Read ../../analysis/TREND_LADDER_STRATEGY.md before touching the defaults ──

That document measures this strategy against 37,500 real ticks. The three findings
that shaped the guards in this file:

  1. The spread is FIXED at 0.24/oz and does not tighten. With no directional
     edge, expectancy is -1 spread per trade, and NO arrangement of target and
     stop escapes it (a full target x trail sweep lands every cell on -0.24).
     => `_spread_guard`: refuse to arm when target <= live spread. A target
        inside the spread cannot win, and the engine must not pretend otherwise.

  2. The ladder is a pure MULTIPLIER, not an edge. Same trigger, same TP, same
     stop -- only the position count changes: 1 pos -$30/ladder, 10 pos -$105.
     Monotonic. Each extra position pays another full spread and buys less
     remaining room.
     => max_positions defaults to 1, and status() warns above that.

  3. The only component that can beat the spread is the operator's discretionary
     trigger -- and that CANNOT be backtested, because only they supply it.
     => `paper` defaults to True. The engine logs the fills it WOULD have got and
        places nothing, so the trigger's edge can be measured before a cent is
        risked. This is the whole point of the engine's first life.

The decision logic lives in `LadderState`, deliberately free of MT5 and of this
class, so the replay harness in analysis/ can run the EXACT code that trades
against historical ticks. If the harness and the engine ever disagree, the engine
has drifted from the model that was validated -- fix the engine, not the doc.
"""

from __future__ import annotations

import logging
import time

import config
from .base import StrategyBase

log = logging.getLogger("XauOrderPad.strategy")


class LadderState:
    """Pure decision logic: ticks in, actions out. No MT5, no I/O, no clock.

    Feed it (bid, ask, t_ms) and it returns a list of actions:
        ("enter", price)          -- open one position at `price`
        ("exit_all", price, why)  -- close everything at `price`

    A SELL enters at the BID (the number on the chart) and exits at the ASK,
    because closing a short means BUYING. That asymmetry is not a modelling
    choice -- it is how the broker fills, and it is the entire cost of the
    strategy. A BUY is the mirror image.
    """

    def __init__(self, side: str, trigger: float, target: float, retrace: float,
                 max_positions: int, entry_mode: str, entry_step: float,
                 entry_gap_ms: int) -> None:
        self.side = side
        self.is_sell = side == "sell"
        self.trigger = float(trigger)
        self.target = float(target)
        self.retrace = float(retrace)
        self.max_positions = int(max_positions)
        self.entry_mode = entry_mode
        self.entry_step = float(entry_step)
        self.entry_gap_ms = int(entry_gap_ms)

        self.armed = False
        self.entries: list[float] = []     # fill prices of OPEN positions
        self.n_taken = 0                   # total ever opened this ladder
        self.extreme: float | None = None  # best price reached (low for sell)
        self._last_entry_ms = 0

    def on_tick(self, bid: float, ask: float, t_ms: int) -> list[tuple]:
        acts: list[tuple] = []
        # The price we ENTER at, and the price we EXIT at. Never the same one.
        entry_px = bid if self.is_sell else ask
        exit_px = ask if self.is_sell else bid
        # "Our way" -- the chart price moving in our favour.
        mark = bid if self.is_sell else ask

        if not self.armed:
            crossed = (mark < self.trigger) if self.is_sell else (mark > self.trigger)
            if not crossed:
                return acts
            self.armed = True
            self.extreme = mark
            acts.append(("enter", entry_px))
            self.entries.append(entry_px)
            self.n_taken += 1
            self._last_entry_ms = t_ms
            return acts

        # track the extreme (lowest bid for a sell, highest ask for a buy)
        if self.extreme is None:
            self.extreme = mark
        elif (mark < self.extreme) if self.is_sell else (mark > self.extreme):
            self.extreme = mark

        # 1) TARGET -- close any position that has reached +target, marked at the
        #    price we would actually get OUT at.
        still = []
        for e in self.entries:
            pl = (e - exit_px) if self.is_sell else (exit_px - e)
            if pl >= self.target:
                acts.append(("exit_one", exit_px, e))
            else:
                still.append(e)
        self.entries = still

        # 2) RETRACE STOP -- the whole ladder, not per position. It trails the
        #    EXTREME, not the entry, so it tightens as the move runs. (Measured:
        #    that makes it ~4x closer than the target, which is why the target is
        #    hit only 1.7% of the time. See the doc.)
        pull = (mark - self.extreme) if self.is_sell else (self.extreme - mark)
        if self.entries and pull >= self.retrace:
            acts.append(("exit_all", exit_px, "retrace"))
            self.entries = []
            return acts

        # 3) ADD -- only while price is still making new ground our way.
        if len(self.entries) < self.max_positions and self.n_taken < self.max_positions:
            if self.entry_mode == "step":
                # Spaced by PRICE: each entry needs room to clear the spread.
                last = self.entries[-1] if self.entries else entry_px
                moved = (last - entry_px) if self.is_sell else (entry_px - last)
                ok = moved >= self.entry_step
            else:
                # Spaced by TIME. Measured median 1s move is 0.044 -- so entries
                # land ~0.04 apart while each needs 0.24 to break even. Available
                # because it was asked for; it is not the default for that reason.
                last = self.entries[-1] if self.entries else entry_px
                better = (entry_px < last) if self.is_sell else (entry_px > last)
                ok = better and (t_ms - self._last_entry_ms) >= self.entry_gap_ms
            if ok:
                acts.append(("enter", entry_px))
                self.entries.append(entry_px)
                self.n_taken += 1
                self._last_entry_ms = t_ms
        return acts

    def rollback_entry(self, price: float) -> None:
        """Undo an entry the broker REFUSED.

        on_tick() appends to `entries` and then emits the "enter" action, so by the
        time the order is actually sent the state already believes it holds the
        position. If order_send then fails -- market closed, no money, bad stops --
        the state is left holding a PHANTOM: an entry with no ticket behind it.

        That is not cosmetic. The phantom consumes a max_positions slot, makes the
        engine "manage" a position that does not exist, and sends the retrace stop
        chasing a ticket the broker has never heard of. The model must match the
        broker, so an order that did not happen must not appear to have happened.
        """
        for i in range(len(self.entries) - 1, -1, -1):
            if abs(self.entries[i] - price) < 1e-9:
                self.entries.pop(i)
                self.n_taken = max(0, self.n_taken - 1)
                return

    @property
    def done(self) -> bool:
        """Ladder is finished: it fired, and nothing is left open."""
        return self.armed and not self.entries and self.n_taken >= 1


class TrendLadder(StrategyBase):
    ID = "ladder"
    NAME = "Trend Ladder"
    needs_hedging = False          # all positions are the same side

    def __init__(self) -> None:
        super().__init__()
        p = dict(config.LADDER_DEFAULTS)
        self.side = str(p["side"])
        self.trigger = float(p["trigger"])
        self.volume = float(p["volume"])
        self.max_positions = int(p["max_positions"])
        self.entry_mode = str(p["entry_mode"])
        self.entry_step = float(p["entry_step"])
        self.entry_gap_ms = int(p["entry_gap_ms"])
        self.target = float(p["target"])
        self.retrace = float(p["retrace"])
        self.hard_sl = float(p["hard_sl"])
        self.max_daily_loss = float(p["max_daily_loss"])
        self.paper = bool(p["paper"])
        # runtime
        self._ladder: LadderState | None = None
        self._tickets: list[int] = []
        self._paper_pl = 0.0            # realized $/oz, paper mode only
        self._paper_trades = 0
        self._ladders_done = 0
        self._last_spread = 0.0
        self._adopt_basis: str | None = None     # how the extreme was reconstructed
        self._adopt_extreme: float | None = None

    # ---- params -----------------------------------------------------------
    def _defaults(self) -> dict:
        return dict(config.LADDER_DEFAULTS)

    def _params(self) -> dict:
        return {"side": self.side, "trigger": self.trigger, "volume": self.volume,
                "max_positions": self.max_positions, "entry_mode": self.entry_mode,
                "entry_step": self.entry_step, "entry_gap_ms": self.entry_gap_ms,
                "target": self.target, "retrace": self.retrace,
                "hard_sl": self.hard_sl, "max_daily_loss": self.max_daily_loss,
                "paper": self.paper}

    def _apply(self, params: dict) -> None:
        for k in ("trigger", "volume", "entry_step", "target", "retrace",
                  "hard_sl", "max_daily_loss"):
            if params.get(k) is not None:
                setattr(self, k, float(params[k]))
        for k in ("max_positions", "entry_gap_ms"):
            if params.get(k) is not None:
                setattr(self, k, int(params[k]))
        if params.get("side") in ("buy", "sell"):
            self.side = params["side"]
        if params.get("entry_mode") in ("step", "timer"):
            self.entry_mode = params["entry_mode"]
        if params.get("paper") is not None:
            self.paper = bool(params["paper"])
        # Changing params mid-ladder would mean a ladder running under two
        # different rules -- and a P&L nobody can attribute. Start fresh instead.
        self._ladder = None

    def _extra_status(self) -> dict:
        warn = None
        if self.max_positions > 1:
            warn = (f"max_positions={self.max_positions}: the ladder MULTIPLIES cost, "
                    f"not edge — each extra position pays another ~{self._last_spread:.2f}/oz "
                    f"spread. Measured: 1 pos −$30/ladder, 10 pos −$105.")
        return {
            "paper": self.paper,
            "open_positions": len(self._ladder.entries) if self._ladder else 0,
            "ladders_done": self._ladders_done,
            "paper_pl_per_oz": round(self._paper_pl, 3),
            "paper_trades": self._paper_trades,
            "spread": round(self._last_spread, 3),
            "warning": warn,
        }

    def _on_killed(self) -> None:
        self._ladder = None
        self._tickets = []

    # ---- crash recovery ---------------------------------------------------
    def _adopt(self, worker, positions: list) -> int:
        """Rebuild the ladder from the broker's open positions after a restart.

        Entries, tickets and side all come straight off the positions. The one thing
        the broker does NOT store is the EXTREME -- the lowest bid (highest ask) the
        ladder reached -- and that is precisely what the retrace stop measures from.
        Lose it and the stop is meaningless.

        So reconstruct it from tick history: the earliest position's open time is when
        this ladder began, and min(bid) over that window IS the extreme, exactly.

        If the ticks are unavailable (weekend, gap, broker returns nothing) fall back
        to the best ENTRY price. Entries are only ever added at a new extreme, so the
        best entry is a genuinely OBSERVED extreme -- not a guess. It is conservative
        (the true extreme can only be further on), and the log says the basis is
        approximate. Never silently invent a stop basis.
        """
        poss = sorted(positions, key=lambda p: int(p.time))
        is_sell = int(poss[0].type) == 1                # MT5: 0=BUY, 1=SELL
        side = "sell" if is_sell else "buy"
        entries = [float(p.price_open) for p in poss]
        # Same {entry, ticket} shape _enter() builds, so exit_one can close a single
        # adopted position by its entry price exactly as it would a live one.
        tickets = [{"entry": float(p.price_open), "ticket": int(p.ticket)} for p in poss]

        # best entry = a real observed extreme (fallback + a floor on the tick scan)
        best_entry = min(entries) if is_sell else max(entries)

        extreme, basis = best_entry, "entries (approximate — no ticks)"
        try:
            ticks = worker.ticks_since(int(poss[0].time))
            if ticks is not None and len(ticks):
                if is_sell:
                    tx = float(min(t["bid"] for t in ticks))
                    extreme, basis = min(tx, best_entry), "ticks"
                else:
                    tx = float(max(t["ask"] for t in ticks))
                    extreme, basis = max(tx, best_entry), "ticks"
        except Exception:
            log.exception("ladder adopt: tick replay failed; using entry-price basis",
                          extra={"event": "ladder_adopt_ticks_failed", "strategy": self.ID})

        self.side = side
        st = LadderState(side, trigger=self.trigger, target=self.target,
                         retrace=self.retrace, max_positions=max(self.max_positions,
                                                                 len(entries)),
                         entry_mode=self.entry_mode, entry_step=self.entry_step,
                         entry_gap_ms=self.entry_gap_ms)
        st.armed = True                 # it already fired -- do not re-trigger
        st.entries = entries
        st.n_taken = len(entries)
        st.extreme = extreme
        self._ladder = st
        self._tickets = tickets
        self._adopt_basis = basis
        self._adopt_extreme = extreme

        log.warning("ladder adopted %d orphaned position(s) from the broker",
                    len(entries),
                    extra={"event": "ladder_adopted", "strategy": self.ID,
                           "side": side, "entries": entries,
                           "tickets": [x["ticket"] for x in tickets],
                           "extreme": round(extreme, 3), "extreme_basis": basis})
        return len(entries)

    def _adopt_detail(self) -> dict:
        return {"extreme": self._adopt_extreme, "extreme_basis": self._adopt_basis}

    # ---- control ----------------------------------------------------------
    def update(self, params: dict | None, enabled: bool | None) -> dict:
        """Enable, then immediately re-check the spread guard.

        Without this the guard only ran on the next worker poll, so POST returned
        `enabled: true, error: null` for a configuration the engine was about to
        refuse ~66 ms later. The caller toasted "enabled" and the UI then silently
        flipped to disabled. An API that reports success for a request it is in the
        middle of rejecting is worse than one that just says no.
        """
        res = super().update(params, enabled)
        if self.enabled and not self._spread_guard(self._last_spread):
            return self.status()          # _spread_guard already disabled + explained
        return res

    # ---- main loop --------------------------------------------------------
    def evaluate(self, worker, st: dict) -> None:
        # Track the spread even while DISABLED. It is the number the operator needs
        # in order to choose a target -- and _tick() (where it used to be captured)
        # never runs until the engine is armed. That left the UI showing a spread of
        # zero right up until the moment it was too late to be useful.
        bid, ask = st.get("bid"), st.get("ask")
        if bid and ask:
            self._last_spread = float(ask) - float(bid)
        super().evaluate(worker, st)

    def _tick(self, worker, st: dict) -> None:
        bid, ask = st.get("bid"), st.get("ask")
        if not bid or not ask:
            self._state = "waiting: no quote"
            return
        spread = float(ask) - float(bid)
        self._last_spread = spread

        # MANAGING an adopted book: work the exits (target + retrace) on the positions
        # we inherited, but open nothing new. Note this runs BEFORE the trigger and
        # spread guards -- those gate NEW entries, and a position already open must be
        # managed regardless of whether a fresh one would be allowed. Bailing out here
        # on "no trigger set" would strand exactly the positions we just rescued.
        if self._managing:
            if self._ladder is None:
                self._clear_managing_if_flat(worker)
                return
            acts = self._ladder.on_tick(float(bid), float(ask), int(time.time() * 1000))
            for a in acts:
                if a[0] == "enter":
                    continue                       # managing-only: never open new risk
                self._do(worker, a)
            self._state = (f"managing {len(self._ladder.entries)} adopted position(s)"
                           if self._ladder.entries else "managing")
            if not self._ladder.entries:
                self._ladder = None
                self._clear_managing_if_flat(worker)
            return

        if self.trigger <= 0:
            self._state = "waiting: no trigger price set"
            return
        # A target inside the spread is not a strategy, it is a fee. Refuse.
        if not self._spread_guard(spread):
            return

        if self._ladder is None:
            self._ladder = LadderState(
                self.side, self.trigger, self.target, self.retrace,
                self.max_positions, self.entry_mode, self.entry_step,
                self.entry_gap_ms)

        acts = self._ladder.on_tick(float(bid), float(ask), int(time.time() * 1000))
        for a in acts:
            self._do(worker, a)

        if self._ladder.done:
            self._ladders_done += 1
            self._ladder = None
            self._state = "armed"
        elif self._ladder.armed:
            self._state = "active" if self._ladder.entries else "armed"
        else:
            self._state = f"armed: waiting for {self.side.upper()} trigger {self.trigger:g}"

    def _spread_guard(self, spread: float) -> bool:
        if self.target > spread:
            return True
        self._disable_with(
            f"refused: target {self.target:.2f}/oz is inside the live spread "
            f"{spread:.2f}/oz — a winning trade would still net "
            f"{self.target - spread:+.2f}/oz. Raise the target above the spread.")
        return False

    # ---- execution --------------------------------------------------------
    def _do(self, worker, act: tuple) -> None:
        kind = act[0]
        if kind == "enter":
            self._enter(worker, act[1])
        elif kind == "exit_one":
            self._exit(worker, act[1], "target", entry=act[2])
        elif kind == "exit_all":
            self._exit(worker, act[1], act[2])

    def _enter(self, worker, price: float) -> None:
        if self.paper:
            self._paper_trades += 1
            log.info("ladder PAPER entry",
                     extra={"event": "ladder_paper_entry", "strategy": self.ID,
                            "side": self.side, "fill": round(price, 3),
                            "n": len(self._ladder.entries)})
            return
        sl = self.hard_sl          # broker-side backstop if this process dies
        r = worker.strategy_place(self.MAGIC, self.side, round(self.volume, 2),
                                  sl, 0.0, comment="XauLadder")
        if not r.get("ok"):
            # The broker said no, so the ladder must un-believe the entry. Leaving it
            # in place would leave the engine managing a position that does not exist.
            if self._ladder is not None:
                self._ladder.rollback_entry(price)
            self._error = f"entry failed: {r.get('error')}"
            log.warning("ladder entry failed — entry rolled back",
                        extra={"event": "ladder_entry_failed", "strategy": self.ID,
                               "error": r.get("error"), "rolled_back": round(price, 3)})
            return
        # Pair the ticket with the price the LadderState thinks it entered at, so a
        # single position hitting its target can be closed on its own. Keyed on the
        # state's price rather than the broker's fill: the state is what emits
        # exit_one, and matching on a slipped fill price would never find the ticket.
        self._tickets.append({"entry": float(price), "ticket": int(r["ticket"])})
        self._error = None
        log.info("ladder entry",
                 extra={"event": "ladder_entry", "strategy": self.ID,
                        "side": self.side, "ticket": r.get("ticket"),
                        "requested": round(price, 3), "fill": r.get("price"),
                        "volume": self.volume})

    def _exit(self, worker, price: float, why: str, entry: float | None = None) -> None:
        if self.paper:
            # Realized P&L in $/oz, using the fill we WOULD have got. This number
            # is the whole point of paper mode: it is what the trigger is worth,
            # net of the spread, before any money is at stake.
            legs = [entry] if entry is not None else list(self._ladder.entries)
            for e in legs:
                pl = (e - price) if self.side == "sell" else (price - e)
                self._paper_pl += pl
                log.info("ladder PAPER exit",
                         extra={"event": "ladder_paper_exit", "strategy": self.ID,
                                "why": why, "entry": round(e, 3),
                                "exit": round(price, 3), "pl_per_oz": round(pl, 3),
                                "cum_pl_per_oz": round(self._paper_pl, 3)})
            return

        # exit_one carries the entry price of the ONE position that hit its target, so
        # close only that one. Closing the whole list here (as this used to) meant the
        # first position to reach its target flattened the entire ladder -- silently
        # throwing away every other position, including ones still running.
        if entry is not None:
            doomed = [x for x in self._tickets if abs(x["entry"] - entry) < 1e-9]
            if not doomed:                       # already gone (broker SL/TP beat us)
                return
        else:
            doomed = list(self._tickets)

        for x in doomed:
            r = worker.strategy_close_ticket(self.MAGIC, x["ticket"])
            ok = bool(r.get("ok"))
            log.info("ladder exit",
                     extra={"event": "ladder_exit", "strategy": self.ID,
                            "why": why, "ticket": x["ticket"],
                            "entry": round(x["entry"], 3), "ok": ok,
                            "error": r.get("error")})
            if ok:
                self._tickets.remove(x)
