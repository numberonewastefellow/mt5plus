"""Exploratory analysis of the extracted MT5 session.

Reconstructs the trading cycles from unique.csv and reports how the strategy actually behaved:
lot progression, position counts, drawdown carried, and what each cycle earned.

    python eda.py --out "D:\\llm\\ios\\xausd_video_out"

Writes cycles.csv next to unique.csv and prints a summary. Figures in this report come from
OCR of a phone-camera recording, so treat them as close-but-not-exact; rows carrying a `flag`
in unique.csv are the ones to distrust first.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter


def num(row: dict, key: str):
    try:
        return float(row[key])
    except (ValueError, KeyError):
        return None


def build_cycles(rows: list[dict]) -> list[dict]:
    by_id: dict[int, list[dict]] = {}
    for r in rows:
        by_id.setdefault(int(r["cycle_id"]), []).append(r)

    cycles = []
    for cid, rs in sorted(by_id.items()):
        if cid == 0:
            continue
        live = [r for r in rs if not r["flat"]]
        if not live:
            continue
        balances = [num(r, "balance") for r in live if num(r, "balance") is not None]
        pnls = [num(r, "pnl_total") for r in live if num(r, "pnl_total") is not None]
        lots = [float(x) for r in live for x in r["position_lots"].split()
                if _isfloat(x)]
        npos = [int(r["n_positions_visible"]) for r in live]
        ltps = [num(r, "ltp") for r in live if num(r, "ltp") is not None]
        dirs = Counter(d for r in live for d in r["position_dirs"].split())

        # Balance is fixed while a grid is running -- it only moves when something closes -- so
        # the MODE over the open phase is the cycle's opening balance, immune both to OCR slips
        # and to the closing jump landing inside the cycle (the flat screen between cycles is
        # brief and is not always captured as its own state).
        start_bal = Counter(balances).most_common(1)[0][0] if balances else None
        end_bal = None

        cycles.append({
            "cycle_id": cid,
            "frame_first": live[0]["frame_idx"],
            "frame_last": live[-1]["frame_idx"],
            "t_start_s": float(live[0]["video_time_s"]),
            "t_end_s": float(live[-1]["video_time_s"]),
            "clock_start": live[0]["clock_est"],
            "clock_end": live[-1]["clock_est"],
            "states": len(live),
            "balance_start": start_bal,
            "balance_end": end_bal,
            "profit": (end_bal - start_bal) if (end_bal is not None and start_bal is not None) else None,
            "max_positions_visible": max(npos) if npos else 0,
            "lot_min": min(lots) if lots else None,
            "lot_max": max(lots) if lots else None,
            "lot_first": lots[0] if lots else None,
            "worst_drawdown": min(pnls) if pnls else None,
            "best_openpnl": max(pnls) if pnls else None,
            "ltp_min": min(ltps) if ltps else None,
            "ltp_max": max(ltps) if ltps else None,
            "direction": dirs.most_common(1)[0][0] if dirs else "",
        })

    # A cycle's realised result is the step up to the next cycle's opening balance. Derived
    # here, after every cycle has a robust opening balance, rather than from a closing frame
    # that may never have been captured.
    for cur, nxt in zip(cycles, cycles[1:]):
        if cur["balance_start"] is not None and nxt["balance_start"] is not None:
            cur["balance_end"] = nxt["balance_start"]
            cur["profit"] = nxt["balance_start"] - cur["balance_start"]
    return cycles


def _isfloat(x: str) -> bool:
    try:
        float(x)
        return True
    except ValueError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with open(os.path.join(a.out, "unique.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    cycles = build_cycles(rows)
    path = os.path.join(a.out, "cycles.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cycles[0].keys()))
        w.writeheader()
        w.writerows(cycles)

    bals = [num(r, "balance") for r in rows if num(r, "balance") is not None]
    lots = sorted({float(x) for r in rows for x in r["position_lots"].split() if _isfloat(x)})
    profits = [c["profit"] for c in cycles if c["profit"] is not None]
    dds = [c["worst_drawdown"] for c in cycles if c["worst_drawdown"] is not None]

    print("=" * 72)
    print("SESSION")
    print("=" * 72)
    print(f"  states                {len(rows)}")
    print(f"  cycles                {len(cycles)}")
    print(f"  balance               {bals[0]:.2f} -> {bals[-1]:.2f}   (peak {max(bals):.2f})")
    if bals[0] > 0:
        print(f"  growth                {bals[-1] / bals[0]:,.0f}x")
    print(f"  wall clock            {rows[0]['clock_est']} -> {rows[-1]['clock_est']}")
    ltps = [num(r, "ltp") for r in rows if num(r, "ltp") is not None]
    if ltps:
        print(f"  XAUUSD range          {min(ltps):.3f} - {max(ltps):.3f}")

    print("\n" + "=" * 72)
    print("LOT SIZES")
    print("=" * 72)
    print(f"  distinct lots         {len(lots)}")
    print(f"  range                 {min(lots):.2f} - {max(lots):.2f}")
    print(f"  values                {', '.join(f'{x:g}' for x in lots[:24])}"
          + (" ..." if len(lots) > 24 else ""))

    print("\n" + "=" * 72)
    print("CYCLES")
    print("=" * 72)
    print(f"  {'id':>3s} {'clock':>13s} {'states':>7s} {'dir':>5s} {'maxpos':>7s} "
          f"{'lots':>15s} {'bal_start':>11s} {'profit':>10s} {'worst_dd':>10s}")
    for c in cycles:
        lot = (f"{c['lot_min']:g}-{c['lot_max']:g}"
               if c["lot_min"] is not None else "-")
        print(f"  {c['cycle_id']:>3d} {c['clock_start']:>6s}-{c['clock_end']:<6s} "
              f"{c['states']:>7d} {c['direction']:>5s} {c['max_positions_visible']:>7d} "
              f"{lot:>15s} "
              f"{('%.2f' % c['balance_start']) if c['balance_start'] is not None else '-':>11s} "
              f"{('%+.2f' % c['profit']) if c['profit'] is not None else '-':>10s} "
              f"{('%.2f' % c['worst_drawdown']) if c['worst_drawdown'] is not None else '-':>10s}")

    if profits:
        wins = [p for p in profits if p > 0]
        print("\n" + "=" * 72)
        print("OUTCOMES")
        print("=" * 72)
        print(f"  cycles with a result  {len(profits)}")
        print(f"  winners               {len(wins)} ({100 * len(wins) / len(profits):.0f}%)")
        print(f"  total profit          {sum(profits):+,.2f}")
        print(f"  median profit         {sorted(profits)[len(profits) // 2]:+,.2f}")
        print(f"  largest win           {max(profits):+,.2f}")
        print(f"  largest loss          {min(profits):+,.2f}")
    if dds:
        print(f"  worst open drawdown   {min(dds):,.2f}")
        print(f"  median cycle drawdown {sorted(dds)[len(dds) // 2]:,.2f}")

    with open(os.path.join(a.out, "eda_summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"cycles": cycles, "lots": lots,
                   "balance_start": bals[0], "balance_end": bals[-1],
                   "balance_peak": max(bals), "states": len(rows)}, fh, indent=1)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
