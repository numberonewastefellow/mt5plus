"""
Offline unit tests for RiderState — no MT5, synthetic bars. Runs both as a plain
script and under pytest.

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe test_rider.py
"""
from __future__ import annotations

import numpy as np

from rider_state import RiderConfig, RiderState, entry_signal, kelly_lot, Suggestion


def _bar(o, h, l, c, atr=1.0, atr_med=0.5, hour=14):
    return dict(o=o, h=h, l=l, c=c, atr=atr, atr_med=atr_med, hour=hour, equity=500.0)


def test_kelly_is_small_and_floored():
    cfg = RiderConfig()
    assert kelly_lot(500, cfg) == 0.01                 # 1% of $500 / ($6*100) -> floored to min
    assert kelly_lot(6000, cfg) == 0.10                # scales with equity: 1%*6000/600 = 0.10
    assert kelly_lot(1e9, cfg) <= 1.0                  # capped at lot_max


def test_enters_on_thrust_in_regime_then_fills_next_bar():
    cfg = RiderConfig(thrust_mult=1.0, use_ny_hours=False)
    st = RiderState(cfg)
    # bar 1: strong up body (2.0) vs atr 1.0 in high-vol (atr>atr_med) -> arm
    acts = st.on_bar(**_bar(100.0, 102.2, 99.9, 102.0))
    assert all(a.kind in ("flat", "hold") for a in acts)   # armed, not yet entered
    assert st.pending_dir == 1
    # bar 2: fills at this open
    acts = st.on_bar(**_bar(102.0, 103.0, 101.8, 102.5))
    kinds = [a.kind for a in acts]
    assert "enter" in kinds
    ent = next(a for a in acts if a.kind == "enter")
    assert ent.side == "buy" and ent.entry_ref == 102.0 and ent.sl == 102.0 - cfg.sl


def test_stops_out_and_bounds():
    cfg = RiderConfig(thrust_mult=1.0, sl=6.0, trail=6.0)
    st = RiderState(cfg)
    st.on_bar(**_bar(100.0, 102.2, 99.9, 102.0))        # arm up
    st.on_bar(**_bar(102.0, 103.0, 101.8, 102.5))        # enter long @102, stop @96
    # a bar that trades down through the stop
    acts = st.on_bar(**_bar(102.5, 102.6, 95.0, 96.0))
    assert any(a.kind == "close" and "stop" in a.reason for a in acts)
    assert not st.in_pos                                  # flat again (one-at-a-time)


def test_one_position_at_a_time():
    cfg = RiderConfig(thrust_mult=1.0)
    st = RiderState(cfg)
    st.on_bar(**_bar(100.0, 102.2, 99.9, 102.0)); st.on_bar(**_bar(102.0, 103.0, 101.8, 102.5))
    assert st.in_pos
    # another thrust while in position must NOT open a second entry
    acts = st.on_bar(**_bar(102.5, 108.0, 102.4, 107.5))
    assert not any(a.kind == "enter" for a in acts)


def test_signal_bounds_and_pit():
    import pandas as pd
    n = 300
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 0.5, n))
    df = pd.DataFrame({"o": c, "h": c + 0.5, "l": c - 0.5, "c": c,
                       "body": rng.normal(0, 1, n)}, index=pd.RangeIndex(n))
    df["atr"] = 0.8
    df["hour"] = 14
    sig = entry_signal(df, RiderConfig())
    assert set(np.unique(sig)).issubset({-1.0, 0.0, 1.0})
    assert len(sig) == n


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for t in tests:
        try:
            t(); print(f"  PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            fails += 1; print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{'ALL PASSED' if not fails else str(fails)+' FAILED'}  ({len(tests)} tests)")
    return fails


if __name__ == "__main__":
    raise SystemExit(_run_all())
