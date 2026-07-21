"""
Core types for the XAUUSD tick-strategy harness.

This is the v2 ``AlphaModel -> Signal`` contract (from the ai-hedge-fund/v2
study) ported to this repo's constraints:

  * numpy-only, **no pandas / no pydantic** (the research venv has neither) --
    plain ``dataclasses`` instead.
  * one instrument (XAUUSD), **tick** data -- not cross-sectional daily equities.
  * **research only.** Nothing here can place, size, or route an order. A model
    emits a *view*; a human decides. See harness/README.md.

Why an interface at all: every candidate signal becomes one swappable object
with the same shape, so they compose, blend, and are judged by the *same*
honest walk-forward -- the methodology that (in analysis/) was the only thing
that told the truth about whether an edge was real.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np

if TYPE_CHECKING:  # avoids a runtime circular import; features.py imports core
    from .features import TickFeatures


@dataclass
class Signal:
    """One model's view on one tick.

    ``value`` is a graded conviction in [-1, +1]  (-1 = max short, +1 = max
    long, 0 = no view / abstain). Graded magnitude is the upgrade over the
    original scripts, which returned only discrete -1/0/+1.

    ``confidence`` in [0, 1] is a SEPARATE, direction-agnostic strength channel
    (e.g. spike magnitude). The analysis findings showed volume/velocity
    predicts the *size* of the next move far better than its *direction*, so the
    two are kept distinct on purpose.
    """

    model_name: str
    symbol: str
    i: int                       # tick index the view was formed on
    ts: int                      # unix-ms of that tick
    value: float = 0.0           # directional conviction in [-1, +1]
    confidence: float = 0.0      # direction-agnostic strength in [0, 1]
    reasoning: str = ""
    components: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Fail loud on a malformed view rather than silently trading garbage.
        if not (-1.0 <= self.value <= 1.0):
            raise ValueError(f"{self.model_name}: value {self.value} outside [-1,1]")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"{self.model_name}: confidence {self.confidence} outside [0,1]")


@runtime_checkable
class AlphaModel(Protocol):
    """The one interface every analyst implements.

    ``predict`` sees only ticks up to and including ``i`` (point-in-time by
    construction -- indices > i are the unknown future). Returns a Signal;
    an abstain is ``value == 0``.
    """

    name: str

    def predict(self, feats: "TickFeatures", i: int) -> Signal: ...


@dataclass
class Ticks:
    """Raw bid/ask tick stream for one symbol -- the harness's only data input.

    Deliberately decoupled from MetaTrader5: build it from a live MT5 pull
    (harness/tickdata.load_ticks) OR from a synthetic fixture
    (harness/tickdata.synth_ticks), so every feature/model/backtest test runs
    with no terminal attached. Arrays are parallel numpy vectors of length n.

    This feed carries **no traded volume and no order book** (confirmed by
    live probe): ``last``/``volume`` are always 0. Only bid/ask move -- so
    ``mid`` and its velocity are the entire information content.
    """

    symbol: str
    point: float                 # price increment (0.01 for XAUUSD)
    contract: float              # oz per lot (100 for gold) -- $ context only
    t_msc: np.ndarray            # int64 unix milliseconds
    bid: np.ndarray
    ask: np.ndarray

    @property
    def mid(self) -> np.ndarray:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> np.ndarray:
        return self.ask - self.bid

    @property
    def n(self) -> int:
        return len(self.bid)
