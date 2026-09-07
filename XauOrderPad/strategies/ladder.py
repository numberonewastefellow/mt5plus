"""Trend-Ladder — DEMO-ONLY. Arm a trigger, pyramid into the move, exit on a stop.

    Arm    : side + trigger price ("SELL if it goes below 4119")
    Enter  : once triggered, add positions while price keeps moving your way
    Target : each position closes at +`target` $/oz
    Stop   : `stop_mode` decides — trail the extreme, or hold a floor at the trigger

── Read ../../analysis/TREND_LADDER_STRATEGY.md before touching the defaults ──

That document measures this strategy against 37,500 real ticks. The three findings
that shaped the guards in this file:

  1. The spread is FIXED at 0.24/oz and does not tighten. With no directional
     edge, expectancy is -1 spread per trade, and NO arrangement of target and
     stop escapes it (a full target x trail sweep lands every cell on -0.24).
     => `_param_guard`: refuse to arm when a distance that MUST be crossed to win
        is inside the live spread. Such a setting cannot win, and the engine must
        not pretend otherwise.

  2. The ladder is a pure MULTIPLIER, not an edge. Same trigger, same TP, same
     stop -- only the position count changes: 1 pos -$30/ladder, 10 pos -$105.
     Monotonic. Each extra position pays another full spread and buys less
     remaining room.
     => max_positions defaults to 1, and status() warns above that.

  3. The only component that can beat the spread is the operator's discretionary
     trigger -- and that CANNOT be backtested, because only they supply it.
     => `paper` mode exists: the engine logs the fills it WOULD have got and places
        nothing, so the trigger's edge can be measured before a cent is risked. That
        was the whole point of the engine's first life, and it is still how you cost
        a new trigger. It is no longer the DEFAULT, though -- see config.py. The
        thing that keeps this engine off real money is `allows_real = False`, which
        base.py re-checks on every poll; paper mode was never that guard.

── And one finding this file learned the hard way, in production ──

Run of 2026-07-21, live on demo 472200942, side=buy trigger=4071.60 target=1.00
retrace=0.30 max_positions=1: **68 ladders and 79 closed trades in ~25 minutes**,
net -19.13, with the `target` hit exactly ZERO times.

Two separate mistakes, both now fixed here, both worth understanding before editing:

  * **The trigger was never CONSUMED.** Finding 3 above describes a *one-shot
    discretionary call*. What was built was a standing price level: the ladder
    completed, `_ladder` was set to None, and 66 ms later a fresh one armed against
    the same still-crossed trigger and bought again. The discretionary edge is spent
    on ladder #1; ladders #2..#68 are machine re-entries at a level price happens to
    be oscillating around, which is precisely the thing finding 3 says has no edge.
    => a crossing is now an EVENT: `_rearm_ok` latches off when a ladder ends and
       only latches back on once price has traded back THROUGH the trigger. Plus
       `cooldown_s` and `max_ladders_per_day`.

  * **The target was unreachable and nothing said so.** A target is reachable only
    if price can run `target` WITHOUT first pulling back `retrace`. 1.00 behind a
    0.30 trail is not a strategy, it is a decoration -- but the old guard only
    compared the target to the SPREAD, so it passed the config happily.
    => `_param_guard` refuses what is arithmetically impossible and WARNS about what
       is merely very unlikely. The two are different and are treated differently.

── How it rides a trend: stack, park, and let the human re-load ──

Uncap it (`max_positions = 0` + a real `max_lots`) and one ladder pyramids into the
move, one rung per `entry_step` on each new extreme, exiting on the trailing stop.
When that stop fires the run is DONE, and what happens next is set by `auto_continue`:

  * ONE-SHOT (default) -- the engine PARKS and latches `_needs_attention`; the clients
    alert the operator to SET A NEW LEVEL. A bare re-cross does NOT re-arm. Re-levelling
    resets `_rearm_ok` and the cooldown, so it re-arms from the new price immediately.
    Safe, and matches the operator's workflow (ARM stays on, they set levels).

  * AUTO-CONTINUE (opt-in) -- after the trail banks a run, it keeps taking runs WHILE
    price is still past the trigger, and stops only when price RETURNS to the trigger
    (then it parks + alerts). This is the trend mode; in a chop it churns, so it is
    off by default and throttled by `cooldown_s` / `max_ladders_per_day`.

Either way the engine never opens NEW risk on a mere price wiggle without a rule the
operator chose. `would_fire_now` warns when the current trigger is already on the
crossed side (it would arm instantly, not wait for the move).

The decision logic lives in `LadderState`, deliberately free of MT5 and of this
class, so the replay harness in analysis/ can run the EXACT code that trades
against historical ticks. If the harness and the engine ever disagree, the engine
has drifted from the model that was validated -- fix the engine, not the doc.
"""

from __future__ import annotations

import datetime
import logging
import time

import config
from . import state
from .base import StrategyBase

log = logging.getLogger("XauOrderPad.strategy")


class LadderState:
    """Pure decision logic: ticks in, actions out. No MT5, no I/O, no clock.

    Feed it (bid, ask, t_ms) and it returns a list of actions:
        ("enter", rid, price)                 -- open one position, tagged `rid`
        ("exit_one", rid, exit_px, entry_px)  -- close the ONE position tagged `rid`
        ("exit_all", exit_px, why, rungs)     -- close everything; rungs = [(rid, entry)]

    Every exit action carries the ENTRY price of what it is closing. It has to: the
    rung is removed from `entries` at the moment the action is emitted, so a consumer
    that tried to look the entry up afterwards would find nothing. Paper mode did
    exactly that and silently booked zero P&L for every stop-out -- the one number
    paper mode exists to produce.

    A SELL enters at the BID (the number on the chart) and exits at the ASK,
    because closing a short means BUYING. That asymmetry is not a modelling
    choice -- it is how the broker fills, and it is the entire cost of the
    strategy. A BUY is the mirror image.

    Every rung carries an integer `rid` that is unique for the life of the ladder.
    Rungs used to be identified by their entry PRICE, which worked only while
    `max_positions` was 1: uncapped pyramiding at 5 entries/second produces
    duplicate prices within seconds, and a price-keyed lookup then closes the wrong
    position -- or, worse, reports success having closed nothing.
    """

    def __init__(self, side: str, trigger: float, target: float,
                 max_positions: int, max_lots: float, volume: float,
                 entry_mode: str, entry_step: float, entry_gap_ms: int,
                 stop_mode: str = "retrace", retrace: float = 0.30,
                 floor_offset: float = 0.0, trail_activate: float = 0.0) -> None:
        self.side = side
        self.is_sell = side == "sell"
        self.trigger = float(trigger)
        self.target = float(target)
        # 0 means UNCAPPED for both caps. They are independent: whichever binds
        # first stops the pyramid, so an operator can cap by count, by size, by
        # both, or by neither.
        self.max_positions = int(max_positions)
        self.max_lots = float(max_lots)
        self.volume = float(volume)
        self.entry_mode = entry_mode
        self.entry_step = float(entry_step)
        self.entry_gap_ms = int(entry_gap_ms)
        self.stop_mode = stop_mode
        self.retrace = float(retrace)
        self.floor_offset = float(floor_offset)
        # trail_activate -- the profit gate for the retrace trail; picks between the two
        # trailing-stop models.
        #   PURPOSE : decide WHEN the `retrace` trail is allowed to start closing the ladder.
        #   MEANING : a $/oz distance -- how far in PROFIT (favourable move measured from the first
        #             entry, `_arm_price`) the run must get before the trail goes live.
        #       0.0  (default) = TRAIL-FROM-ENTRY: the trail is live from the first tick, so a dip
        #                        right after entry can close the ladder BELOW entry, at a loss
        #                        (a "chandelier" stop). Kept as the default so behaviour is unchanged.
        #       >= retrace    = ACTIVATE-IN-PROFIT (the standard MT5 "Trailing Stop"): inert until
        #                        +trail_activate, so its first stop lands at breakeven and it never
        #                        books a directional loss; only the broker `hard_sl` protects until
        #                        then. As high as `target` means "run to +X, THEN trail".
        #   USED BY : on_tick (arms `_trail_armed` once the favourable excursion reaches it) and
        #             `_stop_hit` (refuses to fire the retrace stop until armed). `retrace` mode
        #             ONLY -- floor mode has no trail to gate and ignores this value entirely.
        self.trail_activate = float(trail_activate)

        self.armed = False
        self.entries: list[tuple[int, float]] = []   # (rid, fill price) of OPEN rungs
        self.n_taken = 0                   # total ever opened this ladder
        self.extreme: float | None = None  # best price reached (low for sell)
        self._next_rid = 0
        self._last_entry_ms = 0
        # Trail-activation state (implements `trail_activate`, above): "the trail only starts
        # working once the trade is far enough ahead".
        #   _arm_price -- PURPOSE: the fixed price the activation profit is measured FROM.
        #        MEANING: the run's FIRST entry, on the entry-side series (ask for a buy, bid for a
        #        sell); None until the ladder arms. It is the WHOLE run's profit origin and is NOT
        #        re-pointed as later rungs are added -- one run, one basis. USED BY: on_tick, where
        #        profit = (extreme - _arm_price) for a buy, (_arm_price - extreme) for a sell.
        #   _trail_armed -- PURPOSE: the latch that says the retrace trail is now live.
        #        MEANING: False while the run has made < trail_activate of profit; flips True once it
        #        has, and never back within a ladder. Starts True when trail_activate <= 0 (classic
        #        trail-from-entry -- live from the first tick). USED BY: `_stop_hit` (the retrace
        #        stop is a no-op until this is True) and surfaced to the clients as status
        #        `trail_armed` so they can show "trail waiting" vs "trail active".
        self._arm_price: float | None = None
        self._trail_armed = float(trail_activate) <= 0.0

    # ---- geometry ---------------------------------------------------------
    @property
    def open_lots(self) -> float:
        return round(len(self.entries) * self.volume, 8)

    @property
    def floor(self) -> float:
        """The price at which floor mode gives up, on the SAME series as the trigger.

        The operator names a level off the chart ("buy above 4071.60"), so the level
        that cancels it has to be read off that same number -- not off the other side
        of the spread. Realising the exit still costs the spread on top; that is
        surfaced as `effective_stop` rather than hidden inside this number.
        """
        return (self.trigger + self.floor_offset) if self.is_sell \
            else (self.trigger - self.floor_offset)

    def _room(self) -> bool:
        """Is there space for one more rung under BOTH caps?"""
        if self.max_positions > 0 and len(self.entries) >= self.max_positions:
            return False
        if self.max_lots > 0 and (len(self.entries) + 1) * self.volume > self.max_lots + 1e-9:
            return False
        return True

    def _add(self, price: float, t_ms: int) -> tuple:
        rid = self._next_rid
        self._next_rid += 1
        self.entries.append((rid, price))
        self.n_taken += 1
        self._last_entry_ms = t_ms
        return ("enter", rid, price)

    # ---- the loop ---------------------------------------------------------
    def on_tick(self, bid: float, ask: float, t_ms: int) -> list[tuple]:
        acts: list[tuple] = []
        # The price we ENTER at, and the price we EXIT at. Never the same one.
        entry_px = bid if self.is_sell else ask
        exit_px = ask if self.is_sell else bid
        # "Our way" -- the chart price moving in our favour. This is the series the
        # operator set the trigger against, so it is the series that arms us and the
        # series the floor is measured on.
        mark = bid if self.is_sell else ask

        if not self.armed:
            crossed = (mark < self.trigger) if self.is_sell else (mark > self.trigger)
            if not crossed:
                return acts
            self.armed = True
            self.extreme = mark
            self._arm_price = entry_px          # profit basis for the trail's activation
            acts.append(self._add(entry_px, t_ms))
            return acts

        # track the extreme (lowest bid for a sell, highest ask for a buy)
        if self.extreme is None:
            self.extreme = mark
        elif (mark < self.extreme) if self.is_sell else (mark > self.extreme):
            self.extreme = mark

        # Arm the trail once the run has been up by `trail_activate`. Until then the retrace
        # stop is inert (only the broker hard_sl protects), so a dip straight after entry does
        # not close the trade at a loss -- the standard "trailing stop activates in profit".
        if not self._trail_armed and self._arm_price is not None:
            profit = (self._arm_price - self.extreme) if self.is_sell \
                else (self.extreme - self._arm_price)
            if profit >= self.trail_activate:
                self._trail_armed = True

        # 1) TARGET -- close any rung that has reached +target, marked at the price
        #    we would actually get OUT at. Runs before the stop so that on a tick
        #    where both fire, the winner is booked rather than swept up in the flush.
        still = []
        for rid, e in self.entries:
            pl = (e - exit_px) if self.is_sell else (exit_px - e)
            if pl >= self.target:
                acts.append(("exit_one", rid, exit_px, e))
            else:
                still.append((rid, e))
        self.entries = still

        # 2) STOP -- whole-ladder, never per rung.
        if self.entries and self._stop_hit(mark):
            acts.append(("exit_all", exit_px, self.stop_mode, list(self.entries)))
            self.entries = []
            return acts

        # 3) ADD -- only while price is still making new ground our way.
        if self._room():
            last = self.entries[-1][1] if self.entries else entry_px
            if self.entry_mode == "step":
                # Spaced by PRICE: each entry needs room to clear the spread.
                moved = (last - entry_px) if self.is_sell else (entry_px - last)
                ok = moved >= self.entry_step
            else:
                # Spaced by TIME, and only ever at a BETTER price than the last rung
                # -- that "better" test is what makes timer mode mean "keep adding
                # while it runs" rather than "keep adding". Measured median 1s move
                # is 0.044, so entries land ~0.04 apart while each needs a full
                # spread to break even; that is the cost of the cadence, not a bug.
                better = (entry_px < last) if self.is_sell else (entry_px > last)
                ok = better and (t_ms - self._last_entry_ms) >= self.entry_gap_ms
            if ok:
                acts.append(self._add(entry_px, t_ms))
        return acts

    def _stop_hit(self, mark: float) -> bool:
        """Has the ladder-wide stop been breached, on the entry-side price series?

        `floor`   -- one fixed level derived from the trigger. It does NOT move, so
                     the ladder gets the whole distance it has travelled to work in,
                     and is flattened only if price returns to where it was armed.
        `retrace` -- trails the EXTREME, not the entry, so it tightens as the move
                     runs. Measured: that makes it ~4x closer than the target, which
                     is why the target is hit only 1.7% of the time. See the doc.
                     Inert until `_trail_armed` (see `trail_activate`): before the run is
                     up by that much, only the broker hard_sl protects, so the trail can
                     be made to arm at breakeven and never close a loser.
        """
        if self.stop_mode == "floor":
            return (mark >= self.floor) if self.is_sell else (mark <= self.floor)
        if not self._trail_armed:
            return False
        pull = (mark - self.extreme) if self.is_sell else (self.extreme - mark)
        return pull >= self.retrace

    # ---- reconciliation with the broker -----------------------------------
    def rollback_entry(self, rid: int) -> None:
        """Undo an entry the broker REFUSED.

        on_tick() appends the rung and then emits the "enter" action, so by the time
        the order is actually sent the state already believes it holds the position.
        If order_send then fails -- market closed, no money, bad stops -- the state
        is left holding a PHANTOM: a rung with no ticket behind it.

        That is not cosmetic. The phantom consumes a cap slot, makes the engine
        "manage" a position that does not exist, and sends the stop chasing a ticket
        the broker has never heard of. The model must match the broker, so an order
        that did not happen must not appear to have happened.
        """
        self.drop(rid)
        self.n_taken = max(0, self.n_taken - 1)

    def drop(self, rid: int) -> None:
        """Forget one rung -- it is closed, or it never existed."""
        self.entries = [(r, p) for (r, p) in self.entries if r != rid]

    @property
    def done(self) -> bool:
        """Ladder is finished: it fired, and nothing is left open."""
        return self.armed and not self.entries and self.n_taken >= 1


class TrendLadder(StrategyBase):
    ID = "ladder"
    NAME = "Trend Ladder"
    # Every rung is a SEPARATE position with its own entry, ticket and target. A
    # netting account collapses them into one aggregate line, which would make the
    # rung->ticket map fiction and `exit_one` meaningless. This mattered little while
    # max_positions was 1; it is fatal once the pyramid is uncapped.
    needs_hedging = True

    # `paper` used to live here, so that a restart always came back SIMULATING. That was
    # right while `config.LADDER_DEFAULTS["paper"]` was True: NEVER_RESTORE means "ignore
    # what was saved and fall back to the constructor default", so the fallback WAS the
    # safe side. The default is now False (see the note in config.py), which silently
    # inverts it -- an operator who deliberately switched paper ON to measure a trigger
    # would have found the engine placing orders again after the next restart, having
    # changed nothing. A restart must not be able to overrule a deliberate choice in the
    # dangerous direction, so the saved value is honoured.
    #
    # What actually stops a crash-restart loop from trading unattended is `enabled`, which
    # base.py refuses to restore under any circumstance ("was enabled before the restart --
    # NOT re-armed"). Arming stays a human act; this flag only decides what arming means.
    NEVER_RESTORE = frozenset()

    def __init__(self) -> None:
        super().__init__()
        p = dict(config.LADDER_DEFAULTS)
        self.side = str(p["side"])
        self.trigger = float(p["trigger"])
        self.volume = float(p["volume"])
        self.max_positions = int(p["max_positions"])
        self.max_lots = float(p["max_lots"])
        self.entry_mode = str(p["entry_mode"])
        self.entry_step = float(p["entry_step"])
        self.entry_gap_ms = int(p["entry_gap_ms"])
        self.target = float(p["target"])
        self.stop_mode = str(p["stop_mode"])
        self.retrace = float(p["retrace"])
        self.floor_offset = float(p["floor_offset"])
        # $/oz of profit before the retrace trail arms; 0 = trail-from-entry (can lose), >= retrace
        # = activate-in-profit (never a directional loss). Passed straight into each LadderState,
        # which owns the mechanic. Full model: LadderState.__init__ and config.LADDER_DEFAULTS.
        self.trail_activate = float(p["trail_activate"])
        self.hard_sl = float(p["hard_sl"])
        self.max_daily_loss = float(p["max_daily_loss"])
        self.cooldown_s = float(p["cooldown_s"])
        self.max_ladders_per_day = int(p["max_ladders_per_day"])
        self.close_batch = int(p["close_batch"])
        self.paper = bool(p["paper"])
        # Re-arm policy after a run ends. False (default) = ONE-SHOT: the engine parks and
        # waits for the operator to SET LEVEL again. True = AUTO-CONTINUE: after the stop
        # closes a run, keep taking runs WHILE price is still past the trigger, and stop only
        # when price returns to the trigger. Opt-in because in a chop (not a trend) it churns.
        self.auto_continue = bool(p["auto_continue"])
        # runtime
        self._ladder: LadderState | None = None
        self._tickets: dict[int, int] = {}       # rung id -> broker ticket
        self._flush: list[int] = []              # tickets still to close, batched
        self._flush_why = ""
        self._paper_pl = 0.0            # realized $/oz, paper mode only
        self._paper_trades = 0
        self._ladders_done = 0
        self._last_spread = 0.0
        # D1: a trigger CROSSING is an event, not a state. Enabling the engine is the
        # operator's discretionary act, so the first ladder may fire immediately; every
        # later one waits for price to trade back through the trigger.
        self._rearm_ok = True
        self._last_ladder_end = 0.0
        self._ladders_today = 0
        self._today = None
        self._managing_check = 0.0      # last broker re-check while managing (~1 Hz)
        self._runtime_saved = 0.0       # last throttled persist of the daily counters
        self._adopt_basis: str | None = None     # how the extreme was reconstructed
        self._adopt_extreme: float | None = None
        # Manual-reload UX. A ladder rides ONE leg, flushes, and PARKS -- it does not
        # re-enter itself (that would be unattended new risk). Instead it latches
        # `_needs_attention` so both clients can alert the operator to set a new level.
        # Cleared when they re-level, toggle the engine, or it re-arms and takes an entry.
        self._needs_attention = False
        self._attention_reason: str | None = None
        # Last quote seen, kept so status() can answer "would a fresh ladder fire NOW?"
        # even between polls -- the trap where a new level on the already-crossed side
        # arms instantly instead of waiting for the move.
        self._last_bid: float | None = None
        self._last_ask: float | None = None
        # Why the engine is in managing mode, when it is: "adopted" (rebuilt from the broker after
        # a restart) or "params_changed" (a mid-ladder edit handed the open book over). Surfaced in
        # status so the clients can word the two cases differently -- crash recovery vs a routine
        # "your new level is queued behind the open book". Only meaningful while `_managing`.
        self._managing_reason: str | None = None

    # ---- params -----------------------------------------------------------
    def _defaults(self) -> dict:
        return dict(config.LADDER_DEFAULTS)

    def _params(self) -> dict:
        return {"side": self.side, "trigger": self.trigger, "volume": self.volume,
                "max_positions": self.max_positions, "max_lots": self.max_lots,
                "entry_mode": self.entry_mode,
                "entry_step": self.entry_step, "entry_gap_ms": self.entry_gap_ms,
                "target": self.target, "stop_mode": self.stop_mode,
                "retrace": self.retrace, "floor_offset": self.floor_offset,
                "trail_activate": self.trail_activate,
                "hard_sl": self.hard_sl, "max_daily_loss": self.max_daily_loss,
                "cooldown_s": self.cooldown_s,
                "max_ladders_per_day": self.max_ladders_per_day,
                "close_batch": self.close_batch, "paper": self.paper,
                "auto_continue": self.auto_continue}

    def _apply(self, params: dict) -> None:
        for k in ("trigger", "volume", "entry_step", "target", "retrace",
                  "floor_offset", "trail_activate", "max_lots", "hard_sl",
                  "max_daily_loss", "cooldown_s"):
            if params.get(k) is not None:
                setattr(self, k, float(params[k]))
        for k in ("max_positions", "entry_gap_ms", "max_ladders_per_day",
                  "close_batch"):
            if params.get(k) is not None:
                setattr(self, k, int(params[k]))
        if params.get("side") in ("buy", "sell"):
            self.side = params["side"]
        if params.get("entry_mode") in ("step", "timer"):
            self.entry_mode = params["entry_mode"]
        if params.get("stop_mode") in ("retrace", "floor"):
            self.stop_mode = params["stop_mode"]
        if params.get("paper") is not None:
            self.paper = bool(params["paper"])
        if params.get("auto_continue") is not None:
            self.auto_continue = bool(params["auto_continue"])
        # Never below the floor. entry_gap_ms is the ONLY thing bounding how many
        # order_send round trips land on the worker thread, and each one is
        # synchronous inside a ~66 ms poll budget. At 0 the cadence becomes one order
        # PER POLL -- 15/sec -- which starves prices, the P&L guard and the manual
        # order pad, including the very poll that would have decided to stop.
        self.entry_gap_ms = max(int(config.ENTRY_GAP_MS_MIN), int(self.entry_gap_ms))
        self.close_batch = max(1, int(self.close_batch))
        # Changing params mid-ladder would mean ONE ladder running under two different
        # rules -- and a P&L nobody can attribute. So the next ladder starts fresh.
        #
        # But the positions already open are REAL. This used to drop the LadderState
        # while leaving `_tickets` populated, which meant the next poll armed a brand
        # new ladder and bought AGAIN: two live positions, engine believing one, and
        # the original left with nothing watching its exit.
        #
        # So an open book is HANDED OVER rather than abandoned. The existing
        # LadderState is kept -- it holds the rungs and the extreme, and it carries its
        # own copies of the parameters, so the open book finishes under exactly the
        # rules it was opened under. `_managing` blocks every new entry until it has
        # drained; only then does a fresh ladder start, under the new rules.
        if self._ladder is not None and self._ladder.entries:
            self._managing = True
            self._managing_reason = "params_changed"
            log.info("ladder params changed mid-ladder -- managing the open book "
                     "under its original rules, no new entries",
                     extra={"event": "ladder_params_handover", "strategy": self.ID,
                            "open": len(self._ladder.entries)})
        else:
            self._ladder = None
        self._rearm_ok = True

    def _extra_status(self) -> dict:
        warn = None
        if self.max_positions > 1 or self.max_positions == 0:
            cap = "UNCAPPED" if self.max_positions == 0 else str(self.max_positions)
            warn = (f"max_positions={cap}: the ladder MULTIPLIES cost, not edge -- each "
                    f"extra position pays another ~{self._last_spread:.2f}/oz spread. "
                    f"Measured: 1 pos -$30/ladder, 10 pos -$105.")
            if self.max_positions == 0 and self.max_lots <= 0:
                warn += (" With max_lots=0 as well, nothing bounds the book: a slow grind "
                         "at this cadence accumulates rungs faster than the target drains "
                         "them. Set max_lots before running this live.")
        if self.stop_mode == "retrace" and self.target > self.retrace * 2.5:
            warn = ((warn + " ") if warn else "") + (
                f"target {self.target:.2f} sits behind a {self.retrace:.2f} trail: price must "
                f"run the full target without ever pulling back {self.retrace:.2f}. Measured "
                f"live 2026-07-21: 0 target hits in 79 trades.")
        open_n = len(self._ladder.entries) if self._ladder else 0
        # The SAME verdict `_param_guard` would raise, computed here without side effects so a
        # client can grey out ARM with the real sentence BEFORE posting -- instead of posting,
        # being refused, and reading the reason back as an error. `min_stop` is the floor both the
        # trail (retrace/floor_offset) and the target must clear: the live spread. Below it a trade
        # cannot win, which is exactly what the guard refuses.
        guard_ok, guard_reason = self._guard_check(self._last_spread)
        # "Would a fresh ladder fire on the very next tick, given this trigger and the
        # last quote?" -- true when the trigger is already on the crossed side. This is
        # the trap the operator has to be warned about: a SELL level set ABOVE the bid
        # (or a BUY level BELOW the ask) does not wait for the move, it arms instantly.
        # Same condition `LadderState.on_tick` arms on: sell crosses when bid < trigger,
        # buy when ask > trigger.
        would_fire = False
        if self.trigger > 0 and self._last_bid is not None and self._last_ask is not None:
            would_fire = (self._last_bid < self.trigger) if self.side == "sell" \
                else (self._last_ask > self.trigger)
        return {
            "paper": self.paper,
            "open_positions": open_n,
            "open_lots": round(open_n * self.volume, 8),
            "ladders_done": self._ladders_done,
            "ladders_today": self._ladders_today,
            "paper_pl_per_oz": round(self._paper_pl, 3),
            "paper_trades": self._paper_trades,
            "spread": round(self._last_spread, 3),
            "min_stop": round(self._last_spread, 3),
            "guard_ok": guard_ok,
            "guard_reason": guard_reason,
            # Why NEW entries are blocked, when they are. `_managing` alone does not say whether it
            # is a routine mid-ladder edit ("params_changed" -- the open book finishes on its old
            # rules, then the new level goes live) or crash recovery ("adopted" -- positions
            # rebuilt from the broker after a restart). The clients word those very differently, so
            # the distinction is derived here rather than guessed from a state string.
            "managing_reason": (self._managing_reason if self._managing else None),
            # What the dialled stop ACTUALLY costs. The stop is measured on the
            # entry-side price but realised on the exit-side one, so the operator dials
            # X and receives X + spread before any slippage. Derived server-side so both
            # clients render the same number.
            "effective_stop": round((self.floor_offset if self.stop_mode == "floor"
                                     else self.retrace) + self._last_spread, 3),
            "floor_price": (round(self._floor_price(), 3)
                            if self.stop_mode == "floor" and self.trigger > 0 else None),
            "flush_remaining": len(self._flush),
            # Manual-reload alert. Latched when a ladder flushes and PARKS (it does not
            # re-enter itself). The clients edge-trigger a chime/notification on the
            # false->true transition and show `attention_reason`, so the operator knows
            # to set a new level rather than watch price walk away un-traded. Gated on
            # `enabled` so a parked-then-disabled engine does not keep nagging.
            "needs_attention": bool(self._needs_attention and self.enabled),
            "attention_reason": (self._attention_reason
                                 if (self._needs_attention and self.enabled) else None),
            # True when setting/keeping this trigger would arm on the NEXT tick rather
            # than wait for the move -- the "new level fires instantly" trap.
            "would_fire_now": would_fire,
            # Whether the trail is currently ACTIVE. With `trail_activate > 0` the retrace stop
            # is inert until the run is up by that much (only the hard_sl protects); the clients
            # render "trail waiting for +X" vs "trail active" so it is not mistaken for broken.
            "trail_activate": self.trail_activate,
            "trail_armed": bool(self._ladder._trail_armed) if self._ladder else None,
            "warning": warn,
        }

    def _floor_price(self) -> float:
        return (self.trigger + self.floor_offset) if self.side == "sell" \
            else (self.trigger - self.floor_offset)

    def _on_killed(self) -> None:
        self._ladder = None
        self._tickets = {}
        self._flush = []

    # ---- crash recovery ---------------------------------------------------
    def _adopt(self, worker, positions: list) -> int:
        """Rebuild the ladder from the broker's open positions after a restart.

        Entries, tickets and side all come straight off the positions. The one thing
        the broker does NOT store is the EXTREME -- the lowest bid (highest ask) the
        ladder reached -- and that is precisely what the retrace stop measures from.
        Lose it and the stop is meaningless. (Floor mode does not need it, but the
        mode can be switched at any time, so it is reconstructed either way.)

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

        # best entry = a real observed extreme (fallback + a floor on the tick scan)
        best_entry = min(entries) if is_sell else max(entries)

        extreme, basis = best_entry, "entries (approximate -- no ticks)"
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
        # An adopted book may already be larger than the configured cap (the cap was
        # lowered while it was open, or the process died mid-pyramid). Widen the cap to
        # fit rather than pretend the extra positions are not there -- but leave an
        # UNCAPPED setting uncapped.
        cap = 0 if self.max_positions <= 0 else max(self.max_positions, len(entries))
        st = LadderState(side, trigger=self.trigger, target=self.target,
                         max_positions=cap, max_lots=self.max_lots,
                         volume=self.volume, entry_mode=self.entry_mode,
                         entry_step=self.entry_step, entry_gap_ms=self.entry_gap_ms,
                         stop_mode=self.stop_mode, retrace=self.retrace,
                         floor_offset=self.floor_offset, trail_activate=self.trail_activate)
        st.armed = True                 # it already fired -- do not re-trigger
        # Same (rid, price) shape on_tick builds, and the same rid->ticket map, so
        # exit_one can close a single adopted rung exactly as it would a live one.
        st.entries = list(enumerate(entries))
        st._next_rid = len(entries)
        st.n_taken = len(entries)
        st.extreme = extreme
        # A rescued position must be MANAGED now, not left waiting for an activation whose
        # history we cannot reconstruct: arm the trail immediately for an adopted book.
        st._arm_price = extreme
        st._trail_armed = True
        self._ladder = st
        self._tickets = {i: int(p.ticket) for i, p in enumerate(poss)}
        self._flush = []
        self._adopt_basis = basis
        self._adopt_extreme = extreme
        self._managing_reason = "adopted"

        log.warning("ladder adopted %d orphaned position(s) from the broker",
                    len(entries),
                    extra={"event": "ladder_adopted", "strategy": self.ID,
                           "side": side, "entries": entries,
                           "tickets": list(self._tickets.values()),
                           "extreme": round(extreme, 3), "extreme_basis": basis})
        return len(entries)

    def _adopt_detail(self) -> dict:
        return {"extreme": self._adopt_extreme, "extreme_basis": self._adopt_basis}

    # ---- control ----------------------------------------------------------
    def update(self, params: dict | None, enabled: bool | None) -> dict:
        """Enable, then immediately re-check the parameter guard.

        Without this the guard only ran on the next worker poll, so POST returned
        `enabled: true, error: null` for a configuration the engine was about to
        refuse ~66 ms later. The caller toasted "enabled" and the UI then silently
        flipped to disabled. An API that reports success for a request it is in the
        middle of rejecting is worse than one that just says no.
        """
        if enabled:
            # Enabling IS the operator's discretionary act, so the first ladder may
            # fire on a trigger price has already crossed. Everything after it waits.
            self._rearm_ok = True
            self._last_ladder_end = 0.0
        if enabled is not None:
            # Any deliberate toggle resolves the "set a new level" nag -- the operator
            # has just acted on the engine.
            self._needs_attention = False
            self._attention_reason = None
        if params is not None and params.get("trigger") is not None:
            # Setting a new level IS the manual re-engage. It resolves the nag and -- because
            # it is a deliberate act, not a machine re-entry -- clears the cooldown too, so a
            # non-zero `cooldown_s` cannot make the operator wait after they explicitly asked
            # to trade a new level. This lives in update() (the operator path) NOT in _apply(),
            # because reconcile() calls _apply(saved) with the persisted trigger and must NOT
            # wipe the cooldown/latch it just restored from disk.
            self._needs_attention = False
            self._attention_reason = None
            self._last_ladder_end = 0.0
        res = super().update(params, enabled)
        if self.enabled and not self._param_guard(self._last_spread):
            return self.status()          # _param_guard already disabled + explained
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
            # Kept so status() can answer "would a fresh ladder fire NOW?" between polls.
            self._last_bid = float(bid)
            self._last_ask = float(ask)
        super().evaluate(worker, st)

    def _tick(self, worker, st: dict) -> None:
        bid, ask = st.get("bid"), st.get("ask")
        if not bid or not ask:
            self._state = "waiting: no quote"
            return
        spread = float(ask) - float(bid)
        self._last_spread = spread
        now = time.time()

        # DRAINING a flush takes priority over everything, including the trigger. The
        # book is already condemned; the only job left is to get it closed without
        # blowing the poll budget. Nothing new opens until it is empty.
        if self._flush:
            self._drain(worker)
            self._state = f"flushing: {len(self._flush)} left"
            if self._flush:
                return
            self._finish_ladder(now)
            # A book that was being MANAGED (adopted after a restart, or handed over by
            # a param change) has now finished. Ask the broker whether we are genuinely
            # flat before leaving that state -- `_clear_managing_if_flat` checks the
            # positions rather than our own bookkeeping, which is the point of it.
            if self._managing:
                self._clear_managing_if_flat(worker)
            return

        # MANAGING an adopted book: work the exits (target + stop) on the positions we
        # inherited, but open nothing new. Note this runs BEFORE the trigger and
        # parameter guards -- those gate NEW entries, and a position already open must
        # be managed regardless of whether a fresh one would be allowed. Bailing out
        # here on "no trigger set" would strand exactly the positions we just rescued.
        if self._managing:
            if self._ladder is None:
                self._clear_managing_if_flat(worker)
                return
            # Re-check against the BROKER, ~1 Hz.
            #
            # Managing is driven from `_ladder.entries`, which is our own memory. If every
            # position is closed behind our back -- the hard SL fires, the operator flattens
            # by hand, the account is switched -- that memory becomes a phantom book, and the
            # engine sits managing positions that no longer exist. It would only notice at the
            # next stop event, which may be hours away, and until then it refuses to open
            # anything new because its book "looks full". That is precisely the failure
            # strategies/state.py exists to warn about; the broker is the only real answer.
            #
            # Throttled to 1 Hz rather than run every poll: it costs one positions_get, and
            # managing is a transient recovery state, not the hot path. (Same shape as the
            # ~1 Hz throttle on _compute_stats.)
            if now - self._managing_check >= 1.0:
                self._managing_check = now
                if not self.positions(worker):
                    log.info("ladder was managing a book the broker no longer has -- releasing",
                             extra={"event": "ladder_managing_stale", "strategy": self.ID,
                                    "believed": len(self._ladder.entries)})
                    self._ladder = None
                    self._tickets = {}
                    self._clear_managing_if_flat(worker)
                    return
            acts = self._ladder.on_tick(float(bid), float(ask), int(now * 1000))
            for a in acts:
                if a[0] == "enter":
                    # managing-only: never open new risk. The rung must be un-believed
                    # too, or it occupies a cap slot and the stop chases a phantom.
                    self._ladder.rollback_entry(a[1])
                    continue
                self._do(worker, a)
            # "adopted" is crash-recovery language; a routine mid-ladder edit is not that. Word
            # the state string by WHY we are managing so an operator who just nudged the trigger
            # does not read "adopted position(s)" and think the engine restarted.
            if self._ladder.entries:
                n = len(self._ladder.entries)
                self._state = (f"finishing {n} open trade(s) on the old rules; new level queued"
                               if self._managing_reason == "params_changed"
                               else f"managing {n} adopted position(s)")
            else:
                self._state = "managing"
            if not self._ladder.entries and not self._flush:
                self._ladder = None
                self._clear_managing_if_flat(worker)
            return

        if self.trigger <= 0:
            self._state = "waiting: no trigger price set"
            return
        # A stop or target inside the spread is not a strategy, it is a fee. Refuse.
        if not self._param_guard(spread):
            return

        if self._ladder is None:
            gate = self._rearm_gate(float(bid), float(ask), now)
            if gate is not None:
                self._state = gate
                return
            self._ladder = LadderState(
                self.side, self.trigger, self.target, self.max_positions,
                self.max_lots, self.volume, self.entry_mode, self.entry_step,
                self.entry_gap_ms, self.stop_mode, self.retrace, self.floor_offset,
                self.trail_activate)

        acts = self._ladder.on_tick(float(bid), float(ask), int(now * 1000))
        for a in acts:
            self._do(worker, a)

        if self._flush:
            self._state = f"flushing: {len(self._flush)} left"
        elif self._ladder.done:
            self._finish_ladder(now)
        elif self._ladder.armed:
            self._state = (f"active: {len(self._ladder.entries)} rung(s), "
                           f"{self._ladder.open_lots:g} lots"
                           if self._ladder.entries else "armed")
        else:
            self._state = f"armed: waiting for {self.side.upper()} trigger {self.trigger:g}"

    # ---- re-arm gating ----------------------------------------------------
    def _rearm_gate(self, bid: float, ask: float, now: float) -> str | None:
        """May a NEW run start? Returns None to allow, else the reason to display.

        `_rearm_ok` is True only right after a deliberate operator act -- enabling, or a
        SET LEVEL (a trigger change) -- so the FIRST run of an arm always fires. After a
        run ends `_finish_ladder` clears it, and what happens next is the whole point of
        this method, decided by `auto_continue`:

          * ONE-SHOT (auto_continue False, the default) -- the engine PARKS. A bare price
            re-cross does nothing; only the next SET LEVEL (or re-enable) starts another
            run. This is the safe default and matches the operator's workflow (ARM stays
            on, they set levels). It also makes the 68-ladders-in-25-min churn impossible.

          * AUTO-CONTINUE (auto_continue True) -- keep taking runs WHILE price is still on
            the trade side of the trigger (below it for a sell, above it for a buy), and
            STOP only when price returns to the trigger. This is the trend mode: the trail
            closes and banks each run on a bounce, then it re-arms lower, until price comes
            back to the level. In a chop it churns -- hence opt-in, and throttled below.

        `cooldown_s` and `max_ladders_per_day` gate BOTH modes: they are the churn brakes
        that matter for auto-continue, and a `max_ladders_per_day` cap that does not depend
        on the loss being large enough to trip the daily kill-switch.
        """
        self._roll_day()
        if self.max_ladders_per_day > 0 and self._ladders_today >= self.max_ladders_per_day:
            return f"done for today: {self._ladders_today}/{self.max_ladders_per_day} ladders"
        if self.cooldown_s > 0 and self._last_ladder_end:
            left = self.cooldown_s - (now - self._last_ladder_end)
            if left > 0:
                return f"cooldown: {left:.0f}s"
        if self._rearm_ok:
            return None                    # fresh SET LEVEL / enable -- fire this run

        if not self.auto_continue:
            # ONE-SHOT: parked until the operator acts. `_finish_ladder` already latched
            # `needs_attention`; do not auto-clear on any price movement.
            return (f"parked: set a new level to run again "
                    f"(auto-continue is off)")

        # AUTO-CONTINUE: re-arm while price is still PAST the trigger; stop at the trigger.
        if self._needs_attention:
            # Already stopped on a prior return to the level -- stay parked until the operator
            # SETs A LEVEL, even if price dips past the trigger again. A move that came all the
            # way back is over; the next one is a fresh decision, not an automatic re-entry.
            return f"stopped: price returned to {self.trigger:g}; set a new level"
        mark = bid if self.side == "sell" else ask
        past = (mark < self.trigger) if self.side == "sell" else (mark > self.trigger)
        if past:
            self._rearm_ok = True          # keep going -- another run down the move
            return None
        # Price has come back to the level -- the move is over for now. Park and alert.
        self._needs_attention = True
        self._attention_reason = (
            f"price returned to {self.trigger:g} -- auto-continue stopped; "
            f"set a new level to trade the next move")
        return f"stopped: price returned to {self.trigger:g}; set a new level"

    def _roll_day(self) -> None:
        """Reset the daily ladder count at local midnight -- the same boundary
        `strategy_daily_realized` and the history endpoint use, so the numbers on
        screen agree with each other."""
        today = datetime.date.today()
        if self._today != today:
            self._today = today
            self._ladders_today = 0

    # ---- per-day state that must survive a restart ------------------------
    def _runtime(self) -> dict:
        """`max_ladders_per_day` and `cooldown_s` are only worth the name if they
        outlive the process. Both counters used to be memory-only, so a restart reset
        them -- and this box restarts on its own (scheduled task, autostop/autostart),
        which made the 'daily' cap really a 'per-process' cap."""
        rt = super()._runtime()
        rt.update({
            "day": self._today.isoformat() if self._today else None,
            "ladders_today": int(self._ladders_today),
            "last_ladder_end": float(self._last_ladder_end),
        })
        return rt

    def _restore_runtime(self, rt: dict) -> None:
        super()._restore_runtime(rt)
        if not rt:
            return
        today = datetime.date.today()
        # Only TODAY's count survives. Restoring yesterday's would carry a spent daily
        # budget into a fresh day; the date check is what makes it a daily cap and not
        # a running total.
        if rt.get("day") == today.isoformat():
            self._today = today
            self._ladders_today = int(rt.get("ladders_today") or 0)
            # A cooldown is a wall-clock deadline, so it is meaningful across a restart
            # in a way the count is not: if it has already elapsed, it simply does not bite.
            self._last_ladder_end = float(rt.get("last_ladder_end") or 0.0)
            if self._ladders_today:
                log.info("ladder restored today's counters after a restart",
                         extra={"event": "ladder_runtime_restored", "strategy": self.ID,
                                "ladders_today": self._ladders_today,
                                "day": rt.get("day")})

    def _save_runtime(self, now: float) -> None:
        """Persist the counters, THROTTLED.

        `state.save` is a read-modify-write of a JSON file and this is reached from
        `_finish_ladder`, on the worker thread, inside the ~66 ms poll budget that also
        has to fill orders -- CLAUDE.md forbids blocking disk IO there. So it writes at
        most once every `LADDER_RUNTIME_SAVE_MIN_S`. The trade-off is explicit: a crash
        can lose a few seconds of increments (a ladder or two off the daily count),
        which is worth far more than stalling the loop that feeds prices, the P&L guard
        and the manual order pad.
        """
        if now - self._runtime_saved < float(config.LADDER_RUNTIME_SAVE_MIN_S):
            return
        self._runtime_saved = now
        state.save(self.ID, self.enabled, self._params(), self._runtime())

    def _finish_ladder(self, now: float) -> None:
        self._ladders_done += 1
        self._roll_day()
        self._ladders_today += 1
        self._ladder = None
        self._tickets = {}
        self._rearm_ok = False          # must re-cross the trigger before the next one
        self._last_ladder_end = now
        self._state = "armed"
        # The engine has ridden ONE leg and now PARKS -- it deliberately does not
        # re-enter itself (that would be unattended new risk). Latch the nag so the
        # clients can alert the operator: in a continuing trend price will keep going
        # without a re-cross, so nothing new fires until they set a new level.
        #
        # But NOT always. Two cases suppress the nag here:
        #   * a MANAGED book (adopted after a restart, or handed over by a mid-ladder param
        #     change) -- a crash-recovery flush has no "new level" to set, and a handover already
        #     HAS the operator's new level queued. Nagging there is a false alarm.
        #   * AUTO-CONTINUE mode -- the run just ended but the engine intends to keep going while
        #     price is still past the trigger. The nag belongs at the RETURN to the level, which
        #     `_rearm_gate` latches; latching it on every run-end would chime on every bounce.
        why = self._flush_why or "target"
        self._flush_why = ""            # consumed; a target-only finish must not read it stale
        if not self._managing and not self.auto_continue:
            self._needs_attention = True
            self._attention_reason = (
                f"closed out ({why}) -- set a new level to re-engage this "
                f"{self.side.upper()} trend")
            log.info("ladder parked after a leg -- awaiting a new level",
                     extra={"event": "ladder_needs_attention", "strategy": self.ID,
                            "why": why, "trigger": self.trigger, "side": self.side})
        self._save_runtime(now)

    # ---- guards -----------------------------------------------------------
    def _guard_check(self, spread: float) -> tuple[bool, str | None]:
        """Would the current params be REFUSED at this spread? Pure -- mutates nothing.

        Split out of `_param_guard` so the exact same verdict can be handed to the UI
        BEFORE an arm attempt, rather than only surfacing as an `error` string after the
        server has already refused. One source of truth: the panel greys out ARM using
        the sentence the engine would itself have raised, so the two cannot drift.

        Returns (ok, reason). `reason` is None when ok, else the operator-facing line.
        """
        if self.target <= spread:
            return False, (
                f"refused: target {self.target:.2f}/oz is inside the live spread "
                f"{spread:.2f}/oz -- a winning trade would still net "
                f"{self.target - spread:+.2f}/oz. Raise the target above the spread.")
        if self.stop_mode == "floor" and self.floor_offset <= spread:
            return False, (
                f"refused: floor_offset {self.floor_offset:.2f}/oz is inside the live "
                f"spread {spread:.2f}/oz -- the floor sits where the ladder is already "
                f"marked the moment it arms, so it would flush on the first tick. "
                f"Raise it above the spread.")
        if self.stop_mode == "retrace" and self.retrace <= spread:
            return False, (
                f"refused: retrace {self.retrace:.2f}/oz is inside the live spread "
                f"{spread:.2f}/oz -- every rung would stop out for at least "
                f"{-(self.retrace + spread):.2f}/oz before it could move. "
                f"Raise the retrace above the spread.")
        return True, None

    def _param_guard(self, spread: float) -> bool:
        """Refuse the impossible; warn about the merely unlikely.

        The distinction matters. `target <= spread` is arithmetic: a winning trade
        still nets a loss, always, and no amount of skill changes it -- so the engine
        refuses. "target sits behind a tight trail" is a probability, very low but not
        zero, so it is a warning in status() rather than a refusal. Refusing a legal
        configuration the operator may have chosen deliberately would be the engine
        overruling the human; staying silent about one that cannot win was the bug.

        The comparisons live in `_guard_check`; this method is the side-effecting half
        -- it disables and records the reason. Kept re-checked EVERY poll on purpose: a
        spread that widens past a dialled stop must disarm a RUNNING ladder, not only
        block a fresh arm.
        """
        ok, reason = self._guard_check(spread)
        if not ok:
            self._disable_with(reason)
        return ok

    # ---- execution --------------------------------------------------------
    def _do(self, worker, act: tuple) -> None:
        kind = act[0]
        if kind == "enter":
            self._enter(worker, act[1], act[2])
        elif kind == "exit_one":
            self._exit_one(worker, act[1], act[2], act[3])
        elif kind == "exit_all":
            self._exit_all(worker, act[1], act[2], act[3])

    def _enter(self, worker, rid: int, price: float) -> None:
        # An actual entry means the engine has re-engaged (operator re-levelled, or a
        # clean re-cross) -- the "set a new level" nag is resolved either way.
        self._needs_attention = False
        self._attention_reason = None
        if self.paper:
            self._paper_trades += 1
            log.info("ladder PAPER entry",
                     extra={"event": "ladder_paper_entry", "strategy": self.ID,
                            "side": self.side, "fill": round(price, 3),
                            "rid": rid, "n": len(self._ladder.entries)})
            return
        sl = self.hard_sl          # broker-side backstop if this process dies
        r = worker.strategy_place(self.MAGIC, self.side, round(self.volume, 2),
                                  sl, 0.0, comment="XauLadder")
        if not r.get("ok"):
            # The broker said no, so the ladder must un-believe the entry. Leaving it
            # in place would leave the engine managing a position that does not exist.
            if self._ladder is not None:
                self._ladder.rollback_entry(rid)
            self._error = f"entry failed: {r.get('error')}"
            log.warning("ladder entry failed -- entry rolled back",
                        extra={"event": "ladder_entry_failed", "strategy": self.ID,
                               "error": r.get("error"), "rid": rid,
                               "rolled_back": round(price, 3)})
            return
        self._tickets[rid] = int(r["ticket"])
        # Correct the model to the price the broker actually filled at. The snapshot
        # `price` came from a poll up to ~66 ms old, strategy_place then re-read the
        # tick, and DEFAULT_DEVIATION allowed it to slip further still -- one live
        # entry filled BELOW its own trigger. Marking the rung at a price that was
        # never traded puts both the target and the extreme on a fiction.
        fill = r.get("price")
        if fill and self._ladder is not None:
            self._ladder.entries = [(i, float(fill) if i == rid else p)
                                    for (i, p) in self._ladder.entries]
        self._error = None
        log.info("ladder entry",
                 extra={"event": "ladder_entry", "strategy": self.ID,
                        "side": self.side, "ticket": r.get("ticket"), "rid": rid,
                        "requested": round(price, 3), "fill": fill,
                        "volume": self.volume})

    def _exit_one(self, worker, rid: int, price: float, entry: float) -> None:
        """Close the ONE rung that reached its target.

        Keyed on the rung id, never the price. Closing the whole list here (as this
        once did) meant the first position to reach its target flattened the entire
        ladder -- silently throwing away every other rung, including ones still
        running. Matching on price instead was the next bug waiting to happen: at five
        entries a second, duplicate entry prices are routine.
        """
        if self.paper:
            self._book_paper(rid, entry, price, "target")
            return
        ticket = self._tickets.pop(rid, None)
        if ticket is None:
            return                       # already gone (broker SL/TP beat us)
        self._close(worker, ticket, "target", rid)

    def _exit_all(self, worker, price: float, why: str, rungs: list) -> None:
        """Condemn the whole book, then close it in BATCHES.

        `order_send` is synchronous and runs on the worker thread inside a ~66 ms poll
        budget, so closing N positions costs N*2 broker round trips in one cycle. At a
        handful of rungs that is invisible; at several hundred it freezes the loop for
        seconds -- and it is the same loop that feeds prices, the P&L guard, the manual
        order pad and the stop that just fired. So the tickets are queued here and
        drained `close_batch` per cycle by `_drain`, which keeps every individual cycle
        inside its budget while the book still empties promptly.
        """
        if self.paper:
            for rid, entry in rungs:
                self._book_paper(rid, entry, price, why)
            return
        self._flush = list(self._tickets.values())
        self._flush_why = why
        self._tickets = {}
        log.warning("ladder stop hit -- flushing %d position(s)", len(self._flush),
                    extra={"event": "ladder_flush_start", "strategy": self.ID,
                           "why": why, "count": len(self._flush),
                           "price": round(price, 3), "batch": self.close_batch})
        self._drain(worker)

    def _drain(self, worker) -> None:
        """Close up to `close_batch` condemned tickets, then yield the poll."""
        for ticket in self._flush[:self.close_batch]:
            self._close(worker, ticket, self._flush_why, None)
        if not self._flush:
            log.info("ladder flush complete",
                     extra={"event": "ladder_flush_done", "strategy": self.ID,
                            "why": self._flush_why})

    def _close(self, worker, ticket: int, why: str, rid: int | None) -> None:
        r = worker.strategy_close_ticket(self.MAGIC, ticket)
        ok = bool(r.get("ok"))
        err = r.get("error") or ""
        # "ticket not found" means the broker has already closed it -- the hard SL
        # fired, or a previous attempt succeeded and we never saw the answer. Either
        # way it is DONE, and retrying it forever costs a positions_get IPC per poll
        # inside the trading budget. Only a genuine failure stays on the queue.
        gone = ok or "not found" in err.lower()
        if gone and ticket in self._flush:
            self._flush.remove(ticket)
        if not gone:
            self._error = f"close failed: {err}"
        log.info("ladder exit",
                 extra={"event": "ladder_exit", "strategy": self.ID, "why": why,
                        "ticket": ticket, "rid": rid, "ok": ok, "error": r.get("error"),
                        "already_closed": (not ok) and gone})

    def _book_paper(self, rid: int, entry: float, price: float, why: str) -> None:
        """Realized P&L in $/oz, using the fill we WOULD have got. This number is the
        whole point of paper mode: it is what the trigger is worth, net of the spread,
        before any money is at stake."""
        pl = (entry - price) if self.side == "sell" else (price - entry)
        self._paper_pl += pl
        log.info("ladder PAPER exit",
                 extra={"event": "ladder_paper_exit", "strategy": self.ID,
                        "why": why, "rid": rid, "entry": round(entry, 3),
                        "exit": round(price, 3), "pl_per_oz": round(pl, 3),
                        "cum_pl_per_oz": round(self._paper_pl, 3)})
