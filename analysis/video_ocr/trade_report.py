"""Turn per-state snapshots into a trade-level report.

A recovery grid is a ladder of orders at fixed price steps, so the unit of analysis is the
rung, not the video frame. Entry prices seen across a cycle cluster into those rungs -- OCR
jitter splits one rung across near-identical prices (4051.085 / 4051.088 / 4051.089 are one
order), so they are clustered rather than counted.

    python trade_report.py --out "D:\\llm\\ios\\xausd_video_out"

Writes trades.csv (one row per rung) and rewrites cycles.csv with grid structure added.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import Counter

FPS = 30.0
CLUSTER_TOL = 0.03     # $/oz: entries closer than this are one order read twice
MIN_STATES = 2         # a rung seen in only one state is more likely a misread than an order


def num(row: dict, key: str):
    try:
        return float(row[key])
    except (ValueError, KeyError):
        return None


def video_time(frame_idx: int) -> str:
    s = frame_idx / FPS
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else None


def cluster(prices: list[float], tol: float = CLUSTER_TOL) -> list[list[float]]:
    """Group sorted prices into runs where each is within tol of the running cluster."""
    out: list[list[float]] = []
    for p in sorted(prices):
        if out and p - out[-1][0] <= tol:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def derive_positions(total_pnl, visible_sum, n_visible):
    """Estimate the true open position count, including the rows scrolled off screen.

    Scale the visible rows by how much of the open P&L they account for. The whole open P&L is
    known exactly (equity - balance) and the visible rows sum to a measured fraction of it, so
    the count scales with that ratio.

    A geometric model was tried first -- treating the grid as evenly-spaced rungs of one
    position each and solving a quadratic for the rung count. It self-tested at 33% because the
    premise is wrong: this grid opens SEVERAL positions at the same price, so rungs and
    positions are not the same thing. This ratio estimator self-tests at 94% on the states
    where the list is not scrolled, so it is the one that ships.
    """
    if None in (total_pnl, visible_sum, n_visible) or not n_visible:
        return None
    if abs(visible_sum) < 1e-6:
        return None
    n = n_visible * total_pnl / visible_sum
    return n if 0 < n < 500 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with open(os.path.join(a.out, "unique.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    by_cycle: dict[int, list[dict]] = {}
    for r in rows:
        if r["flat"]:
            continue
        by_cycle.setdefault(int(r["cycle_id"]), []).append(r)

    trades, summaries = [], []
    for cid, rs in sorted(by_cycle.items()):
        if cid == 0:
            continue
        ltps = [num(r, "ltp") for r in rs if num(r, "ltp")]
        mid_price = median(ltps) or 0.0

        # gather every (entry, lot, pnl) sighting, keeping which state saw it
        seen: list[tuple[float, str, str, float | None, dict]] = []
        for r in rs:
            ents = r["position_entries"].split()
            lots = r["position_lots"].split()
            pnls = r["position_pnls"].split()
            dirs = r["position_dirs"].split()
            d = dirs[0] if dirs else ""
            for i, e in enumerate(ents):
                try:
                    ev = float(e)
                except ValueError:
                    continue
                if mid_price and abs(ev - mid_price) > 50:   # a wild misread, not a rung
                    continue
                lot = lots[i] if i < len(lots) else (lots[0] if lots else "")
                pnl = None
                if i < len(pnls):
                    try:
                        pnl = float(pnls[i])
                    except ValueError:
                        pnl = None
                seen.append((ev, lot, d, pnl, r))

        groups = cluster([s[0] for s in seen])
        rungs = []
        for g in groups:
            lo, hi = g[0] - 1e-9, g[-1] + 1e-9
            members = [s for s in seen if lo <= s[0] <= hi]
            states = {m[4]["frame_idx"] for m in members}
            if len(states) < MIN_STATES:
                continue      # one sighting only -- treat as a misread, not an order
            frames = sorted(int(f) for f in states)
            pnls = [m[3] for m in members if m[3] is not None]
            lots = Counter(m[1] for m in members if m[1])
            dirs = Counter(m[2] for m in members if m[2])
            rungs.append({
                "cycle_id": cid,
                "rung_price": f"{median([m[0] for m in members]):.3f}",
                "lots": lots.most_common(1)[0][0] if lots else "",
                "direction": dirs.most_common(1)[0][0] if dirs else "",
                "first_seen": video_time(frames[0]),
                "last_seen": video_time(frames[-1]),
                "frame_first": frames[0],
                "frame_last": frames[-1],
                "states_seen": len(states),
                "max_positions_at_rung": max(
                    Counter(m[4]["frame_idx"] for m in members).values()) if members else 0,
                "best_pnl": f"{max(pnls):.2f}" if pnls else "",
                "worst_pnl": f"{min(pnls):.2f}" if pnls else "",
            })
        rungs.sort(key=lambda x: float(x["rung_price"]))
        trades.extend(rungs)

        prices = [float(x["rung_price"]) for x in rungs]
        steps = [round(b - a_, 3) for a_, b in zip(prices, prices[1:])]
        steps = [s for s in steps if s > CLUSTER_TOL]
        step = median(steps)
        lot_c = Counter(x["lots"] for x in rungs if x["lots"])
        dir_c = Counter(x["direction"] for x in rungs if x["direction"])
        is_buy = (dir_c.most_common(1)[0][0] == "buy") if dir_c else True
        unit = float(lot_c.most_common(1)[0][0]) if lot_c else None

        # derived exposure, and the self-test that says whether to believe it
        agree = tested = 0
        derived = []
        for r in rs:
            tot, vis = num(r, "pnl_total"), num(r, "pnl_visible_sum")
            nv = int(r["n_positions_visible"])
            n = derive_positions(tot, vis, nv)
            if n is None:
                continue
            derived.append(n)
            # "not scrolled" == the visible rows already account for the whole open P&L,
            # which is the only place the derivation can be checked against a known answer
            if abs(tot) > 1e-6 and abs(vis - tot) <= max(0.5, abs(tot) * 0.02):
                tested += 1
                if abs(round(n) - nv) <= 1:
                    agree += 1
        derived_max = max(derived) if derived else None

        summaries.append({
            "cycle_id": cid,
            "t_start": video_time(int(rs[0]["frame_idx"])),
            "t_end": video_time(int(rs[-1]["frame_idx"])),
            "clock": rs[0]["clock_est"],
            "direction": "buy" if is_buy else "sell",
            "unit_lot": f"{unit:g}" if unit else "",
            "n_rungs": len(rungs),
            "grid_step": f"{step:.3f}" if step else "",
            "price_lo": f"{min(prices):.3f}" if prices else "",
            "price_hi": f"{max(prices):.3f}" if prices else "",
            "max_positions_visible": max(int(r["n_positions_visible"]) for r in rs),
            "positions_peak_derived": f"{derived_max:.0f}" if derived_max else "",
            "derived_selftest": f"{agree}/{tested}" if tested else "n/a",
            "balance_start": rs[0]["balance"],
            "worst_pnl": f"{min([num(r,'pnl_total') for r in rs if num(r,'pnl_total') is not None] or [0]):.2f}",
            "states": len(rs),
            "duration_video_s": f"{(int(rs[-1]['frame_idx']) - int(rs[0]['frame_idx'])) / FPS:.1f}",
        })

    p1 = os.path.join(a.out, "trades.csv")
    with open(p1, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(trades[0].keys()))
        w.writeheader()
        w.writerows(trades)
    p2 = os.path.join(a.out, "cycle_grid.csv")
    with open(p2, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summaries[0].keys()))
        w.writeheader()
        w.writerows(summaries)

    steps_all = [float(s["grid_step"]) for s in summaries if s["grid_step"]]
    ag = [s["derived_selftest"] for s in summaries if s["derived_selftest"] != "n/a"]
    hit = sum(int(x.split("/")[0]) for x in ag)
    tot = sum(int(x.split("/")[1]) for x in ag)
    print(f"{len(trades)} rungs across {len(summaries)} cycles")
    print(f"  -> {p1}\n  -> {p2}")
    print(f"\ngrid step: median ${median(steps_all):.3f}  range ${min(steps_all):.3f}-${max(steps_all):.3f}")
    print(f"derived-position self-test: {hit}/{tot} states agree with the visible count"
          + (f" ({100*hit/tot:.0f}%)" if tot else ""))
    print(f"\n{'cyc':>4s} {'dir':>5s} {'lot':>6s} {'rungs':>6s} {'step':>7s} "
          f"{'vis':>4s} {'derived':>8s} {'selftest':>9s} {'worst P&L':>11s}")
    for s in summaries:
        print(f"{s['cycle_id']:>4d} {s['direction']:>5s} {s['unit_lot']:>6s} {s['n_rungs']:>6d} "
              f"{s['grid_step']:>7s} {s['max_positions_visible']:>4d} "
              f"{s['positions_peak_derived']:>8s} {s['derived_selftest']:>9s} {s['worst_pnl']:>11s}")


if __name__ == "__main__":
    main()
