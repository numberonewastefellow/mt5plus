"""
Alpha models -- each implements ``AlphaModel.predict(feats, i) -> Signal``.

TickMomentum is the strategy the user picked: "price moving fast -> act on it",
as a fast DETERMINISTIC rule (no LLM -- an LLM is seconds too slow for a
~200ms tick). Two modes test the two hypotheses:

  * FOLLOW -- trade WITH the thrust (momentum/continuation).
  * FADE   -- trade AGAINST it (climactic/exhaustion reversal).

The walk-forward decides which (if either) survives out-of-sample. Prior
bar-level research found the winning direction *flipped* between regimes and
lost OOS -- so we make no assumption and let the honest test rule.

Conviction is GRADED: value = +/- tanh(vel_z / k). This is the upgrade over the
old discrete -1/0/+1 -- a bigger velocity surprise => a stronger view.
"""
from __future__ import annotations

import numpy as np

from .core import Signal
from .features import TickFeatures


class TickMomentum:
    """Velocity-spike momentum model.

    Fires only on a tick whose |vel_z| >= ``spike_z`` (its own gate, which may
    be stricter than the feature layer's base threshold). ``mode`` sets whether
    the view follows or fades the move.
    """

    def __init__(self, mode: str = "follow", spike_z: float = 2.0,
                 conviction_k: float = 3.0):
        if mode not in ("follow", "fade"):
            raise ValueError(f"mode must be 'follow' or 'fade', got {mode!r}")
        self.mode = mode
        self.spike_z = float(spike_z)
        self.conviction_k = float(conviction_k)
        self.name = f"tickmom_{mode}"

    def predict(self, feats: TickFeatures, i: int) -> Signal:
        sym = feats.ticks.symbol
        ts = int(feats.ticks.t_msc[i])
        z = feats.vel_z[i]
        if not np.isfinite(z) or abs(z) < self.spike_z:
            return Signal(self.name, sym, i, ts, value=0.0, confidence=0.0,
                          reasoning="no velocity spike")

        graded = float(np.tanh(z / self.conviction_k))     # signed, in (-1, 1); sign == sign(velocity)
        value = graded if self.mode == "follow" else -graded
        conf = float(min(1.0, abs(z) / (2.0 * self.spike_z)))
        vel = float(feats.velocity[i])
        direction = "LONG" if value > 0 else "SHORT"
        return Signal(
            self.name, sym, i, ts, value=value, confidence=conf,
            reasoning=(f"{self.mode} velocity spike: vel_z={z:+.2f} "
                       f"(Δmid={vel:+.2f} over {feats.vel_win} ticks) -> {direction}"),
            components={"vel_z": float(z), "velocity": vel,
                        "quote_rate": float(feats.quote_rate[i])},
            metadata={"mode": self.mode},
        )
