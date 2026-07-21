"""
XAUUSD tick-strategy harness (research only).

A v2-style ``AlphaModel -> Signal`` engine at tick granularity, with an honest
rolling walk-forward. Builds and *tests* strategies; it never places, sizes, or
routes an order -- a human keeps the trigger. See README.md.
"""
from .core import AlphaModel, Signal, Ticks
from .features import TickFeatures, build_features
from .models import TickMomentum
from .backtest import Trade, simulate, summarize, walk_forward, walk_forward_generic
from .bracket import simulate_bracket, walk_forward_bracket
from .sessions import active_hours, restrict_to_active_sessions
from .tickdata import load_ticks, synth_ticks

__all__ = [
    "AlphaModel", "Signal", "Ticks", "TickFeatures", "build_features",
    "TickMomentum", "Trade", "simulate", "summarize", "walk_forward",
    "walk_forward_generic", "simulate_bracket", "walk_forward_bracket",
    "active_hours", "restrict_to_active_sessions",
    "load_ticks", "synth_ticks",
]
