# MT5 Algos

Source code for MetaTrader 5 Expert Advisors (EAs), version-controlled here.

## ⚠️ Golden rule

**Edit code only in this folder (`mt5\Experts\<Strategy>\`).**
**Never edit files directly in the MT5 terminal Experts folder** — that folder is a
*deploy target only*. It is hidden under `AppData`, is not in git, and gets overwritten on
every deploy. This local repo is the single source of truth.

## Workflow

```
edit here  ──>  deploy.ps1  ──>  MT5 compiles  ──>  attach to chart  ──>  test on DEMO
(git)           (copy + compile)                    (Algo Trading on)
```

1. Edit/develop the strategy under `mt5\Experts\<Strategy>\`.
2. Run the deploy script (copies into the terminal + compiles):
   ```powershell
   cd D:\llm\ios\mt5plus\mt5
   .\deploy.ps1                          # deploy + compile ALL strategies
   .\deploy.ps1 -Strategy XauTickAccumulator   # just one
   .\deploy.ps1 -Strategy GoldScalperMulti -NoCompile  # copy only
   ```
3. In MT5: turn on the **Algo Trading** toolbar button (green), then drag/re-attach the EA
   onto its chart (tick **Allow Algo Trading**). Confirm the 🙂 in the chart's top-right.
4. **Test on the DEMO account first.** Watch the **Experts** / **Journal** / **Trade** tabs.
5. Commit your changes (`git add . && git commit -m "..."`).

> If you change an `.mqh` include, re-run `deploy.ps1` — MT5 only sees what's in its own
> Experts folder.

## Folder structure

Each strategy is **self-contained in its own folder**. The contents of each
`Experts\<Strategy>\` folder are exactly what gets dropped into MT5's `MQL5\Experts\` root.

```
mt5\
  README.md
  deploy.ps1
  Experts\
    GoldBreakoutGrid\
      GoldBreakoutGrid.mq5          # main EA
      GoldBreakoutGrid.ex5          # compiled (rebuilt on deploy)
      GoldBreakoutGrid\*.mqh        # 8 include modules
    GoldScalperMulti\
      GoldScalperMulti.mq5
      GoldScalperMulti.ex5
      GoldScalperMulti\*.mqh        # 9 include modules
    XauTickAccumulator\
      XauTickAccumulator.mq5        # single file, no custom includes
      XauTickAccumulator.ex5
    LatencyBench\
      LatencyBench.mq5
    XauusdORBShooter\
      XauusdORBShooter.mq5
```

**Why the nested same-name folder** (e.g. `GoldBreakoutGrid\GoldBreakoutGrid\`)?
The EAs use quote-relative includes like `#include "GoldBreakoutGrid/Defines.mqh"`, which
MT5 resolves next to the `.mq5`. Keeping that nested layout means the includes resolve both
locally and after deploy, with no source edits.

## Strategies

| Strategy | Symbol | What it does |
|---|---|---|
| **XauTickAccumulator** | XAUUSDm | On each new M1 candle, buys 1 order/sec while price is above the candle open, up to 10 positions, then closes all. SL = entry − $0.50 (must exceed spread), TP = entry + $1.00. |
| **GoldBreakoutGrid** | XAUUSDm | Asian-range breakout, triple-EMA (H4) trend + EMA-fan-spread filter, ATR-based TP, per-position + global loss caps, Friday flat. (v4.20) |
| **GoldScalperMulti** | XAUUSDm | Multi-strategy scalper: VPIN flow toxicity + VWAP bands + session breakout, with per-strategy toggles and $5–$10 TP scalping. |
| **XauusdORBShooter** | XAUUSD | Low-latency long-only ORB scalper; tick-driven breakout of prior candle high via OrderSendAsync, stacked positions, daily loss halt. (VPS-oriented) |
| **LatencyBench** | any | Diagnostic, not a trading EA. Measures tick inter-arrival / order round-trip latency to validate VPS co-location before deploying real EAs. |

## Deploy target & toolchain

- Terminal Experts folder (deploy target):
  `C:\Users\Pandu\AppData\Roaming\MetaQuotes\Terminal\53785E099C927DB68A545C249CDBCE06\MQL5\Experts`
- Compiler: `C:\Program Files\MetaTrader 5 EXNESS\metaeditor64.exe`

If either path changes, update the two variables at the top of `deploy.ps1`.

## Git notes

- This tree is tracked from the repo root `D:\llm\ios\mt5plus`.
- `config.js` (web app API key) is git-ignored; `.ex5`/`.log` are tracked intentionally.
- Always commit source changes here — the MT5 copy is disposable.
