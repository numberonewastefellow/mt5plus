"""Daily-rotating JSON Lines logger for XauOrderPad.

Why this exists
---------------
When the order pad ever drives real money, every order — placed, filled,
rejected, or closed — needs to be reconstructable AFTER the fact, without
re-running anything. The browser audit JSON is per-test; this file is the
always-on system-of-record on disk.

Format
------
Each log line is a self-contained JSON object (JSON Lines / JSONL). One
event per line. Heterogeneous schemas across event types are fine — pandas
loads the file with one call and fills missing fields with NaN:

    pd.read_json("~/Documents/XauOrderPad/XauOrderPad-2026-06-29.log",
                 lines=True)

Files
-----
Path:      <user-home>/Documents/XauOrderPad/XauOrderPad-YYYY-MM-DD.log
Append:    Multiple server restarts on the same day all append to the same
           file (no overwrite, no extra suffix).
Rollover:  At local midnight. The NEW file is named with the NEW day's date
           (not the standard TimedRotatingFileHandler behaviour of leaving
           the live file under yesterday's name).
Retention: backupCount=100 historical files; older ones are pruned on rollover.

Call-site usage
---------------
At any module:

    import logging
    log = logging.getLogger("XauOrderPad.worker")
    log.info(
        "order placed",
        extra={
            "event": "order_filled",
            "ticket": 12345,
            "symbol": "XAUUSD",
            "side": "BUY",
            "volume": 0.01,
            "requested_price": 4001.23,
            "fill_price": 4001.25,
            "slippage": 0.02,
            "retcode": 10009,
            "magic": 532026,
            "auto_test": True,
        },
    )

All `extra={…}` fields are merged into the JSON object flat. The `event`
field is used as the primary slicing key in downstream analysis.

Pure stdlib — no new pip dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import log_context


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    app_name: str = "XauOrderPad",
    *,
    file_prefix: Optional[str] = None,
    log_dir: Optional[Path] = None,
    backup_days: int = 100,
    console: bool = True,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure and return the root logger for the app.

    Idempotent: safe to call multiple times — subsequent calls leave the
    existing handlers in place (so importing the server module twice does
    not duplicate every line in the file).

    Parameters
    ----------
    app_name : str
        Both the logger namespace AND the filename prefix.
    log_dir : Path, optional
        Override the directory. Defaults to ~/Documents/<app_name>/.
    backup_days : int
        Number of historical daily files to keep. Older files are deleted
        when a rollover fires.
    console : bool
        If True, also tee to stderr in plain text (so the run.bat console
        window stays useful for live debugging).
    level : int
        Minimum log level (default INFO).

    Returns
    -------
    The configured top-level logger (e.g. for use as `log = setup_logging(...)`).
    Child loggers via `logging.getLogger("XauOrderPad.worker")` etc. inherit.
    """
    root = logging.getLogger(app_name)
    root.setLevel(level)

    # Idempotency guard: if we already attached our handler, don't add again.
    if any(isinstance(h, DailyDatedRotatingHandler) for h in root.handlers):
        return root

    # `app_name` is the LOGGER NAMESPACE and must stay "XauOrderPad": every module
    # does logging.getLogger("XauOrderPad.worker") etc., and those are children of
    # that exact name. Renaming it per instance would leave the handlers on a logger
    # nothing writes to -- the server would run with NO audit trail at all, silently.
    # So the per-instance part is a separate FILENAME prefix.
    prefix = file_prefix or app_name

    # Resolve log directory: default to ~/Documents/<app_name>/ on all platforms.
    # Deliberately keyed on app_name, not the prefix: every instance's log belongs in
    # ONE directory, distinguished by filename, so a day's trading across all accounts
    # loads with a single glob.
    if log_dir is None:
        log_dir = Path.home() / "Documents" / app_name
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # File handler — JSONL, daily rotation, dated filename.
    file_handler = DailyDatedRotatingHandler(
        log_dir=log_dir,
        app_name=prefix,
        backup_count=backup_days,
    )
    # Stamp instance/account onto every record.
    #
    # On the HANDLER, not on the logger. Almost every line in this app is logged via a
    # CHILD logger (getLogger("XauOrderPad.worker") etc.), and Logger.callHandlers walks
    # the ancestor chain collecting their HANDLERS while skipping their FILTERS -- only
    # the logger the record was logged through gets its filters applied. A filter on
    # "XauOrderPad" would therefore stamp nothing except the handful of lines logged
    # through that exact logger, and would look like it worked.
    ctx_filter = log_context.ContextFilter()

    file_handler.setFormatter(JsonlFormatter())
    file_handler.setLevel(level)
    file_handler.addFilter(ctx_filter)
    root.addHandler(file_handler)

    # Console handler — plain text, for the operator watching the run.bat window.
    if console:
        console_handler = logging.StreamHandler(stream=sys.stderr)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        console_handler.setLevel(level)
        root.addHandler(console_handler)

    # Prevent double-emission via Python's root logger.
    root.propagate = False

    root.info(
        "logger ready",
        extra={
            "event": "logger_started",
            "log_dir": str(log_dir),
            "backup_days": backup_days,
            "today_file": str(file_handler.baseFilename),
            "pid": os.getpid(),
        },
    )
    return root


# ---------------------------------------------------------------------------
# JSON Lines formatter
# ---------------------------------------------------------------------------

# Standard LogRecord fields we don't want to spam into every JSON line.
_LOGRECORD_INTERNAL = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
})


class JsonlFormatter(logging.Formatter):
    """Format every LogRecord as a single-line JSON object.

    Always present:
        ts     -- local-time ISO 8601 with TZ offset (pandas-parseable)
        level  -- INFO / WARNING / ERROR / etc.
        event  -- short event name (falls back to logger name if missing)
        msg    -- human-readable message

    Plus every `extra={…}` field the call site attached.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        ts = datetime.fromtimestamp(record.created).astimezone()
        obj: dict[str, Any] = {
            "ts": ts.isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "msg": record.getMessage(),
        }
        # Merge any user-supplied structured fields (skip logging internals).
        for k, v in record.__dict__.items():
            if k in _LOGRECORD_INTERNAL or k in obj or k.startswith("_"):
                continue
            obj[k] = v
        # Attach exception info if present (one-liner; full traceback as string).
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info).replace("\n", " | ")
        # `default=str` keeps Decimal / datetime / Path / etc. serialisable.
        return json.dumps(obj, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Daily-dated rotating file handler
# ---------------------------------------------------------------------------

class DailyDatedRotatingHandler(TimedRotatingFileHandler):
    """Like TimedRotatingFileHandler but the live file always has TODAY's
    date in the filename (not yesterday's). Restart-safe (append mode).

    Filename pattern:  <app_name>-YYYY-MM-DD.log

    Stdlib's TimedRotatingFileHandler keeps a fixed `baseFilename` forever
    and adds a date suffix to the ROTATED file. That means after midnight
    the live file still has yesterday's date in its name, which the user
    explicitly didn't want. This subclass instead recomputes `baseFilename`
    at every rollover so the live file is always `<app>-<today>.log`.
    """

    def __init__(
        self,
        log_dir: Path,
        app_name: str,
        backup_count: int = 100,
        encoding: str = "utf-8",
    ) -> None:
        self.log_dir = Path(log_dir)
        self.app_name = app_name
        super().__init__(
            filename=str(self._path_for_today()),
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding=encoding,
            delay=False,
            utc=False,
        )

    def _path_for_today(self) -> Path:
        return self.log_dir / f"{self.app_name}-{date.today().isoformat()}.log"

    def doRollover(self) -> None:  # noqa: D401
        """At midnight: close current stream, point to <app>-<today>.log,
        prune anything older than `backupCount` days, re-open in append mode.
        """
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]

        # Switch the live file pointer to today's (new) date.
        self.baseFilename = str(self._path_for_today())

        # Best-effort prune of files older than the retention window.
        self._prune_old_files()

        # Re-open in append mode so any pre-existing content is preserved
        # (e.g. if the file already exists because the server restarted today).
        if not self.delay:
            self.stream = self._open()

        # Recompute the next midnight rollover (mirrors the parent class logic).
        current_time = int(time.time())
        new_rollover_at = self.computeRollover(current_time)
        # Avoid scheduling a rollover that's already in the past (clock skew).
        while new_rollover_at <= current_time:
            new_rollover_at = new_rollover_at + self.interval
        self.rolloverAt = new_rollover_at

    def _prune_old_files(self) -> None:
        """Delete log files older than `backupCount` days.

        Matches both today's-style files (<app>-YYYY-MM-DD.log) and any
        legacy rotated files (<app>-YYYY-MM-DD.log.YYYY-MM-DD) that might
        exist from an earlier configuration.
        """
        if self.backupCount <= 0:
            return
        cutoff = date.today() - timedelta(days=self.backupCount)
        pattern = f"{self.app_name}-*"
        for path in self.log_dir.glob(pattern):
            try:
                # Parse the date out of the filename's first YYYY-MM-DD chunk.
                stem = path.name[len(self.app_name) + 1 :]  # strip "<app>-"
                date_str = stem[:10]  # "YYYY-MM-DD"
                file_date = date.fromisoformat(date_str)
            except (ValueError, IndexError):
                continue  # not one of ours, skip
            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass  # best-effort; permissions / locked file etc.
