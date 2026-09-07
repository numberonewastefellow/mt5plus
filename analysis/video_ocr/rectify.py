"""Screen-quad detection and perspective rectification for phone-camera MT5 recordings.

Single source of truth for geometry, shared by stage_a_scan.py and stage_b_ocr.py so the two
stages cannot drift apart.

The source is a phone camera pointed at *another* phone's screen: the screen occupies only
~426x973 of each 1080x1440 frame, is tilted, and drifts ~60px while shrinking ~6% over the
video. Nothing can use fixed pixel coordinates -- every frame is rectified first, and all ROIs
are expressed as fractions of the rectified screen.
"""
from __future__ import annotations

import cv2
import numpy as np

# Rectified screen sizes. Native screen is ~426x973 within the source frame.
DETECT_W, DETECT_H = 380, 820      # cheap, for Stage A change detection
OCR_W, OCR_H = 760, 1640           # ~1.7x native, verified legible for OCR / archival

# ROI fractions of the rectified screen: (x0, y0, x1, y1).
CLOCK = (0.02, 0.012, 0.20, 0.040)       # status-bar time
PNL_TOP = (0.05, 0.042, 0.42, 0.107)     # "Trade" / running total P&L (widened: a tight box
                                         # clipped the leading "-4 " off "-4 593.97 USD")
RIGHT_COL = (0.60, 0.10, 0.975, 0.895)   # the change signal: every value on screen
# y1 stops at 0.895 so the static nav bar (and its red Messages badge) stays out of the signal.
# x1 stops at 0.975: measured across the video, value digits always end by 0.957 while the
# curved-bezel highlight starts at 0.99. Cropping to the full width imported the bezel and the
# floating overlay button, which merged with the last digit ("3.16" read as "3.1").
POS_LEFT_X = (0.02, 0.60)                # x-span of a position row's text: symbol, direction,
                                         # lots, and "entry -> current". Its y-span is dynamic
                                         # (it starts wherever the position list starts), so
                                         # this is an x-pair, not a full ROI.
NAVBAR = (0.02, 0.90, 0.99, 0.95)        # static, for MT5-ness check
LABELS = (0.03, 0.115, 0.42, 0.20)       # static, for MT5-ness check

# Quad is expanded outward by this fraction before warping. A tight minAreaRect clipped the
# last digit of the right column, producing "0.4" where the truth was "0.41".
QUAD_EXPAND = 0.01

_MIN_AREA_FRAC = 0.10   # below this we assume no phone screen in frame (a cut)


def find_quad(frame: np.ndarray) -> np.ndarray | None:
    """Locate the bright phone screen and return its corners as TL, TR, BR, BL float32.

    Returns None when no sufficiently large bright quad is present -- this doubles as the
    cut / no-screen detector.
    """
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    grey = cv2.GaussianBlur(grey, (9, 9), 0)
    _, th = cv2.threshold(grey, 150, 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(biggest) < _MIN_AREA_FRAC * frame.shape[0] * frame.shape[1]:
        return None

    box = cv2.boxPoints(cv2.minAreaRect(biggest))
    s = box.sum(axis=1)
    d = np.diff(box, axis=1).ravel()
    quad = np.array(
        [box[np.argmin(s)], box[np.argmin(d)], box[np.argmax(s)], box[np.argmax(d)]],
        dtype=np.float32,
    )
    return _expand(quad, QUAD_EXPAND)


def _expand(quad: np.ndarray, frac: float) -> np.ndarray:
    """Push each corner outward from the quad centre, so the warp cannot clip edge glyphs."""
    centre = quad.mean(axis=0)
    return (centre + (quad - centre) * (1.0 + frac)).astype(np.float32)


def rectify(frame: np.ndarray, quad: np.ndarray, w: int, h: int) -> np.ndarray:
    """Warp the screen quad to a canonical upright w x h image."""
    dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(frame, m, (w, h), flags=cv2.INTER_CUBIC)


def crop(rect: np.ndarray, roi: tuple[float, float, float, float]) -> np.ndarray:
    """Crop a fractional ROI out of a rectified screen."""
    h, w = rect.shape[:2]
    x0, y0, x1, y1 = roi
    return rect[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def colour_mask(bgr: np.ndarray) -> np.ndarray:
    """Isolate MT5's blue (profit) and red (loss) value glyphs.

    The footage has a heavy pink moire cast from photographing an LCD, so this works in HSV
    rather than on raw channels. Verified clean across frames spanning the whole video.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = hsv[..., 0].astype(np.int16)
    s = hsv[..., 1].astype(np.int16)
    v = hsv[..., 2].astype(np.int16)
    blue = (h > 95) & (h < 135) & (s > 60) & (v > 60)
    # Red starts at hue 164, not the textbook ~168: photographing an LCD casts everything pink
    # and drags red glyphs toward magenta. Do not lower this further -- at 155 and below the
    # summary block's own dark text starts registering as red, and the split fires above the
    # first row (measured: 40 of 60 sampled frames broke that way).
    red = ((h < 12) | (h > 164)) & (s > 70) & (v > 60)
    return (blue | red).astype(np.uint8)


def min_diff(a: np.ndarray, b: np.ndarray, rng: int = 3) -> float:
    """Minimum XOR rate (percent) of `a` against `b` over +/- rng pixel shifts.

    Cheap stand-in for full image registration. Handheld jitter survives rectification at
    ~1-2px; without this compensation every text edge flips and swamps the real signal.
    Measured at 21ms for five ROIs versus 167ms for cv2.findTransformECC.
    """
    core = a[rng:-rng, rng:-rng]
    best = 1.0
    for dy in range(-rng, rng + 1):
        for dx in range(-rng, rng + 1):
            shifted = b[rng + dy:b.shape[0] - rng + dy, rng + dx:b.shape[1] - rng + dx]
            if shifted.shape != core.shape:
                continue
            v = float((core != shifted).mean())
            if v < best:
                best = v
    return best * 100.0


def _norm_grey(bgr: np.ndarray) -> np.ndarray:
    grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    return cv2.normalize(grey, None, 0, 255, cv2.NORM_MINMAX).astype(np.float32)


class Mt5Matcher:
    """Decides whether a rectified frame shows the MT5 trade screen.

    The video is a montage with cuts to other content. Both the nav bar and the
    Balance/Equity label block are static in MT5, so correlating them against a reference
    frame separates MT5 screens from everything else.
    """

    def __init__(self, reference: np.ndarray, threshold: float):
        self.threshold = threshold
        self._refs = [_norm_grey(crop(reference, roi)) for roi in (NAVBAR, LABELS)]

    def score(self, rect: np.ndarray) -> float:
        scores = []
        for ref, roi in zip(self._refs, (NAVBAR, LABELS)):
            cur = _norm_grey(crop(rect, roi))
            if cur.shape != ref.shape:
                cur = cv2.resize(cur, (ref.shape[1], ref.shape[0]))
            scores.append(float(cv2.matchTemplate(cur, ref, cv2.TM_CCOEFF_NORMED)[0, 0]))
        return min(scores)

    def is_mt5(self, rect: np.ndarray) -> bool:
        return self.score(rect) >= self.threshold


def build_reference(video: str, frame_idx: int, w: int = DETECT_W, h: int = DETECT_H):
    """Grab one known-good MT5 frame and return it rectified, for use as the match reference."""
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"could not read reference frame {frame_idx} from {video}")
    quad = find_quad(frame)
    if quad is None:
        raise RuntimeError(f"no screen quad found in reference frame {frame_idx}")
    return rectify(frame, quad, w, h)
