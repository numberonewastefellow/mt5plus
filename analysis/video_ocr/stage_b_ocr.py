"""Stage B: OCR the Stage A candidates and dedup exactly on the parsed values.

Stage A over-keeps by ~2-3x because pixel comparison cannot separate LCD flicker from a
single-digit change. This stage reads the numbers, so dedup becomes exact.

OCR is not perfect (~5-10% per-value error: 2.84 -> 2.04, trailing punctuation). Rather than
chase that with image preprocessing, errors are corrected by MAJORITY VOTE across adjacent
frames that show the same content. Grouping exploits a property of the data: when price moves,
*every* position's P&L changes together, along with equity and the running total -- so a real
change alters most of the value vector, while an OCR slip alters exactly one element.

Runs entirely locally on Tesseract. Do not send these frames to a hosted vision model: ~4000
images is millions of input tokens for a job that finishes here in a couple of minutes.
"""
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import re
import shutil
from collections import Counter

import cv2
import numpy as np
import pytesseract

import rectify as R

TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

DIGITS = "--psm 6 -c tessedit_char_whitelist=0123456789.,-"
DIGITS_LINE = "--psm 7 -c tessedit_char_whitelist=0123456789.,-"
CLOCK_CFG = "--psm 7 -c tessedit_char_whitelist=0123456789:"

# MT5 renders thousands with a space: "26 325.27". Capture that, then strip the spaces.
NUM = re.compile(r"-?\d[\d ]*[.,]\d{2}")
CLOCK_RE = re.compile(r"(\d{1,2}):(\d{2})")
STEM_RE = re.compile(r"^f(\d+)_")


def _frame_of(stem: str) -> int:
    m = STEM_RE.match(stem)
    return int(m.group(1)) if m else -1

# Fraction of value-vector positions that must agree for two frames to be the same content.
AGREE = 0.70


SPLIT_MARGIN = 18   # px of clearance above the first coloured row, so its glyph tops survive
SPLIT_RUN = 10      # consecutive coloured rows required before the split is believed
PAD = 20            # white border; tesseract loses lines that touch the crop edge


def _strip_border_blobs(bw: np.ndarray, min_area: int = 150) -> np.ndarray:
    """Erase dark shapes that run into the left or right edge of the crop.

    The phone's curved-bezel highlight and MT5's floating round button both bleed in from the
    screen edge and touch the value glyphs; where they merge, tesseract loses the final digit.
    Value text never reaches the crop border (digits stop at 0.957 of screen width, the crop
    ends at 0.975), so anything touching an edge is background and safe to remove.
    """
    ink = (bw == 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    out = bw.copy()
    w = bw.shape[1]
    for i in range(1, count):
        x, _y, cw, _ch, area = stats[i]
        if area >= min_area and (x <= 0 or x + cw >= w - 1):
            out[labels == i] = 255
    return out


def _grey(bgr: np.ndarray, scale: int) -> np.ndarray:
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    grey = cv2.resize(grey, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return cv2.normalize(grey, None, 0, 255, cv2.NORM_MINMAX)


def _finish(bw: np.ndarray, pad: int, strip: bool) -> np.ndarray:
    if strip:
        bw = _strip_border_blobs(bw)      # must run before padding, while edges still touch
    if pad:
        bw = cv2.copyMakeBorder(bw, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    return bw


def _prep(bgr: np.ndarray, scale: int = 2, pad: int = PAD, strip: bool = True) -> np.ndarray:
    """Otsu binarisation -- best for the blue/red position P&Ls."""
    return _finish(cv2.threshold(_grey(bgr, scale), 0, 255,
                                 cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1], pad, strip)


def _prep_summary(bgr: np.ndarray, scale: int = 2, pad: int = PAD) -> np.ndarray:
    """Adaptive binarisation -- best for the dark summary text.

    The summary block sits where the curved bezel and the floating overlay button wash the
    background unevenly. A global Otsu threshold is dragged by that gradient and drops the
    final digit of a row; a locally adaptive one is not. Measured on hand-checked fixtures:
    adaptive 4/4 vs Otsu 3/4 here, and the reverse on the coloured P&L rows, so the two
    blocks are binarised differently on purpose.
    """
    bw = cv2.adaptiveThreshold(_grey(bgr, scale), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 12)
    return _finish(bw, pad, True)


def _parse_clock(text: str) -> str:
    """Read the status-bar clock, repairing the colon.

    The clock is small and blurry at this capture resolution, so tesseract routinely drops the
    separator and returns "1212" or "124". Digits alone are recoverable; a bare hour is not.
    """
    m = CLOCK_RE.search(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else ""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 4:
        h, mi = int(digits[:2]), int(digits[2:])
    elif len(digits) == 3:
        h, mi = int(digits[0]), int(digits[1:])
    else:
        return ""
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else ""


def _numbers(text: str) -> list[str]:
    out = []
    for m in NUM.findall(text):
        v = m.replace(" ", "").replace(",", ".").rstrip(".,")
        out.append(v)
    return out


def _split_by_ink(col: np.ndarray) -> int:
    """Row index where the position list starts, found by ink colour.

    MT5 draws the account summary (Balance/Equity/Margin/Free margin/Margin Level) in dark
    text and every position's P&L in blue or red. So the first row containing coloured ink is
    the top of the position list. This is exact regardless of how many summary rows are
    showing -- and the summary grows from 3 rows to 5 partway through this video, so a
    row-count assumption would silently mis-assign every field after that point.

    Returns len(col) when there are no open positions.
    """
    mask = R.colour_mask(col)
    thr = max(3.0, 0.02 * col.shape[1])
    on = mask.sum(axis=1) > thr
    # Require a sustained band, not a single row: stray coloured pixels off the bezel or the
    # moire pattern otherwise trip the split above the first summary row.
    run = 0
    for i, v in enumerate(on):
        run = run + 1 if v else 0
        if run >= SPLIT_RUN:
            return max(0, i - run + 1 - SPLIT_MARGIN)
    return col.shape[0]


POS_CFG = ("--psm 6 -c tessedit_char_whitelist="
           "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,-> ")
# XAUUSD quotes carry 3 decimals, which distinguishes them from the 2-decimal P&L figures.
PRICE_RE = re.compile(r"\d[\d ]*\.\d{3}")
# Tesseract drops the spaces, so this matches "XAUUSDm,buy0.99" as readily as "buy 0.99".
LOT_RE = re.compile(r"(buy|sell)\s*(\d+\.\d{1,2})", re.I)


def _ocr_positions(rect: np.ndarray, split: int) -> dict:
    """Read the left side of each position row: direction, lot size, entry and current price.

    One --psm 6 pass over the whole block rather than one per row: tesseract keeps the rows on
    separate lines, and a per-row pass costs an OCR call per position for no accuracy gain.
    """
    h, w = rect.shape[:2]
    y0 = int(R.RIGHT_COL[1] * h) + split
    y1 = int(R.RIGHT_COL[3] * h)
    if y1 - y0 < 30:
        return {"dirs": [], "lots": [], "entries": [], "ltp": ""}

    block = rect[y0:y1, int(R.POS_LEFT_X[0] * w):int(R.POS_LEFT_X[1] * w)]
    grey = _grey(block, 2)
    bw = cv2.adaptiveThreshold(grey, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 12)
    bw = cv2.copyMakeBorder(bw, PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=255)
    text = pytesseract.image_to_string(bw, config=POS_CFG)

    dirs, lots, entries, currents = [], [], [], []
    for line in text.splitlines():
        prices = [p.replace(" ", "") for p in PRICE_RE.findall(line)]
        if len(prices) >= 2:
            entries.append(prices[0])
            currents.append(prices[1])
        for d, lot in LOT_RE.findall(line):
            dirs.append(d.lower())
            lots.append(lot)

    # Every open position quotes the SAME instrument, so the current price is one number
    # repeated down the column. Taking its mode corrects the odd misread for free.
    ltp = Counter(currents).most_common(1)[0][0] if currents else ""
    return {"dirs": dirs, "lots": lots, "entries": entries, "ltp": ltp}


def _ocr_one(path: str) -> dict:
    img = cv2.imread(path)
    if img is None:
        return {"stem": os.path.basename(path), "summary": [], "pnls": [], "dirs": [],
                "lots": [], "entries": [], "ltp": "", "clock": "", "top_pnl": ""}

    col = R.crop(img, R.RIGHT_COL)
    split = _split_by_ink(col)

    summary, pnls = [], []
    if split > 20:
        summary = _numbers(pytesseract.image_to_string(_prep_summary(col[:split]), config=DIGITS))
    if split < col.shape[0] - 20:
        pnls = _numbers(pytesseract.image_to_string(_prep(col[split:]), config=DIGITS))

    clock = _parse_clock(pytesseract.image_to_string(_prep(R.crop(img, R.CLOCK), 4, 25),
                                                     config=CLOCK_CFG))

    # Best-effort only: this glyph is large, stylised and blurred, and tesseract mangles it.
    # The reliable figure is the sum of the position P&Ls, derived downstream.
    top = _numbers(pytesseract.image_to_string(_prep(R.crop(img, R.PNL_TOP), 3),
                                               config=DIGITS_LINE))

    pos = _ocr_positions(img, split)

    return {
        "stem": os.path.splitext(os.path.basename(path))[0],
        "summary": summary,
        "pnls": pnls,
        "dirs": pos["dirs"],
        "lots": pos["lots"],
        "entries": pos["entries"],
        "ltp": pos["ltp"],
        "values": summary + pnls,
        "clock": clock,
        "top_pnl": top[0] if top else "",
    }


def _worker(paths: list[str]) -> list[dict]:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT
    return [_ocr_one(p) for p in paths]


def _same_content(a: list[str], b: list[str]) -> bool:
    """True when two value vectors look like the same screen with at most OCR noise."""
    if not a or not b or len(a) != len(b):
        return False
    agree = sum(1 for x, y in zip(a, b) if x == y)
    return agree / len(a) >= AGREE


def _vote_field(group: list[dict], key: str) -> tuple[list[str], float]:
    """Majority-vote each slot of one value list across same-content frames."""
    vectors = [g[key] for g in group if g[key]]
    if not vectors:
        return [], 1.0
    n = Counter(len(v) for v in vectors).most_common(1)[0][0]
    vectors = [v for v in vectors if len(v) == n]
    voted = [Counter(v[i] for v in vectors).most_common(1)[0][0] for i in range(n)]
    agree = sum(sum(1 for x, y in zip(v, voted) if x == y) for v in vectors)
    return voted, agree / max(1, n * len(vectors))


def _vote(group: list[dict]) -> dict:
    summary, c1 = _vote_field(group, "summary")
    pnls, c2 = _vote_field(group, "pnls")
    lots, _ = _vote_field(group, "lots")
    entries, _ = _vote_field(group, "entries")
    dirs, _ = _vote_field(group, "dirs")
    ltps = [g["ltp"] for g in group if g.get("ltp")]
    clocks = [g["clock"] for g in group if g["clock"]]
    tops = [g["top_pnl"] for g in group if g["top_pnl"]]
    mid = group[len(group) // 2]          # most settled frame of the group
    return {
        "stem": mid["stem"],
        "summary": summary,
        "pnls": pnls,
        "values": summary + pnls,
        "lots": lots,
        "entries": entries,
        "dirs": dirs,
        "ltp": Counter(ltps).most_common(1)[0][0] if ltps else "",
        "clock": Counter(clocks).most_common(1)[0][0] if clocks else "",
        "top_pnl": Counter(tops).most_common(1)[0][0] if tops else "",
        "n_frames": len(group),
        # Every source frame that voted on this state, so any suspect value can be traced
        # back to the exact JPGs in full/ and screen/ and checked by eye.
        "frames": [_frame_of(g["stem"]) for g in group],
        "confidence": round(min(c1, c2), 3),
    }


EMPTY_SUMMARY = {"balance": "", "equity": "", "margin": "", "free_margin": "", "margin_level": ""}


def _close(a: str, b: str, tol: float) -> bool:
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False
    return abs(x - y) <= tol * max(1.0, abs(x), abs(y))


def _repair_equity(named: dict) -> str:
    """Recompute equity from MT5's identity free_margin = equity - margin.

    Some OCR errors are SYSTEMATIC, not random: MT5's floating round button sits at a fixed
    screen position, so it corrupts the same digit on every frame of a state and majority
    voting just elects the wrong value (measured at f16000 -- the misread beat the truth 4 to
    1 across the state's whole 7-frame window). Arithmetic is the way out. margin and
    free_margin are read from different rows, so their agreement is independent evidence:
    at f16000, -59.68 + 21790.98 = 21731.30, exactly the hand-checked value that voting missed.

    Only overrides when the recomputed figure also satisfies the margin-level identity, so a
    bad free_margin read cannot corrupt a good equity.
    """
    try:
        equity = float(named["equity"])
        margin = float(named["margin"])
        free_margin = float(named["free_margin"])
        level = float(named["margin_level"])
    except ValueError:
        return ""
    if abs(equity - (free_margin + margin)) <= 0.02 * max(1.0, abs(equity)):
        return ""
    cand = free_margin + margin
    if abs(margin) < 1e-6:
        return "equity_inconsistent"
    if abs(cand / margin * 100.0 - level) > 0.02 * max(1.0, abs(level)):
        return "equity_inconsistent"
    named["equity"] = f"{cand:.2f}"
    return "equity_repaired"


def _margin_level_ok(summary: list[str]) -> bool:
    """Check MT5's identity margin_level == equity / margin * 100."""
    try:
        equity, margin, level = float(summary[1]), float(summary[2]), float(summary[4])
    except (ValueError, IndexError):
        return False
    if abs(margin) < 1e-6:
        return False
    return _close(str(equity / margin * 100.0), str(level), 0.02)


def _name_summary(summary: list[str]) -> tuple[dict, str]:
    """Assign the summary rows to field names by how many rows MT5 is showing.

    3 rows = Balance / Equity / Free margin (no margin in use).
    5 rows = Balance / Equity / Margin / Free margin / Margin Level (%).
    Anything else means OCR dropped or invented a row -- name what we can and flag it.
    """
    if len(summary) == 3:
        named = {"balance": summary[0], "equity": summary[1], "margin": "",
                 "free_margin": summary[2], "margin_level": ""}
        # With no margin in use MT5 shows equity and free margin as the same number; if they
        # differ, the three rows we read are not the three rows we think they are.
        return named, "" if _close(summary[1], summary[2], 0.02) else "summary_3row_mismatch"
    if len(summary) == 5:
        named = {"balance": summary[0], "equity": summary[1], "margin": summary[2],
                 "free_margin": summary[3], "margin_level": summary[4]}
        flag = _repair_equity(named)
        # Margin Level (%) is equity/margin*100 by definition. A near-zero P&L row that leaked
        # up from the position list also yields five values, and would otherwise be silently
        # mis-named as a genuine 5-row summary; this identity is what tells them apart.
        if not _margin_level_ok([named["balance"], named["equity"], named["margin"],
                                 named["free_margin"], named["margin_level"]]):
            flag = (flag + ";summary_5row_mismatch").lstrip(";")
        return named, flag
    named = dict(EMPTY_SUMMARY)
    if len(summary) >= 1:
        named["balance"] = summary[0]
    if len(summary) >= 2:
        named["equity"] = summary[1]
    return named, f"summary_rows={len(summary)}"


def _repair_clocks(rows: list[dict]) -> None:
    """Resolve and repair the status-bar clock series in place.

    The clock is 12-hour with no AM/PM marker and is small enough that tesseract regularly
    mangles the hour ("17:26" and "01:44" both appear in a recording that only spans 11:43 to
    16:46). Two facts make it recoverable: each reading has just two possible absolute times
    (AM or PM), and the recording runs strictly forward. So take the longest non-decreasing
    chain over those candidates -- readings that cannot sit on it are the misreads -- then
    interpolate an estimate against frame_idx for every row.

    Sets `phone_clock` to the accepted reading (blank when rejected) and `clock_est` to the
    interpolated wall-clock for every row.
    """
    idx = [i for i, r in enumerate(rows) if r["phone_clock"]]
    cands: list[list[int]] = []
    for i in idx:
        h, m = (int(x) for x in rows[i]["phone_clock"].split(":"))
        h12 = h % 12
        cands.append(sorted({h12 * 60 + m, (h12 + 12) * 60 + m}))

    n = len(idx)
    best = [[1] * len(c) for c in cands]
    prev: list[list[tuple[int, int] | None]] = [[None] * len(c) for c in cands]
    for i in range(n):
        for ci, v in enumerate(cands[i]):
            for j in range(i):
                for cj, u in enumerate(cands[j]):
                    if u <= v and best[j][cj] + 1 > best[i][ci]:
                        best[i][ci] = best[j][cj] + 1
                        prev[i][ci] = (j, cj)

    if not n:
        for r in rows:
            r["clock_est"] = ""
        return

    def walk(node):
        out = {}
        while node is not None:
            i, ci = node
            out[idx[i]] = cands[i][ci]
            node = prev[i][ci]
        return out

    # Longest chain first; ties broken by the SMALLEST span. A 12-hour clock makes "1:47"
    # either 01:47 or 13:47, and both readings can sit on an equally long chain -- but a
    # single trading session is compact, so the tighter interpretation is the right one.
    # Without this tie-break the series anchored in the small hours and every interpolated
    # timestamp was ~10 hours out.
    ends = [(i, ci) for i in range(n) for ci in range(len(cands[i]))]
    top = max(best[i][ci] for i, ci in ends)
    chain = min((walk(e) for e in ends if best[e[0]][e[1]] == top),
                key=lambda c: max(c.values()) - min(c.values()))

    for i, r in enumerate(rows):
        if i in chain:
            continue
        r["phone_clock"] = ""          # could not be reconciled with a forward-running clock

    anchors = sorted((int(rows[i]["frame_idx"]), v) for i, v in chain.items())
    for r in rows:
        f = int(r["frame_idx"])
        if f <= anchors[0][0]:
            est = anchors[0][1]
        elif f >= anchors[-1][0]:
            est = anchors[-1][1]
        else:
            k = next(j for j in range(1, len(anchors)) if anchors[j][0] >= f)
            (f0, v0), (f1, v1) = anchors[k - 1], anchors[k]
            est = v0 if f1 == f0 else v0 + (v1 - v0) * (f - f0) / (f1 - f0)
        r["clock_est"] = f"{int(est) // 60 % 24:02d}:{int(est) % 60:02d}"
    print(f"clock: {len(chain)}/{len(idx)} readings kept, {len(rows)} rows interpolated")


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2]


def _repair_rows(v: dict) -> str:
    """Repair position rows using MT5's own arithmetic: pnl = ±(ltp − entry) × lots × 100.

    Each row carries four numbers tied by one equation, so three good reads determine the
    fourth. Measured before this pass, the equation held on 88% of rows -- and the 12% that
    failed were exactly the OCR errors (an entry read as 4950.058 where the market was at
    4049, a lot read as 089.21 among 0.33s). That makes the residual a repair signal, not just
    a quality flag.

    Repairs are attempted in order of which field is most likely the wrong one:
      1. a lot that differs from the frame's modal lot -- grids trade a uniform size
      2. an entry far from the other entries -- rungs sit within a dollar of each other
      3. otherwise the P&L, which is the value with no independent corroboration

    Returns a description of what was changed, for the `repaired` column. Nothing is altered
    silently; a value that cannot be reconciled is left exactly as read.
    """
    try:
        ltp = float(v["ltp"])
    except (ValueError, TypeError, KeyError):
        return ""
    lots, ents, pnls = v["lots"], v["entries"], v["pnls"]
    n = min(len(lots), len(ents), len(pnls))
    if n == 0:
        return ""

    def fnum0(x):
        try:
            return float(x)
        except ValueError:
            return None

    # A lot an order of magnitude out of family ("089.21" among 0.33s) is a misread on its own
    # evidence, so snap it before anything else -- this does not depend on the lists lining up.
    _lv = [x for x in (fnum0(l) for l in lots) if x]
    if _lv:
        _unit = Counter(_lv).most_common(1)[0][0]
        for i, l in enumerate(lots):
            lv = fnum0(l)
            if lv and _unit and lv > _unit * 10:
                lots[i] = f"{_unit:g}"

    # The three lists come from different OCR passes -- P&Ls from the right column, lots and
    # entries from the left. If one pass dropped a row the lists no longer line up, and index i
    # points at three different positions. Repairing across that misalignment invents numbers
    # (it produced corrections of thousands of dollars). Only reconcile when the rows agree on
    # how many there are; otherwise say so and change nothing.
    if not (len(lots) == len(ents) == len(pnls)):
        return "rows_misaligned"

    dirs = v.get("dirs") or []
    sign = -1.0 if (dirs and Counter(dirs).most_common(1)[0][0] == "sell") else 1.0

    def fnum(x):
        try:
            return float(x)
        except ValueError:
            return None

    lot_vals = [x for x in (fnum(l) for l in lots[:n]) if x]
    ent_vals = [x for x in (fnum(e) for e in ents[:n]) if x and abs(x - ltp) < 50]
    if not lot_vals:
        return ""
    unit = Counter(lot_vals).most_common(1)[0][0]
    ent_mid = _median(ent_vals) if ent_vals else ltp

    notes = []

    # Before touching any row: if EVERY row disagrees by about the same amount, the shared
    # value is the wrong one, not each row independently. Each row implies its own current
    # price; when those agree with each other but not with the OCR'd LTP, the LTP is the
    # outlier and repairing the rows would bake one misread into all of them.
    implied = []
    for i in range(n):
        e, l, p = fnum(ents[i]), fnum(lots[i]), fnum(pnls[i])
        if None in (e, l, p) or l == 0 or abs(e - ltp) > 50:
            continue
        implied.append(e + sign * p / (l * 100.0))
    if len(implied) >= 3:
        mid = _median(implied)
        spread = max(implied) - min(implied)
        if spread <= 0.05 and abs(mid - ltp) > 0.02:
            notes.append(f"ltp:{v['ltp']}->{mid:.3f}")
            ltp = mid
            v["ltp"] = f"{mid:.3f}"

    def consistent(e, l, p):
        return abs(sign * (ltp - e) * l * 100.0 - p) <= max(0.05, abs(p) * 0.02)

    for i in range(n):
        e, l, p = fnum(ents[i]), fnum(lots[i]), fnum(pnls[i])
        if None in (e, l, p) or l == 0:
            continue
        if consistent(e, l, p):
            continue

        # 1. odd lot out -- the frame trades one size. A lot an order of magnitude off family
        # (089.21 among 0.33s) is a misread whether or not it reconciles the equation.
        if abs(l - unit) > 1e-9 and (consistent(e, unit, p) or l > unit * 10):
            notes.append(f"lots[{i}]:{lots[i]}->{unit:g}")
            lots[i] = f"{unit:g}"
            l = unit
            if consistent(e, l, p):
                continue

        # 2. entry far from the other rungs -- recompute it from the P&L
        cand = ltp - sign * p / (l * 100.0)
        if abs(e - ent_mid) > 1.0 and abs(cand - ent_mid) <= 1.0:
            notes.append(f"entry[{i}]:{ents[i]}->{cand:.3f}")
            ents[i] = f"{cand:.3f}"
            continue

        # 3. entry and lot both look sound, so the P&L is the misread one
        if abs(e - ent_mid) <= 1.0 and abs(l - unit) < 1e-9:
            calc = sign * (ltp - e) * l * 100.0
            notes.append(f"pnl[{i}]:{pnls[i]}->{calc:.2f}")
            pnls[i] = f"{calc:.2f}"

    v["values"] = v["summary"] + v["pnls"]
    return " ".join(notes)


def _repair_summary_series(rows: list[dict]) -> int:
    """Repair the summary block using the neighbouring states.

    Two facts about a running grid make this possible without any new reading:
      * balance does not move while positions are open, so a state whose balance disagrees with
        BOTH neighbours -- while those neighbours agree with each other -- has a misread digit;
      * in the 3-row layout equity and free margin are the same number shown twice, so when
        they disagree one of them is wrong, and the right one is whichever keeps the open-P&L
        series (equity - balance) continuous with the states either side.

    Repairs are recorded in `repaired` and the resolved flag is cleared, so the row stops
    asking for manual review only when it was actually resolved.
    """
    def num(r, k):
        try:
            return float(r[k])
        except (ValueError, KeyError):
            return None

    fixed = 0
    for i, r in enumerate(rows):
        prev = rows[i - 1] if i else None
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if not prev or not nxt:
            continue

        # --- balance: neighbours agree, this one does not ---
        if "balance_drop" in r["flag"]:
            b, pb, nb = num(r, "balance"), num(prev, "balance"), num(nxt, "balance")
            if None not in (b, pb, nb) and abs(pb - nb) < 0.005 and abs(b - pb) > 0.005:
                r["repaired"] = (r.get("repaired", "") + f" balance:{r['balance']}->{pb:.2f}").strip()
                r["balance"] = f"{pb:.2f}"
                r["flag"] = ";".join(f for f in r["flag"].split(";") if f != "balance_drop")
                fixed += 1

        # --- 3-row layout: equity and free margin are the same quantity ---
        if "summary_3row_mismatch" in r["flag"]:
            eq, fm, bal = num(r, "equity"), num(r, "free_margin"), num(r, "balance")
            if None in (eq, fm, bal):
                continue
            near = [num(x, "pnl_total") for x in (prev, nxt) if not x["flag"]]
            near = [v for v in near if v is not None]
            if not near:
                continue
            target = sum(near) / len(near)
            best = min((eq, fm), key=lambda c: abs((c - bal) - target))
            other = fm if best is eq else eq
            # Only act when one candidate is clearly the better fit, not on a coin toss.
            if abs((other - bal) - target) > 2 * abs((best - bal) - target) + 0.01:
                if abs(best - eq) > 0.005:
                    r["repaired"] = (r.get("repaired", "") + f" equity:{r['equity']}->{best:.2f}").strip()
                    r["equity"] = f"{best:.2f}"
                else:
                    r["repaired"] = (r.get("repaired", "") +
                                     f" free_margin:{r['free_margin']}->{best:.2f}").strip()
                r["free_margin"] = f"{best:.2f}"
                r["pnl_total"] = f"{best - bal:.2f}"
                r["flag"] = ";".join(f for f in r["flag"].split(";") if f != "summary_3row_mismatch")
                fixed += 1
    return fixed


def _mark_cycles(rows: list[dict]) -> int:
    """Label each state with the trading cycle it belongs to.

    A recovery grid runs until every position is closed; MT5 then shows the summary block with
    an empty position list and equity back level with balance. That flat screen is the boundary
    between one cycle and the next. Two independent signals are accepted because either can be
    missed on its own: no visible position rows, or equity == balance.

    Sets `flat`, `cycle_id` and `cycle_event` (cycle_start / cycle_end).
    """
    def is_flat(r: dict) -> bool:
        if r["n_positions_visible"] == 0:
            return True
        return bool(r["balance"]) and _close(r["balance"], r["equity"], 0.001)

    cycle = 0
    prev_flat = True
    for r in rows:
        flat = is_flat(r)
        r["flat"] = "1" if flat else ""
        r["cycle_event"] = ""
        if prev_flat and not flat:
            cycle += 1
            r["cycle_event"] = "cycle_start"
        elif not prev_flat and flat:
            r["cycle_event"] = "cycle_end"
        r["cycle_id"] = cycle
        prev_flat = flat
    return cycle


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--reuse-ocr", action="store_true", default=True,
                    help="reuse ocr_raw.json when it matches the candidate count")
    ap.add_argument("--force-ocr", dest="reuse_ocr", action="store_false")
    ap.add_argument("--copy", action="store_true", default=True,
                    help="copy surviving frames into unique_full/ and unique_screen/")
    a = ap.parse_args()

    pytesseract.pytesseract.tesseract_cmd = TESSERACT

    with open(os.path.join(a.out, "candidates.csv"), encoding="utf-8") as fh:
        cands = list(csv.DictReader(fh))
    print(f"OCR over {len(cands)} candidates on {a.workers} workers ...")

    paths = [os.path.join(a.out, "screen", c["stem"] + ".jpg") for c in cands]
    cache = os.path.join(a.out, "ocr_raw_v2.json")   # v2: adds positions + ltp

    # OCR is the slow half (~10 min). Cache it so the grouping, voting and clock-repair logic
    # downstream can be re-run in seconds instead of re-reading every image.
    results = None
    if a.reuse_ocr and os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            cached = json.load(fh)
        if len(cached) == len(paths):
            results = cached
            print(f"reusing cached OCR from {cache}")
    if results is None:
        chunks = [paths[i::a.workers] for i in range(a.workers)]
        with mp.Pool(a.workers) as pool:
            results = [r for c in pool.map(_worker, chunks) for r in c]
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(results, fh)

    by_stem = {r["stem"]: r for r in results}
    ordered = [by_stem[c["stem"]] for c in cands if c["stem"] in by_stem]
    meta = {c["stem"]: c for c in cands}

    # Group adjacent same-content frames, then vote inside each group.
    groups: list[list[dict]] = []
    for r in ordered:
        if groups and _same_content(groups[-1][-1]["values"], r["values"]):
            groups[-1].append(r)
        else:
            groups.append([r])
    voted = [_vote(g) for g in groups]
    for v in voted:
        v['repaired'] = _repair_rows(v)

    # Exact dedup: identical voted vectors that survived as separate groups.
    unique: list[dict] = []
    for v in voted:
        if unique and unique[-1]["values"] == v["values"] and unique[-1]["top_pnl"] == v["top_pnl"]:
            unique[-1]["n_frames"] += v["n_frames"]
            unique[-1]["frames"].extend(v["frames"])
            continue
        unique.append(v)

    rows = []
    prev_balance = None
    for v in unique:
        summary, flag = _name_summary(v["summary"])
        pnls = v["pnls"]
        m = meta[v["stem"]]
        try:
            b = float(summary["balance"])
            if prev_balance is not None and b < prev_balance * 0.5:
                # A halving of balance between adjacent states is far more likely a dropped
                # digit than a real event, so surface it rather than trusting it.
                flag = (flag + ";balance_drop").lstrip(";")
            prev_balance = b
        except ValueError:
            flag = (flag + ";no_balance").lstrip(";")
        # pnl_visible_sum only covers the rows on screen. MT5's position list SCROLLS, so on
        # most frames there are further positions below the fold -- measured, the two figures
        # disagree on 82% of states. equity - balance is the account's true open P&L and is
        # built from two well-read values, so that is the number to trust.
        try:
            pnl_visible = f"{sum(float(x) for x in pnls):.2f}"
        except ValueError:
            pnl_visible = ""
        try:
            pnl_total = f"{float(summary['equity']) - float(summary['balance']):.2f}"
        except ValueError:
            pnl_total = ""

        frames = sorted(v["frames"])
        rows.append({
            "frame_idx": m["frame_idx"], "video_time_s": m["video_time_s"],
            "phone_clock": v["clock"],
            "pnl_total": pnl_total,
            "pnl_visible_sum": pnl_visible,
            "top_pnl_ocr": v["top_pnl"],
            **summary,
            "ltp": v["ltp"],
            "n_positions_visible": len(pnls),
            "position_pnls": " ".join(pnls),
            "position_dirs": " ".join(sorted(set(v["dirs"]))),
            "position_lots": " ".join(v["lots"]),
            "position_entries": " ".join(v["entries"]),
            "frame_first": frames[0] if frames else "",
            "frame_last": frames[-1] if frames else "",
            "source_frames": " ".join(str(f) for f in frames),
            "n_frames_merged": v["n_frames"], "ocr_confidence": v["confidence"],
            "flag": flag,
            "repaired": v.get("repaired", ""),
            "stem": v["stem"],
            "screen_file": f"screen/{v['stem']}.jpg",
            "full_file": f"full/{v['stem']}.jpg",
        })

    nfix = _repair_summary_series(rows)
    print(f'summary repairs from neighbour continuity: {nfix}')
    _repair_clocks(rows)
    ncycles = _mark_cycles(rows)

    path = os.path.join(a.out, "unique.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    if a.copy:
        for sub in ("unique_full", "unique_screen"):
            os.makedirs(os.path.join(a.out, sub), exist_ok=True)
        for r in rows:
            for src, dst in (("full", "unique_full"), ("screen", "unique_screen")):
                s = os.path.join(a.out, src, r["stem"] + ".jpg")
                if os.path.exists(s):
                    shutil.copy2(s, os.path.join(a.out, dst, r["stem"] + ".jpg"))

    flagged = sum(1 for r in rows if r["flag"])
    print(f"{len(cands)} candidates -> {len(groups)} groups -> {len(rows)} unique states")
    print(f"trading cycles detected: {ncycles}")
    print(f"flagged for review: {flagged}")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
