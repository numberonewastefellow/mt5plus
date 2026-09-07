"""Emit the running price / drawdown trajectory as a narrow, plottable file.

The interesting question about a recovery grid is not where it ended but how far under water it
went first. That series already exists inside unique.csv, buried among 30 columns; this pulls it
out and adds the figure that actually ranks the risk.

    python make_ltp_series.py --out "D:\\llm\\ios\\xausd_video_out"

Writes ltp_series.csv (one row per state) and cycle_drawdown.csv (the shape of each recovery).
"""
from __future__ import annotations

import argparse
import csv
import os

FPS = 30.0


def num(row: dict, key: str):
    try:
        return float(row[key])
    except (ValueError, KeyError):
        return None


def video_time(frame_idx: int) -> str:
    s = frame_idx / FPS
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with open(os.path.join(a.out, "unique.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    series = []
    for r in rows:
        bal, pnl = num(r, "balance"), num(r, "pnl_total")
        # The percentage is what makes cycles comparable: -$46,017 means nothing until you know
        # it sat on a $46,071 balance.
        pct = (pnl / bal * 100.0) if (bal and pnl is not None and abs(bal) > 1e-9) else None
        series.append({
            "video_time": video_time(int(r["frame_idx"])),
            "video_time_s": r["video_time_s"],
            "frame_idx": r["frame_idx"],
            "clock_est": r["clock_est"],
            "cycle_id": r["cycle_id"],
            "ltp": r["ltp"],
            "balance": r["balance"],
            "equity": r["equity"],
            "pnl_total": r["pnl_total"],
            "pnl_pct_of_balance": f"{pct:.2f}" if pct is not None else "",
            "n_positions_visible": r["n_positions_visible"],
            "flat": r["flat"],
            "flag": r["flag"],
        })

    p1 = os.path.join(a.out, "ltp_series.csv")
    with open(p1, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(series[0].keys()))
        w.writeheader()
        w.writerows(series)

    # Per-cycle drawdown path: every state from open to close, so the recovery shape is visible
    # rather than only its minimum.
    by = {}
    for s in series:
        if s["flat"]:
            continue
        by.setdefault(int(s["cycle_id"]), []).append(s)

    paths = []
    for cid, ss in sorted(by.items()):
        if cid == 0:
            continue
        pnls = [float(s["pnl_total"]) for s in ss if s["pnl_total"]]
        pcts = [float(s["pnl_pct_of_balance"]) for s in ss if s["pnl_pct_of_balance"]]
        if not pnls:
            continue
        trough = min(range(len(pnls)), key=lambda i: pnls[i])
        paths.append({
            "cycle_id": cid,
            "states": len(ss),
            "t_start": ss[0]["video_time"],
            "t_end": ss[-1]["video_time"],
            "t_worst": ss[min(trough, len(ss) - 1)]["video_time"],
            "worst_pnl": f"{min(pnls):.2f}",
            "worst_pct_of_balance": f"{min(pcts):.1f}" if pcts else "",
            "best_pnl": f"{max(pnls):.2f}",
            "final_pnl": f"{pnls[-1]:.2f}",
            "recovered_from": f"{min(pnls):.2f} -> {pnls[-1]:.2f}",
            "pnl_path": " ".join(f"{v:.2f}" for v in pnls),
        })

    p2 = os.path.join(a.out, "cycle_drawdown.csv")
    with open(p2, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(paths[0].keys()))
        w.writeheader()
        w.writerows(paths)

    print(f"{len(series)} states -> {p1}")
    print(f"{len(paths)} cycles  -> {p2}\n")
    print("Deepest drawdowns, and where in the video to watch them:")
    print(f"  {'cyc':>4s} {'worst at':>9s} {'worst $':>12s} {'% of bal':>9s}  recovery")
    worst = sorted(paths, key=lambda p: float(p["worst_pct_of_balance"] or 0))[:10]
    for p in worst:
        print(f"  {p['cycle_id']:>4d} {p['t_worst']:>9s} {p['worst_pnl']:>12s} "
              f"{p['worst_pct_of_balance']:>8s}%  {p['recovered_from']}")


if __name__ == "__main__":
    main()
