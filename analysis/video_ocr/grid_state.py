r"""Pure decision logic for the XAUUSD recovery grid reconstructed from the video.

No MT5, no clock, no IO -- (bid, ask, open-ticket-set) in, actions out. Same shape as
`XauOrderPad/strategies/straddle_ladder.StraddleGridState`, for the same reason: the object that
decides is the object the replay drives, so a backtest and a live engine cannot diverge.

Actions the caller executes:

    ("open", side, lot, n)  -> open n positions of `lot` on `side`, then register_entries(...)
    ("close_all", reason)   -> flatten every open ticket, then register_flat(balance_after)

── The strategy, as measured (not as described) ──

Re-derived from `D:\llm\ios\xausd_video_out\*.csv`, because the prose had drifted from the data:

  * **There is NO stop loss.** 44 of 45 cycles went underwater; **86% of those closed in profit
    anyway** -- the basket is HELD through the drawdown until price bounces. Only 4 cycles ever
    closed negative (-0.14, -6.91, -30.00, **-6017.56**), at no consistent level, so those are
    discretionary bail-outs and not a rule that can be coded.
  * **The exit is per BASKET, not per position.** The whole basket goes at once, and 12% of
    visible legs (33/286) close at a loss, carried by the rest. NOTE the sign convention that
    makes this easy to get wrong: `entry_exit_distance.signed_distance` is `exit - entry`, a raw
    price delta, NOT a P&L sign -- a SELL profits when it is negative. Reading every negative as
    a loss gives 37%, which is threefold too high.
  * Typical worst drawdown before recovery: **-45% of balance**. 88% of closes taken in profit.
  * The unit lot is fixed when a cycle opens and never changes within it (954/955 multi-position
    states hold identical lots) -- escalation is in the NUMBER of positions, not their size.

── Why `PARKED` exists ──

With no stop loss, the recorded behaviour at full depth was to hold and hope. It worked 86% of the
time and once cost $6,017.56. So depth exhaustion while still underwater is an explicit state that
LATCHES and stops, exactly as `strategies/ladder.py` latches `needs_attention`. This module will
not silently keep holding, and it will not invent a stop loss the strategy does not have. It
surfaces the state and lets a human decide.

Analysis only. Nothing here places, modifies or closes an order; it emits intentions for a replay
to score. Putting it on an account is a separate, deliberate human action.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONTRACT = 100.0        # oz per lot, XAUUSD

FLAT, OPEN, PARKED = "flat", "open", "parked"


@dataclass
class Position:
    ticket: int
    side: str            # "buy" | "sell"
    lot: float
    entry: float         # fill price: buy at ask, sell at bid
    rung: int            # 0 = first entry


@dataclass
class Cycle:
    """Everything carried for one basket. The risk lives in these fields, not in the parameters."""
    index: int
    side: str
    unit_lot: float           # fixed at cycle open, never changes within the cycle
    balance_open: float
    target: float             # $ of open P&L that closes the basket
    first_entry: float = 0.0
    rungs: int = 0
    positions: int = 0
    depth_cap: int = 0
    group: int = 1            # positions opened per rung, FIXED at open like unit_lot
    basket_pnl: float = 0.0
    worst_pnl: float = 0.0            # drawdown watermark, $
    worst_pct_of_balance: float = 0.0
    best_pnl: float = 0.0             # HIGH-WATER MARK -- what the trail measures from
    trail_armed: bool = False         # set once the basket has been in profit
    was_underwater: bool = False
    park_reason: str | None = None
    entries: list[float] = field(default_factory=list)
    open_oz: float = 0.0              # total ounces open; converts $/oz trail to $
    # Set by the driver so a cycle's DURATION is measurable. The operator states a cycle lasts
    # under a minute and holds 10+ positions; neither claim was testable without these.
    t_open_ms: int = 0
    t_close_ms: int = 0
    i_open: int = 0

    @property
    def depth_left(self) -> int:
        return max(0, self.depth_cap - self.positions)


class GridState:
    """The grid as a state machine: FLAT -> OPEN -> (ADD)* -> UNWIND -> FLAT, or -> PARKED.

    ADD and UNWIND are transitions rather than resting states -- an add returns to OPEN on the
    same tick and an unwind emits one ("close_all", reason), so a caller never observes them as a
    phase. `phase` is therefore one of FLAT / OPEN / PARKED.
    """

    def __init__(self, engine, direction: str, balance: float, *,
                 hard_cap: int | None = None, auto_reenter: bool = True,
                 use_trail: bool = False, use_dual_exit: bool = False) -> None:
        if direction not in ("buy", "sell"):
            raise ValueError(f"direction must be buy|sell, got {direction!r}")
        self.engine = engine
        self.direction = direction
        self.balance = float(balance)
        self.hard_cap = hard_cap
        self.auto_reenter = bool(auto_reenter)
        # Legacy $/oz trail. REFUTED -- kept switchable only so the replay can cost it against the
        # dual-arm form. See the exit comment in on_tick.
        self.use_trail = bool(use_trail)
        # The DUAL-ARM exit: a target at ~30% of balance OR a give-back of ~15% of balance from
        # the peak, whichever a poll sees first. This is the rule the evidence supports; it is
        # still opt-in because the give-back band (12-18%) and the poll cadence are not yet pinned.
        self.use_dual_exit = bool(use_dual_exit)
        # Set by the driver each tick when running a volatility-scaled step; None = use the
        # engine's fixed add_step. See the note in on_tick.
        self.dynamic_step: float | None = None

        self.phase = FLAT
        self.cycle: Cycle | None = None
        self.n_cycles = 0
        self.positions: dict[int, Position] = {}
        self.park_reason: str | None = None
        self.refused: str | None = None       # set when the account cannot open the first rung

    # ---------------- what the caller reports back ----------------

    def register_entries(self, tickets: list[int], fill: float, lot: float) -> None:
        """Confirm the fills for an ("open", ...) action -- one action is one rung."""
        c = self.cycle
        assert c is not None, "register_entries with no open cycle"
        rung = c.rungs
        for t in tickets:
            self.positions[t] = Position(t, c.side, lot, fill, rung)
        c.positions += len(tickets)
        c.open_oz += len(tickets) * lot * CONTRACT
        c.rungs += 1
        c.entries.append(fill)
        if c.rungs == 1:                      # this was the cycle's first rung
            c.first_entry = fill

    def register_flat(self, balance_after: float) -> None:
        """Confirm the basket is gone and book the new balance."""
        was_parked = self.phase == PARKED
        self.positions.clear()
        self.balance = float(balance_after)
        self.cycle = None
        # A basket that PARKED and then closed on its bounce stays latched. Parking is a decision
        # to stop trading this run and hand it to a human; banking the recovery is not permission
        # to start another. Only FLAT re-enters.
        self.phase = PARKED if was_parked else FLAT

    # ---------------- the tick ----------------

    def on_tick(self, bid: float, ask: float, open_ids: set[int]) -> list[tuple]:
        # A tracked ticket missing from open_ids was closed by the broker. This strategy places
        # no SL and no TP, so that should never happen -- but if it does, drop it rather than
        # keep marking a position that is gone.
        for t in [t for t in self.positions if t not in open_ids]:
            del self.positions[t]

        if self.phase == PARKED:
            # KEEP MARKING a parked basket. It is still open and still moving -- parking stops the
            # engine from acting, not the market from moving against it. An earlier version
            # returned here before updating, which froze `worst_pnl` at the moment of parking and
            # understated every parked cycle's drawdown: one basket reported a 43% worst drawdown
            # while sitting at -1004% of balance at the horizon. Reporting the smaller number was
            # the single most misleading thing in this analysis.
            c = self.cycle
            if c is not None and self.positions:
                c.basket_pnl = self.basket_pnl(bid, ask)
                c.best_pnl = max(c.best_pnl, c.basket_pnl)
                if c.basket_pnl < c.worst_pnl:
                    c.worst_pnl = c.basket_pnl
                    c.worst_pct_of_balance = (100.0 * c.worst_pnl / c.balance_open
                                              if c.balance_open else 0.0)
                # A PARKED BASKET MUST STILL BE ALLOWED TO TAKE ITS BOUNCE. Parking stops the
                # engine ADDING; it is not a decision to hold a winner forever. Returning [] here
                # unconditionally meant a basket that parked at the low and then recovered was
                # never closed: replaying the operator's own 4599.50 case at $10 it reached
                # +$4.53 against a $2.89 target and was still scored as a park. That inflated
                # every "parked" count in the replay corpus, including the headline 104/104.
                #
                # Only the EXIT runs here. No adds, no re-entry -- the latch still holds those,
                # which is the whole point of the state.
                if c.basket_pnl > 0:
                    acts = self._exit_check(c)
                    if acts:
                        return acts
            return []
        if self.phase == FLAT:
            # auto_reenter=False runs exactly ONE cycle and then stops. That is what a GUIDED
            # strategy needs: a human calls buy or sell, the algorithm handles that one basket,
            # and it does not decide on its own to open another. The flag was previously stored
            # but never honoured, so every replay re-entered continuously from tick 0 -- which
            # tested entry timing and direction, not the position machinery.
            if not self.auto_reenter and self.n_cycles > 0:
                return []
            return self._open_cycle(bid, ask)

        c = self.cycle
        assert c is not None
        c.basket_pnl = self.basket_pnl(bid, ask)
        c.best_pnl = max(c.best_pnl, c.basket_pnl)
        if c.basket_pnl < c.worst_pnl:
            c.worst_pnl = c.basket_pnl
            c.worst_pct_of_balance = (100.0 * c.worst_pnl / c.balance_open
                                      if c.balance_open else 0.0)
        if c.basket_pnl < 0:
            c.was_underwater = True

        # 1) THE EXIT. Structure CONFIRMED; the constant is not yet pinned.
        #
        # CONFIRMED by cycle 29 (OPERATOR_VERIFIED.md): the exit is a SIMULTANEOUS BASKET CLOSE.
        # Every position exited at one price, 4049.291. `close_all` below is therefore the right
        # abstraction -- an earlier reading of cycle 7 suggested per-position take-profits firing
        # in sequence, and that is now retired.
        #
        # THE RULE is a basket TRAIL whose give-back is a PERCENTAGE OF BALANCE, in profit only,
        # currently bounded to a 12-18% band (cycle 5 needs 12.2%, cycle 6 15.0%, cycle 7 18.3%;
        # cycle 29 lands in the same band). Measuring the give-back in $/oz was the earlier error:
        # it mixes lot size and position count into what is really a risk fraction, which is why
        # 0.060 / 0.100 / 0.130 / 0.354 looked like noise. One more observed cycle with an exactly
        # known position count pins the constant; until then `use_trail` stays False.
        #
        # Models refuted along the way, kept so they are not re-proposed:
        #
        #   * FIXED TARGET (basket_pnl >= target) is refuted by cycle 5. Its path is
        #     -0.79, 0.29, 0.71, -0.55, -0.34, -0.19, 0.53 -> CLOSED. It reached +0.71 and did
        #     NOT close, then closed lower at +0.53. No threshold does that.
        #
        #   * TRAILING STOP is refuted by cycle 7. Per trade it ran
        #     +0.32 -> 0.08 -> 0.20 -> 0.32 -> 0.19 -> CLOSED: it survived a $0.24 give-back and
        #     then closed on a $0.13 one. No high-water trail does that. On cycle 5 a trail of any
        #     size fires at -0.55, four states before the real close.
        #
        # TWO CONSTRAINTS THAT DID HELP, both principled rather than fitted (best score 1/4 -> 2/4):
        #
        #   * IN PROFIT ONLY -- this strategy does not close at a loss, so an exit firing on a
        #     retrace into the red was never possible. With it, cycle 5 reproduces EXACTLY.
        #   * A CHECK CADENCE -- cycle 7 is reproduced exactly by a $0.13 trail checked every 2nd
        #     state. Its +0.32 -> +0.08 -> +0.32 round trip took ~3 real seconds, so a slower poll
        #     never sees the dip. The contradiction may be LATENCY rather than logic.
        #
        # Tuning stopped there deliberately: four cycles against three free parameters is fitting
        # noise. `use_trail` stays False until more observed cycles arrive.
        #
        # `target` (28.9% of balance) is kept as the default because it reproduces the AGGREGATE
        # outcome -- verified against ground truth, since cycle_table.pnl_before matches the
        # realised balance step on 25/42 cycles at median error $0.00. It describes what the trail
        # typically yields, not what triggers it. Do not present it as the discovered rule.
        acts = self._exit_check(c)
        if acts:
            return acts

        # 2) add a rung once price has run far enough against the basket.
        #
        # `dynamic_step`, when the driver sets it, replaces the engine's fixed add_step with a
        # volatility-scaled one (k x trailing range). The static $0.18 tolerates ~$2 of adverse
        # movement on an instrument that moves $86-186 a day, which is why 104 of 104 replay runs
        # parked; a scaled step is the fix being tested.
        adverse = self._adverse(bid, ask)
        step = self.dynamic_step if self.dynamic_step else self.engine.add_step
        # The batch size is the CYCLE's, fixed at open beside unit_lot -- not re-read from the
        # engine mid-cycle, and never larger than the depth still budgeted.
        if c.positions < c.depth_cap and adverse >= step * c.rungs:
            return [("open", c.side, c.unit_lot, max(1, min(c.group, c.depth_left)))]

        # 3) depth exhausted while underwater -> PARK and latch. No stop loss exists; holding
        #    silently is what cost the recorded run $6,017.56, so this surfaces instead.
        if c.depth_left <= 0 and c.basket_pnl < 0 and adverse >= self.engine.add_step * c.rungs:
            self.phase = PARKED
            self.park_reason = c.park_reason = (
                f"depth {c.positions}/{c.depth_cap} exhausted, basket {c.basket_pnl:+.2f} "
                f"({c.worst_pct_of_balance:.0f}% of balance) and price still running")
            return []

        return []

    # ---------------- internals ----------------

    def _exit_check(self, c: Cycle) -> list[tuple]:
        """Every exit arm, in one place. Called from OPEN and from PARKED.

        `c.target` is a FIXED DOLLAR AMOUNT set at cycle open (a fraction of the balance), and that
        is the whole mechanism: a basket that has added positions reaches the same dollars on a
        SMALLER price move. At $1 with one 0.01 lot the target is $0.289 = $0.289/oz; after the
        basket has grown to three positions the same $0.289 is only $0.096/oz. That is exactly the
        operator's "normally 0.20-0.30, but about 0.10 once it has gone into loss and come back" --
        one rule, not two.

        A target that scaled with open volume would cancel it precisely, leaving the required move
        constant at every depth and making the adds pointless. See the MQL5 note in GridEngine.mqh.
        """
        if self.use_dual_exit and c.balance_open > 0 and c.basket_pnl > 0:
            eng = self.engine
            tgt = getattr(eng, "exit_target_pct", 30.0) / 100.0 * c.balance_open
            gb = getattr(eng, "exit_giveback_pct", 15.0) / 100.0 * c.balance_open
            # TARGET arm: a steady rise caught by a poll. Cycle 10 closed here, at 30.1%.
            if c.basket_pnl >= tgt:
                return [("close_all", "target_pct")]
            # TRAIL arm: a spike missed between polls, seen retracing. Cycles 5/6/7 closed here.
            if c.best_pnl > 0 and c.basket_pnl <= c.best_pnl - gb:
                return [("close_all", "giveback_pct")]

        if self.use_trail and c.open_oz > 0:      # legacy $/oz form; refuted, kept for comparison
            if c.basket_pnl > 0:
                c.trail_armed = True
            give_back = getattr(self.engine, "trail_give_back", 0.10) * c.open_oz
            if c.trail_armed and c.best_pnl > 0 and c.basket_pnl <= c.best_pnl - give_back:
                return [("close_all", "trail")]
        if c.basket_pnl >= c.target:
            return [("close_all", "target")]
        return []

    def _open_cycle(self, bid: float, ask: float) -> list[tuple]:
        eng = self.engine
        lot = eng.lot_for(self.balance)
        cap = eng.max_positions_for(self.balance)
        if self.hard_cap is not None:
            cap = min(cap, self.hard_cap)
        # The batch is a FUNCTION OF BALANCE, not the constant `per_rung`. The observed run opened
        # ONE position at $0.30-$0.99 and grew the count with the account (1,1,1,2,3,3,4,4,6,6)
        # before the lot ever moved. Hardcoding 3 put three times the intended exposure on the
        # smallest account -- and, because the depth budget was also priced in batches of 3, made
        # a $1 account look unfundable when it could hold exactly one position.
        per = (eng.positions_for_open(self.balance) if hasattr(eng, "positions_for_open")
               else max(1, getattr(eng, "per_rung", 1)))

        # Refuse rather than round up. A budget that affords zero positions means this account
        # cannot run this strategy -- opening one anyway would be a different, riskier strategy
        # wearing the same name.
        if cap < per:
            self.refused = (f"risk budget {eng.risk_pct:.0f}% of ${self.balance:,.2f} affords "
                            f"{cap} positions at {lot} lot; {per} needed to open")
            self.phase = PARKED
            self.park_reason = self.refused
            return []
        if lot < eng.min_lot:
            self.refused = f"required lot {lot} below minimum {eng.min_lot}"
            self.phase = PARKED
            self.park_reason = self.refused
            return []

        self.n_cycles += 1
        self.cycle = Cycle(
            index=self.n_cycles, side=self.direction, unit_lot=lot,
            balance_open=self.balance, target=eng.take_profit_for(self.balance),
            depth_cap=cap, group=per,
        )
        self.phase = OPEN
        return [("open", self.direction, lot, per)]

    def _adverse(self, bid: float, ask: float) -> float:
        """How far price has run AGAINST the basket, from the first entry, in $/oz.

        Measured on the side the basket would close at, so it is the move the basket has actually
        suffered rather than a mid-price approximation.
        """
        c = self.cycle
        if c is None or not c.entries:
            return 0.0
        return (c.first_entry - bid) if c.side == "buy" else (ask - c.first_entry)

    def basket_pnl(self, bid: float, ask: float) -> float:
        """Open P&L, marked at the price each leg would actually CLOSE at.

        A buy closes on the bid and a sell on the ask, so the spread is inside this number. Using
        the mid here would report a profit the account could not realise, and the target would
        fire early on every cycle.
        """
        total = 0.0
        for p in self.positions.values():
            px = bid if p.side == "buy" else ask
            move = (px - p.entry) if p.side == "buy" else (p.entry - px)
            total += move * p.lot * CONTRACT
        return total

    # ---------------- what a caller renders ----------------

    def status(self) -> dict:
        c = self.cycle
        return {
            "phase": self.phase,
            "direction": self.direction,
            "balance": round(self.balance, 2),
            "cycles": self.n_cycles,
            "park_reason": self.park_reason,
            "cycle": None if c is None else {
                "index": c.index, "unit_lot": c.unit_lot, "positions": c.positions,
                "rungs": c.rungs, "depth_cap": c.depth_cap, "depth_left": c.depth_left,
                "basket_pnl": round(c.basket_pnl, 2), "target": round(c.target, 2),
                "worst_pnl": round(c.worst_pnl, 2),
                "worst_pct_of_balance": round(c.worst_pct_of_balance, 1),
                "was_underwater": c.was_underwater,
            },
        }
