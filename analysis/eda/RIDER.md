# Vol-Regime Trend-Rider — algorithm, verification, limits

The strategy that packages the verified findings (see `INVESTIGATION_JOURNEY.md`) into one
object. **Research/paper stage** — a *marginal, regime-dependent* lead, not a proven money-maker.
It is being wired into the XauOrderPad strategy framework as a **tap-to-confirm** engine (it
surfaces a live trade card; the human places each order — Claude never auto-fires).

## The algorithm (`rider_state.py`)

`RiderState` is pure decision logic (no MT5/IO/clock), mirroring `strategies/ladder.py:LadderState`,
so the exact code that is backtested is the code that runs live.

| Stage | Rule | Why |
|---|---|---|
| **Regime gate** | act only when `ATR > rolling-median ATR` (optionally also NY hours 13–16 UTC) | volatility is the only predictable structure; don't trade dead tapes |
| **Entry** | M5 **thrust-follow**: bar with `\|body\| > 1.5×ATR` → trade the candle's direction | the faint momentum tilt that survived the sealed holdout |
| **Exit** | **trailing stop**: `sl=$6`, `trail=$6`, far cap `tp=$50`, time-stop 24 bars | rides gold's fat-tail winners — the real edge |
| **Sizing** | fixed-fractional (Kelly-small): risk `1%` of equity on the stop; **never pyramid into size** | the sizing math shows big-lot pyramiding is luck/ruin |

Honest by construction: entries fill at the **next bar's open** (never the signal wick); one full
spread per round trip; one position at a time.

## Verified numbers (`rider.py`, via the cent-verified `patternlib` engine, M5)

```
config                         n    win%   exp$/oz   ctrl    edge     PF
high-vol gate (ATR>median)   544   39.7%   +0.971   +0.386  +0.585   1.36
high-vol + NY hours          156   39.1%   +1.272   +0.384  +0.888   1.45
```
`edge` = expectancy minus a random-direction control (isolates entry skill from the trailing-exit
harvest). Positive **every month** Jan–Jun. Per lot: +$0.585/oz ≈ **+$58/lot** edge over random.

**Cross-verification:** the live `RiderState.on_bar` machine, driven over the same history,
reproduces the backtest to **544 trades / +$529.7 vs +$528.1** (diff $1.7 = the spread offset). The
first attempt drifted 2× — the cross-check caught it (the machine was skipping the fill-bar stop) and
it was fixed. The live logic == the validated logic.

## Limits — read before trusting it

- **The edge is marginal.** Its bootstrap CI includes zero (~92% one-sided). This is a *lead*.
- **Regime-dependent** (`rider_stress.py`): as the tape quiets 4×, expectancy falls +$0.97→+$0.39/oz.
  It stays *marginally positive* here (fixed $6 stop + tiny spread bound the downside), but the test
  compresses move *size* only — a **choppy, high-reversal** month is still untested and could be worse.
- **The sealed holdout is spent.** The only real confirmation left is a **forward paper-test on fresh
  data** — which is exactly what the tap-to-confirm live engine provides (log each card vs outcome).

## Run

```bat
set PY=C:\Users\Pandu\.conda\envs\test_env\python.exe
%PY% test_rider.py       :: offline unit tests (no MT5)
%PY% rider.py            :: backtest + cross-verify + monthly + sizing
%PY% rider_stress.py     :: quiet-regime stress test
```

## Files
`rider_state.py` (pure logic) · `rider.py` (backtest+cross-verify) · `rider_stress.py` (stress) ·
`test_rider.py` (unit tests) · reuses `patternlib.py` (verified engine). The live server engine
(Phase 1) is `XauOrderPad/strategies/rider.py`, which wraps this same `RiderState` and surfaces its
suggestions — **containing no order-placing call**.
