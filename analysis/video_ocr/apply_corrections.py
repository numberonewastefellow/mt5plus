"""Apply hand-checked values from flagged_review.csv back into unique.csv.

The corrections do double duty. They fix the rows, and because every corrected field can be
compared against what the OCR originally said, they also form the first **labelled test set**
for this dataset -- the only way to state a real accuracy figure rather than an internal
consistency one. Every repair the pipeline made automatically is scored the same way, which is
how we find out whether the arithmetic repairs were right.

    python apply_corrections.py --out "D:\\llm\\ios\\xausd_video_out"

Writes unique.csv in place (after backing it up) and prints the measured agreement.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil

FIELDS = ["balance", "equity", "margin", "free_margin", "margin_level", "ltp",
          "n_positions_visible", "position_lots", "position_entries", "position_pnls"]


def norm(v: str) -> str:
    """Compare numbers by value, not by spelling, so 4049.30 == 4049.3."""
    v = (v or "").strip()
    if not v:
        return ""
    try:
        return f"{float(v):.4f}"
    except ValueError:
        return " ".join(v.split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true", help="score only, write nothing")
    a = ap.parse_args()

    upath = os.path.join(a.out, "unique.csv")
    rpath = os.path.join(a.out, "flagged_review.csv")
    if not os.path.exists(rpath):
        raise SystemExit(f"no review file at {rpath} -- run make_review_list.py first")

    with open(upath, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    with open(rpath, encoding="utf-8") as fh:
        review = list(csv.DictReader(fh))

    by_frame = {r["frame_idx"]: r for r in rows}

    agree = {f: 0 for f in FIELDS}
    differ = {f: 0 for f in FIELDS}
    examples: list[tuple] = []
    repaired_checked = repaired_right = 0
    touched = 0

    for rev in review:
        target = by_frame.get(rev["frame_idx"])
        if target is None:
            continue
        changed = []
        for f in FIELDS:
            human = (rev.get(f"correct_{f}") or "").strip()
            if not human:
                continue                      # not checked -- say nothing about it
            ocr = target.get(f, "")
            if norm(human) == norm(ocr):
                agree[f] += 1
            else:
                differ[f] += 1
                if len(examples) < 15:
                    examples.append((rev["video_time"], rev["frame_idx"], f, ocr, human))
                target[f] = human
                changed.append(f)
            # score the pipeline's own automatic repairs against the human reading
            if f in (target.get("repaired") or ""):
                repaired_checked += 1
                if norm(human) == norm(ocr):
                    repaired_right += 1
        if changed:
            touched += 1
            prior = target.get("human_corrected") or ""
            target["human_corrected"] = " ".join(dict.fromkeys(prior.split() + changed))

    checked = sum(agree.values()) + sum(differ.values())
    if not checked:
        print("No correct_* cells filled in yet -- nothing to apply.")
        print(f"Fill them in at {rpath}, then re-run this.")
        return

    print(f"fields checked by hand : {checked}")
    print(f"states updated         : {touched}\n")
    print(f"{'field':22s}{'checked':>9s}{'OCR right':>11s}{'accuracy':>10s}")
    for f in FIELDS:
        n = agree[f] + differ[f]
        if not n:
            continue
        print(f"{f:22s}{n:>9d}{agree[f]:>11d}{100*agree[f]/n:>9.1f}%")
    tot_ok = sum(agree.values())
    print(f"{'ALL':22s}{checked:>9d}{tot_ok:>11d}{100*tot_ok/checked:>9.1f}%")
    print("\n^ that is the real OCR accuracy on the states the pipeline was unsure about."
          "\n  Unflagged states are expected to be better than this, not worse.")

    if repaired_checked:
        print(f"\nautomatic repairs that a human also checked: {repaired_checked}, "
              f"of which correct: {repaired_right} ({100*repaired_right/repaired_checked:.0f}%)")
        print("  a repair the human disagrees with is a bug in the repair rule, not a typo.")

    if examples:
        print(f"\ncorrections applied (first {len(examples)}):")
        print(f"  {'video':>9s} {'frame':>7s} {'field':16s} {'was':>14s}  ->  now")
        for t, fr, f, was, now in examples:
            print(f"  {t:>9s} {fr:>7s} {f:16s} {was[:14]:>14s}  ->  {now}")

    if a.dry_run:
        print("\n--dry-run: unique.csv not modified")
        return

    shutil.copy2(upath, upath + ".bak")
    fields = list(rows[0].keys())
    if "human_corrected" not in fields:
        fields.append("human_corrected")
    with open(upath, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            r.setdefault("human_corrected", "")
            w.writerow(r)
    print(f"\nwrote {upath}  (backup at {os.path.basename(upath)}.bak)")
    print("Re-run trade_report.py / make_ltp_series.py / eda.py to pick the corrections up.")


if __name__ == "__main__":
    main()
