"""The complete rule set, as executable config plus what can honestly be simulated.

The lot engine alone is not buildable -- a grid needs an entry, an add trigger, a cap and an
exit. This carries all of them, each with the spread of the measurement behind it.

    python strategy_spec.py --out "D:\\llm\\ios\\xausd_video_out"

Writes strategy_spec.json and strategy_sim.csv.

WHAT THIS CANNOT SIMULATE, stated up front: the extracted data is per-cycle aggregates, not
ticks. So changing the exit target or the position cap cannot be re-run against the price path
-- there is no intra-cycle series to re-run against. The simulation below varies the LOT (which
is scale-invariant and therefore safe to vary) and holds the observed exit and drawdown
percentages fixed. Cap sensitivity is reported as the range it could move, not as a result.

NEXT STEP: replay against real XAUUSD data, then demo. The three parameters this file could not
test -- position cap, add step, and whether the ~29%-of-balance exit is a rule or an artifact of
one path -- all become testable the moment there is a tick series to run against. See
lot_position_engine.py for what that replay needs.

Analysis only. Nothing here sizes or places a live order, on any account, demo or real.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass, asdict, field


@dataclass
class StrategySpec:
    """Every rule, with the measurement behind it.

    Confidence notes are part of the spec on purpose: several of these rest on small clean
    samples, and a builder needs to know which numbers are firm and which are indicative.
    """
    # --- sizing: lot = a * balance^b, floored at the broker minimum ---
    # Defaults are the SURVIVAL-fitted values, not the best fit to the operator's own lots.
    # Fitting to what they actually traded (a=0.00211, b=0.76) reproduces their choices well but
    # blows the account when replayed -- worst drawdown 140% of balance. These are the most
    # aggressive constants that hold every drawdown in the record.
    lot_a: float = 0.00891
    lot_b: float = 0.58           # exponent 0.58-0.84 across fits; always < 1 (sub-linear)
    min_lot: float = 0.01         # confirmed: $0.30 balance trades 0.01 on Exness unlimited
    lot_ladder: list[float] = field(default_factory=lambda:
                                    [0.01, 0.04, 0.07, 0.09, 0.10, 0.33, 0.99, 1.99, 3.99, 6.88])
    snap_to_ladder: bool = True   # the survival fit was found on the ladder form

    # --- grid ---
    add_step: float = 0.18        # $/oz adverse before the next rung; median of 78 real adds
    # SETTLED BY DIRECT OBSERVATION -- see OPERATOR_VERIFIED.md. Cycle 8 opened four trades at one
    # price, cycle 7 four at 4049.145, cycle 16 four then four more. Entries arrive in simultaneous
    # groups of 2-4.
    #
    # This briefly read 1, from add_events.add_type (116 new_rung vs 17 same_rung). That counted
    # add EVENTS, not positions per event, and was wrong. See the confounding note on
    # Engine.per_rung: this and add_step are not separately identifiable from the drawdown data.
    positions_per_rung: int = 3
    max_positions: int = 27       # clean-only p90. Including misaligned states it reads 34.

    # --- exit ---
    take_profit_pct: float = 26.3  # of balance. p25 16%, p75 36%; 1.8x spread, tightest measure
    # An absolute or per-lot target was tested and fits far worse (5.2x and 1112x spread).

    # --- not derivable from this recording ---
    direction: str = "external"   # persists in runs of ~3, 71% after a win. A signal we cannot see.

    def lot_for(self, balance: float) -> float:
        if balance <= 0:
            return self.min_lot
        raw = self.lot_a * balance ** self.lot_b
        if self.snap_to_ladder:
            below = [v for v in self.lot_ladder if v <= raw]
            raw = below[-1] if below else self.min_lot
        return max(self.min_lot, round(raw, 2))

    def take_profit_for(self, balance: float) -> float:
        return balance * self.take_profit_pct / 100.0


def load_cycles(out_dir: str) -> list[dict]:
    with open(os.path.join(out_dir, "cycle_table.json"), encoding="utf-8") as fh:
        rows = json.load(fh)
    out = []
    for i, r in enumerate(rows):
        if not (r["bal_open"] and r["lot"]):
            continue
        nxt = rows[i + 1]["bal_open"] if i + 1 < len(rows) else None
        out.append({
            "cycle": r["cycle"], "bal_open": r["bal_open"], "lot": float(r["lot"]),
            "profit": (nxt - r["bal_open"]) if nxt else None,
            "pnl_at_exit": r["pnl_before"], "worst_dd": r["worst_dd"],
            "positions": r["nder_before"], "quality": r["quality"],
        })
    return out


def holdout_take_profit(cycles: list[dict], split: int = 30) -> dict:
    """Does the ~26%-of-balance exit target hold on cycles the fit never saw?

    This is the single most load-bearing number in the spec, so it gets tested out of sample
    rather than quoted from the fit.
    """
    def pcts(cs):
        return sorted(100 * c["pnl_at_exit"] / c["bal_open"]
                      for c in cs
                      if c["pnl_at_exit"] is not None and c["bal_open"] and c["pnl_at_exit"] > 0)
    tr, te = pcts(cycles[:split]), pcts(cycles[split:])
    med = lambda v: v[len(v) // 2] if v else None
    return {
        "train_n": len(tr), "train_median": round(med(tr), 1) if tr else None,
        "test_n": len(te), "test_median": round(med(te), 1) if te else None,
        "all_median": round(med(pcts(cycles)), 1),
        "all_p25": round(pcts(cycles)[len(pcts(cycles)) // 4], 1),
        "all_p75": round(pcts(cycles)[3 * len(pcts(cycles)) // 4], 1),
    }


def simulate(spec: StrategySpec, cycles: list[dict]) -> list[dict]:
    """Compound the spec's lot through the observed price action, gated on survival.

    Only the lot varies. Each cycle's profit-per-lot and drawdown-per-lot are properties of the
    price path, so scaling the lot scales both -- which is exactly the trade-off being tested.
    Survival is a hard gate: a rule that compounds faster but cannot hold the observed
    excursion has not done better.
    """
    bal = next((c["bal_open"] for c in cycles if c["profit"] is not None), 0.30)
    out = []
    for c in cycles:
        if c["profit"] is None:
            continue
        lot = spec.lot_for(bal)
        scale = lot / c["lot"]
        profit = c["profit"] * scale
        dd = (c["worst_dd"] or 0) * scale
        survived = bal + dd > 0
        out.append({
            "cycle": c["cycle"],
            "actual_balance": round(c["bal_open"], 2), "spec_balance": round(bal, 2),
            "actual_lot": c["lot"], "spec_lot": lot,
            "spec_profit": round(profit, 2),
            "spec_drawdown": round(dd, 2),
            "drawdown_pct": round(100 * abs(dd) / bal, 1) if bal > 0 else None,
            "target_profit": round(spec.take_profit_for(bal), 2),
            "actual_pct_of_balance": (round(100 * c["pnl_at_exit"] / c["bal_open"], 1)
                                      if c["pnl_at_exit"] is not None and c["bal_open"] else None),
            "survived": "yes" if survived else "NO",
            "quality": c["quality"],
        })
        if not survived:
            out[-1]["blown"] = "yes"
            return out
        bal += profit
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cycles = load_cycles(a.out)
    spec = StrategySpec()
    print(f"cycles: {len(cycles)}\n")

    print("EXIT RULE HOLD-OUT  (the most load-bearing number in the spec)")
    ho = holdout_take_profit(cycles)
    print(f"  fitted on first {ho['train_n']} cycles : median {ho['train_median']}% of balance")
    print(f"  tested on next  {ho['test_n']} cycles : median {ho['test_median']}% of balance")
    print(f"  all cycles                  : median {ho['all_median']}%  "
          f"(p25 {ho['all_p25']}%, p75 {ho['all_p75']}%)")
    if ho["train_median"] and ho["test_median"]:
        move = abs(ho["test_median"] - ho["train_median"]) / ho["train_median"]
        print(f"  -> out-of-sample median moves {100*move:.0f}%  "
              f"{'(holds)' if move < 0.35 else '(DOES NOT HOLD -- treat 26% as indicative)'}")

    print("\nSIMULATION  (spec picks the lot; exit % and drawdown % from the observed path)")
    sim = simulate(spec, cycles)
    final = sim[-1]["spec_balance"] + sim[-1]["spec_profit"] if sim else 0
    actual = cycles[-1]["bal_open"] + (cycles[-1]["profit"] or 0)
    blown = any(s.get("blown") for s in sim)
    worst = max((s["drawdown_pct"] or 0) for s in sim)
    print(f"  final ${final:,.2f}  vs actual ${actual:,.2f}  ({final/actual:.2f}x)"
          + ("   ACCOUNT BLOWN" if blown else ""))
    print(f"  worst drawdown reached: {worst:.0f}% of balance")

    # The contrast that justifies the defaults: fitting to what the operator actually traded
    # reproduces their lots well and then kills the account.
    fitted = StrategySpec(lot_a=0.00211, lot_b=0.76, snap_to_ladder=False)
    fs = simulate(fitted, cycles)
    ffinal = fs[-1]["spec_balance"] + fs[-1]["spec_profit"] if fs else 0
    fworst = max((x["drawdown_pct"] or 0) for x in fs)
    print(f"  fitting instead to the operator's OWN lots (a=0.00211, b=0.76):")
    print(f"    final ${ffinal:,.2f} ({ffinal/actual:.2f}x)   worst drawdown {fworst:.0f}%"
          + ("   ACCOUNT BLOWN" if any(x.get("blown") for x in fs) else ""))

    print("\nRISK / REWARD, as the data actually shows it")
    ex = [s["actual_pct_of_balance"] for s in sim if s["actual_pct_of_balance"]]
    dds = sorted((s["drawdown_pct"] or 0) for s in sim)
    exs = sorted(x for x in ex if x > 0)
    print(f"  gain at exit : median {exs[len(exs)//2]:.0f}% of balance")
    print(f"  risk to get there: median {dds[len(dds)//2]:.0f}% of balance, "
          f"p90 {dds[int(len(dds)*.9)]:.0f}%, max {dds[-1]:.0f}%")
    print(f"  -> roughly {dds[len(dds)//2]/max(exs[len(exs)//2],1):.1f}x as much risked as gained, "
          f"per cycle")

    print("\nCAP SENSITIVITY -- reported as a limit, not a result")
    print("  The cap cannot be re-simulated: the data is per-cycle aggregates, so there is no")
    print("  intra-cycle series to re-run with a different cap. What is measurable:")
    pos = sorted(c["positions"] for c in cycles if c["positions"])
    for name, v in (("p75", pos[3 * len(pos) // 4]), ("p90 clean", 27), ("p90 all data", 34),
                    ("max observed", pos[-1])):
        n = sum(1 for p in pos if p > v)
        print(f"    cap {name:14s} = {v:>4.0f}  ->  {n:>2d}/{len(pos)} observed cycles "
              f"({100*n/len(pos):.0f}%) would have been cut short")

    with open(os.path.join(a.out, "strategy_sim.csv"), "w", newline="", encoding="utf-8") as fh:
        cols = list(dict.fromkeys(k for row in sim for k in row))
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(sim)
    payload = {
        "spec": asdict(spec),
        "exit_holdout": ho,
        "simulation": {"final": round(final, 2), "actual": round(actual, 2),
                       "ratio": round(final / actual, 3), "blown": blown,
                       "worst_drawdown_pct": worst},
        "confidence": {
            "lot_exponent": "0.58-0.84 across fits; hold-out weak (9 ladder steps)",
            "add_step": "median of 78 real adds; only 11 rows fully clean",
            "max_positions": "clean-only p90; 34 including rows_misaligned states",
            "take_profit_pct": f"median {ho['all_median']}%, p25 {ho['all_p25']}%, p75 {ho['all_p75']}%",
            "direction": "not derivable from this recording; treat as an input",
            "leverage": "unlimited (Exness demo, confirmed: $0.30 balance trades 0.01 lot)",
        },
    }
    with open(os.path.join(a.out, "strategy_spec.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    print(f"\n-> strategy_spec.json, strategy_sim.csv (in {a.out})")


if __name__ == "__main__":
    main()
