"""
Research shim for the rider's pure logic. The canonical numpy-only decision code
lives in XauOrderPad/strategies/rider_core.py (imported below), so the backtest
and the live server engine run the EXACT same RiderState. This module only adds
`entry_signal` — the vectorized (pandas) entry-direction array for the
patternlib backtest, which is research-only.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

# import the single source of truth (numpy-only pure logic)
_CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                     "XauOrderPad", "strategies")
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from rider_core import (  # noqa: E402
    NY_HOURS, RiderConfig, RiderState, Suggestion, kelly_lot,
)

__all__ = ["NY_HOURS", "RiderConfig", "RiderState", "Suggestion", "kelly_lot", "entry_signal"]


def entry_signal(df: pd.DataFrame, cfg: RiderConfig) -> np.ndarray:
    """+1/-1/0 per bar: thrust direction, gated by the volatility regime.
    Point-in-time. Feeds patternlib.backtest so the backtest is the validated edge."""
    atr = df["atr"]
    atr_med = atr.rolling(cfg.atr_win).median()
    regime = (atr > atr_med).to_numpy() & np.isfinite(atr_med.to_numpy())
    if cfg.use_ny_hours:
        regime = regime & np.isin(df["hour"].to_numpy(), NY_HOURS)
    thrust = (df["body"].abs() > cfg.thrust_mult * atr).to_numpy() & np.isfinite(atr.to_numpy())
    s = np.sign(df["body"].to_numpy())
    sig = np.zeros(len(df))
    fire = regime & thrust
    sig[fire & (s > 0)] = 1
    sig[fire & (s < 0)] = -1
    return sig
