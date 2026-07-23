"""The midnight rollover must never be able to silence the application.

It did, twice, on the EC2 box:

    XauOrderPad-2026-07-17.log  513,221  23:59:46
    XauOrderPad-2026-07-18.log        0  00:01:53   <- died at the rollover
    XauOrderPad-2026-07-19.log   MISSING            <- silent all day
    ...
    XauOrderPad-2026-07-21.log  737,375  23:59:21
    XauOrderPad-2026-07-22.log        0  00:01:41   <- died again

A live trading server ran for two days with no audit trail, and nothing said so: the
console handler writes to stderr and the scheduled task discards it.

The mechanism is in the stdlib's emit path --

    if self.shouldRollover(record): self.doRollover()
    logging.FileHandler.emit(self, record)          # skipped when doRollover raises

-- combined with `shouldRollover` being `now >= self.rolloverAt`. Assign `rolloverAt` last
and one exception leaves the deadline in the past, so every later record re-enters the
failing rollover and is dropped by handleError. Silent and permanent.

These tests inject failures at each stage and assert that records still reach a file.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from logger_setup import DailyDatedRotatingHandler


def mk(tmp_path: Path) -> DailyDatedRotatingHandler:
    h = DailyDatedRotatingHandler(log_dir=tmp_path, app_name="T", backup_count=100)
    h.setFormatter(logging.Formatter("%(message)s"))
    return h


def rec(msg: str) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)


def contents(tmp_path: Path) -> str:
    return "".join(p.read_text("utf-8") for p in sorted(tmp_path.glob("T-*.log")))


def force_due(h: DailyDatedRotatingHandler) -> None:
    """Make the next record trigger a rollover."""
    h.rolloverAt = int(time.time()) - 1


# ---------------------------------------------------------------- the regression

@pytest.mark.parametrize("boom", ["_prune_old_files", "_path_for_today"])
def test_a_failing_rollover_does_not_silence_logging(tmp_path, monkeypatch, boom):
    """The exact 18-July / 22-July failure: one exception inside doRollover used to drop
    every subsequent record for the life of the process."""
    h = mk(tmp_path)
    h.emit(rec("before"))
    assert "before" in contents(tmp_path)

    monkeypatch.setattr(DailyDatedRotatingHandler, boom,
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(logging, "raiseExceptions", False)   # production behaviour
    force_due(h)
    h.emit(rec("during"))

    # And, critically, everything AFTER it.
    for i in range(5):
        h.emit(rec(f"after-{i}"))
    h.flush()

    body = contents(tmp_path)
    for i in range(5):
        assert f"after-{i}" in body, (
            f"records stopped reaching disk after a failing {boom} -- "
            f"this is the bug that cost two days of audit trail")


def test_a_failing_rollover_still_advances_the_deadline(tmp_path, monkeypatch):
    """The specific property that makes the failure permanent rather than momentary:
    if `rolloverAt` stays in the past, EVERY later record retries the broken rollover."""
    h = mk(tmp_path)
    monkeypatch.setattr(DailyDatedRotatingHandler, "_prune_old_files",
                        lambda self: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(logging, "raiseExceptions", False)
    force_due(h)
    now = int(time.time())
    h.emit(rec("x"))
    assert h.rolloverAt > now, "the deadline stayed in the past -- every record will retry"


def test_an_unopenable_file_self_heals(tmp_path, monkeypatch):
    """If today's file cannot be opened at rollover time, logging must resume by itself
    once whatever held it lets go -- not stay dead until a restart."""
    h = mk(tmp_path)
    monkeypatch.setattr(logging, "raiseExceptions", False)
    calls = {"n": 0}
    real_open = DailyDatedRotatingHandler._open

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("locked")
        return real_open(self)

    monkeypatch.setattr(DailyDatedRotatingHandler, "_open", flaky)
    force_due(h)
    h.emit(rec("during-failure"))
    h.emit(rec("recovered"))
    h.flush()
    assert "recovered" in contents(tmp_path)


# ---------------------------------------------------------------- unchanged behaviour

def test_a_healthy_rollover_still_rotates(tmp_path):
    """The fix must not cost the feature: the live file still carries TODAY's date."""
    h = mk(tmp_path)
    h.emit(rec("one"))
    force_due(h)
    h.emit(rec("two"))
    h.flush()
    from datetime import date
    expected = tmp_path / f"T-{date.today().isoformat()}.log"
    assert expected.exists()
    assert h.baseFilename == str(expected)
    body = contents(tmp_path)
    assert "one" in body and "two" in body


def test_the_failure_is_reported_out_of_band(tmp_path, monkeypatch):
    """Logging cannot report its own outage through itself, and on the box stderr belongs
    to a scheduled task that discards it. That is precisely why the 18-July failure went
    unnoticed for two days. A sibling file is the only channel that survives."""
    h = mk(tmp_path)
    monkeypatch.setattr(DailyDatedRotatingHandler, "_prune_old_files",
                        lambda self: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(logging, "raiseExceptions", False)
    force_due(h)
    h.emit(rec("x"))

    err = tmp_path / "T-logging-errors.log"
    assert err.exists(), "a logging failure left no trace anywhere"
    assert "rollover failed" in err.read_text("utf-8")


def test_the_error_file_never_raises(tmp_path, monkeypatch):
    """An error path that can raise is not an error path -- if the fallback file is
    unwritable the application must still not see an exception from a log call."""
    h = mk(tmp_path)
    monkeypatch.setattr(logging, "raiseExceptions", False)
    monkeypatch.setattr("builtins.open",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no disk")))
    h.handleError(rec("x"))          # must simply return


def test_append_mode_survives_a_restart(tmp_path):
    """Two handlers over the same day must not truncate each other -- a restart already
    happens twice per deploy."""
    h1 = mk(tmp_path)
    h1.emit(rec("first-process"))
    h1.close()
    h2 = mk(tmp_path)
    h2.emit(rec("second-process"))
    h2.flush()
    body = contents(tmp_path)
    assert "first-process" in body and "second-process" in body
