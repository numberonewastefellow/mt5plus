"""Cut the short list of video moments worth checking by eye.

Not the 146 flagged states -- those are mostly summary-block noise that does not change any
conclusion. These ~45 rows are the ones where a wrong number would change what we believe
about the strategy: the lot ladder, whether the basket really empties at each cycle, the grid
step, and the specific frames where OCR is known to have failed.

    python make_verify_list.py --out "D:\\llm\\ios\\xausd_video_out"

Fill in correct_value (and notes) for whatever you check, then feed it back through
apply_corrections.py, which scores the pipeline against your readings.
"""
from __future__ import annotations

import argparse
import csv
import os

FPS = 30.0
CLEAN_CLOSES = 12
CLEAN_ADDS = 12


def rd(out: str, name: str) -> list[dict]:
    path = os.path.join(out, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    ladder = rd(a.out, "lot_ladder.csv")
    closes = rd(a.out, "close_events.csv")
    adds = rd(a.out, "add_events.csv")

    rows: list[dict] = []

    def add(group, video_time, frame, cycle, what, says, extra=""):
        rows.append({
            "group": group, "video_time": video_time, "frame_idx": frame,
            "cycle_id": cycle, "what_to_confirm": what, "pipeline_says": says,
            "context": extra, "correct_value": "", "notes": "",
        })

    # 1. the lot ladder -- settles "is there an 11th size" and the 0.04 -> 0.07 step
    for l in ladder:
        add("lot_step_up", l["first_seen_video_time"], l["first_seen_frame"], l["cycle_first"],
            f"lot size on screen should be {l['lot']}",
            f"lot {l['lot']}, balance {l['balance_at_step_up']}",
            f"first appearance of this size; prev-lot ratio {l['lot_ratio_vs_prev'] or 'n/a'}")

    # 2. does the basket actually empty? the claim that was wrong before
    lasts = [c for c in closes if c.get("is_last_leg") == "yes"]
    clean = [c for c in lasts if c.get("basket_emptied") == "yes" and c.get("quality") == "ok"]
    if len(clean) < CLEAN_CLOSES:      # fall back to repaired rows rather than short-changing it
        clean += [c for c in lasts
                  if c.get("basket_emptied") == "yes" and c not in clean][:CLEAN_CLOSES - len(clean)]
    for c in clean[:CLEAN_CLOSES]:
        add("basket_empties", c["video_time"], c["frame_after"], c["cycle_id"],
            "after this close, is the Positions list EMPTY and equity == balance?",
            f"open P&L after = {c['open_pnl_after']}, visible positions = {c['n_positions_after']}",
            f"leg {c['unwind_leg']} of {c['legs_in_cycle']}; realised {c['realised']}")

    # 3. the frames where OCR is known to have failed -- these produced the wrong close model
    bad = [c for c in lasts if c.get("basket_emptied") == "no"]
    bad += [c for c in closes
            if c.get("quality", "") != "ok" and abs(float(c["realised"] or 0)) > 500][:6]
    seen = set()
    for c in bad:
        key = c["frame_after"]
        if key in seen:
            continue
        seen.add(key)
        add("ocr_suspect", c["video_time"], c["frame_after"], c["cycle_id"],
            "read balance and equity exactly -- these are the frames OCR got wrong",
            f"balance {c['balance_before']} -> {c['balance_after']}, realised {c['realised']}",
            f"open P&L after {c['open_pnl_after']}; quality={c['quality']}")

    # 4. the grid step -- only from adds the pipeline is confident about
    good = [e for e in adds
            if e.get("quality") == "ok" and e.get("add_type") == "new_rung"
            and e.get("price_move_since_prev_add")]
    for e in good[:CLEAN_ADDS]:
        add("grid_step", e["video_time"], e["frame_after"], e["cycle_id"],
            "read the newest position's entry price (the top row's second number)",
            f"new rung {e['rung_price_added']}, step {e['price_move_since_prev_add']}",
            f"lot {e['lots_added']}, equity {e['equity_before']} -> {e['equity_after']}")

    path = os.path.join(a.out, "verify_list.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    counts = Counter(r["group"] for r in rows)
    print(f"{len(rows)} rows -> {path}\n")
    for g, n in counts.most_common():
        print(f"  {g:16s} {n:3d}")
    print("\nWhat each group settles:")
    print("  lot_step_up      the 10-size ladder, incl. the 0.04 -> 0.07 step")
    print("  basket_empties   whether the whole basket closes each cycle (the disputed point)")
    print("  ocr_suspect      the frames whose bad reads caused the wrong conclusion")
    print("  grid_step        the ~$0.18 spacing between adds")
    print("\nStart with basket_empties -- that is the one in dispute.")
    print(f"\n{'group':16s}{'video':>10s}{'cycle':>7s}  what to confirm")
    for r in rows:
        if r["group"] == "basket_empties":
            print(f"{r['group']:16s}{r['video_time']:>10s}{r['cycle_id']:>7s}  {r['pipeline_says']}")


if __name__ == "__main__":
    main()
