"""A FAKE MetaTrader5 module. TEST ONLY. NEVER let this reach the EC2 box.

Why this exists
---------------
`mt5_worker.py:19` does `import MetaTrader5 as mt5` at module level, and the real package
is Windows-only (no Linux wheel). That single import is the ONLY thing preventing the real,
unmodified `server.py` from running in a Linux container. Put this file on PYTHONPATH and
the whole server boots -- routing, auth, the /ws loop, _close_where, all of it -- with no
production code changed.

That makes it possible to test the money paths (does CLOSE LOSING close ONLY the loser?
does a failed close really come back as HTTP 200?) without a broker and without risking a
real account.

THE DANGER
----------
This module pretends to fill orders. A server running against it will cheerfully report
"order filled, ticket #100001" while nothing whatsoever happened at any broker. If it were
ever loaded in production, the operator would see a healthy-looking order pad that trades
into the void.

Three rails stop that:

  1. It REFUSES TO IMPORT unless XAUORDERPAD_FAKE_MT5=1 is explicitly set. A stray
     PYTHONPATH on the EC2 box raises loudly instead of silently faking a broker.
  2. The compose service publishes port 8766, never 8765, so it cannot collide with the
     real server.
  3. account_info() self-identifies: server="STUB-NOT-REAL", login=999999, is_demo=True.
     Anything reading /api/state can see instantly that this is not a broker.

What it does NOT do
-------------------
It models OUR logic, not the broker's. There is no slippage, no requote, no partial fill,
no INVALID_VOLUME, no filling-mode negotiation. Passing tests here do NOT mean CLOSE LOSING
works against a real broker -- that still has to be proven on a demo account.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

if os.environ.get("XAUORDERPAD_FAKE_MT5") != "1":
    raise RuntimeError(
        "REFUSING TO LOAD: this is the FAKE MetaTrader5 stub (XauOrderPad/testing/stub). "
        "It pretends to fill orders and must never run against a real account. "
        "Set XAUORDERPAD_FAKE_MT5=1 only in the test container. "
        "If you are seeing this on the trading box, your PYTHONPATH is wrong -- and this "
        "exception just saved you from a server that trades into the void."
    )

__version__ = "0.0.0-STUB"

# ---- constants (values are arbitrary; only identity/comparison matters) ----------------
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_REMOVE = 1, 5, 2
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 4, 5
ORDER_TYPE_BUY_STOP_LIMIT, ORDER_TYPE_SELL_STOP_LIMIT = 6, 7
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
ORDER_TIME_GTC = 0
TIMEFRAME_M1 = 1
SYMBOL_TRADE_MODE_FULL = 4
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_REQUOTE = 10004
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY = 1, 2, 3
DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1

_lock = threading.RLock()


class _State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.positions: dict[int, SimpleNamespace] = {}
        self.next_ticket = 100_001
        self.connected = True
        self.trade_allowed = True
        self.frozen = False          # freeze the tick -> snapshot stops changing
        self.fail_order_send = False  # order_send returns a non-DONE retcode
        self.bid = 2400.00
        self.ticks = 0
        self.tick_time = int(time.time())
        # Call counters. `positions_get` is the one that matters: _close_where MUST
        # re-read live broker state inside its retry loop rather than snapshotting once,
        # and a call count is the only way to prove it actually does.
        self.calls: dict[str, int] = {"positions_get": 0, "order_send": 0}


_s = _State()


# ---- test control surface (NOT part of the real MetaTrader5 API) -----------------------

def _reset() -> None:
    with _lock:
        _s.reset()


def _seed(side: str, volume: float, profit: float, magic: int = 532026) -> int:
    """Add one open position with a chosen profit sign. Returns its ticket."""
    with _lock:
        t = _s.next_ticket
        _s.next_ticket += 1
        _s.positions[t] = SimpleNamespace(
            ticket=t,
            type=POSITION_TYPE_BUY if side == "buy" else POSITION_TYPE_SELL,
            volume=float(volume),
            price_open=_s.bid,
            sl=0.0, tp=0.0,
            profit=float(profit),
            time=int(time.time()),
            magic=int(magic),
        )
        return t


def _book() -> dict[int, float]:
    """{ticket: profit} for everything still open. The oracle for 'did we close the RIGHT one'."""
    with _lock:
        return {t: p.profit for t, p in _s.positions.items()}


def _freeze(on: bool = True) -> None:
    """Stop the tick moving, so the /ws snapshot is byte-identical poll to poll.

    This is what makes the heartbeat testable: with a frozen tick the skip-unchanged
    dedupe suppresses every frame, and only the heartbeat keeps the socket alive.
    """
    with _lock:
        _s.frozen = on


def _fail_order_send(on: bool = True) -> None:
    with _lock:
        _s.fail_order_send = on


def _set_connected(on: bool) -> None:
    with _lock:
        _s.connected = on


def _calls() -> dict[str, int]:
    with _lock:
        return dict(_s.calls)


# ---- the fake MetaTrader5 API ----------------------------------------------------------

def initialize(**kwargs) -> bool:
    return True


def login(login_id: int, password: str = "", server: str = "") -> bool:
    return True


def shutdown() -> None:
    return None


def last_error() -> tuple[int, str]:
    return (0, "stub: no error")


def terminal_info():
    with _lock:
        if not _s.connected:
            return SimpleNamespace(connected=False, trade_allowed=False, path="/stub")
        return SimpleNamespace(connected=True, trade_allowed=_s.trade_allowed, path="/stub")


def account_info():
    with _lock:
        return SimpleNamespace(
            balance=10_000.0,
            equity=10_000.0 + sum(p.profit for p in _s.positions.values()),
            currency="USD",
            login=999999,                 # obviously not a real account number
            server="STUB-NOT-REAL",       # visible in /api/state -- cannot be mistaken for a broker
            trade_mode=0,                 # 0 = demo (never 2 = real)
            margin_mode=2,                # hedging
        )


def symbol_info(symbol: str):
    return SimpleNamespace(
        name=symbol, digits=2, point=0.01,
        volume_min=0.01, volume_step=0.01, volume_max=100.0,
        trade_mode=SYMBOL_TRADE_MODE_FULL,
        filling_mode=1,     # _pick_filling() reads this
    )


def symbol_info_tick(symbol: str):
    with _lock:
        # A moving tick makes every snapshot differ, so the skip-unchanged dedupe never
        # fires -- that is what the "dedupe still works" and hz-clamp tests need.
        # _freeze() pins it instead, so the dedupe DOES engage and the heartbeat is the only
        # thing keeping the socket alive -- which is what the BE-1 test needs.
        #
        # It must change on EVERY call: a tick that only moves once a second would let the
        # dedupe suppress frames and make a working server look broken.
        if not _s.frozen:
            _s.ticks += 1
            _s.bid = round(2400.00 + (_s.ticks % 500) * 0.01, 2)
            _s.tick_time = int(time.time())
        # `time` must freeze too. The server copies it into the snapshot as `tick_time`, and
        # only `ts` is excluded from the dedupe comparison -- so a tick_time that keeps
        # ticking makes the snapshot differ once a second, a frame flows once a second, and
        # the heartbeat test PASSES EVEN WITH THE HEARTBEAT REMOVED. (Caught by the mutation
        # test, not by reading.) A real MT5 tick_time also only moves when a tick arrives,
        # so freezing it is the honest model of a dead feed.
        return SimpleNamespace(
            bid=_s.bid, ask=round(_s.bid + 0.22, 2),
            volume=5, time=_s.tick_time,
        )


def symbols_get(pattern: str = ""):
    return (SimpleNamespace(name="XAUUSD"),)


def symbol_select(symbol: str, enable: bool = True) -> bool:
    return True


def positions_get(symbol: str | None = None, ticket: int | None = None):
    with _lock:
        _s.calls["positions_get"] += 1
        vals = list(_s.positions.values())
        if ticket is not None:
            vals = [p for p in vals if p.ticket == ticket]
        return tuple(vals)


def orders_get(symbol: str | None = None):
    return ()


def _seed_deals():
    """A deterministic book of today's closed trades for the /api/history endpoint test.

    Five positions, each an IN leg + an OUT leg, paired by position_id:
      wins:   +48.00 (1001), +9.00 (1004)         -> gross_profit +57.00
      losses: -22.00 (1002), -52.00 (1005)        -> gross_loss   -74.00
      flat:     0.00 (1003)
    So net = -17.00, wins=2, losses=2, flat=1, closed_count=5, biggest_win=+48, biggest_loss=-52.
    `pnl` on an OUT leg = profit + swap + commission; win/loss is classified on `profit` alone.
    """
    base = int(time.time()) - 3600
    def deal(ticket, pid, entry, dtype, price, t, profit=0.0, swap=0.0, comm=0.0):
        return SimpleNamespace(
            ticket=ticket, order=ticket, position_id=pid, entry=entry, type=dtype,
            price=price, time=t, profit=profit, swap=swap, commission=comm,
            symbol="XAUUSD", volume=0.10, magic=0, comment="",
        )
    d = []
    # 1001 BUY win +48 (profit 50, commission -2)
    d += [deal(1, 1001, DEAL_ENTRY_IN, DEAL_TYPE_BUY, 2400.00, base + 10),
          deal(2, 1001, DEAL_ENTRY_OUT, DEAL_TYPE_SELL, 2405.00, base + 70, 50.0, 0.0, -2.0)]
    # 1002 SELL loss -22 (profit -20, commission -2)
    d += [deal(3, 1002, DEAL_ENTRY_IN, DEAL_TYPE_SELL, 2410.00, base + 120),
          deal(4, 1002, DEAL_ENTRY_OUT, DEAL_TYPE_BUY, 2412.00, base + 180, -20.0, 0.0, -2.0)]
    # 1003 BUY flat 0
    d += [deal(5, 1003, DEAL_ENTRY_IN, DEAL_TYPE_BUY, 2400.00, base + 200),
          deal(6, 1003, DEAL_ENTRY_OUT, DEAL_TYPE_SELL, 2400.00, base + 260, 0.0, 0.0, 0.0)]
    # 1004 BUY win +9 (profit 10, commission -1)
    d += [deal(7, 1004, DEAL_ENTRY_IN, DEAL_TYPE_BUY, 2400.00, base + 300),
          deal(8, 1004, DEAL_ENTRY_OUT, DEAL_TYPE_SELL, 2401.00, base + 360, 10.0, 0.0, -1.0)]
    # 1005 SELL loss -52 (profit -50, commission -2)
    d += [deal(9, 1005, DEAL_ENTRY_IN, DEAL_TYPE_SELL, 2415.00, base + 400),
          deal(10, 1005, DEAL_ENTRY_OUT, DEAL_TYPE_BUY, 2420.00, base + 460, -50.0, 0.0, -2.0)]
    return tuple(d)


_SEED_DEALS = _seed_deals()


def history_deals_get(*args, **kwargs):
    return _SEED_DEALS


def copy_rates_from_pos(symbol: str, timeframe: int, start: int, count: int):
    return None     # only used by the strategy, which is disabled by default


def order_send(request: dict):
    """Fill a market order, or close a position when `position` is present."""
    with _lock:
        _s.calls["order_send"] += 1

        if _s.fail_order_send:
            # A well-formed answer that says "no". This is what a requote storm looks like,
            # and it is the path that makes /close_where return HTTP 200 with ok:false --
            # the exact shape the Android B1 fix depends on.
            return SimpleNamespace(
                retcode=TRADE_RETCODE_REQUOTE, order=0, deal=0,
                price=0.0, volume=0.0, comment="stub: forced failure",
            )

        pos_ticket = request.get("position")
        if pos_ticket is not None:
            _s.positions.pop(int(pos_ticket), None)      # closed
            return SimpleNamespace(
                retcode=TRADE_RETCODE_DONE, order=int(pos_ticket), deal=int(pos_ticket),
                price=_s.bid, volume=float(request.get("volume") or 0.0),
                comment="stub: closed",
            )

        # A new market order.
        t = _s.next_ticket
        _s.next_ticket += 1
        is_buy = request.get("type") == ORDER_TYPE_BUY
        _s.positions[t] = SimpleNamespace(
            ticket=t,
            type=POSITION_TYPE_BUY if is_buy else POSITION_TYPE_SELL,
            volume=float(request.get("volume") or 0.01),
            price_open=_s.bid,
            sl=0.0, tp=0.0, profit=0.0,
            time=int(time.time()),
            magic=int(request.get("magic") or 0),
        )
        return SimpleNamespace(
            retcode=TRADE_RETCODE_DONE, order=t, deal=t,
            price=_s.bid, volume=float(request.get("volume") or 0.01),
            comment="stub: filled",
        )
