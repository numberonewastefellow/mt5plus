"""Automated strategy engines.

Each engine is DEMO-ONLY, owns its own magic number (config.STRATEGY_MAGICS), and
runs independently: the worker evaluates every registered engine on each poll, and
every MT5 helper filters by magic, so no engine can see or close another's book.

Registration is the ONE place a new engine gets wired in. Add it to ENGINES and it
appears in the worker loop, in GET /api/strategies, and in both UIs.
"""

from .base import StrategyBase
from .ladder import LadderState, TrendLadder
from .rider import VolRegimeRider
from .straddle import VolumeSpikeStraddle

# Instantiated once per worker (see Mt5Worker.__init__).
ENGINES = (VolumeSpikeStraddle, TrendLadder, VolRegimeRider)

__all__ = ["StrategyBase", "VolumeSpikeStraddle", "TrendLadder", "LadderState",
           "ENGINES"]
