"""Lot and position sizing for the XAUUSD recovery grid.

Self-contained and importable -- no dependency on the OCR pipeline at runtime, so this module
can be lifted out on its own. The constants come from `strategy_spec.py`, which fitted them to
42 cycles of an observed run.

    python lot_position_engine.py --out "D:\\llm\\ios\\xausd_video_out"

The central idea: **the position cap is a risk budget, not a measurement.**

For n positions of lot L at rungs d apart, the distances from price form an arithmetic series,
so the open loss at full depth has a closed form:

    drawdown(n) = L * 100 * d * n(n+1)/2

That was derived without reference to the observed position counts and then checked against the
observed drawdowns. It means a cap does not have to be guessed: state how much drawdown is
acceptable and `max_safe_positions` returns the depth that implies. The observed operator's
effective cap corresponds to a risk budget this module will tell you outright -- see the report
it prints.

NEXT STEP -- replay against real market data on demo
----------------------------------------------------
Every constant here was fitted to ONE five-hour run reconstructed by OCR, in a $28 price range,
with nine lot steps. That is enough to specify the rules and nothing like enough to trust them.
The next step is to replay the whole rule set against real XAUUSD history and then on a demo
account, which is what turns these from a reconstruction into something measured.

What the replay needs, and what it will settle:

  * REAL PRICE DATA. The extraction gives per-cycle aggregates, not ticks, so three things
    could not be tested here and can be tested there: the position cap (no intra-cycle series
    existed to re-run), the add step (11 clean rows), and whether the ~29%-of-balance exit is a
    rule or a coincidence of this path. Pull with mt5.copy_ticks_range / copy_rates_range --
    read-only, and see analysis/harness/tickdata.py::load_ticks which already chunks it.
  * A DIRECTION SIGNAL. Direction is an input, not a rule: it persisted in runs of ~3 with 71%
    persistence after a win, which is a signal not visible in the recording. A replay has to
    supply one, and the result will say as much about that signal as about this engine.
  * THE SURVIVAL GATE KEPT ON. The observed run touched ~99% of balance on nine cycles. A
    replay that reports return without reporting worst drawdown has measured the wrong thing.

Expect the replay to disagree with these defaults. That is the point of running it: risk_pct
defaults to the observed median of 44%, which is more conservative than the operator actually
was, and the lot constants are survival-fitted rather than fitted to what they traded.

Analysis only. This module computes sizes; it does not place, modify or close anything, and no
order call belongs in it. Putting the engine on a demo account is a deliberate human action --
demo and real are treated identically here.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import dataclass, field

CONTRACT = 100.0        # oz per lot, XAUUSD


@dataclass
class Engine:
    """Every constant carries the confidence behind it -- several rest on small samples.

    Defaults are the SURVIVAL-fitted sizing, not the best fit to the operator's own lots.
    Fitting what they actually traded (lot_a=0.00211, lot_b=0.76) reproduces 74% of their
    choices and then blows the account on replay, at 140% of balance drawn down.
    """
    # sizing -- exponent measured 0.58-0.84 across independent fits; below 1 in all of them,
    # so lot grows more slowly than balance and risk per trade falls as the account grows
    lot_a: float = 0.00891
    lot_b: float = 0.58
    min_lot: float = 0.01           # confirmed: $0.30 balance trades 0.01 on Exness unlimited
    max_lot: float = 100.0
    lot_ladder: list[float] = field(default_factory=lambda:
                                    [0.01, 0.04, 0.07, 0.09, 0.10, 0.33, 0.99, 1.99, 3.99, 6.88])
    snap_to_ladder: bool = True

    # grid -- median of 78 real adds; only 11 rows fully clean, so treat as 0.10-0.18
    add_step: float = 0.18

    # Positions opened at each rung. SETTLED BY DIRECT OBSERVATION -- see OPERATOR_VERIFIED.md:
    # cycle 8 opened FOUR trades at one price (4050.155), cycle 7 four at 4049.145, cycle 16
    # four at 4049.462 then four more at 4049.409. Entries come in simultaneous groups of 2-4.
    #
    # An earlier value of 1.15 here was WRONG. It came from add_events.add_type (116 new_rung vs
    # 17 same_rung), which counts add EVENTS, not positions per event -- the position delta at an
    # add is median 2, and 26 of 45 cycles open with 3+ visible at once.
    #
    # CONFOUNDING, do not present either number as independently measured: `per_rung` and
    # `add_step` are not separately identifiable from the drawdown data, which constrains only
    # their product. per_rung=3 at a $0.54 step gives the same drawdown curve as per_rung=1 at
    # $0.18. The old "validation fits at 0.91x with per_rung=1" was two errors cancelling: an
    # inflated derived position count against an under-counted per_rung. Expect validate() to
    # read worse now -- that is the previous coincidence going away, not a regression.
    per_rung: int = 3

    # Depth is governed by a drawdown budget rather than a fixed count, because the observed
    # count is a derived figure and its cap of 27 exceeds the whole account below ~$6,000.
    #
    # The default IS what the run actually did: observed drawdown was a median 44% of the
    # balance a cycle opened with (54% across clean rows only), p75 80%, p90 100%. Seven of 44
    # cycles ran to 90% or beyond. Its correlation with log(balance) is -0.05, i.e. flat across
    # four orders of magnitude -- which is why one constant is the right shape here and not a
    # balance-dependent budget.
    risk_pct: float = 44.0

    # Optional hard ceiling on top of the budget. None means the budget alone decides.
    # The observed run reached 27 at its deepest; that was where price turned, not a limit.
    max_positions: int | None = None

    # exit -- 28.9% of balance is the typical OUTCOME, not the trigger.
    #
    # Verified against ground truth (the realised balance step): `cycle_table.pnl_before`
    # reproduces it on 25/42 cycles with median error $0.00, while `cycle_drawdown.final_pnl`
    # matches only 8/42 at median error $44.90. On the realised step the median is 28.9%
    # (p25 22.3%, p75 34.8%), so this constant is right.
    #
    # BUT THE MECHANISM IS A TRAILING STOP, not a threshold -- see OPERATOR_VERIFIED.md. Cycle 5
    # reached +0.71, did NOT close, and closed at +0.53 after a retrace; cycle 6 peaked at
    # $0.38/oz and closed at $0.28; cycle 7 peaked $0.32 and closed $0.19. Give-back from peak is
    # $0.06-0.13/oz and the median cycle keeps 75% of its peak. A fixed target cannot produce
    # cycle 5. Use this figure to SIZE expectations; use the trail to DECIDE the exit.
    take_profit_pct: float = 28.9

    # ---- THE DUAL-ARM EXIT (see OPERATOR_VERIFIED.md) ----
    #
    # NOTHING HERE IS FIXED -- not a dollar target, not a percentage of profit, not a trade count.
    # Both arms are fractions of BALANCE, so the rule is scale-free: it behaved the same at $1.47
    # and at $1,181. That is why the earlier $/oz framing failed -- it mixed lot size and position
    # count into what is really a risk fraction.
    #
    # Splitting all 40 profitable cycles by whether they gave anything back:
    #   closed AT their peak (n=14): peak = close = 32.2% of balance (median)
    #   gave back            (n=26): peaked at 59.8%, closed at 26.8%
    #
    # The peak-closers never exceeded ~32%; the give-backers overshot to ~60% then retraced. Two
    # arms, and which fires depends on the price path:
    #
    #   TARGET arm  -- price rises steadily, a poll catches it at ~30% -> close at the peak.
    #                  Cycle 10 is the proof: +1.76 on a 5.84 balance = 30.1%, closed with NO
    #                  give-back at all, having earlier dipped 2% without closing.
    #   TRAIL arm   -- price spikes between polls, the next poll sees a retrace -> close lower.
    #                  Cycles 5/6/7 peaked at 48%/57%/45%, all ABOVE the target, and closed below.
    #
    # The poll cadence is what lets a fast spike be missed; it is the reason a single rule could
    # never fit both groups. Cycle 7's +0.32 -> +0.08 -> +0.32 round trip took ~3 real seconds.
    exit_target_pct: float = 30.0        # close when basket P&L reaches this % of balance
    exit_giveback_pct: float = 15.0      # or when it retraces this % of balance from its peak
    exit_poll_seconds: float = 0.0       # 0 = check every tick; >0 = throttle, the latency arm

    # Legacy $/oz form, kept only so old callers do not break. REFUTED as a rule -- the give-back
    # is a fraction of balance, not a price distance. Use exit_giveback_pct.
    trail_give_back: float = 0.10

    # ---------------- sizing ----------------

    def lot_for(self, balance: float) -> float:
        if balance <= 0:
            return self.min_lot
        raw = self.lot_a * balance ** self.lot_b
        if self.snap_to_ladder:
            below = [v for v in self.lot_ladder if v <= raw]
            raw = below[-1] if below else self.min_lot
        return max(self.min_lot, min(self.max_lot, round(raw, 2)))

    def take_profit_for(self, balance: float) -> float:
        return balance * self.take_profit_pct / 100.0

    # ---------------- grid ----------------

    def max_positions_for(self, balance: float) -> int:
        """The depth the budget allows, tightened by any hard ceiling that was set.

        Sized in whole BATCHES of `_group_ladder(balance)`, not of the constant `per_rung`. Above
        $2.50 the two are identical, so the validated mapping (3, 3, 6, 6, 9, 9 against observed
        3, 3, 6, 6, 9, 6) is untouched. Below it the old form returned **0** -- a $1 account was
        told it could hold no positions, purely because the batch was hardcoded at 3.
        """
        n = self.max_safe_positions(balance, self.risk_pct, per=self._group_ladder(balance))
        return min(n, self.max_positions) if self.max_positions is not None else n

    def _group_ladder(self, balance: float) -> int:
        """The batch ladder alone, with no reference to the cap -- see positions_for_open.

        Kept separate so `max_positions_for` can size its budget in whole batches without the two
        calling each other in a circle.
        """
        if balance < 1.10:
            return 1
        if balance < 1.40:
            return 2
        if balance < 2.50:
            return 3
        return max(1, self.per_rung)

    def positions_for_open(self, balance: float) -> int:
        """How many positions to open SIMULTANEOUSLY at one price, given the balance.

        `per_rung` is a constant, and that is wrong at the bottom of the ladder. The observed run
        started at $0.30 and its group size grew with the account before the lot did:

            balance   0.30  0.76  0.99  1.14  1.47  2.00  2.84  3.60  4.76  5.84
            observed     1     1     1     2     3     3     4     4     6     6

        (`cycles.csv:max_positions_visible`; cycles 1-3 never added, so their group IS the total.)
        It then saturates -- OPERATOR_VERIFIED.md records "simultaneous groups of 2-4 at one price"
        at $2.84, $3.60, $23.73 and $1,181, because above ~$14 the LOT starts growing instead of the
        count. So this ladder stops at `per_rung` rather than continuing upward.

        Thresholds are read off six hand-verified cycles, so treat them as a shape rather than as
        precise boundaries. What matters is the bottom: a $1 account opens ONE position, not three.
        """
        g = self._group_ladder(balance)
        cap = self.max_positions_for(balance)
        # The cap is a whole number of batches (max_safe_positions returns rungs x group), so it is
        # >= g whenever the budget affords even one rung. Clamping is belt-and-braces.
        return max(1, min(g, cap)) if cap > 0 else g

    def rungs_for(self, n_positions: int) -> int:
        """How many price levels `n_positions` occupy, at `per_rung` positions each."""
        if n_positions <= 0:
            return 0
        return -(-n_positions // max(1, self.per_rung))    # ceil division

    def should_add(self, adverse_move: float, n_open: int, balance: float) -> bool:
        """Add another rung once price has run `add_step` further, if the budget still allows.

        `adverse_move` is measured from the FIRST entry, so the r-th rung opens at r*add_step --
        which is exactly the basket `drawdown_at` prices. The two used to disagree: this compared
        against `add_step * n_open` (positions), which only matched when per_rung was 1.
        """
        if n_open >= self.max_positions_for(balance):
            return False
        return adverse_move >= self.add_step * self.rungs_for(n_open)

    def drawdown_at(self, n: int, lot: float) -> float:
        """Open loss with n positions of `lot` open, at full grid depth.

        Rungs sit `add_step` apart with `per_rung` positions on each. The loss is priced with
        price one step BEYOND the last rung -- the instant before the next add would fire, which
        is the worst case at this depth and therefore the right basis for a risk budget.

        With r = rungs_for(n) and per_rung positions per level, the distances from that pricing
        point are r, r-1 ... 1 steps, so:

            drawdown = lot * 100 * add_step * per_rung * r(r+1)/2

        At per_rung = 1 this is the original lot*100*d*n(n+1)/2, unchanged. A partly-filled top
        rung is summed exactly rather than rounded up, so the two agree at every n.

        NOT included: the spread. This prices price DISTANCE only, while a real basket also
        carries the bid/ask on every open position -- `spread * n * lot * 100`, which is $74.25
        for 15 positions of 0.99 lot at the $0.05 gold spread. grid_replay.py's gate 3 measures
        the gap and finds this formula at 0.97x of realised worst drawdown for exactly that
        reason. So the risk budget derived from it UNDER-states true drawdown by about one
        spread per position; at tight caps that is noise, at depth 30+ it is not.
        """
        if n <= 0:
            return 0.0
        per = max(1, self.per_rung)
        r = self.rungs_for(n)
        steps = sum(r - (i // per) for i in range(n))   # exact, incl. a partial top rung
        return lot * CONTRACT * self.add_step * steps

    # ---------------- the cap, as a decision rather than a guess ----------------

    def max_safe_positions(self, balance: float, risk_pct: float,
                           lot: float | None = None, per: int | None = None) -> int:
        """Deepest grid whose full-depth drawdown stays inside `risk_pct` of balance.

        The inverse of drawdown_at. This is the intended way to set the cap: the data says how
        drawdown grows with depth, and only the operator can say how much is acceptable.

        `per` overrides the batch size for the calculation. It exists because the batch is a
        function of balance at the bottom of the ladder (see `positions_for_open`), and pricing a
        $1 account's depth in batches of 3 answers a question nobody asked. Omitted, it is
        `per_rung`, so every existing caller is unchanged.
        """
        lot = self.lot_for(balance) if lot is None else lot
        per = max(1, self.per_rung if per is None else per)
        budget = balance * risk_pct / 100.0
        k = budget / (lot * CONTRACT * self.add_step * per)
        r = int(math.floor((-1 + math.sqrt(1 + 8 * k)) / 2.0))    # deepest affordable RUNG
        return max(0, r * per)

    def risk_table(self, balance: float, depths=range(1, 51)) -> list[dict]:
        lot = self.lot_for(balance)
        out = []
        for n in depths:
            dd = self.drawdown_at(n, lot)
            out.append({"balance": round(balance, 2), "lot": lot, "positions": n,
                        "drawdown": round(dd, 2),
                        "drawdown_pct_of_balance": round(100 * dd / balance, 1) if balance else None,
                        "survivable": "yes" if dd < balance else "NO"})
        return out


# ------------------------------------------------------------------ validation & report

def validate(engine: Engine, out_dir: str) -> dict:
    """Check the closed form against the observed drawdowns.

    Only the cycles where the position list was NOT scrolled are usable: elsewhere the derived
    count is inflated, which shows up as the model appearing to under-predict.
    """
    path = os.path.join(out_dir, "cycle_table.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    trusted, scrolled = [], []
    for r in rows:
        lot = float(r["lot"]) if r["lot"] else None
        n, nv, dd = r["nder_before"], r["npos_before"], r["worst_dd"]
        if not (lot and n and dd):
            continue
        pred = engine.drawdown_at(int(round(n)), lot)
        if pred <= 0:
            continue
        (trusted if n <= (nv or 0) + 1 else scrolled).append(pred / abs(dd))
    def summarise(v):
        if not v:
            return None
        v = sorted(v)
        within = sum(1 for x in v if 1 / 1.67 <= x <= 1.67)
        return {"n": len(v), "median": round(v[len(v) // 2], 2),
                "within_1_67x": within, "pct": round(100 * within / len(v))}
    return {"list_not_scrolled": summarise(trusted), "list_scrolled": summarise(scrolled)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--risk", type=float, default=None,
                    help="drawdown budget as %% of balance (default: 44, the observed median)")
    a = ap.parse_args()
    eng = Engine() if a.risk is None else Engine(risk_pct=a.risk)

    print("LOT + POSITION ENGINE\n")
    print(f"  lot          = snap_to_ladder({eng.lot_a} * balance^{eng.lot_b}), floor {eng.min_lot}")
    print(f"  add          = every ${eng.add_step:.2f} further against the basket")
    print(f"  exit         = open P&L >= {eng.take_profit_pct}% of balance")
    print(f"  drawdown(n)  = lot * 100 * {eng.add_step} * n(n+1)/2\n")

    v = validate(eng, a.out)
    if v:
        print("VALIDATION of the drawdown formula against observed cycles")
        for k, s in v.items():
            if s:
                print(f"  {k:20s} n={s['n']:>3d}  median predicted/actual {s['median']:.2f}   "
                      f"within 1.67x: {s['pct']}%")
        print("  The scrolled group looks worse because its derived position count is inflated,")
        print("  not because the formula is wrong -- that asymmetry is the evidence for it.\n")

    print(f"DEPTH FROM THE RISK BUDGET  ({eng.risk_pct:.0f}% of balance"
          + ("  <- the observed median" if abs(eng.risk_pct - 44.0) < 0.01 else "") + ")")
    print(f"  {'balance':>12s}{'lot':>8s}{'safe depth':>12s}{'drawdown':>13s}{'% of bal':>10s}")
    for bal in (0.30, 10, 100, 1_000, 10_000, 50_000):
        lot = eng.lot_for(bal)
        n = eng.max_positions_for(bal)
        dd = eng.drawdown_at(n, lot)
        print(f"  {bal:>12,.2f}{lot:>8.2f}{n:>12d}{dd:>13,.2f}{100*dd/bal:>9.1f}%")

    print(f"\n  WHAT THE OBSERVED CAP ACTUALLY COSTS")
    print(f"  {'balance':>12s}{'lot':>8s}{'depth':>7s}{'drawdown':>13s}{'% of bal':>10s}")
    for bal in (0.30, 10, 100, 1_000, 10_000, 50_000):
        lot = eng.lot_for(bal)
        dd = eng.drawdown_at(27, lot)
        flag = "   <-- exceeds balance" if dd >= bal else ""
        print(f"  {bal:>12,.2f}{lot:>8.2f}{27:>7d}{dd:>13,.2f}"
              f"{100*dd/bal:>9.1f}%{flag}")
    print(f"\n  A cap of 27 is not a safety limit at these sizes -- it is the depth")
    print("  the operator happened to reach. Set the cap from a budget instead.")

    rows = []
    for bal in (0.30, 1, 5, 10, 50, 100, 500, 1_000, 5_000, 10_000, 25_000, 50_000, 69_000):
        rows.extend(eng.risk_table(bal))
    path = os.path.join(a.out, "risk_table.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n-> risk_table.csv ({len(rows)} rows: balance x depth 1-50) in {a.out}")


if __name__ == "__main__":
    main()
