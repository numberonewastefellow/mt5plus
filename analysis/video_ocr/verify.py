"""Consistency checks over unique.csv.

The OCR figures are not independent -- MT5's own arithmetic ties them together, and a recovery
grid ties them together again across a cycle. Every check here compares numbers that were read
from *different* parts of the screen, so agreement is real evidence rather than a tautology.

Checks that can only be true by construction (pnl_total == equity - balance, for instance) are
deliberately excluded: they would inflate the pass rate without testing anything.

    python verify.py --out "D:\\llm\\ios\\xausd_video_out"
"""
from __future__ import annotations

import argparse
import csv
import os


def f(row: dict, key: str):
    try:
        return float(row[key])
    except (ValueError, KeyError):
        return None


def rel_close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def check_flat_rows(rows):
    """At a flat screen every position is closed, so balance, equity and free margin agree."""
    bad = []
    flats = [r for r in rows if r["flat"]]
    for r in flats:
        b, e, fm = f(r, "balance"), f(r, "equity"), f(r, "free_margin")
        if b is None or e is None:
            continue
        if not rel_close(b, e, 0.001) or (fm is not None and not rel_close(b, fm, 0.001)):
            bad.append((r["frame_idx"], b, e, fm))
    return len(flats), bad


def check_balance_within_cycle(rows):
    """Balance only moves when positions close, so it should hold steady inside a cycle."""
    bad = []
    by_cycle = {}
    for r in rows:
        if not r["flat"]:
            by_cycle.setdefault(r["cycle_id"], []).append(r)
    for cid, rs in by_cycle.items():
        vals = [(r["frame_idx"], f(r, "balance")) for r in rs if f(r, "balance") is not None]
        if len(vals) < 2:
            continue
        lo = min(v for _, v in vals)
        hi = max(v for _, v in vals)
        if not rel_close(lo, hi, 0.02):
            bad.append((cid, lo, hi, len(vals)))
    return len(by_cycle), bad


def check_free_margin_identity(rows):
    """free_margin == equity - margin, on the 5-row layout."""
    n = 0
    bad = []
    for r in rows:
        e, m, fm = f(r, "equity"), f(r, "margin"), f(r, "free_margin")
        if None in (e, m, fm) or not r["margin"]:
            continue
        n += 1
        if not rel_close(fm, e - m, 0.01):
            bad.append((r["frame_idx"], fm, e - m))
    return n, bad


def check_margin_level(rows):
    """margin_level == equity / margin * 100."""
    n = 0
    bad = []
    for r in rows:
        e, m, lv = f(r, "equity"), f(r, "margin"), f(r, "margin_level")
        if None in (e, m, lv) or not m:
            continue
        n += 1
        if not rel_close(lv, e / m * 100.0, 0.01):
            bad.append((r["frame_idx"], lv, e / m * 100.0))
    return n, bad


def check_ltp_continuity(rows, max_jump=5.0):
    """XAUUSD cannot jump far between adjacent states; a big step is an OCR slip."""
    n = 0
    bad = []
    prev = None
    for r in rows:
        v = f(r, "ltp")
        if v is None:
            continue
        n += 1
        if prev is not None and abs(v - prev) > max_jump:
            bad.append((r["frame_idx"], prev, v))
        prev = v
    return n, bad


def check_entries_near_ltp(rows, band=50.0):
    """Entry prices sit near the market; anything far off is a misread."""
    n = 0
    bad = []
    for r in rows:
        ltp = f(r, "ltp")
        if ltp is None or not r["position_entries"]:
            continue
        for e in r["position_entries"].split():
            try:
                ev = float(e)
            except ValueError:
                continue
            n += 1
            if abs(ev - ltp) > band:
                bad.append((r["frame_idx"], ev, ltp))
    return n, bad


def check_clock_monotonic(rows):
    n = 0
    bad = []
    prev = None
    for r in rows:
        if not r["phone_clock"]:
            continue
        h, m = (int(x) for x in r["phone_clock"].split(":"))
        v = h * 60 + m
        n += 1
        if prev is not None and v < prev:
            bad.append((r["frame_idx"], prev, v))
        prev = v
    return n, bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--show", type=int, default=4, help="examples to print per failing check")
    a = ap.parse_args()

    with open(os.path.join(a.out, "unique.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    print(f"unique states: {len(rows)}")
    print(f"flagged      : {sum(1 for r in rows if r['flag'])} "
          f"({100 * sum(1 for r in rows if r['flag']) / len(rows):.0f}%)")
    print(f"cycles       : {max(int(r['cycle_id']) for r in rows)}\n")

    checks = [
        ("flat rows: balance==equity==free_margin", check_flat_rows),
        ("balance steady within a cycle", check_balance_within_cycle),
        ("free_margin == equity - margin", check_free_margin_identity),
        ("margin_level == equity/margin*100", check_margin_level),
        ("LTP continuity (<$5 between states)", check_ltp_continuity),
        ("entry prices within $50 of LTP", check_entries_near_ltp),
        ("phone clock monotonic", check_clock_monotonic),
    ]
    print(f"{'check':42s}{'tested':>8s}{'failed':>8s}{'pass':>8s}")
    for name, fn in checks:
        n, bad = fn(rows)
        rate = 100 * (n - len(bad)) / n if n else 100.0
        print(f"{name:42s}{n:>8d}{len(bad):>8d}{rate:>7.1f}%")
        for ex in bad[:a.show]:
            print(f"      e.g. {ex}")


if __name__ == "__main__":
    main()
