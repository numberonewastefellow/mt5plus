"""Stage A: scan every frame and keep candidates where the value column changed.

Tuned for RECALL, not precision. Pixel/mask comparison cannot distinguish a genuine
single-digit change from LCD flicker (measured: they are the same magnitude), so this stage
deliberately over-keeps ~2-3x and lets stage_b_ocr.py do the exact dedup on parsed values.

Each frame is perspective-rectified before anything is compared -- the camera is handheld and
the screen drifts ~60px while shrinking ~6% across the video, so fixed pixel ROIs slide off.

Change detection compares against the LAST KEPT frame (an anchor), not the previous frame, so
jitter cannot accumulate into a false trigger.
"""
from __future__ import annotations

import argparse
import csv
import multiprocessing as mp
import os

import cv2
import numpy as np

import rectify as R

DEFAULT_THRESHOLD = 0.15
JPEG_FULL = 95
JPEG_SCREEN = 92


def _worker(job) -> list[dict]:
    (video, start, end, out_dir, threshold, mt5_threshold, ref_path, save) = job

    ref = cv2.imread(ref_path)
    matcher = R.Mt5Matcher(ref, mt5_threshold)

    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    anchor: np.ndarray | None = None
    rows: list[dict] = []
    segment = 0

    for idx in range(start, end):
        ok, frame = cap.read()
        if not ok:
            break

        quad = R.find_quad(frame)
        if quad is None:
            if anchor is not None:
                segment += 1       # a cut: force the next MT5 frame to be kept
            anchor = None
            continue

        small = R.rectify(frame, quad, R.DETECT_W, R.DETECT_H)
        if not matcher.is_mt5(small):
            if anchor is not None:
                segment += 1
            anchor = None
            continue

        mask = R.colour_mask(R.crop(small, R.RIGHT_COL))

        if anchor is None:
            diff = 100.0
        elif anchor.shape != mask.shape:
            diff = 100.0
        else:
            diff = R.min_diff(anchor, mask)

        if diff <= threshold:
            continue

        anchor = mask

        t = idx / fps
        stem = f"f{idx:06d}_t{t:07.2f}"
        if save:
            big = R.rectify(frame, quad, R.OCR_W, R.OCR_H)
            cv2.imwrite(os.path.join(out_dir, "full", stem + ".jpg"), frame,
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_FULL])
            cv2.imwrite(os.path.join(out_dir, "screen", stem + ".jpg"), big,
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_SCREEN])

        grey = cv2.cvtColor(R.crop(small, R.RIGHT_COL), cv2.COLOR_BGR2GRAY)
        rows.append({
            "frame_idx": idx,
            "video_time_s": round(t, 3),
            "diff": round(diff, 4),
            "segment": f"{start}_{segment}",
            "sharpness": round(float(cv2.Laplacian(grey, cv2.CV_64F).var()), 2),
            "stem": stem,
        })

    cap.release()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="percent of the value-column mask that must change (lower = more frames)")
    ap.add_argument("--mt5-threshold", type=float, default=0.283,
                    help="from calibrate.py")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="stop after N frames (for testing)")
    ap.add_argument("--no-save", action="store_true", help="scan only, write no JPGs")
    a = ap.parse_args()

    for sub in ("full", "screen"):
        os.makedirs(os.path.join(a.out, sub), exist_ok=True)

    cap = cv2.VideoCapture(a.video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if a.limit:
        total = min(total, a.limit)

    ref_path = os.path.join(a.out, "reference.jpg")
    cv2.imwrite(ref_path, R.build_reference(a.video, 9000))

    bounds = np.linspace(0, total, a.workers + 1).astype(int)
    jobs = [(a.video, int(bounds[i]), int(bounds[i + 1]), a.out, a.threshold,
             a.mt5_threshold, ref_path, not a.no_save)
            for i in range(a.workers)]

    print(f"scanning {total} frames across {a.workers} workers, threshold {a.threshold}")
    with mp.Pool(a.workers) as pool:
        chunks = pool.map(_worker, jobs)

    rows = sorted((r for c in chunks for r in c), key=lambda r: r["frame_idx"])
    path = os.path.join(a.out, "candidates.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["frame_idx", "video_time_s", "diff", "segment",
                                           "sharpness", "stem"])
        w.writeheader()
        w.writerows(rows)

    kept = len(rows)
    print(f"kept {kept} / {total} frames ({100 * kept / max(total, 1):.1f}%) -> {path}")
    print(f"segments (cut-separated runs of MT5 screen): {len({r['segment'] for r in rows})}")


if __name__ == "__main__":
    main()
