"""Pre-flight calibration for the frame-extraction pipeline.

Run this before stage_a_scan.py. It answers three questions that the scan's correctness
depends on, and that were left open by the initial measurements:

  1. What is_mt5 threshold separates MT5 trade screens from the montage's other content?
     (A quick pass at 0.45 wrongly rejected f700-3300, so the threshold needs sweeping.)
  2. Is quad detection at half resolution accurate enough to use? Stage A's speed depends
     on it -- quad detection at full 1080x1440 dominates the per-frame cost.
  3. Do the fractional ROIs still land on target across the whole video, including the
     later frames where the summary block grows two extra rows?

Writes annotated grid overlays to <out>/calib/ for eyeball verification of (3).
"""
from __future__ import annotations

import argparse
import os

import cv2
import numpy as np

import rectify as R

# Frames known to show a valid MT5 trade screen (verified by eye).
GOOD = [3000, 9000, 13000, 16000, 18000]
# Frames known NOT to show MT5 (a cut to other content).
BAD = [13800, 13900, 14000]


def sweep_threshold(video: str) -> float:
    ref = R.build_reference(video, 9000)
    matcher = R.Mt5Matcher(ref, threshold=0.0)
    cap = cv2.VideoCapture(video)

    def score_of(idx: int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            return None
        quad = R.find_quad(frame)
        if quad is None:
            return None
        return matcher.score(R.rectify(frame, quad, R.DETECT_W, R.DETECT_H))

    good = {i: score_of(i) for i in GOOD}
    bad = {i: score_of(i) for i in BAD}
    cap.release()

    print("== is_mt5 score sweep ==")
    for i, s in good.items():
        print(f"  GOOD f{i:<6d} score {('none' if s is None else f'{s:.3f}')}")
    for i, s in bad.items():
        print(f"  BAD  f{i:<6d} score {('none' if s is None else f'{s:.3f}')}")

    gs = [s for s in good.values() if s is not None]
    bs = [s for s in bad.values() if s is not None]
    if not gs:
        raise SystemExit("no good frames scored -- reference or geometry is broken")
    lo_good = min(gs)
    hi_bad = max(bs) if bs else -1.0
    if hi_bad >= lo_good:
        print(f"  !! NO CLEAN SEPARATION: worst good {lo_good:.3f} <= best bad {hi_bad:.3f}")
        print("     Frames that fail is_mt5 are only skipped, never mis-parsed, so bias low.")
    thr = round(max(0.05, (lo_good + max(hi_bad, 0.0)) / 2.0), 3) if hi_bad < lo_good else round(lo_good * 0.9, 3)
    print(f"  worst good {lo_good:.3f} | best bad {hi_bad:.3f} -> recommended threshold {thr}")
    return thr


def check_halfres_quad(video: str, n: int = 200, step: int = 40) -> bool:
    """Compare full-res quad corners against quad detected at half res and scaled up."""
    cap = cv2.VideoCapture(video)
    errs = []
    checked = 0
    idx = 0
    while checked < n:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            full = R.find_quad(frame)
            small = R.find_quad(cv2.resize(frame, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
            if full is not None and small is not None:
                errs.append(float(np.abs(full - small * 2.0).max()))
                checked += 1
            elif (full is None) != (small is None):
                errs.append(999.0)   # disagreement about whether a screen is present
                checked += 1
        idx += 1
    cap.release()

    if not errs:
        print("== half-res quad ==\n  no comparable frames")
        return False
    e = np.array(errs)
    print("== half-res quad vs full-res ==")
    print(f"  n={len(e)}  p50 {np.median(e):.2f}px  p90 {np.percentile(e, 90):.2f}px  max {e.max():.2f}px")
    ok = e.max() < 2.0
    print(f"  {'PASS' if ok else 'FAIL'} (need max < 2.0px)")
    if not ok:
        print("  -> run stage A with --full-res-quad (slower, ~4x quad cost)")
    return ok


def dump_grids(video: str, out: str) -> None:
    """Render ROI overlays across the video so ROI placement can be eyeballed."""
    d = os.path.join(out, "calib")
    os.makedirs(d, exist_ok=True)
    cap = cv2.VideoCapture(video)
    rois = {
        "CLOCK": (R.CLOCK, (255, 0, 0)),
        "PNL_TOP": (R.PNL_TOP, (0, 200, 255)),
        "RIGHT_COL": (R.RIGHT_COL, (0, 0, 255)),
        "NAVBAR": (R.NAVBAR, (0, 200, 0)),
        "LABELS": (R.LABELS, (255, 0, 255)),
    }
    written = 0
    for idx in (1000, 3000, 5000, 7000, 9000, 11000, 13000, 15000, 16000, 18000):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        quad = R.find_quad(frame)
        if quad is None:
            continue
        rect = R.rectify(frame, quad, R.OCR_W, R.OCR_H)
        vis = rect.copy()
        h, w = vis.shape[:2]
        for name, (roi, colour) in rois.items():
            x0, y0, x1, y1 = roi
            p0 = (int(x0 * w), int(y0 * h))
            p1 = (int(x1 * w), int(y1 * h))
            cv2.rectangle(vis, p0, p1, colour, 2)
            cv2.putText(vis, name, (p0[0] + 3, p0[1] - 4), 0, 0.45, colour, 1)
        cv2.imwrite(os.path.join(d, f"grid_f{idx:06d}.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 92])
        written += 1
    cap.release()
    print(f"== ROI overlays ==\n  wrote {written} images to {d}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    thr = sweep_threshold(a.video)
    print()
    check_halfres_quad(a.video)
    print()
    dump_grids(a.video, a.out)
    print(f"\nUse: --mt5-threshold {thr}")


if __name__ == "__main__":
    main()
