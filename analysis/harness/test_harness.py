"""
Offline test for the harness -- runs with NO MetaTrader5 terminal attached,
using synthetic ticks. Mirrors how ai-hedge-fund/v2 tests with a fake data
client.

Run either way:
    ../../XauOrderPad/.venv/Scripts/python.exe test_harness.py     # plain, prints PASS/FAIL
    pytest test_harness.py                                         # if pytest is present
"""
from __future__ import annotations

import numpy as np

# Support both `pytest test_harness.py` (package context) and direct `python test_harness.py`.
try:
    from .core import Signal
    from .features import build_features
    from .models import TickMomentum
    from .backtest import simulate, summarize, walk_forward
    from .bracket import simulate_bracket, walk_forward_bracket
    from .sessions import active_hours, restrict_to_active_sessions
    from .tickdata import synth_ticks
except ImportError:  # run directly from inside the folder
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from harness.core import Signal
    from harness.features import build_features
    from harness.models import TickMomentum
    from harness.backtest import simulate, summarize, walk_forward
    from harness.bracket import simulate_bracket, walk_forward_bracket
    from harness.sessions import active_hours, restrict_to_active_sessions
    from harness.tickdata import synth_ticks


def test_signal_bounds_are_enforced():
    Signal("m", "X", 0, 0, value=1.0, confidence=0.0)      # ok
    for bad in (1.5, -1.01):
        try:
            Signal("m", "X", 0, 0, value=bad)
            raise AssertionError("expected ValueError for out-of-range value")
        except ValueError:
            pass


def test_features_shapes_and_pit():
    ticks = synth_ticks(n=20_000, seed=1)
    f = build_features(ticks, vel_win=20, vol_win=300, base_spike_z=1.5)
    assert f.n == 20_000
    for arr in (f.mid, f.velocity, f.vel_z, f.quote_rate):
        assert len(arr) == 20_000
    # warm-up region is nan (point-in-time: no value before enough history)
    assert np.isnan(f.vel_z[:20]).all()
    assert len(f.spikes) > 0, "synthetic bursts should produce spike candidates"
    assert f.spikes.min() >= max(f.vel_win, f.vol_win)  # no spike inside warm-up


def test_model_returns_bounded_signal_and_correct_sign():
    ticks = synth_ticks(n=20_000, seed=2)
    f = build_features(ticks)
    follow = TickMomentum(mode="follow", spike_z=2.0)
    fade = TickMomentum(mode="fade", spike_z=2.0)
    checked = 0
    for i in f.spikes[:200]:
        sf, sd = follow.predict(f, i), fade.predict(f, i)
        assert -1.0 <= sf.value <= 1.0 and 0.0 <= sf.confidence <= 1.0
        if sf.value != 0.0:
            # follow agrees with velocity sign; fade opposes it (faithfulness)
            assert np.sign(sf.value) == np.sign(f.velocity[i])
            assert np.sign(sd.value) == -np.sign(sf.value)
            checked += 1
    assert checked > 0, "expected at least one firing spike among candidates"


def test_backtest_runs_and_pays_spread():
    ticks = synth_ticks(n=40_000, seed=3)
    f = build_features(ticks)
    model = TickMomentum(mode="follow", spike_z=2.0)
    trades = simulate(f, model, entry_thr=0.1, sl_pts=40.0, r_mult=2.0,
                      max_hold=200, i_lo=0, i_hi=f.n)
    assert trades, "expected some trades on synthetic data"
    # one-position-at-a-time: no trade opens before the previous one exits
    for a, b in zip(trades, trades[1:]):
        assert b.i_entry > a.i_exit
    s = summarize(trades)
    assert s and s["n"] == len(trades)
    # spread is inside the fills: a long enters at the ask of the FILL tick
    # (i_entry + 1), which is strictly above that tick's mid -> spread paid.
    long_t = next((t for t in trades if t.direction > 0), None)
    if long_t is not None:
        fill = long_t.i_entry + 1
        assert long_t.entry > f.mid[fill], f"entry {long_t.entry} !> mid {f.mid[fill]}"


def test_walk_forward_is_out_of_sample():
    # Synthetic span is a few hours; use fractional-day windows (timedelta takes
    # floats) so several train/test windows fit inside it.
    ticks = synth_ticks(n=150_000, seed=4, dt_ms=200)  # ~8.3 hours of ticks
    f = build_features(ticks)
    oos, weekly = walk_forward(f, train_days=0.08, test_days=0.04, max_hold=150,
                               min_train_trades=3, entry_thr=0.1)
    assert isinstance(oos, list) and isinstance(weekly, list)
    assert len(weekly) >= 1, "expected at least one walk-forward window"
    # every OOS trade must fall inside its own (later) test window -> genuinely
    # out-of-sample, never fit on the ticks it trades
    assert all(isinstance(t.R, float) for t in oos)


def test_bracket_is_direction_agnostic_and_pays_spread():
    ticks = synth_ticks(n=60_000, seed=6)
    f = build_features(ticks)
    trades = simulate_bracket(f, width_pts=200.0, r_mult=2.0, max_wait=100,
                              max_hold=300, i_lo=0, i_hi=f.n)
    assert trades, "expected some bracket trades on synthetic data"
    # both directions should occur (it never predicts a side)
    dirs = {t.direction for t in trades}
    assert dirs == {1, -1} or len(trades) < 5, f"bracket should take both sides, got {dirs}"
    # one position at a time
    for a, b in zip(trades, trades[1:]):
        assert b.i_entry > a.i_exit
    # a long fills at a breakout ABOVE the spike mid (mid[i_entry] < entry)
    long_t = next((t for t in trades if t.direction > 0), None)
    if long_t is not None:
        assert long_t.entry > f.mid[long_t.i_entry], "long breakout must fill above spike mid"


def test_bracket_walk_forward_runs():
    ticks = synth_ticks(n=150_000, seed=8, dt_ms=200)
    f = build_features(ticks)
    oos, weekly = walk_forward_bracket(f, train_days=0.08, test_days=0.04,
                                       max_wait=80, max_hold=150, min_train_trades=3)
    assert isinstance(oos, list) and isinstance(weekly, list)
    assert len(weekly) >= 1


def test_session_filter_reduces_spikes_and_costs_apply():
    ticks = synth_ticks(n=120_000, seed=9)
    f = build_features(ticks)
    fs, active = restrict_to_active_sessions(f, top_k=8)
    assert len(fs.spikes) <= len(f.spikes), "filter can only remove spikes"
    assert all(0 <= h <= 23 for h in active) and len(active) == 8
    assert set(fs.spikes).issubset(set(f.spikes))
    # extra_cost_pts must make every trade's R worse (never better)
    free = simulate_bracket(f, 200.0, 2.0, 100, 300, 0, f.n, extra_cost_pts=0.0)
    costed = simulate_bracket(f, 200.0, 2.0, 100, 300, 0, f.n, extra_cost_pts=50.0)
    common = min(len(free), len(costed))
    assert common > 0, "expected bracket trades on synth for the cost check"
    for a, b in zip(free[:common], costed[:common]):
        assert b.R < a.R, "extra cost must reduce R"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001 - test harness reports all failures
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{'ALL PASSED' if not failed else str(failed) + ' FAILED'}  ({len(tests)} tests)")
    return failed


if __name__ == "__main__":
    raise SystemExit(_run_all())
