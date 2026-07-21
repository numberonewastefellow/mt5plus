"""
Session filter -- restrict trading to the most active hours of the day.

The gross edge (+0.069R at zero cost) is an *average* over all spikes. The
hypothesis: spikes during high-activity sessions (London / NY open, overlaps,
news windows) produce bigger follow-through relative to the spread, so filtering
to them lifts the edge and widens the margin over cost.

Self-calibrating and timezone-proof: instead of hard-coding clock windows (the
broker's timezone is ambiguous -- GMT+2/+3), we rank hours by tick activity and
keep the busiest `top_k`. Those ARE the London/NY sessions, by definition.
"""
from __future__ import annotations

import dataclasses as dc

import numpy as np

from .features import TickFeatures


def _hour_of_day(t_msc: np.ndarray) -> np.ndarray:
    return ((t_msc // 3_600_000) % 24).astype(int)


def active_hours(feats: TickFeatures, top_k: int = 8) -> list[int]:
    """The `top_k` busiest hours-of-day by tick count (the active sessions)."""
    hrs = _hour_of_day(feats.ticks.t_msc)
    counts = np.bincount(hrs, minlength=24)
    return sorted(int(h) for h in np.argsort(counts)[::-1][:top_k])


def restrict_to_active_sessions(feats: TickFeatures, top_k: int = 8):
    """Return (filtered_feats, active_hours). Keeps only spikes whose hour is in
    the busiest `top_k` hours; everything else about `feats` is unchanged."""
    active = set(active_hours(feats, top_k))
    spike_hours = _hour_of_day(feats.ticks.t_msc[feats.spikes])
    keep = np.array([h in active for h in spike_hours], dtype=bool)
    filtered = dc.replace(feats, spikes=feats.spikes[keep])
    return filtered, sorted(active)
