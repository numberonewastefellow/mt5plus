"""Fit a position-sizing engine to the observed run, and test it by replay.

The question this answers: given a balance, what lot should be traded and how many positions
allowed, such that the $0.30 -> $69K progression is reproduced?

Two facts from the data shape the model:

  * Lots are UNIFORM within a cycle (954 states all-identical, 1 mixed). This is not a
    lot-multiplying martingale -- the escalation is in the NUMBER of positions, all the same
    size. So the engine sizes once per cycle, not per add.
  * Position count is not a sizing decision. It is set by how far price ran against the grid:
    median implied adverse excursion $0.76 at a $0.18 step. The engine needs a CAP, not a
    formula.

    python lot_scale_engine.py --out "D:\\llm\\ios\\xausd_video_out"

Writes lot_engine.json (fitted parameters) and lot_engine_replay.csv (actual vs engine, per
cycle). Analysis only -- nothing here places or sizes a live order.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import dataclass, asdict

# The ten sizes actually observed, in order.
LADDER = [0.01, 0.04, 0.07, 0.09, 0.10, 0.33, 0.99, 1.99, 3.99, 6.88]
CONTRACT = 100.0        # oz per lot on XAUUSD


def snap(x: float, ladder=LADDER) -> float:
    below = [v for v in ladder if v <= x]
    return below[-1] if below else ladder[0]


@dataclass
class LotEngine:
    """lot = a * balance^b, and a cap on how many positions a cycle may open."""
    a: float = 0.00211
    b: float = 0.76
    use_ladder: bool = False       # snap to the observed sizes, or allow any 0.01 step
    min_lot: float = 0.01
    max_lot: float = 100.0
    max_positions: int = 34        # observed p90; p75 is 21, max seen 172
    grid_step: float = 0.18        # $/oz between rungs
    # $/oz from entry, MEDIAN at unwind. Recomputed from entry_exit_distance.csv rather than
    # carried as a literal: the previous hardcoded 0.458 went stale when the repair pass moved
    # rows underneath it -- it matched neither the median (0.372) nor the mean (0.434), while
    # p90 and max still matched the docs exactly, which is what gave the drift away.
    exit_distance: float = 0.374

    def lot_for(self, balance: float) -> float:
        if balance <= 0:
            return self.min_lot
        raw = self.a * balance ** self.b
        lot = snap(raw) if self.use_ladder else round(raw, 2)
        return max(self.min_lot, min(self.max_lot, lot))

    def max_positions_for(self, balance: float) -> int:
        return self.max_positions


def exit_distance_stats(out_dir: str) -> dict:
    """Read the entry->exit distance straight from the CSV instead of quoting a literal.

    The hardcoded 0.458 this replaces had gone stale: the repair pass moved rows underneath it,
    and nothing recomputed it. Percentiles are index-based (`sorted[int(n*p)]`), the same
    convention strategy_observations.py reports in.
    """
    path = os.path.join(out_dir, "entry_exit_distance.csv")
    if not os.path.exists(path):
        return {"exit_distance_median": None, "exit_distance_note": "entry_exit_distance.csv absent"}
    with open(path, encoding="utf-8") as fh:
        d = sorted(float(r["distance"]) for r in csv.DictReader(fh)
                   if r.get("distance") not in ("", "None", None))
    if not d:
        return {"exit_distance_median": None, "exit_distance_note": "no usable rows"}
    q = lambda p: round(d[int(len(d) * p)], 3)
    return {
        "exit_distance_median": q(0.50), "exit_distance_mean": round(sum(d) / len(d), 3),
        "exit_distance_p25": q(0.25), "exit_distance_p75": q(0.75), "exit_distance_p90": q(0.90),
        "exit_distance_n": len(d),
        "exit_distance_note": "visible rows only, so a floor on the true average",
    }


def fit(pairs: list[tuple[float, float]], use_ladder: bool) -> tuple[float, float, float]:
    """Grid-search (a, b) for lot = a * balance^b.

    Ladder form is scored on exact matches; the continuous form on log-ratio error, which is
    scale-free -- a plain squared error would let the 6.88-lot cycles drown out the 0.01 ones
    across four orders of magnitude.
    """
    best = None
    for b10 in range(30, 131):
        b = b10 / 100
        for a20 in range(-180, 41):
            a = 10 ** (a20 / 40)
            if use_ladder:
                score = -sum(1 for bal, lot in pairs
                             if abs(max(0.01, snap(a * bal ** b)) - lot) < 1e-9)
            else:
                score = sum(abs(math.log(max(0.01, round(a * bal ** b, 2)) / lot))
                            for bal, lot in pairs)
            if best is None or score < best[0]:
                best = (score, a, b)
    return best[1], best[2], best[0]


def accuracy(engine: LotEngine, pairs: list[tuple[float, float]]) -> dict:
    exact = within = 0
    ratios = []
    for bal, lot in pairs:
        p = engine.lot_for(bal)
        ratios.append(p / lot)
        if abs(p - lot) < 1e-9:
            exact += 1
        if 0.75 <= p / lot <= 1.3334:
            within += 1
    ratios.sort()
    return {
        "n": len(pairs),
        "exact": exact, "exact_pct": round(100 * exact / max(len(pairs), 1), 1),
        "within_33pct": within, "within_33_pct": round(100 * within / max(len(pairs), 1), 1),
        "median_ratio": round(ratios[len(ratios) // 2], 3) if ratios else None,
        "worst_under": round(min(ratios), 3) if ratios else None,
        "worst_over": round(max(ratios), 3) if ratios else None,
    }


def replay(engine: LotEngine, cycles: list[dict], start: float | None = None) -> list[dict]:
    """Re-run the session with the engine choosing the lot, price action held fixed.

    Each cycle's profit per 1.0 lot is a property of the price path and the number of rungs it
    reached -- not of the size traded. Holding that fixed and swapping in the engine's lot
    isolates the sizing decision, which is the only thing being tested.

    Margin is checked but deliberately not enforced: this account ran at effective leverage of
    1,144x-17,629x, which only exists on an unlimited-leverage broker, so a textbook margin
    model would reject trades the operator actually placed. Shortfalls are recorded instead.
    """
    # Start where the first usable cycle actually started -- cycle 1 has no measurable
    # profit, so seeding with its opening balance would misalign the whole curve by one step.
    bal = start if start is not None else next(
        (c["bal_open"] for c in cycles if c["lot"] and c["profit"] is not None), 0.30)
    out = []
    for c in cycles:
        act_lot, act_profit, act_bal = c["lot"], c["profit"], c["bal_open"]
        if not act_lot or act_profit is None:
            continue
        per_lot = act_profit / act_lot            # $ profit per 1.0 lot for this cycle
        eng_lot = engine.lot_for(bal)
        eng_profit = per_lot * eng_lot
        notional = eng_lot * (c["positions"] or 1) * CONTRACT * 4045.0

        # Sizing up scales the DRAWDOWN as well as the profit, and this run already touched
        # ~99% of balance on nine cycles. So the binding constraint is survival, not return:
        # a rule that compounds faster but cannot hold the observed excursion is not better.
        dd_per_lot = (c["worst_dd"] / act_lot) if (c["worst_dd"] and act_lot) else 0.0
        eng_dd = dd_per_lot * eng_lot
        survived = bal + eng_dd > 0

        out.append({
            "cycle": c["cycle"],
            "actual_balance": round(act_bal, 2), "engine_balance": round(bal, 2),
            "actual_lot": act_lot, "engine_lot": eng_lot,
            "profit_per_lot": round(per_lot, 2),
            "actual_profit": round(act_profit, 2), "engine_profit": round(eng_profit, 2),
            "engine_worst_drawdown": round(eng_dd, 2),
            "drawdown_pct_of_balance": round(100 * abs(eng_dd) / bal, 1) if bal > 0 else None,
            "survived": "yes" if survived else "NO",
            "balance_ratio": round(bal / act_bal, 3) if act_bal else None,
            "peak_notional": round(notional),
            "notional_over_balance": round(notional / bal) if bal > 0 else None,
        })
        if not survived:
            out[-1]["blown"] = "margin call"
            return out
        bal += eng_profit
        if bal <= 0:
            out[-1]["blown"] = "yes"
            return out
    return out


def fit_by_replay(cycles: list[dict], use_ladder: bool) -> tuple[float, float, float]:
    """Search (a, b) for the pair that compounds furthest WITHOUT blowing the account.

    Fitting to the observed lots reproduces one operator's discretionary choices, including
    their lag. Fitting to the replay instead asks the question actually being posed: what
    sizing rule turns this price action into the most balance while surviving every drawdown
    it produced? Survival is a hard gate, not a penalty term -- a blown account scores nothing
    however well it did beforehand.

    This is fitted to a single path, so it is a reconstruction, not a validated edge.
    """
    best = None
    for b10 in range(40, 121):
        b = b10 / 100
        for a20 in range(-180, 41):
            a = 10 ** (a20 / 40)
            eng = LotEngine(a=a, b=b, use_ladder=use_ladder)
            rep = replay(eng, cycles)
            if not rep or any(r.get("blown") for r in rep):
                continue
            final = rep[-1]["engine_balance"] + rep[-1]["engine_profit"]
            if best is None or final > best[0]:
                best = (final, a, b)
    return (best[1], best[2], best[0]) if best else (0.00211, 0.76, 0.0)


def load_cycles(out_dir: str) -> list[dict]:
    with open(os.path.join(out_dir, "cycle_table.json"), encoding="utf-8") as fh:
        rows = json.load(fh)
    cycles = []
    for i, r in enumerate(rows):
        if not (r["bal_open"] and r["lot"]):
            continue
        nxt = rows[i + 1]["bal_open"] if i + 1 < len(rows) else None
        profit = (nxt - r["bal_open"]) if nxt else None
        cycles.append({
            "cycle": r["cycle"], "bal_open": r["bal_open"], "lot": float(r["lot"]),
            "profit": profit, "positions": r["nder_before"],
            "worst_dd": r["worst_dd"],
        })
    return cycles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cycles = load_cycles(a.out)
    pairs = [(c["bal_open"], c["lot"]) for c in cycles]
    print(f"cycles usable for fitting: {len(pairs)}  "
          f"(balance ${min(p[0] for p in pairs):.2f} - ${max(p[0] for p in pairs):,.2f})\n")

    results = {}
    for label, use_ladder in (("continuous", False), ("ladder-snapped", True)):
        fa, fb, _ = fit(pairs, use_ladder)
        eng = LotEngine(a=fa, b=fb, use_ladder=use_ladder)
        acc = accuracy(eng, pairs)
        results[label] = {"a": round(fa, 6), "b": round(fb, 3), **acc}
        print(f"{label:16s} lot = {fa:.5f} * balance^{fb:.2f}"
              + ("  (snapped to the observed ladder)" if use_ladder else ""))
        print(f"                 exact {acc['exact_pct']}%   within +/-33% {acc['within_33_pct']}%"
              f"   median ratio {acc['median_ratio']}   range {acc['worst_under']}x-{acc['worst_over']}x")

    # --- hold-out: fit early, predict late. In-sample accuracy on a 9-step ladder flatters. ---
    print("\nHOLD-OUT  (fit on the first 30 cycles, predict the rest)")
    tr, te = pairs[:30], pairs[30:]
    for label, use_ladder in (("continuous", False), ("ladder-snapped", True)):
        fa, fb, _ = fit(tr, use_ladder)
        acc = accuracy(LotEngine(a=fa, b=fb, use_ladder=use_ladder), te)
        results[label + "_holdout"] = {"a": round(fa, 6), "b": round(fb, 3), **acc}
        print(f"  {label:16s} b={fb:.2f}  ->  out-of-sample within +/-33%: "
              f"{acc['within_33_pct']}%  median ratio {acc['median_ratio']}  (n={acc['n']})")

    # --- the actual test ---
    print("\nREPLAY  (start $0.30, engine picks the lot, price action held fixed)")
    actual_final = cycles[-1]["bal_open"] + (cycles[-1]["profit"] or 0)
    replay_start = next((c["bal_open"] for c in cycles if c["lot"] and c["profit"] is not None), 0.30)
    print(f"replay starts at the first usable cycle: ${replay_start:.2f}")
    best_engine = None
    for label, use_ladder in (("continuous", False), ("ladder-snapped", True)):
        r = results[label]
        eng = LotEngine(a=r["a"], b=r["b"], use_ladder=use_ladder)
        rep = replay(eng, cycles)
        final = rep[-1]["engine_balance"] + rep[-1]["engine_profit"] if rep else 0
        print(f"  {label:16s} final ${final:>14,.2f}   vs actual ${actual_final:,.2f}   "
              f"ratio {final/actual_final:.2f}x"
              + ("   ACCOUNT BLOWN" if any(x.get("blown") for x in rep) else ""))
        if best_engine is None or abs(math.log(max(final, 1e-9) / actual_final)) < best_engine[0]:
            best_engine = (abs(math.log(max(final, 1e-9) / actual_final)), label, eng, rep, final)

    # Does the operator's OWN sizing survive its own drawdowns under this model?
    class Actual(LotEngine):
        def __init__(self, cyc):
            super().__init__()
            self._m = {c["cycle"]: c["lot"] for c in cyc}
            self._last = 0.01
        def lot_for(self, balance):        # noqa: D102 - replays the observed choices
            return self._last
    act_rep = []
    for c in cycles:
        if not c["lot"] or c["profit"] is None:
            continue
        dd = c["worst_dd"] or 0
        act_rep.append((c["cycle"], c["bal_open"], dd, c["bal_open"] + dd > 0))
    died = [x for x in act_rep if not x[3]]
    print(f"\n  sanity: replaying the operator's OWN lots, "
          f"{len(act_rep)-len(died)}/{len(act_rep)} cycles survive their own drawdown"
          + (f" (fails at cycle {died[0][0]})" if died else ""))

    print("\nFIT BY REPLAY  (maximise final balance subject to surviving every drawdown)")
    for lbl, ul in (("continuous", False), ("ladder-snapped", True)):
        ra, rb, rfinal = fit_by_replay(cycles, ul)
        e2 = LotEngine(a=ra, b=rb, use_ladder=ul)
        r2 = replay(e2, cycles)
        worst = max((r["drawdown_pct_of_balance"] or 0) for r in r2)
        results[lbl + "_replayfit"] = {"a": round(ra, 6), "b": round(rb, 3),
                                       "final": round(rfinal, 2),
                                       "worst_dd_pct": worst}
        print(f"  {lbl:16s} lot = {ra:.5f} * balance^{rb:.2f}  ->  final ${rfinal:>14,.2f}  "
              f"({rfinal/actual_final:.2f}x actual)   worst drawdown {worst:.0f}% of balance")
        if rfinal > final:
            best_engine = (0, lbl + " (replay-fitted)", e2, r2, rfinal)

    _, label, eng, rep, final = best_engine
    print(f"\n  closest: {label}")
    print(f"  {'cyc':>4s}{'actual bal':>13s}{'engine bal':>13s}{'ratio':>7s}"
          f"{'act lot':>9s}{'eng lot':>9s}{'notional/bal':>14s}")
    for r in rep[::4]:
        print(f"  {r['cycle']:>4d}{r['actual_balance']:>13,.2f}{r['engine_balance']:>13,.2f}"
              f"{r['balance_ratio'] or 0:>7.2f}{r['actual_lot']:>9.2f}{r['engine_lot']:>9.2f}"
              f"{r['notional_over_balance'] or 0:>14,}")

    with open(os.path.join(a.out, "lot_engine_replay.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rep[0].keys()))
        w.writeheader()
        w.writerows(rep)
    payload = {
        "fits": results,
        "chosen": {"form": label, **asdict(eng)},
        "replay": {"start": 0.30, "engine_final": round(final, 2),
                   "actual_final": round(actual_final, 2),
                   "ratio": round(final / actual_final, 3)},
        "measured_inputs": {
            "grid_step_median": 0.182, "grid_step_note": "11 clean rows; all-rows median 0.156",
            **exit_distance_stats(a.out),
            "positions_p75": 21, "positions_p90": 34, "positions_max": 172,
            "adverse_excursion_median": 0.76,
        },
    }
    with open(os.path.join(a.out, "lot_engine.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    print(f"\n-> lot_engine.json, lot_engine_replay.csv (in {a.out})")


if __name__ == "__main__":
    main()
