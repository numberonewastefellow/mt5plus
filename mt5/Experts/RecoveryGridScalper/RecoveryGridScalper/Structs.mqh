//+------------------------------------------------------------------+
//| Structs.mqh - the cycle's carried state                          |
//|                                                                  |
//| The risk in this strategy lives in these fields, not in the       |
//| parameters: how deep the basket went, how far underwater it sat,  |
//| and how long it has been open.                                    |
//+------------------------------------------------------------------+
#property strict

//+------------------------------------------------------------------+
//| One cycle = one basket, opened on the operator's click and closed |
//| all at once. Every field here is written to the cycle CSV.        |
//+------------------------------------------------------------------+
struct SCycle
  {
   int               id;                // 1-based, per EA session
   bool              is_buy;            // the operator's direction; fixed for the cycle
   double            lot;               // unit lot, FIXED at open - escalation is in the
                                        // NUMBER of positions, not their size
   datetime          t_open;
   datetime          t_close;
   int               batches;           // initial batch + each add
   int               positions;         // total positions opened
   double            total_lots;
   double            first_entry;       // reference price for the adverse-move test
   double            sum_entry_lots;    // for the volume-weighted average entry
   double            best_pnl;          // high-water mark of basket P&L
   double            worst_pnl;         // drawdown watermark - the number that matters
   double            worst_equity;
   double            balance_open;
   datetime          t_last_add;        // half of the add throttle
   double            last_add_price;    // the other half: adverse step since the last add

   // FIXED AT OPEN, exactly like `lot`. `target` in particular must NOT be recomputed from the
   // live volume: a target that grows with the basket leaves the required price move constant at
   // every depth, so adding positions buys no progress toward the exit and the grid cannot
   // recover. Holding it fixed is what makes a deeper basket close on a SMALLER move -- $0.289/oz
   // on one 0.01 lot, $0.096/oz once the same $0.289 is spread over three.
   double            target;            // $ of open basket P&L that closes the cycle
   int               depth_cap;         // most positions the risk budget allows this cycle
   int               group;             // positions opened per rung

   // Which add-block reason has already been logged (0 none, 1 margin, 2 depth). The note used
   // to be written on EVERY tick while blocked -- cycle 55 wrote 15 identical rows, each with a
   // FileFlush, inside the poll path. Log the transition, not the state.
   int               block_logged;

   void              Reset()
     {
      id=0; is_buy=false; lot=0.0; t_open=0; t_close=0;
      batches=0; positions=0; total_lots=0.0;
      first_entry=0.0; sum_entry_lots=0.0;
      best_pnl=0.0; worst_pnl=0.0; worst_equity=0.0; balance_open=0.0;
      t_last_add=0; last_add_price=0.0;
      target=0.0; depth_cap=0; group=1; block_logged=0;
     }

   double            AvgEntry() const
     {
      return (total_lots>0.0 ? sum_entry_lots/total_lots : 0.0);
     }
  };
//+------------------------------------------------------------------+
