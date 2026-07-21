"""
Tick features -- the only inputs a model gets. Pure numpy; no MT5, no pandas.

On this feed the entire information content is the bid/ask path (no volume, no
book), so every feature is derived from ``mid``:

  * velocity   -- Δmid over a short window (the "price moving fast" signal).
  * scale      -- rolling std of per-tick mid returns (a local volatility unit).
  * vel_z      -- velocity normalized by its expected scale => a unitless,
                  regime-robust "how extreme is this move" score.
  * quote_rate -- ticks/sec over the window; the activity proxy that stands in
                  for the missing volume.
  * spikes     -- indices where |vel_z| >= a base threshold (candidate entries),
                  precomputed so the backtest loops only over interesting ticks.

Point-in-time by construction: feature[i] uses only ticks <= i (all windows
look backward). Index into the future is never read.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .core import Ticks


@dataclass
class TickFeatures:
    ticks: Ticks
    vel_win: int
    vol_win: int
    base_spike_z: float
    mid: np.ndarray
    velocity: np.ndarray        # price change over vel_win ticks
    scale: np.ndarray           # expected |velocity| scale (vol unit)
    vel_z: np.ndarray           # velocity / scale  (nan until warmed up)
    quote_rate: np.ndarray      # ticks per second over vel_win
    spikes: np.ndarray          # int indices, |vel_z| >= base_spike_z

    @property
    def n(self) -> int:
        return self.ticks.n


def build_features(ticks: Ticks, vel_win: int = 20, vol_win: int = 300,
                   base_spike_z: float = 1.5) -> TickFeatures:
    """Compute all tick features in one pass. ``vel_win``/``vol_win`` are in ticks."""
    mid = ticks.mid
    n = len(mid)
    ret = np.zeros(n)
    ret[1:] = np.diff(mid)

    # velocity: mid[i] - mid[i-vel_win]  (== sum of last vel_win returns)
    velocity = np.full(n, np.nan)
    if n > vel_win:
        velocity[vel_win:] = mid[vel_win:] - mid[:-vel_win]

    # rolling std of per-tick returns -> local volatility; expected velocity
    # scale over vel_win ticks ~ ret_std * sqrt(vel_win) (random-walk scaling).
    # Vectorized trailing-window std (population, matches np.std) via cumsums so
    # it stays O(n) over the millions of ticks a live pull produces.
    scale = np.full(n, np.nan)
    if n > vol_win:
        c1 = np.concatenate(([0.0], np.cumsum(ret)))
        c2 = np.concatenate(([0.0], np.cumsum(ret * ret)))
        idx = np.arange(vol_win, n)
        s1 = c1[idx] - c1[idx - vol_win]           # sum of ret over [i-w, i)
        s2 = c2[idx] - c2[idx - vol_win]           # sum of ret^2
        mean = s1 / vol_win
        var = np.maximum(s2 / vol_win - mean * mean, 0.0)
        std = np.sqrt(var)
        scale[idx] = np.where(std > 0, std, np.nan)
    exp_scale = scale * np.sqrt(vel_win)
    vel_z = velocity / np.where(np.isnan(exp_scale) | (exp_scale == 0), np.nan, exp_scale)

    # quote rate (ticks/sec) over the velocity window -- the volume stand-in
    quote_rate = np.full(n, np.nan)
    t = ticks.t_msc.astype("float64")
    if n > vel_win:
        dt_s = (t[vel_win:] - t[:-vel_win]) / 1000.0
        quote_rate[vel_win:] = np.where(dt_s > 0, vel_win / dt_s, np.nan)

    lo = max(vel_win, vol_win) + 1
    valid = np.arange(lo, n)
    az = np.abs(vel_z[valid])
    spikes = valid[np.isfinite(az) & (az >= base_spike_z)]

    return TickFeatures(
        ticks=ticks, vel_win=vel_win, vol_win=vol_win, base_spike_z=base_spike_z,
        mid=mid, velocity=velocity, scale=scale, vel_z=vel_z,
        quote_rate=quote_rate, spikes=spikes.astype(int),
    )
