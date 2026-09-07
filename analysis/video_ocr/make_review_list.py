"""Build the human-verification worklist for the flagged states.

Every state the pipeline is unsure about, with the exact place in the video to look and blank
columns to write the correct numbers into. Filling this file in IS the correction step --
apply_corrections.py reads it straight back, so verification and repair are one pass.

    python make_review_list.py --out "D:\\llm\\ios\\xausd_video_out"

Video is 30 fps, so video_time is simply frame_idx / 30 -- scrub to it in any player. The
deskewed screen JPG named in screen_file is usually easier to read than the video frame.
"""
from __future__ import annotations

import argparse
import csv
import os

FPS = 30.0

# Plain-English reason per flag, so the worklist says what to look at rather than naming a rule.
WHAT = {
    "summary_rows=0": "no summary values read at all - read Balance / Equity / Free margin",
    "summary_rows=1": "only 1 summary value read - read all of Balance / Equity / Free margin",
    "summary_rows=2": "only 2 summary values read (expected 3, or 5 when Margin is showing)",
    "summary_rows=4": "4 summary values read (expected 3, or 5 when Margin is showing)",
    "summary_rows=6": "6 summary values - a position P&L leaked up into the summary block",
    "summary_3row_mismatch": "3-row layout but equity != free margin; they must be equal - one is misread",
    "summary_5row_mismatch": "margin_level should equal equity / margin * 100, and does not",
    "equity_inconsistent": "free_margin should equal equity - margin, and does not",
    "equity_repaired": "equity was AUTO-CORRECTED from free_margin + margin - confirm the new value",
    "balance_drop": "balance more than halved vs the previous state - likely a dropped digit",
    "no_balance": "balance could not be read",
}

READ = ["balance", "equity", "margin", "free_margin", "margin_level", "ltp",
        "n_positions_visible", "position_lots", "position_entries", "position_pnls"]


def video_time(frame_idx: int) -> str:
    s = frame_idx / FPS
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def explain(flag: str) -> str:
    parts = [WHAT.get(f.split("=")[0] if f.startswith("summary_rows") else f, f)
             for f in flag.split(";") if f]
    # summary_rows=N keys are stored with the N, so try the exact key first
    parts = [WHAT.get(f, p) for f, p in zip([x for x in flag.split(";") if x], parts)]
    return " | ".join(dict.fromkeys(parts))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--all", action="store_true",
                    help="include every state, not just the flagged ones")
    a = ap.parse_args()

    with open(os.path.join(a.out, "unique.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    picked = rows if a.all else [r for r in rows if r["flag"]]
    # Worst first: the lower the vote agreement, the more likely the row is wrong.
    picked.sort(key=lambda r: (float(r["ocr_confidence"]), r["flag"]))

    fields = (["video_time", "frame_idx", "cycle_id", "flag", "what_to_check",
               "ocr_confidence", "screen_file"]
              + READ
              + [f"correct_{k}" for k in READ]
              + ["notes"])

    path = os.path.join(a.out, "flagged_review.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in picked:
            out = {
                "video_time": video_time(int(r["frame_idx"])),
                "frame_idx": r["frame_idx"],
                "cycle_id": r["cycle_id"],
                "flag": r["flag"],
                "what_to_check": explain(r["flag"]),
                "ocr_confidence": r["ocr_confidence"],
                "screen_file": r["screen_file"],
                "notes": "",
            }
            for k in READ:
                out[k] = r.get(k, "")
                out[f"correct_{k}"] = ""      # blank: this is what the human fills in
            w.writerow(out)

    print(f"{len(picked)} states -> {path}")
    print("\nFill in only the correct_* columns you actually check; leave the rest blank.")
    print("Then: python apply_corrections.py --out <dir>\n")
    print("Start here (worst OCR agreement first):")
    print(f"  {'video':>9s} {'frame':>7s} {'conf':>5s}  {'balance':>10s} {'equity':>10s}  flag")
    for r in picked[:10]:
        print(f"  {video_time(int(r['frame_idx'])):>9s} {r['frame_idx']:>7s} "
              f"{r['ocr_confidence']:>5s}  {r['balance']:>10s} {r['equity']:>10s}  {r['flag']}")


if __name__ == "__main__":
    main()
