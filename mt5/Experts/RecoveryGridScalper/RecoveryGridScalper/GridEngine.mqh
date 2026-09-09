//+------------------------------------------------------------------+
//| GridEngine.mqh - the state machine. THE RULES LIVE HERE ONLY.    |
//|                                                                  |
//| IDLE --(operator clicks BUY/SELL)--> RUNNING                      |
//|                                                                  |
//|   each tick, net = sum(open P&L):                                 |
//|     net > 0 AND net >= target ..... CLOSE ALL --> IDLE            |
//|     net > 0 AND net <= peak - gb ... CLOSE ALL --> IDLE           |
//|     net <  0 AND adverse >= step                                  |
//|            AND elapsed >= cooldown                                |
//|            AND free margin >= floor                               |
//|            AND positions  <  depth cap ... ADD a batch            |
//|     otherwise ..................... hold                          |
//|                                                                  |
//| EVERYTHING THAT SIZES OR EXITS IS A FUNCTION OF BALANCE.          |
//| `target` and `gb` are percentages of the balance the cycle opened |
//| with; the batch and the depth cap come from RGS_GroupFor /        |
//| RGS_MaxPositionsFor. Nothing here is a fixed dollar amount, a     |
//| fixed price distance or a fixed trade count - that is what makes  |
//| the same rule fit a $1.47 account and a $1,181 one.               |
//|                                                                  |
//| Three things are deliberate and must not be "simplified":         |
//|                                                                  |
//| 1. THERE IS NO STOP LOSS. A losing basket is held and added to,   |
//|    never cut. That is the strategy as observed - 44 of 45 video   |
//|    cycles went underwater and 86% of those still closed green.    |
//|    It is also why this is demo-only: the tail is an account.      |
//|                                                                  |
//| 2. THE ADD IS THROTTLED BY BOTH PRICE AND TIME. The draft spec    |
//|    said "add immediately while in loss"; on a ~5 tick/s feed that |
//|    opens hundreds of positions in seconds. The operator's own     |
//|    cycles show rung gaps of $0.037/$0.053/$0.070 with batches     |
//|    seconds apart, which is what the two throttles reproduce.      |
//|                                                                  |
//| 3. THE TARGET IS FIXED IN DOLLARS AT CYCLE OPEN. It was once      |
//|    `TargetMove x (lots / 0.01)` - proportional to open volume,    |
//|    which reduces to "bid >= avg_entry + $0.30/oz" at EVERY depth. |
//|    Adding positions then buys ZERO progress toward the exit: the  |
//|    average falls and the target falls with it, by exactly the     |
//|    same amount. A live $1 cycle dipped, recovered and hung. Held  |
//|    fixed instead, the same dollars arrive on a smaller move as    |
//|    the basket grows - $0.289/oz on one 0.01 lot, $0.096/oz across |
//|    three - which is the operator's "normally 0.20-0.30, about     |
//|    0.10 once it has been in loss and come back", as ONE rule.     |
//+------------------------------------------------------------------+
#property strict
#include "Defines.mqh"
#include "Structs.mqh"
#include "Utils.mqh"
#include "TradeExec.mqh"
#include "Logger.mqh"

class CRgsEngine
  {
private:
   string            m_sym;
   CRgsExec         *m_exec;
   CRgsLogger       *m_log;
   SCycle            m_cyc;
   ENUM_RGS_STATE    m_state;
   int               m_next_id;
   double            m_day_realised;   // for the daily loss kill
   datetime          m_day;

   //--- inputs, copied in at Init
   double            m_target_pct, m_giveback_pct;
   //--- the time-decayed $/oz floor, and which way the grid adds
   bool              m_quick_on, m_quick_decay;
   double            m_quick_start, m_quick_floor, m_quick_arm_pct;
   //--- THE EXPOSURE CAP, in $/oz. Ounces are capped at balance/m_ruin_move, so gold must move
   //--- this far against the whole basket to consume the account. Larger = smaller = safer.
   //--- 0 disables it. See RGS_MaxOuncesFor() in Utils.mqh for the full rationale.
   double            m_ruin_move;
   //--- The LAST finished cycle, kept so the panel can still show it. Survives until the next
   //--- cycle closes; `m_prev_valid` is false until the first one does.
   SCycle            m_prev;
   double            m_prev_bal_after;
   string            m_prev_reason;
   bool              m_prev_valid;
   int               m_quick_secs;
   ENUM_RGS_ADD_MODE m_add_mode;
   double            m_add_step, m_min_free, m_max_lots, m_kill_pct;
   double            m_day_start_bal;  // balance the trading day opened with
   int               m_cooldown;
   bool              m_auto_restart;

   //--- REAL margin cost per 1.00 lot, in account currency. 0.0 = not yet known.
   //---
   //--- ACCOUNT_LEVERAGE and OrderCalcMargin() cannot be trusted on every account: on one tested
   //--- account both reported a lot as costing ~$0 margin when the broker's own server actually
   //--- charged ~$22 for a 0.01 lot fill (measured via ACCOUNT_MARGIN_FREE before/after). Sizing
   //--- decisions here are therefore NOT based on the leverage field or OrderCalcMargin() at all -
   //--- this is learned instead from the account's own real fills (see OpenBatch), or set directly
   //--- via the RealMarginPerLotUSD input as a manual override/escape hatch.
   double            m_margin_per_lot;

   //--- THE DAILY LOSS KILL, made actually daily and actually scale-free.
   //---
   //--- It used to be a FIXED DOLLAR amount (`DailyLossKill = 0.50`) while every other constant
   //--- in this engine is a fraction of balance. On a $98 account that halted trading after
   //--- losing 0.5% of it. Worse, `m_day` was declared and never used, so nothing ever reset:
   //--- "daily" was a misnomer and the only way out was the RESUME button.
   //---
   //--- And commission alone could trip it. `m_day_realised` accumulates `net_broker`, which
   //--- includes commission (0.11 per 0.01-lot entry here), so at 3 entries a cycle TWO
   //--- break-even cycles spent 0.66 and tripped a 0.50 kill on their own.
   void              RollDay()
     {
      datetime now=TimeCurrent();
      datetime today=now-(now%86400);
      if(m_day!=today)
        {
         m_day=today;
         m_day_realised=0.0;
         m_day_start_bal=AccountInfoDouble(ACCOUNT_BALANCE);
         if(m_state==RGS_HALTED && m_kill_pct>0.0) m_state=RGS_IDLE;   // a NEW day clears it
        }
     }

   //--- 0 (or a zero opening balance) means the kill is OFF entirely.
   double            KillLevel() const
     {
      if(m_kill_pct<=0.0) return 0.0;
      return m_kill_pct/100.0*m_day_start_bal;
     }

   //--- Record the basket's extremes. Called from the tick AND from inside OpenBatch,
   //--- because the deepest moment of a cycle is usually the instant right after an add:
   //--- OnTick sampled only at the TOP of the tick, so a batch that fired later in the same
   //--- tick moved the basket without the watermark ever seeing it. Cycle 51 logged
   //--- worst_pnl -2.37 / worst_equity 9.47 while the trade log showed -2.52 / 8.99 at the
   //--- last fill. Understating drawdown is the one direction a risk log must never fail in.
   void              MarkExtremes(const double net)
     {
      if(net>m_cyc.best_pnl)  m_cyc.best_pnl=net;
      if(net<m_cyc.worst_pnl) m_cyc.worst_pnl=net;
      double eq=AccountInfoDouble(ACCOUNT_EQUITY);
      if(eq<m_cyc.worst_equity) m_cyc.worst_equity=eq;
     }

   //--- Open one batch and log every fill individually.
   int               OpenBatch(const int count,const double lot,const bool first)
     {
      int opened=0;
      for(int k=0;k<count;k++)
        {
         double tl,np; RGS_CountOpen(m_sym,tl,np);
         if(tl+lot > m_max_lots+1e-9)
           {
            m_log.Note(m_cyc.id,StringFormat("MaxTotalLots %.2f reached, batch stopped at %d/%d",
                                             m_max_lots,k,count));
            break;
           }
         // REAL margin pre-check, calibrated from this account's own past fills - not from
         // ACCOUNT_LEVERAGE or OrderCalcMargin(), neither of which can be trusted here (see
         // m_margin_per_lot). Skipped until a real number exists: the first fill of a session
         // still goes through untested below, and calibrates FROM that attempt for every one
         // after it. Refusing here (rather than letting order_send do it) saves a round-trip
         // to a broker rejection we can already predict.
         if(m_margin_per_lot>0.0)
           {
            double need=m_margin_per_lot*lot;
            double free=AccountInfoDouble(ACCOUNT_MARGIN_FREE);
            if(need>free)
              {
               m_log.Note(m_cyc.id,StringFormat("REAL MARGIN BLOCKED: %.2f lot needs ~$%.2f "
                                                "(calibrated from live fills), only $%.2f free - "
                                                "batch stopped at %d/%d",lot,need,free,k,count));
               break;
              }
           }

         string cmt=StringFormat("RGS c%d b%d",m_cyc.id,m_cyc.batches+1);
         double margin_before=AccountInfoDouble(ACCOUNT_MARGIN_FREE);
         if(!m_exec.Open(m_cyc.is_buy,lot,cmt))
           {
            m_log.Trade(m_cyc.id,"REJECT",0,m_cyc.is_buy,lot,0.0,0.0,np,(int)0,tl,
                        m_exec.LastRetcode(),"open failed");
            break;
           }
         // Learn the account's real margin cost from every fill that actually happens, so
         // sizing tracks whatever the broker really enforces - including price moves - without
         // ever needing to know why the leverage field or OrderCalcMargin() were wrong.
         double margin_used=margin_before-AccountInfoDouble(ACCOUNT_MARGIN_FREE);
         if(margin_used>0.0) m_margin_per_lot=margin_used/lot;
         double px=m_exec.LastPrice();
         opened++;
         m_cyc.positions++;
         m_cyc.total_lots   += lot;
         m_cyc.sum_entry_lots += px*lot;
         if(first && m_cyc.first_entry==0.0) m_cyc.first_entry=px;
         int n2; double tl2,np2; n2=RGS_CountOpen(m_sym,tl2,np2);
         MarkExtremes(np2);                      // the post-add extreme, see MarkExtremes
         m_log.Trade(m_cyc.id,"OPEN",m_exec.LastTicket(),m_cyc.is_buy,lot,px,
                     0.0,np2,n2,tl2,m_exec.LastRetcode(),cmt);
        }
      if(opened>0)
        {
         m_cyc.batches++;
         m_cyc.t_last_add=TimeCurrent();
         m_cyc.last_add_price=(m_cyc.is_buy ? SymbolInfoDouble(m_sym,SYMBOL_ASK)
                                            : SymbolInfoDouble(m_sym,SYMBOL_BID));
        }
      return opened;
     }

   void              FinishCycle(const ENUM_RGS_CLOSE_REASON why)
     {
      double bal_before=AccountInfoDouble(ACCOUNT_BALANCE);
      int n; double tl,np; n=RGS_CountOpen(m_sym,tl,np);
      double exit_px=(m_cyc.is_buy ? SymbolInfoDouble(m_sym,SYMBOL_BID)
                                   : SymbolInfoDouble(m_sym,SYMBOL_ASK));
      int failed=0;
      // n==0 means the basket is already gone (stop-out / manual close): nothing to close.
      int closed=(n>0 ? m_exec.CloseAll(failed) : 0);
      m_cyc.t_close=TimeCurrent();

      double bal_after=AccountInfoDouble(ACCOUNT_BALANCE);
      // `realised` is the balance step across THIS CLOSE only. It is deliberately NOT
      // balance_after - balance_open: commission is charged at every OPEN, so those two differ
      // by exactly the cycle's commission. Both are logged; do not conflate them.
      double realised =bal_after-bal_before;

      // The broker's own arithmetic, read back from history. This is the only figure that is
      // right when someone else closed the basket - a stop-out books its loss BEFORE the EA
      // notices, so `realised` above reads ~0 for exactly the cycles that cost the most.
      double commission=0.0; int deals_out=0;
      double net_broker=RGS_RealisedSince(m_sym,m_cyc.t_open,commission,deals_out);
      m_day_realised += net_broker;

      string reason="target";
      if(why==RGS_CLOSE_GIVEBACK) reason="giveback";
      if(why==RGS_CLOSE_MANUAL)   reason="manual";
      if(why==RGS_CLOSE_KILL)     reason="daily_loss_kill";
      if(why==RGS_CLOSE_DEINIT)   reason="ea_removed";
      if(why==RGS_CLOSE_EXTERNAL) reason="external";
      if(why==RGS_CLOSE_QUICK)    reason="quick";
      if(failed>0) reason=reason+"_PARTIAL";

      m_log.Trade(m_cyc.id,"CLOSE",0,m_cyc.is_buy,tl,exit_px,0.0,np,closed,tl,
                  0,StringFormat("closed %d, failed %d, broker net %+.2f",closed,failed,net_broker));
      m_log.Cycle(m_cyc,exit_px,realised,bal_after,reason,commission,net_broker);

      if(failed>0)
         Print("RGS *** ",failed," position(s) FAILED TO CLOSE - check the Trade tab. ***");

      // KEEP THE FINISHED CYCLE. `m_cyc.Reset()` on the next line is what used to destroy it, so
      // the panel's peak/drawdown snapped to 0 the instant a cycle closed and the operator could
      // not review what had just happened without opening the CSV. Snapshot here, one line before.
      m_prev          = m_cyc;
      m_prev_bal_after= bal_after;
      m_prev_reason   = reason;
      m_prev_valid    = true;

      m_state=RGS_IDLE;
      m_cyc.Reset();

      if(KillLevel()>0.0 && m_day_realised <= -KillLevel())
        {
         m_state=RGS_HALTED;
         PrintFormat("RGS HALTED: day realised %.2f hit the kill at -%.2f "
                     "(%.1f%% of the day's opening balance %.2f). Set EnableDailyLossKill=false to "
                     "disable, or press RESUME to clear it.",
                     m_day_realised,KillLevel(),m_kill_pct,m_day_start_bal);
        }
     }

public:
   void              Init(const string sym,CRgsExec *ex,CRgsLogger *lg,
                          const double target_pct,const double giveback_pct,
                          const double add_step,const int cooldown,const double min_free,
                          const double max_lots,const double kill_pct,const bool auto_restart,
                          const bool quick_on,const bool quick_decay,const double quick_start,
                          const double quick_floor,const int quick_secs,const double quick_arm_pct,
                          const ENUM_RGS_ADD_MODE add_mode,const double ruin_move,
                          const double real_margin_per_lot=0.0)
     {
      m_sym=sym; m_exec=ex; m_log=lg;
      m_target_pct=target_pct; m_giveback_pct=giveback_pct;
      m_add_step=add_step; m_cooldown=cooldown; m_min_free=min_free;
      m_max_lots=max_lots; m_kill_pct=kill_pct; m_auto_restart=auto_restart;
      m_quick_on=quick_on; m_quick_decay=quick_decay; m_quick_start=quick_start;
      m_quick_floor=quick_floor; m_quick_secs=quick_secs; m_add_mode=add_mode;
      m_quick_arm_pct=quick_arm_pct; m_ruin_move=ruin_move;
      m_prev_valid=false; m_prev_bal_after=0.0; m_prev_reason=""; m_prev.Reset();
      m_margin_per_lot=(real_margin_per_lot>0.0 ? real_margin_per_lot : 0.0);
      m_state=RGS_IDLE; m_next_id=1; m_day_realised=0.0; m_day=0;
      m_day_start_bal=AccountInfoDouble(ACCOUNT_BALANCE);
      RollDay();
      m_cyc.Reset();
     }

   ENUM_RGS_STATE    State()   const { return m_state; }
   int               CycleId() const { return m_cyc.id; }
   bool              IsBuy()   const { return m_cyc.is_buy; }
   int               DepthCap() const { return m_cyc.depth_cap; }
   int               Group()    const { return m_cyc.group; }
   double            BestPnl()  const { return m_cyc.best_pnl; }
   //--- 0 when the arm is off or not yet armed, so the panel shows "-" rather than a number the
   //--- engine will not act on.
   double            QuickPerOz() const
     {
      if(!m_quick_on || m_state!=RGS_RUNNING) return 0.0;
      if(m_cyc.worst_pnl > -(m_quick_arm_pct/100.0*m_cyc.balance_open)) return 0.0;
      return QuickExitPerOz();
     }
   //--- The last finished cycle, pre-formatted. Built here rather than in the panel or the EA so
   //--- there is exactly ONE place that knows the layout, and the panel stays a pure renderer.
   //--- Returns "-" until a cycle has closed.
   string            PrevSummary() const
     {
      if(!m_prev_valid) return "-";
      return StringFormat("#%d %s lot %.2f  peak %+.2f  dd %+.2f  bal %.2f->%.2f (%s)",
                          m_prev.id,(m_prev.is_buy?"BUY":"SELL"),m_prev.lot,
                          m_prev.best_pnl,m_prev.worst_pnl,
                          m_prev.balance_open,m_prev_bal_after,m_prev_reason);
     }
   ENUM_RGS_ADD_MODE AddMode() const { return m_add_mode; }
   void              SetAddMode(const ENUM_RGS_ADD_MODE m) { m_add_mode=m; }
   double            DayRealised() const { return m_day_realised; }
   double            MarginPerLot() const { return m_margin_per_lot; }
   double            KillAt()      const { return KillLevel(); }

   //--- The close-all threshold, in account currency. A FIXED DOLLAR AMOUNT, fixed when the
   //--- cycle opened at ExitTargetPct% of the balance then. It does NOT depend on how many
   //--- positions are currently open, and that is the entire point.
   //---
   //--- This replaced `TargetMove x (lots / 0.01)`, which was proportional to open volume and
   //--- therefore reduced to "bid >= avg_entry + $0.30/oz" at EVERY depth: adding positions
   //--- lowered the average and raised the target by exactly the same amount, so a basket that
   //--- had added six times was no closer to getting out than one that had just opened. A live
   //--- $1 cycle dipped, recovered, and sat there.
   //---
   //--- Held fixed, the same $ is reached on a smaller move as the basket grows, which is what
   //--- the operator reported and what the watched cycles show: ~$0.29/oz on one 0.01 lot,
   //--- ~$0.10/oz once three are open. Predicted vs observed on the five hand-watched cycles is
   //--- 0.69-1.08x -- see analysis/video_ocr/test_fixed_dollar_exit.py, gate 1.
   double            CloseTarget() const { return m_cyc.target; }

   //--- The give-back arm: close when the basket retraces this much from its own high-water
   //--- mark. `best_pnl` was already being tracked and read by nothing.
   double            GiveBack() const { return m_giveback_pct/100.0*m_cyc.balance_open; }

   //--- THE TIME-DECAYED EXIT, in $/oz above the basket's AVERAGE entry.
   //---
   //--- `net = total_oz x (price - avg_entry)`, so `net >= X * total_oz` is exactly
   //--- "price is X above average entry" - the number the operator reads off the chart.
   //---
   //--- It decays LINEARLY from QuickExitStartUSD to QuickExitFloorUSD over
   //--- QuickExitDecaySec, so a cycle that has been running gets easier to close. That is
   //--- the point: duration is the strongest single predictor of trouble in this log -
   //--- cycles finishing inside 40 s had a median worst drawdown of 5-15% of balance, while
   //--- those still open past 40 s had a median of 57-89%.
   //---
   //--- NOTE, measured: at QuickExitStartUSD = 0.30 this arm is nearer than the
   //--- ExitTargetPct arm on 31 of 31 logged cycles, so in practice it REPLACES the target
   //--- rather than supplementing it. That is a deliberate choice, not an oversight.
   double            QuickExitPerOz() const
     {
      if(!m_quick_on) return 0.0;
      if(!m_quick_decay || m_quick_secs<=0) return m_quick_start;
      double age=(double)(TimeCurrent()-m_cyc.t_open);
      double k  =MathMin(1.0,MathMax(0.0,age/(double)m_quick_secs));
      return m_quick_start-(m_quick_start-m_quick_floor)*k;
     }

   void              SetPaused(const bool p)
     {
      if(p && m_state==RGS_IDLE)      m_state=RGS_HALTED;
      else if(!p && m_state==RGS_HALTED) { m_state=RGS_IDLE; m_day_realised=0.0; }
     }

   //--- Operator pressed BUY or SELL.
   bool              StartCycle(const bool is_buy,const double lot)
     {
      if(m_state!=RGS_IDLE)
        {
         Print("RGS: ignored - a cycle is already running (or the EA is paused).");
         return false;
        }
      double use=RGS_NormalizeLot(m_sym,lot);
      if(use<=0.0)
        {
         PrintFormat("RGS: REFUSED - lot %.4f is below the broker minimum %.2f. "
                     "There is no smaller position available.",
                     lot,SymbolInfoDouble(m_sym,SYMBOL_VOLUME_MIN));
         return false;
        }
      double bal=AccountInfoDouble(ACCOUNT_BALANCE);
      int    cap=RGS_MaxPositionsFor(bal,use,m_ruin_move);
      int    grp=RGS_GroupFor(bal,use,m_ruin_move);

      // REFUSE rather than round up. A budget that affords zero positions means this account
      // cannot run this strategy; opening one anyway would be a different, riskier strategy
      // wearing the same name. Ported from grid_state._open_cycle, which does the same.
      if(cap < 1)
        {
         // Say WHICH cap refused, and - for the exposure cap - exactly what to do about it.
         // The minimum 0.01 lot is 1 oz, so trading needs balance >= RuinMoveUSD. Telling the
         // operator "set RuinMoveUSD to X" beats a bare refusal they cannot act on.
         double max_oz=RGS_MaxOuncesFor(bal,m_ruin_move);
         if(m_ruin_move>0.0 && max_oz < use*RGS_OZ_PER_LOT)
            PrintFormat("RGS: REFUSED - the exposure cap allows %.1f oz at balance %.2f with "
                        "RuinMoveUSD %.2f, but one %.2f lot position is %.0f oz. Needs balance "
                        ">= %.2f, or set RuinMoveUSD to %.2f (a %.2f move would then end the "
                        "account).",max_oz,bal,m_ruin_move,use,use*RGS_OZ_PER_LOT,
                        m_ruin_move*use*RGS_OZ_PER_LOT,bal/(use*RGS_OZ_PER_LOT),
                        bal/(use*RGS_OZ_PER_LOT));
         else
            PrintFormat("RGS: REFUSED - a %.0f%% drawdown budget on %.2f affords 0 positions at "
                        "%.2f lot. This account is too small for this strategy; it is not a "
                        "setting you can turn up.",RGS_RISK_PCT,bal,use);
         m_log.Note(0,StringFormat("REFUSED bal=%.2f lot=%.2f cap=0 ruin_move=%.2f max_oz=%.1f",
                                   bal,use,m_ruin_move,max_oz));
         return false;
        }

      // Second, INDEPENDENT refusal: the risk-budget cap above says how many positions the
      // strategy's price-drawdown model can absorb - it says nothing about whether the broker
      // will actually let this account open even ONE, which is a real margin question the risk
      // budget was never meant to answer. Only checked once a real number is known (see
      // m_margin_per_lot); the very first cycle of a session still starts untested and
      // calibrates from its own attempt.
      if(m_margin_per_lot>0.0)
        {
         double need=m_margin_per_lot*use;
         double free=AccountInfoDouble(ACCOUNT_MARGIN_FREE);
         if(need>free)
           {
            PrintFormat("RGS: REFUSED - %.2f lot needs ~$%.2f real margin (calibrated from live "
                        "fills), only $%.2f free. This account cannot afford this tier's lot size "
                        "right now.",use,need,free);
            m_log.Note(0,StringFormat("REFUSED bal=%.2f lot=%.2f margin_need=%.2f margin_free=%.2f",
                                      bal,use,need,free));
            return false;
           }
        }

      m_cyc.Reset();
      m_cyc.id=m_next_id++;
      m_cyc.is_buy=is_buy;
      m_cyc.lot=use;
      m_cyc.t_open=TimeCurrent();
      m_cyc.balance_open=bal;
      m_cyc.worst_equity=AccountInfoDouble(ACCOUNT_EQUITY);
      m_cyc.depth_cap=cap;
      m_cyc.group=grp;
      m_cyc.target=m_target_pct/100.0*bal;      // FIXED here, never recomputed. See CloseTarget().
      m_state=RGS_RUNNING;
      // The START line names the ADD MODE and the calibrated margin cost, because neither was
      // recoverable from any log before: a cycle's mode could not be told from its fills (a
      // whipsaw makes GRID and TREND produce the same adds), and gold's margin went
      // $0.00 -> $2,205.60 -> $16.00 per lot inside 24 h with nothing marking the change.
      //
      // `lot cap` is the number of positions MaxTotalLots actually allows at this lot size. It is
      // frequently FAR below `depth cap` - on 2026-09-08 the depth cap said 18 and the lot cap
      // allowed 3 - and it is the one that binds. See PARAMETERS.md, "the three ceilings".
      int    lot_cap=(m_max_lots>0.0 && use>0.0 ? (int)MathFloor(m_max_lots/use+1e-9) : 0);
      double max_oz =RGS_MaxOuncesFor(bal,m_ruin_move);
      double held_oz=cap*use*RGS_OZ_PER_LOT;
      PrintFormat("RGS CYCLE %d START %s lot %.2f  balance %.2f  batch %d  depth cap %d  "
                  "lot cap %d  max oz %.0f (ruin move %.2f $/oz)  mode %s  margin/lot %.2f  "
                  "target %.3f (%.1f%%)  give-back %.3f (%.1f%%)",
                  m_cyc.id,(is_buy?"BUY":"SELL"),use,bal,grp,cap,
                  lot_cap,max_oz,m_ruin_move,RGS_AddModeName(m_add_mode),m_margin_per_lot,
                  m_cyc.target,m_target_pct,GiveBack(),m_giveback_pct);
      // The number the operator should read before clicking: at full depth, how far gold has to
      // move against the basket to consume the balance, and how far it has to move to hit target.
      if(held_oz>0.0)
         PrintFormat("RGS CYCLE %d RISK: at full depth %.0f oz - target needs %.3f $/oz, "
                     "the account is gone at %.3f $/oz.",
                     m_cyc.id,held_oz,m_cyc.target/held_oz,bal/held_oz);
      // ...and the same facts as a NOTE, so they land in the TRADES CSV too. The Print above only
      // reaches the Experts log, which is not what the analysis scripts read. Deliberately a NOTE
      // rather than a new cycles-CSV column: a column would change the v2 header mid-file and
      // break every script that reads it, for a fact that fits in the comment field.
      m_log.Note(m_cyc.id,StringFormat("cycle open mode=%s lot=%.2f depth_cap=%d lot_cap=%d "
                                       "margin_per_lot=%.2f target=%.3f giveback=%.3f",
                                       RGS_AddModeName(m_add_mode),use,cap,lot_cap,
                                       m_margin_per_lot,m_cyc.target,GiveBack()));
      if(OpenBatch(grp,use,true)<=0)
        {
         Print("RGS: initial batch opened nothing - returning to IDLE.");
         m_state=RGS_IDLE;
         m_cyc.Reset();
         return false;
        }
      return true;
     }

   void              CloseNow(const ENUM_RGS_CLOSE_REASON why)
     {
      int n; double tl,np; n=RGS_CountOpen(m_sym,tl,np);
      if(n<=0)
        {
         // CLOSE ALL on an empty book is a no-op, not an error - UNLESS this cycle had
         // positions, in which case someone else closed them between the last tick and this
         // click, and the cycle still deserves a row. Same reasoning as OnTick.
         if(m_state==RGS_RUNNING && m_cyc.positions>0) { FinishCycle(RGS_CLOSE_EXTERNAL); return; }
         if(m_state==RGS_RUNNING) { m_state=RGS_IDLE; m_cyc.Reset(); }
         return;
        }
      FinishCycle(why);
     }

   //--- Called every tick.
   void              OnTick()
     {
      RollDay();
      if(m_state!=RGS_RUNNING) return;

      int n; double tl,net; n=RGS_CountOpen(m_sym,tl,net);
      if(n<=0)
        {
         // THE BASKET VANISHED AND WE DID NOT CLOSE IT - a broker stop-out, or a manual close
         // from the Trade tab. This used to reset silently to IDLE, so a liquidated cycle left
         // NO ROW in the cycle log. On 2026-09-03 the log therefore showed 8 of 13 cycles and
         // omitted every stop-out - which is to say, exactly the losses: the file read +3.09
         // while the account lost 10.71. Any measurement taken from it was biased toward the
         // winners. Log it like any other close, with the broker's own figure.
         if(m_cyc.positions>0) { FinishCycle(RGS_CLOSE_EXTERNAL); return; }
         m_state=RGS_IDLE; m_cyc.Reset(); return;
        }

      MarkExtremes(net);

      // 1) THE EXIT - two arms, both evaluated IN PROFIT ONLY.
      //
      //    In-profit-only is not a safety bolt-on, it is the observed strategy: 88% of closes
      //    are green and cycle 7 was held through a 92%-of-balance drawdown, so an exit that
      //    fires on a retrace INTO the red was never possible.
      //
      //    TARGET arm   - a steady rise the poll catches. Cycle 10 closed here, at 30.1% of
      //                   balance, with no give-back at all.
      //    GIVE-BACK arm- a spike missed between polls, seen retracing. Cycles 5, 6, 7 and 29
      //                   closed here, at 12.2%, 15.0%, 18.3% of balance and in-band.
      //
      //    Both are fractions of the balance the cycle OPENED with, so the rule is scale-free:
      //    it behaved the same at $1.47 and at $1,181.
      if(net > 0.0)
        {
         if(net >= m_cyc.target)
           {
            FinishCycle(RGS_CLOSE_TARGET);
            return;
           }
         if(m_cyc.best_pnl > 0.0 && net <= m_cyc.best_pnl-GiveBack())
           {
            FinishCycle(RGS_CLOSE_GIVEBACK);
            return;
           }
         // QUICK arm - "price is X above the basket's AVERAGE entry", X decaying with age.
         // net == total_oz x (price - avg_entry), so comparing net to X x total_oz IS that
         // statement, in the units the operator reads off the chart.
         //
         // THE GATE IS LOAD-BEARING. Ungated, at QuickExitStartUSD = 0.30, this arm is nearer
         // than the ExitTargetPct arm on 31 of 31 logged cycles: it would fire first every time
         // and the 28.9% target would become dead code. Requiring the basket to have been down
         // QuickExitArmPct% of balance FIRST keeps the target alive on the cycles that never got
         // into trouble (16 of 31, and they are the clean winners), and hands the quick exit only
         // to baskets that went underwater and recovered - the case it was asked for.
         if(m_quick_on && tl>0.0
            && m_cyc.worst_pnl <= -(m_quick_arm_pct/100.0*m_cyc.balance_open))
           {
            double peroz=QuickExitPerOz();
            if(peroz>0.0 && net >= peroz*tl*RGS_OZ_PER_LOT)
              {
               FinishCycle(RGS_CLOSE_QUICK);
               return;
              }
           }
        }

      // 2) ADD while underwater, throttled by BOTH price and time, and bounded by the depth
      //    the risk budget allowed at open. `m_max_lots` is now a backstop, not the cap.
      // GRID adds only while underwater; TREND adds on movement either way. Everything
      // downstream - step, cooldown, margin, lot cap, depth cap - is identical, so the two modes
      // differ in exactly one place and stay comparable when the logs are analysed later.
      bool may_add=(m_add_mode==RGS_MODE_GRID ? (net<0.0) : true);
      if(may_add)
        {
         double px=(m_cyc.is_buy ? SymbolInfoDouble(m_sym,SYMBOL_ASK)
                                 : SymbolInfoDouble(m_sym,SYMBOL_BID));
         double signed_adv=(m_cyc.is_buy ? m_cyc.last_add_price-px : px-m_cyc.last_add_price);
         double adverse=(m_add_mode==RGS_MODE_GRID ? signed_adv
                                                   : MathAbs(px-m_cyc.last_add_price));
         int    room   =m_cyc.depth_cap-n;
         bool step_ok =(adverse >= m_add_step);
         bool time_ok =(TimeCurrent()-m_cyc.t_last_add >= m_cooldown);
         bool marg_ok =(AccountInfoDouble(ACCOUNT_MARGIN_FREE) >= m_min_free);
         bool lots_ok =(tl+m_cyc.lot <= m_max_lots+1e-9);
         bool deep_ok =(room > 0);
         // The block notes fire ONCE per transition, not once per tick. While blocked,
         // `t_last_add` never advances, so step_ok/time_ok stay true and the old code wrote a
         // row - with a FileFlush - on every single tick: cycle 55 produced 15 identical lines
         // of disk IO inside the poll path.
         if(step_ok && time_ok && marg_ok && lots_ok && deep_ok)
           {
            OpenBatch((int)MathMin(m_cyc.group,room),m_cyc.lot,false);
            m_cyc.block_logged=0;
           }
         else if(step_ok && time_ok && !marg_ok)
           {
            if(m_cyc.block_logged!=1)
              {
               m_cyc.block_logged=1;
               m_log.Note(m_cyc.id,StringFormat("add BLOCKED: free margin %.2f < floor %.2f",
                                                AccountInfoDouble(ACCOUNT_MARGIN_FREE),m_min_free));
              }
           }
         else if(step_ok && time_ok && !deep_ok)
           {
            if(m_cyc.block_logged!=2)
              {
               m_cyc.block_logged=2;
               // Name WHICH cap bound it. depth_cap is min(staircase budget, exposure cap), and
               // saying "the risk budget is spent" when the exposure cap was the binder sends
               // the operator to the wrong dial. Runs once per transition, not per tick.
               int stair=RGS_MaxPositionsFor(m_cyc.balance_open,m_cyc.lot,0.0);
               string why=(m_ruin_move>0.0 && m_cyc.depth_cap<stair
                           ? StringFormat("the EXPOSURE CAP (RuinMoveUSD %.2f -> %.1f oz max)",
                                          m_ruin_move,
                                          RGS_MaxOuncesFor(m_cyc.balance_open,m_ruin_move))
                           : "the risk budget");
               m_log.Note(m_cyc.id,StringFormat("add BLOCKED: depth %d/%d - %s is spent, "
                                                "holding for the bounce",n,m_cyc.depth_cap,why));
              }
           }
         // MaxTotalLots. This branch did not exist, so the day's most binding constraint wrote
         // NOTHING: on 2026-09-08 five of seven cycles opened one batch (0.33 lot x 3 = 0.99) and
         // then sat still, because a 4th would have been 1.32 > 1.00 - and no log said so. It was
         // only ever visible when the cap bit INSIDE OpenBatch (a lot small enough to stop the
         // batch mid-way, e.g. 0.10); at 0.33 it bites here, one level up. Same class as the
         // log-blindness defect of 2026-09-03: the log has to record why the engine did nothing.
         else if(step_ok && time_ok && !lots_ok)
           {
            if(m_cyc.block_logged!=3)
              {
               m_cyc.block_logged=3;
               m_log.Note(m_cyc.id,StringFormat("add BLOCKED: MaxTotalLots %.2f reached - %d "
                                                "positions, %.2f lots open, next add needs %.2f "
                                                "(depth cap %d was never the limit)",
                                                m_max_lots,n,tl,tl+m_cyc.lot,m_cyc.depth_cap));
              }
           }
        }
     }

   //--- For the panel.
   void              Snapshot(int &n,double &lots,double &net) const
     {
      n=RGS_CountOpen(m_sym,lots,net);
     }
   bool              AutoRestart() const { return m_auto_restart; }
  };
//+------------------------------------------------------------------+
