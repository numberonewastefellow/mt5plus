"""Per-day strategy state that must survive a restart.

The daily-loss limit is only a DAILY limit if it remembers the day. It did not: `_killed`
lived in memory and `state.save` ran only from `update()`, so a restart -- or simply
flicking the enable switch -- handed the engine a fresh budget. On 2026-07-21 the rider
tripped its kill-switch at 20:02 and was armed again minutes later with the latch cleared.

Nothing here needs a broker or a server: `reconcile()` is driven directly against a stub
worker, and `strategies.state._path` is redirected to tmp_path so the real control file is
never touched.
"""

from __future__ import annotations

import datetime
import json
import types

import pytest

from strategies import state
from strategies.base import StrategyBase
from strategies.ladder import TrendLadder


TODAY = datetime.date.today().isoformat()
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect the control file. Without this the suite would read -- and rewrite --
    the operator's real strategies.json."""
    p = tmp_path / "strategies.json"
    monkeypatch.setattr(state, "_path", lambda: p)
    return p


class StubWorker:
    """Only the surface reconcile()/_check_kill touch."""

    def __init__(self, positions=(), realized=0.0):
        self._pos = list(positions)
        self.realized = realized
        self.closed = []
        self.poll_count = 1

    def strategy_positions(self, magic):
        return [p for p in self._pos if p.magic == magic]

    def strategy_daily_realized(self, magic):
        return self.realized

    def strategy_close_ticket(self, magic, ticket):
        self.closed.append(ticket)
        self._pos = [p for p in self._pos if p.ticket != ticket]
        return {"ok": True, "ticket": ticket}

    def ticks_since(self, ts):
        return None


def pos(ticket, magic, profit=0.0, price=4000.0, ptype=0):
    return types.SimpleNamespace(ticket=ticket, magic=magic, profit=profit,
                                 price_open=price, type=ptype, volume=0.01,
                                 time=1784600000)


def engine(**params):
    e = TrendLadder()
    base = dict(side="buy", trigger=4000.0, volume=0.01, target=2.0,
                stop_mode="floor", floor_offset=0.5, max_daily_loss=200.0, paper=False)
    base.update(params)
    e._apply(base)
    return e


# ---------------------------------------------------------------- the kill latch

def test_kill_switch_persists_the_latch_and_its_day(isolated_state):
    e = engine()
    e.enabled = True
    w = StubWorker(positions=[pos(1, e.MAGIC, profit=-250.0)], realized=0.0)

    assert e._check_kill(w) is True, "the limit was breached; it should have fired"
    assert e._killed is True and e._killed_day == TODAY
    assert w.closed == [1], "the engine's own book must be flattened"

    saved = json.loads(isolated_state.read_text("utf-8"))["ladder"]["runtime"]
    assert saved["killed"] is True
    assert saved["killed_day"] == TODAY, (
        "the latch was written without its day -- it cannot be a DAILY limit")


def test_a_restart_the_same_day_comes_back_killed():
    """The exact 2026-07-21 sequence: kill, restart, and the engine is free again."""
    e = engine()
    e.enabled = True
    e._check_kill(StubWorker(positions=[pos(1, e.MAGIC, profit=-250.0)]))

    fresh = engine()                       # a new process
    fresh.reconcile(StubWorker())
    assert fresh._killed is True, "the latch did not survive the restart"
    assert fresh.enabled is False
    assert fresh._state == "killed"
    assert "breached" in (fresh._error or "").lower()


def test_a_latch_from_yesterday_is_dropped():
    """A daily limit that outlives its day disables the engine forever."""
    e = engine()
    state.save("ladder", False, e._params(),
               {"killed": True, "killed_day": YESTERDAY})
    fresh = engine()
    fresh.reconcile(StubWorker())
    assert fresh._killed is False, "yesterday's latch is not today's limit"
    assert fresh._state != "killed"


def test_re_enabling_does_not_reset_the_day():
    """Trip at -200, flick the switch, lose another 200 -- the hole being closed."""
    e = engine()
    e.enabled = True
    e._check_kill(StubWorker(positions=[pos(1, e.MAGIC, profit=-250.0)]))
    assert e._killed is True

    e.update(None, True)                   # operator flicks it back on
    assert e._killed is True, "re-arming silently cleared the day's kill"
    assert e._state == "killed"
    assert "already breached" in (e._error or "")


def test_raising_the_limit_is_the_deliberate_reopen():
    """The escape hatch, and it has to be a decision about RISK -- not a toggle."""
    e = engine(max_daily_loss=200.0)
    e.enabled = True
    e._check_kill(StubWorker(positions=[pos(1, e.MAGIC, profit=-250.0)]))
    assert e._killed is True

    e.update({"max_daily_loss": 500.0}, True)
    assert e._killed is False, "raising the limit should reopen the day"
    assert e._state == "armed"
    assert e.enabled is True


def test_lowering_the_limit_does_not_reopen():
    e = engine(max_daily_loss=200.0)
    e.enabled = True
    e._check_kill(StubWorker(positions=[pos(1, e.MAGIC, profit=-250.0)]))
    e.update({"max_daily_loss": 50.0}, True)
    assert e._killed is True, "a LOWER limit must not clear a kill"


def test_the_latch_expires_when_the_day_rolls_over():
    e = engine()
    e.enabled = True
    e._check_kill(StubWorker(positions=[pos(1, e.MAGIC, profit=-250.0)]))
    e._killed_day = YESTERDAY               # pretend midnight passed
    e.enabled = True

    st = {"bid": 4000.0, "ask": 4000.4, "healthy": True,
          "account": {"is_demo": True, "margin_mode": 2}}
    e.evaluate(StubWorker(), st)
    assert e._killed is False and e._killed_day is None


# ---------------------------------------------------------------- ladder counters

def test_ladder_daily_counters_survive_a_restart():
    """`max_ladders_per_day` and `cooldown_s` were memory-only, so a restart reset both
    -- and this box restarts itself."""
    e = engine()
    e._today = datetime.date.today()
    e._ladders_today = 7
    e._last_ladder_end = 1784600000.0
    e.update(None, False)                   # any control change persists

    fresh = engine()
    fresh.reconcile(StubWorker())
    assert fresh._ladders_today == 7, "the daily ladder count did not survive"
    assert fresh._last_ladder_end == pytest.approx(1784600000.0)


def test_yesterdays_ladder_count_is_not_inherited():
    e = engine()
    state.save("ladder", False, e._params(),
               {"day": YESTERDAY, "ladders_today": 40, "last_ladder_end": 1.0})
    fresh = engine()
    fresh.reconcile(StubWorker())
    assert fresh._ladders_today == 0, "a fresh day must start with a fresh budget"


def test_runtime_save_is_throttled_off_the_poll_path(monkeypatch):
    """`_finish_ladder` runs inside the ~66 ms budget; it must not write on every ladder."""
    e = engine()
    writes = []
    monkeypatch.setattr(state, "save", lambda *a, **k: writes.append(1))

    e._finish_ladder(1000.0)
    e._finish_ladder(1001.0)                # 1 s later -- inside the throttle
    e._finish_ladder(1002.0)
    assert len(writes) == 1, f"wrote {len(writes)} times inside the throttle window"

    e._finish_ladder(1000.0 + 60)           # well past it
    assert len(writes) == 2


# ---------------------------------------------------------------- the base default

def test_engines_without_extra_state_still_round_trip():
    """`_runtime` on the base class must be enough on its own -- a subclass that never
    overrides it still gets a working daily latch."""
    assert StrategyBase._runtime is not TrendLadder._runtime
    e = engine()
    rt = e._runtime()
    for k in ("killed", "killed_day", "day", "ladders_today", "last_ladder_end"):
        assert k in rt, f"{k} missing from the persisted runtime"
