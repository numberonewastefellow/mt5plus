//+------------------------------------------------------------------+
//| Utils.mqh - lot tiers, basket queries, and the exposure warning   |
//+------------------------------------------------------------------+
#property strict
#include "Defines.mqh"

//+------------------------------------------------------------------+
//| Lot by balance tier.                                             |
//|                                                                  |
//| Read off the operator's own session: 0.01 up to ~$23, 0.04 to     |
//| ~$46, then 0.07. Extended upward along the ladder the video       |
//| showed - 0.01, 0.04, 0.07, 0.09, 0.10, 0.33, 0.99, 1.99, 3.99,    |
//| 6.88 - which is exactly 10 sizes and no others.                   |
//|                                                                  |
//| The lot is FIXED when a cycle opens. Escalation within a cycle is |
//| in the NUMBER of positions; only a new cycle re-reads the tier.   |
//+------------------------------------------------------------------+
double RGS_TierLot(const double balance)
  {
   if(balance <  23.0) return 0.01;
   if(balance <  46.0) return 0.04;
   if(balance < 130.0) return 0.07;
   if(balance < 310.0) return 0.09;
   if(balance < 600.0) return 0.10;
   if(balance < 1500.0) return 0.33;
   if(balance < 3400.0) return 0.99;
   if(balance < 13000.0) return 1.99;
   if(balance < 27000.0) return 3.99;
   return 6.88;
  }

//+------------------------------------------------------------------+
//| HOW MANY POSITIONS, AND HOW MANY AT ONCE - both from the balance. |
//|                                                                  |
//| Ported from analysis/video_ocr/lot_position_engine.py so the EA   |
//| and the replay cannot diverge. Do not re-derive these here.       |
//|                                                                  |
//| For n positions of lot L at rungs d apart with g on each rung,    |
//| the distances from the pricing point form an arithmetic series:   |
//|                                                                  |
//|     drawdown(r rungs) = L * 100 * d * g * r(r+1)/2               |
//|                                                                  |
//| Invert it against a drawdown budget and the cap is a DECISION     |
//| rather than a guess: state what drawdown is acceptable, and the   |
//| depth follows. `InitialBatch = 3` used to be a constant here, and |
//| a $1 account therefore opened three positions - 3x the exposure   |
//| the operator's own run carried at that balance.                  |
//|                                                                  |
//| THE CALIBRATION CONSTANTS BELOW ARE NOT THE ENGINE'S LIVE ADD     |
//| STEP. RGS_RISK_STEP is 0.18, the value the budget was fitted with |
//| in Python; the engine actually adds every AddStepUSD = 0.05, the  |
//| verified rung gap ($0.000/0.037/0.053/0.070 across four watched   |
//| cycles). They are not interchangeable: `per_rung` and `add_step`  |
//| are confounded in the drawdown data, which constrains only their  |
//| PRODUCT, so the fit cannot be re-expressed at a different step    |
//| without re-fitting. Using the 0.18 calibration with a 0.05 live   |
//| step makes the cap CONSERVATIVE - it allows fewer positions than  |
//| the budget strictly permits - which is the safe direction and the |
//| reason it is left alone.                                         |
//|                                                                  |
//| *** THAT LAST PARAGRAPH IS TRUE FOR **GRID** ONLY. ***            |
//| Corrected 2026-09-08, after the account was wiped in 2m10s.       |
//| The staircase model assumes each new rung is DEEPER UNDERWATER    |
//| than the last, so a full-depth basket loses the sum of a          |
//| staircase. Two things break that live:                            |
//|   1. Rungs are not 0.18 apart. In a fast move the 2s cooldown -   |
//|      not AddStepUSD - sets the spacing; five batches landed        |
//|      ~1 $/oz apart inside a $2 band in 10 seconds.                |
//|   2. TREND adds on FAVOURABLE moves. There is then no staircase   |
//|      at all: every ounce is underwater together on a reversal.    |
//| Measured: with the lot cap removed, the 44% budget was breached   |
//| at 68.6% and 82.3% of balance on the same afternoon.              |
//|                                                                  |
//| The budget is therefore NO LONGER the risk control. It is kept    |
//| for parity with lot_position_engine.py, but every caller takes    |
//| min(staircase, EXPOSURE CAP) - see RGS_MaxOuncesFor below, which  |
//| is mode-independent because it bounds OUNCES HELD, and ounces do  |
//| not care how they were acquired.                                  |
//+------------------------------------------------------------------+
#define RGS_RISK_STEP   0.18      // calibration step for the budget, NOT the live add step
#define RGS_RISK_PCT    44.0      // observed median drawdown, % of the balance a cycle opened with

//--- Positions opened SIMULTANEOUSLY at one price.
//--- Observed (cycles.csv max_positions_visible), and cycles 1-3 never added so their
//--- count IS the batch:  1 @ $0.30-0.99, 2 @ $1.14, 3 @ $1.47-2.00, then 3-4 upward.
//--- It saturates rather than growing forever: above ~$14 the LOT grows instead, which is
//--- why the operator was still opening groups of 4-5 at $23 and at $1,181.
int RGS_GroupLadder(const double balance)
  {
   if(balance < 1.10) return 1;
   if(balance < 1.40) return 2;
   if(balance < 2.50) return 3;
   return 3;
  }

//+------------------------------------------------------------------+
//| THE EXPOSURE CAP - the risk control, as of 2026-09-08.            |
//|                                                                  |
//| PURPOSE, in one sentence: hold few enough ounces that gold has to |
//| move `ruin_move` dollars against the whole basket before the      |
//| account is gone.                                                  |
//|                                                                  |
//|     max_ounces = balance / ruin_move                              |
//|                                                                  |
//| So `ruin_move` is the distance to RUIN, not a distance you        |
//| survive: at exactly that move the balance is consumed. LARGER     |
//| VALUE = SMALLER POSITIONS = SAFER. Safety comes from setting it   |
//| far outside normal excursions - observed adverse excursions on    |
//| 2026-09-08 were 0.45-1.97 $/oz, so at the 5.00 default the worst  |
//| of them costs 39% of balance and the move that actually wiped the |
//| account (1.234 $/oz) costs 24.7% instead of 94%.                  |
//|                                                                  |
//| WHY THIS AND NOT A LOT COUNT. MaxTotalLots is a fixed number of   |
//| lots, so it means a different risk at every balance and every lot |
//| tier; it was the only thing holding exposure down, and raising it |
//| 1.00 -> 50.00 wiped the account in 2m10s. This scales with the    |
//| balance and cannot be outgrown.                                   |
//|                                                                  |
//| IT ALSO SETS A MINIMUM VIABLE BALANCE. The broker's smallest lot  |
//| is 0.01 = 1 oz, so trading needs balance >= ruin_move. At the 5.00 |
//| default a $1 account is REFUSED - and that refusal is honest,     |
//| because 1 oz on $1 has a ruin move of $1 and no setting can       |
//| change that. To run a $1 account anyway, set RuinMoveUSD = 1.0    |
//| and understand that a $1 move ends it.                            |
//|                                                                  |
//| Mode-independent ON PURPOSE: it bounds ounces held, and ounces do |
//| not care whether GRID or TREND acquired them.                     |
//+------------------------------------------------------------------+
double RGS_MaxOuncesFor(const double balance,const double ruin_move)
  {
   if(balance <= 0.0 || ruin_move <= 0.0) return 0.0;
   return balance/ruin_move;
  }

//--- Deepest grid whose full-depth drawdown stays inside RGS_RISK_PCT of balance.
//--- Priced in whole BATCHES, so the answer is always a multiple of the batch size and
//--- can never come back smaller than one batch when the budget affords a rung at all.
//---
//--- `ruin_move > 0` additionally applies the exposure cap above and returns the SMALLER of
//--- the two. The staircase half is kept only for parity with lot_position_engine.py; the
//--- exposure cap is what actually binds. `ruin_move = 0` disables it (old behaviour).
int RGS_MaxPositionsFor(const double balance,const double lot,const double ruin_move=0.0)
  {
   if(balance <= 0.0 || lot <= 0.0) return 0;
   int    g      = RGS_GroupLadder(balance);
   double budget = balance*RGS_RISK_PCT/100.0;
   double per    = lot*RGS_OZ_PER_LOT*RGS_RISK_STEP*g;
   if(per <= 0.0) return 0;
   double k = budget/per;
   int    r = (int)MathFloor((-1.0+MathSqrt(1.0+8.0*k))/2.0);   // deepest affordable RUNG
   if(r < 0) r = 0;
   int cap = r*g;

   if(ruin_move > 0.0)
     {
      double max_oz  = RGS_MaxOuncesFor(balance,ruin_move);
      int    exp_cap = (int)MathFloor(max_oz/(lot*RGS_OZ_PER_LOT)+1e-9);
      if(exp_cap < cap) cap = exp_cap;
      if(cap < 0) cap = 0;
     }
   return cap;
  }

//--- The batch to open, never larger than the depth the budget can hold.
int RGS_GroupFor(const double balance,const double lot,const double ruin_move=0.0)
  {
   int g   = RGS_GroupLadder(balance);
   int cap = RGS_MaxPositionsFor(balance,lot,ruin_move);
   if(cap <= 0) return g;          // caller decides whether to refuse; it needs the real cap
   return (int)MathMax(1,MathMin(g,cap));
  }

//+------------------------------------------------------------------+
//| Clamp a lot to the broker's volume constraints. Refuses (0) below |
//| the minimum rather than silently rounding UP into a bigger        |
//| position than intended.                                          |
//+------------------------------------------------------------------+
double RGS_NormalizeLot(const string sym,const double want)
  {
   double vmin =SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double vmax =SymbolInfoDouble(sym,SYMBOL_VOLUME_MAX);
   double vstep=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   if(vstep<=0.0) vstep=0.01;
   double v=MathFloor(want/vstep+1e-9)*vstep;
   v=NormalizeDouble(v,2);
   if(v<vmin-1e-9) return 0.0;      // refuse, do not round up
   if(v>vmax)      v=vmax;
   return v;
  }

//+------------------------------------------------------------------+
//| Basket queries - always filtered on our magic and symbol so the   |
//| EA can never touch a position it did not open.                    |
//+------------------------------------------------------------------+
int RGS_CountOpen(const string sym,double &total_lots,double &net_pnl)
  {
   int n=0; total_lots=0.0; net_pnl=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=RGS_MAGIC) continue;
      if(PositionGetString(POSITION_SYMBOL)!=sym) continue;
      n++;
      total_lots += PositionGetDouble(POSITION_VOLUME);
      net_pnl    += PositionGetDouble(POSITION_PROFIT)
                  + PositionGetDouble(POSITION_SWAP);
     }
   return n;
  }

//+------------------------------------------------------------------+
//| WHAT THE BROKER ACTUALLY BOOKED for this cycle.                  |
//|                                                                  |
//| Sums profit + swap + commission over every deal this EA made      |
//| since `from`. Only one cycle runs at a time and the magic is ours |
//| alone, so a time window scopes it soundly.                        |
//|                                                                  |
//| This exists because `balance_after - balance_before` is wrong in  |
//| the case that matters most: a STOP-OUT books its loss BEFORE the  |
//| EA notices the basket is gone, so that difference reads ~0 for    |
//| exactly the cycles that cost the most.                            |
//|                                                                  |
//| It is also the only place commission appears. POSITION_PROFIT     |
//| excludes it, so the EA's live `net` is always better than the     |
//| account's by the commission paid - measured 0.11 per 0.01 lot on  |
//| this account, which is 38% of a $1 cycle's whole target.          |
//+------------------------------------------------------------------+
double RGS_RealisedSince(const string sym,const datetime from,
                         double &commission,int &closed_deals)
  {
   double total=0.0; commission=0.0; closed_deals=0;
   if(!HistorySelect(from-2,TimeCurrent()+2)) return 0.0;
   int n=HistoryDealsTotal();
   for(int i=0;i<n;i++)
     {
      ulong tk=HistoryDealGetTicket(i);
      if(tk==0) continue;
      if(HistoryDealGetInteger(tk,DEAL_MAGIC)!=RGS_MAGIC) continue;
      if(HistoryDealGetString(tk,DEAL_SYMBOL)!=sym)       continue;
      // The SELECT above is deliberately widened by 2 s so a deal on the boundary second is not
      // missed - but each deal must still be filtered EXACTLY, or the slack sweeps in the
      // previous cycle's closing deals. Cycle 9 opened 1 s after cycle 8 closed and reported
      // net_broker +87.34 against a balance move of +41.37: the +45.97 difference was cycle 8's
      // closes, counted twice across the two rows.
      if((datetime)HistoryDealGetInteger(tk,DEAL_TIME) < from) continue;
      double c=HistoryDealGetDouble(tk,DEAL_COMMISSION);
      total      += HistoryDealGetDouble(tk,DEAL_PROFIT)
                  + HistoryDealGetDouble(tk,DEAL_SWAP) + c;
      commission += c;
      if(HistoryDealGetInteger(tk,DEAL_ENTRY)==DEAL_ENTRY_OUT) closed_deals++;
     }
   return total;
  }

//+------------------------------------------------------------------+
//| THE EXPOSURE WARNING.                                            |
//|                                                                  |
//| Replay run 6 (2026-09-02) measured that a $1 account trading the  |
//| 0.01 minimum on gold carries ~234% of the account in per-minute   |
//| price swings, and died on cycle 1-3 in all nine configurations    |
//| tested. Below 0.01 lot there is NO smaller position available, so |
//| this is arithmetic, not strategy - no parameter reaches it.       |
//|                                                                  |
//| This does not block anything. It prints the calculation so a $1   |
//| run cannot look like a normal one.                                |
//+------------------------------------------------------------------+
void RGS_WarnExposure(const string sym,const double balance)
  {
   double vmin =SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double csize=SymbolInfoDouble(sym,SYMBOL_TRADE_CONTRACT_SIZE);
   if(csize<=0.0) csize=RGS_OZ_PER_LOT;
   double oz   =vmin*csize;                  // ounces in one minimum position
   // A representative per-minute range for XAUUSD, measured over 2026-09-02
   // (247k ticks): median $2.34/min. Used only to size the warning.
   double permin=2.34;
   double pct   =(balance>0.0 ? 100.0*oz*permin/balance : 0.0);
   PrintFormat("RGS EXPOSURE: min lot %.2f = %.0f oz. At $%.2f balance one position swings "
               "~$%.2f/min = %.0f%% of the account PER MINUTE.",
               vmin,oz,balance,oz*permin,pct);
   if(pct>=50.0)
      Print("RGS *** WARNING: measured replay (run 6) shows an account this size dies within "
            "1-3 cycles on gold. The lot cannot go below the broker minimum, so no setting "
            "avoids this. Proceeding anyway - DEMO ONLY. ***");
  }
//+------------------------------------------------------------------+
