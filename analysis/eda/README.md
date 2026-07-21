# `analysis/eda/` — XAUUSD exploratory data analysis

Deep EDA to find tradeable structure in XAUUSD, after tick scalping failed on cost.
**Read [`EDA_FINDINGS.md`](EDA_FINDINGS.md) for the results.** Research only — no
execution, no orders.

## Environment (documented for future use)

Two environments, on purpose — data-fetch needs MT5, analysis needs pandas:

| Step | Interpreter | Why |
|---|---|---|
| **Fetch data** (`fetch_data.py`) | `..\..\XauOrderPad\.venv\Scripts\python.exe` | has **MetaTrader5** + numpy |
| **All EDA** (`eda*.py`) | **conda `test_env`** → `C:\Users\Pandu\.conda\envs\test_env\python.exe` | has **pandas + numpy + plotly** |

**`test_env` setup that was done here (one-time):**
```
# pandas installed; numpy pinned <2 so the env's numpy-1.x-compiled packages
# (scipy / matplotlib / pyarrow / numexpr) stop crashing with "_ARRAY_API not found"
C:\Users\Pandu\.conda\envs\test_env\python.exe -m pip install "pandas>=2.0" "numpy<2"
```
Result: numpy 1.26.4, pandas 2.3.3, plotly 5.13.1. Use `test_env` for all future
EDA/analysis in this folder.

## Run order

```bat
:: 1. fetch (STOP the XauOrderPad server first — one MT5 connection per terminal)
..\..\XauOrderPad\.venv\Scripts\python.exe fetch_data.py

:: 2. analysis (any order; each reads analysis/eda/data/*.npz — no MT5 needed)
set PY=C:\Users\Pandu\.conda\envs\test_env\python.exe
%PY% eda1_audit_noise.py        :: noise / random-walk structure
%PY% eda2_bigmoves_volume.py    :: big moves, clustering, volume<->price
%PY% eda3_levels.py             :: pivots, S/R, volume profile, round numbers
%PY% eda4_synthesis.py          :: fade-S/R tradeability screen (in-sample)
%PY% eda_dashboard.py           :: -> eda_dashboard.html (price + S/R + volume)
```

## Files

| File | Role |
|---|---|
| `fetch_data.py` | pull M1..D1 bars + tick sample from MT5 → `data/*.npz` |
| `eda_lib.py` | shared loaders (`load_bars`/`load_ticks`) + stats (autocorr, variance ratio, kurtosis) |
| `eda1_audit_noise.py` | coverage, return distributions, autocorrelation, variance ratio |
| `eda2_bigmoves_volume.py` | big-move frequency, volatility clustering, volume↔price, spike→forward |
| `eda3_levels.py` | pivot / round-number / swing / volume-profile reaction rates |
| `eda4_synthesis.py` | fade-S/R vs break, ± volume filter, net of spread (in-sample screen) |
| `eda_dashboard.py` | Plotly HTML: price + strong S/R + MAs + volume + big-move markers |
| `data/` | saved `.npz` (gitignore-worthy; regenerate with `fetch_data.py`) |

## Headline result

Direction from price alone = random walk. But **volatility is predictable** and
**price reacts at S/R levels ~57–60%**. Fading S/R on M15 looks promising
**in-sample** (PF 1.65) and — unlike tick scalping — the 40-pt spread is negligible
on this timeframe. **Next step: walk-forward it with point-in-time levels** before
believing anything. See `EDA_FINDINGS.md`.
