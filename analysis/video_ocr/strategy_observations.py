"""Extract the observations that pin down the grid's rules.

Not a summary -- the raw evidence for reverse-engineering: how far price moved between adds,
what the account looked like either side of each add, the exact basket at each close, and the
balance booked after it.

    python strategy_observations.py --out "D:\\llm\\ios\\xausd_video_out"

Writes add_events.csv, close_events.csv and lot_ladder.csv, and prints per-field coverage so
gaps are visible rather than averaged away.

One structural fact drives the design: balance moves 165 times across only 43 cycles, because a
basket is unwound leg by leg over a few seconds rather than in a single step. Read one
transition at a time this looks like the strategy closing single positions and holding the
rest -- it is not. Grouped by cycle, open P&L returns to ~0 after the last close on 91% of
cycles, and the four exceptions all sit on states the OCR flagged. The basket goes in full.
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import Counter, defaultdict

FPS = 30.0


def num(row: dict, key: str):
    try:
        return float(row[key])
    except (ValueError, KeyError, TypeError):
        return None


def s(row: dict, key: str) -> str:
    return row.get(key, "") or ""


def video_time(frame_idx: int) -> str:
    t = frame_idx / FPS
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def quality(*rows: dict) -> str:
    """How far to trust a row: the reasons, not just a score."""
    tags = set()
    for r in rows:
        if s(r, "flag"):
            tags.add("flagged")
        if "rows_misaligned" in s(r, "repaired"):
            tags.add("rows_misaligned")
        elif s(r, "repaired"):
            tags.add("repaired")
    return ";".join(sorted(tags)) or "ok"      # sorted, so one state of affairs = one label


def derived_positions(row: dict):
    """Visible rows scaled by the share of open P&L they account for (self-tests at 95%)."""
    tot, vis = num(row, "pnl_total"), num(row, "pnl_visible_sum")
    try:
        nv = int(row["n_positions_visible"])
    except (ValueError, KeyError):
        return None
    if tot is None or vis is None or not nv or abs(vis) < 1e-6:
        return None
    n = nv * tot / vis
    return n if 0 < n < 500 else None


def unit_lot(row: dict):
    lots = [x for x in s(row, "position_lots").split()]
    return Counter(lots).most_common(1)[0][0] if lots else ""


def build_adds(rows: list[dict]) -> list[dict]:
    out = []
    per_cycle = defaultdict(int)
    last_add_price: dict[int, float] = {}
    last_add_ltp: dict[int, float] = {}
    for a, b in zip(rows, rows[1:]):
        if a["flat"] or b["flat"] or a["cycle_id"] != b["cycle_id"]:
            continue
        before = set(s(a, "position_entries").split())
        after = set(s(b, "position_entries").split())
        new = sorted(after - before)
        try:
            na, nb = int(a["n_positions_visible"]), int(b["n_positions_visible"])
        except ValueError:
            continue
        if not new and nb <= na:
            continue

        cid = int(b["cycle_id"])
        per_cycle[cid] += 1
        ltp = num(b, "ltp") or num(a, "ltp")
        rung = None
        for e in new:
            try:
                v = float(e)
            except ValueError:
                continue
            if ltp is None or abs(v - ltp) < 50:
                rung = v
                break
        prev = last_add_price.get(cid)
        if rung is not None:
            last_add_price[cid] = rung
        prev_ltp = last_add_ltp.get(cid)
        if ltp is not None:
            last_add_ltp[cid] = ltp
        # An add either opens a new rung or stacks another lot on one already held; the grid
        # does both, so say which rather than leaving the rung column blank.
        add_type = "new_rung" if rung is not None else ("same_rung" if new else "scrolled")

        out.append({
            "video_time": video_time(int(b["frame_idx"])),
            "frame_before": a["frame_idx"], "frame_after": b["frame_idx"],
            "cycle_id": cid, "add_index_in_cycle": per_cycle[cid],
            "ltp_at_add": f"{ltp:.3f}" if ltp is not None else "",
            "rung_price_added": f"{rung:.3f}" if rung is not None else "",
            "price_move_since_prev_add": (f"{rung - prev:+.3f}"
                                          if (rung is not None and prev is not None) else ""),
            "ltp_move_since_prev_add": (f"{ltp - prev_ltp:+.3f}"
                                        if (ltp is not None and prev_ltp is not None) else ""),
            "add_type": add_type,
            "lots_added": unit_lot(b), "direction": (s(b, "position_dirs").split() or [""])[0],
            "n_positions_before": na, "n_positions_after": nb,
            "positions_derived_before": _fmt(derived_positions(a)),
            "positions_derived_after": _fmt(derived_positions(b)),
            "equity_before": s(a, "equity"), "equity_after": s(b, "equity"),
            "free_margin_before": s(a, "free_margin"), "free_margin_after": s(b, "free_margin"),
            "margin_before": s(a, "margin"), "margin_after": s(b, "margin"),
            "balance": s(b, "balance"),
            "pnl_total_before": s(a, "pnl_total"), "pnl_total_after": s(b, "pnl_total"),
            "basket_entries_before": s(a, "position_entries"),
            "basket_entries_after": s(b, "position_entries"),
            # The video is sped up 5-24x, so "immediately" is only as good as this gap.
            "gap_s": f"{(int(b['frame_idx']) - int(a['frame_idx'])) / FPS:.2f}",
            "quality": quality(a, b),
        })
    return out


def _fmt(v):
    return f"{v:.1f}" if v is not None else ""


def build_closes(rows: list[dict]) -> list[dict]:
    """Every balance change, labelled by its place in the cycle's unwind.

    A basket is not closed in one step -- it is unwound leg by leg over a few seconds, so one
    liquidation shows up as several balance changes. Reading each of those in isolation makes
    the strategy look as though it were closing single positions and holding the rest, which is
    wrong: grouped by cycle, open P&L returns to ~0 after the last close on 91% of cycles, i.e.
    the whole basket goes. Legs are therefore numbered within their cycle rather than
    classified individually.

    Grouping is by cycle, not by a time gap. A 0.5-8s gap threshold gives anywhere from 52 to
    36 groups, so any single threshold would be an arbitrary choice dressed up as a finding.
    """
    per_cycle: dict[str, int] = defaultdict(int)
    total_legs: Counter = Counter()
    events = []
    for a, b in zip(rows, rows[1:]):
        ba, bb = num(a, "balance"), num(b, "balance")
        if None in (ba, bb) or abs(bb - ba) <= max(0.01, abs(ba) * 0.001):
            continue
        events.append((a, b, bb - ba))
        total_legs[a["cycle_id"]] += 1

    out = []
    for a, b, realised in events:
        try:
            na, nb = int(a["n_positions_visible"]), int(b["n_positions_visible"])
        except ValueError:
            na = nb = 0
        pt = num(a, "pnl_total")
        pt_after = num(b, "pnl_total")
        cid = a["cycle_id"]
        per_cycle[cid] += 1
        leg = per_cycle[cid]
        legs = total_legs[cid]
        match = ""
        if pt is not None:
            match = "yes" if abs(realised - pt) <= max(0.05, abs(pt) * 0.03) else "no"
        out.append({
            "video_time": video_time(int(b["frame_idx"])),
            "frame_before": a["frame_idx"], "frame_after": b["frame_idx"],
            "cycle_id": cid,
            "unwind_leg": leg, "legs_in_cycle": legs,
            "is_last_leg": "yes" if leg == legs else "",
            "open_pnl_after": s(b, "pnl_total"),
            "basket_emptied": ("yes" if (pt_after is not None
                                         and abs(pt_after) <= max(0.10, abs(pt or 0) * 0.10))
                               else "no") if leg == legs else "",
            "balance_before": s(a, "balance"), "balance_after": s(b, "balance"),
            "realised": f"{realised:+.2f}",
            "pnl_total_before": s(a, "pnl_total"),
            "realised_matches_basket": match,
            "n_positions_before": na, "n_positions_after": nb,
            "positions_derived_before": _fmt(derived_positions(a)),
            "ltp_at_close": s(a, "ltp"),
            "equity_before": s(a, "equity"), "free_margin_before": s(a, "free_margin"),
            "margin_before": s(a, "margin"),
            "basket_entries_before": s(a, "position_entries"),
            "basket_lots_before": s(a, "position_lots"),
            "basket_pnls_before": s(a, "position_pnls"),
            "gap_s": f"{(int(b['frame_idx']) - int(a['frame_idx'])) / FPS:.2f}",
            "quality": quality(a, b),
        })
    return out


def build_ladder(rows: list[dict]) -> list[dict]:
    seq = []
    for r in rows:
        u = unit_lot(r)
        if u:
            seq.append((r, u))

    first: dict[str, dict] = {}
    last: dict[str, dict] = {}
    counts: Counter = Counter()
    rowcount: Counter = Counter()
    for r, u in seq:
        first.setdefault(u, r)
        last[u] = r
        counts[u] += 1
        rowcount[u] += len(s(r, "position_lots").split())

    out = []
    prev_lot = prev_bal = None
    for u in sorted(first, key=lambda x: float(x)):
        fr, lr = first[u], last[u]
        bal = num(fr, "balance")
        out.append({
            "lot": u,
            "first_seen_video_time": video_time(int(fr["frame_idx"])),
            "first_seen_frame": fr["frame_idx"],
            "last_seen_video_time": video_time(int(lr["frame_idx"])),
            "cycle_first": fr["cycle_id"], "cycle_last": lr["cycle_id"],
            "balance_at_step_up": s(fr, "balance"),
            "lot_ratio_vs_prev": f"{float(u) / prev_lot:.2f}" if prev_lot else "",
            "balance_ratio_vs_prev": (f"{bal / prev_bal:.2f}"
                                      if (bal and prev_bal) else ""),
            "lot_pct_of_balance": f"{float(u) / bal * 100:.3f}" if bal else "",
            "states_seen": counts[u], "rows_seen": rowcount[u],
        })
        prev_lot = float(u)
        if bal:
            prev_bal = bal
    return out


def build_distances(rows: list[dict], closes: list[dict]) -> list[dict]:
    """Entry-to-exit distance per position, measured at the state the unwind begins.

    Only the visible rows can be measured, and those are the top of the list -- the deepest
    grid rungs sit off screen and would be further from the exit. So this is a floor on the
    true average, not the whole basket.
    """
    first_leg = {c["cycle_id"]: c for c in reversed(closes) if c["unwind_leg"] == 1}
    by_frame = {r["frame_idx"]: r for r in rows}
    out = []
    for cid, c in sorted(first_leg.items(), key=lambda kv: int(kv[0])):
        a = by_frame.get(c["frame_before"])
        if a is None:
            continue
        exit_px = num(a, "ltp")
        if exit_px is None:
            continue
        ents = s_list(a, "position_entries")
        lots = s_list(a, "position_lots")
        dirs = s_list(a, "position_dirs")
        for i, e in enumerate(ents):
            try:
                ev = float(e)
            except ValueError:
                continue
            if abs(ev - exit_px) > 50:        # a misread entry, not a rung
                continue
            out.append({
                "cycle_id": cid,
                "video_time": c["video_time"],
                "frame": a["frame_idx"],
                "exit_price": f"{exit_px:.3f}",
                "entry_price": f"{ev:.3f}",
                "distance": f"{abs(exit_px - ev):.3f}",
                "signed_distance": f"{exit_px - ev:+.3f}",
                "lots": lots[i] if i < len(lots) else (lots[0] if lots else ""),
                "direction": dirs[0] if dirs else "",
                "quality": quality(a),
            })
    return out


def s_list(row: dict, key: str) -> list[str]:
    return s(row, key).split()


def coverage(events: list[dict], fields: list[str], label: str) -> None:
    print(f"\n{label}: {len(events)} events")
    for f in fields:
        n = sum(1 for e in events if e.get(f) not in ("", None))
        print(f"   {f:28s} {n:>5d}/{len(events)}  {100*n/max(len(events),1):5.1f}%")


def write(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with open(os.path.join(a.out, "unique.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    adds = build_adds(rows)
    closes = build_closes(rows)
    ladder = build_ladder(rows)

    write(os.path.join(a.out, "add_events.csv"), adds)
    write(os.path.join(a.out, "close_events.csv"), closes)
    write(os.path.join(a.out, "lot_ladder.csv"), ladder)
    dists = build_distances(rows, closes)
    write(os.path.join(a.out, "entry_exit_distance.csv"), dists)

    coverage(adds, ["equity_before", "equity_after", "free_margin_before", "free_margin_after",
                    "margin_before", "rung_price_added", "price_move_since_prev_add",
                    "ltp_move_since_prev_add", "positions_derived_before"], "ADD EVENTS")
    coverage(closes, ["balance_before", "balance_after", "pnl_total_before",
                      "basket_entries_before", "basket_pnls_before", "equity_before",
                      "free_margin_before"], "CLOSE EVENTS")

    lasts = [c for c in closes if c["is_last_leg"] == "yes"]
    emptied = sum(1 for c in lasts if c["basket_emptied"] == "yes")
    legs = Counter(int(c["legs_in_cycle"]) for c in lasts)
    med = sorted(int(c["legs_in_cycle"]) for c in lasts)[len(lasts) // 2] if lasts else 0
    print(f"\n{len(closes)} balance changes across {len(lasts)} cycles "
          f"-- median {med} legs per cycle")
    print(f"   legs per cycle: {sorted(legs.items())}")
    print(f"   cycles whose open P&L returns to ~0 after the last close: "
          f"{emptied}/{len(lasts)} = {100*emptied/max(len(lasts),1):.0f}%")
    print("   -> the basket is unwound IN FULL each cycle. The legs are one liquidation spread")
    print("      over a few seconds, not separate decisions to keep or drop positions.")
    exceptions = [c for c in lasts if c["basket_emptied"] == "no"]
    if exceptions:
        print(f"   the {len(exceptions)} cycles that do not clear:")
        for c in exceptions:
            print(f"      cycle {c['cycle_id']:>3s} at {c['video_time']}  open P&L after "
                  f"{c['open_pnl_after']:>10s}  quality={c['quality']}")

    print("\n   add types:", dict(Counter(e["add_type"] for e in adds)))
    print("   'scrolled' = the visible count rose with no new entry price, i.e. the list moved")
    print("   rather than the book. Only new_rung/same_rung are real adds, so the spacing")
    print("   statistics below use those and exclude the scroll artefacts.")

    real = [e for e in adds if e["add_type"] != "scrolled"]
    # Report clean rows separately. A misread digit in a rung price ("4040.507" for
    # "4049.507") shows up as a $9 grid step and would drag the tail of any pooled statistic,
    # so the clean subset is the one to quote and the difference between them is the warning.
    clean = [e for e in real if e["quality"] == "ok"]
    for label, key in (("rung-to-rung spacing (the grid step)", "price_move_since_prev_add"),
                       ("price travelled between adds", "ltp_move_since_prev_add")):
        for tag, src in (("all real adds", real), ("clean rows only", clean)):
            mag = sorted(abs(float(e[key])) for e in src if e[key])
            if not mag:
                continue
            print(f"\n{label} -- {tag}: {len(mag)} measured")
            print(f"   p25 ${mag[len(mag)//4]:.3f}   median ${mag[len(mag)//2]:.3f}   "
                  f"p75 ${mag[3*len(mag)//4]:.3f}   max ${mag[-1]:.3f}")
            print("   histogram to $0.05:",
                  Counter(round(m * 20) / 20 for m in mag).most_common(6))

    print(f"\nLOT LADDER ({len(ladder)} sizes)")
    print(f"  {'lot':>6s} {'first seen':>11s} {'balance':>11s} {'lot x':>6s} {'bal x':>6s} "
          f"{'% of bal':>9s} {'cycle':>6s}")
    for l in ladder:
        print(f"  {l['lot']:>6s} {l['first_seen_video_time']:>11s} "
              f"{l['balance_at_step_up']:>11s} {l['lot_ratio_vs_prev']:>6s} "
              f"{l['balance_ratio_vs_prev']:>6s} {l['lot_pct_of_balance']:>9s} "
              f"{l['cycle_first']:>6s}")

    q = Counter(e["quality"] for e in adds + closes)
    print(f"\nquality of the {len(adds)+len(closes)} events: {dict(q)}")
    print(f"\n-> add_events.csv  close_events.csv  lot_ladder.csv  (in {a.out})")


if __name__ == "__main__":
    main()
