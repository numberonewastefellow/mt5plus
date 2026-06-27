//+------------------------------------------------------------------+
//| GridManager.mqh — Grid DCA lifecycle management                  |
//|                                                                  |
//| Handles: grid add decisions, avg entry recalc, basket P&L,       |
//|          uncle point, time decay, group TP updates               |
//+------------------------------------------------------------------+
#ifndef GRIDMANAGER_MQH
#define GRIDMANAGER_MQH

#include "Defines.mqh"
#include "Structs.mqh"
#include "Utils.mqh"
#include "TradeExecutor.mqh"

// Inputs are declared in GoldBreakoutGrid.mq5 and available here
// via include ordering (this file is included after input declarations)

//+------------------------------------------------------------------+
//| Initialize a new grid group from an initial entry                |
//+------------------------------------------------------------------+
void InitGridGroup(GridGroup &group, ENUM_SIGNAL_DIR dir,
                   ulong ticket, double price, double lots,
                   double atr, double grid_spacing, double tp_distance)
{
   group.Reset();
   group.direction          = dir;
   group.atr_at_entry       = atr;
   group.grid_spacing       = grid_spacing;
   group.initial_tp_distance= tp_distance;
   group.first_entry_time   = TimeCurrent();
   group.status             = GRID_ACTIVE;
   group.grid_level         = 0;
   group.last_grid_price    = price;

   // Add initial position
   group.positions[0].ticket     = ticket;
   group.positions[0].open_price = price;
   group.positions[0].volume     = lots;
   group.positions[0].open_time  = TimeCurrent();
   group.positions[0].active     = true;
   group.position_count = 1;

   group.avg_entry  = price;
   group.total_lots = lots;

   // Initial TP is ATR-based (set on the MT5 position directly)
   if(dir == SIGNAL_BUY)
      group.group_tp = price + tp_distance;
   else
      group.group_tp = price - tp_distance;

   PrintFormat("[GRID] New grid group: %s @ %.2f | TP=%.2f | ATR=%.2f | spacing=%.2f",
               DirToString(dir), price, group.group_tp, atr, grid_spacing);
}

//+------------------------------------------------------------------+
//| Recalculate volume-weighted average entry                        |
//+------------------------------------------------------------------+
void RecalcAvgEntry(GridGroup &group)
{
   double total_value = 0;
   double total_lots  = 0;

   for(int i = 0; i < group.position_count; i++)
   {
      if(!group.positions[i].active)
         continue;
      total_value += group.positions[i].open_price * group.positions[i].volume;
      total_lots  += group.positions[i].volume;
   }

   if(total_lots > 0)
   {
      group.avg_entry  = total_value / total_lots;
      group.total_lots = total_lots;
   }
}

//+------------------------------------------------------------------+
//| Update group TP on all positions in the grid                     |
//| Called after every grid add to set common TP at avg + offset      |
//+------------------------------------------------------------------+
void UpdateAllPositionsTP(GridGroup &group)
{
   // Dynamic offset: ATR-scaled with static minimum floor
   // On volatile weeks (ATR=$30), offset = $3.00 instead of static $1.50
   // On quiet weeks (ATR=$8), offset = $1.50 (floor from InpGroupTPOffset)
   double dynamic_offset = group.atr_at_entry * 0.10;
   double final_offset   = MathMax(dynamic_offset, InpGroupTPOffset);

   double new_tp;
   if(group.direction == SIGNAL_BUY)
      new_tp = group.avg_entry + final_offset;
   else
      new_tp = group.avg_entry - final_offset;

   group.group_tp = new_tp;

   for(int i = 0; i < group.position_count; i++)
   {
      if(!group.positions[i].active)
         continue;
      ModifyPositionTP(group.positions[i].ticket, new_tp);
   }

   PrintFormat("[GRID] Updated group TP=%.2f (avg=%.2f + $%.2f offset [ATR-scaled]) on %d positions",
               new_tp, group.avg_entry, final_offset, group.position_count);
}

//+------------------------------------------------------------------+
//| Calculate basket floating P&L across all active positions        |
//+------------------------------------------------------------------+
double CalcBasketPnL(GridGroup &group)
{
   double total_pnl = 0;
   double contract  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   for(int i = 0; i < group.position_count; i++)
   {
      if(!group.positions[i].active)
         continue;

      double price_diff;
      if(group.direction == SIGNAL_BUY)
         price_diff = bid - group.positions[i].open_price;
      else
         price_diff = group.positions[i].open_price - ask;

      total_pnl += price_diff * group.positions[i].volume * contract;
   }

   group.basket_pnl = total_pnl;
   return total_pnl;
}

//+------------------------------------------------------------------+
//| Get effective basket max loss (considers time decay tightening)  |
//+------------------------------------------------------------------+
double GetEffectiveMaxLoss(const GridGroup &group)
{
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double pct_loss = equity * InpGlobalMaxLossPct / 100.0;
   double max_loss = MathMin(InpGlobalMaxLoss, pct_loss);

   // Tighten if stale
   if(group.grid_adds_halted && InpStaleTightenPct > 0)
      max_loss = max_loss * InpStaleTightenPct / 100.0;

   return max_loss;
}

//+------------------------------------------------------------------+
//| Check if uncle point (basket max loss) is hit                    |
//+------------------------------------------------------------------+
bool IsUnclePointHit(const GridGroup &group)
{
   double max_loss = GetEffectiveMaxLoss(group);
   return (group.basket_pnl <= -max_loss);
}

//+------------------------------------------------------------------+
//| Check if group TP is reached                                     |
//+------------------------------------------------------------------+
bool IsGroupTPHit(const GridGroup &group)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(group.direction == SIGNAL_BUY)
      return (bid >= group.group_tp);
   else
      return (ask <= group.group_tp);
}

//+------------------------------------------------------------------+
//| Check if a grid level should be added                            |
//+------------------------------------------------------------------+
bool ShouldAddGridLevel(const GridGroup &group, double current_price)
{
   // Already at max grid levels
   if(group.grid_level >= InpMaxGridLevels)
      return false;

   // Lot cap
   if(group.total_lots + InpLotSize > InpMaxTotalLots + 0.001)
      return false;

   // Grid adds halted (time decay)
   if(group.grid_adds_halted)
      return false;

   // Price must move grid_spacing from last entry in adverse direction
   double distance;
   if(group.direction == SIGNAL_BUY)
      distance = group.last_grid_price - current_price;  // Price dropped
   else
      distance = current_price - group.last_grid_price;  // Price rose

   if(distance < group.grid_spacing)
      return false;

   // Margin check: free margin must be > min_pct of equity
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(equity > 0)
   {
      double free_pct = (free_margin / equity) * 100.0;
      if(free_pct < InpMinFreeMarginPct)
      {
         PrintFormat("[GRID] Grid add skipped — free margin %.1f%% < %.1f%%",
                     free_pct, InpMinFreeMarginPct);
         return false;
      }
   }

   // Spread check before grid add too
   if(SpreadExceeded(InpMaxSpread * 2.0))  // Slightly relaxed for grid adds
   {
      Print("[GRID] Grid add skipped — spread too wide");
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Add a grid level — place order and update group state            |
//+------------------------------------------------------------------+
bool AddGridLevel(GridGroup &group)
{
   string comment = StringFormat("%s L%d", EA_COMMENT_PREFIX, group.grid_level + 1);

   ulong ticket = PlaceMarketOrder(group.direction, InpLotSize, 0, InpSafetySL, comment);
   if(ticket == 0)
   {
      Print("[GRID] Grid add order FAILED");
      return false;
   }

   // Get fill price from position
   double fill_price = 0;
   if(PositionSelectByTicket(ticket))
      fill_price = PositionGetDouble(POSITION_PRICE_OPEN);
   if(fill_price == 0)
      fill_price = (group.direction == SIGNAL_BUY) ?
                   SymbolInfoDouble(_Symbol, SYMBOL_ASK) :
                   SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // Add to grid group
   int idx = group.position_count;
   if(idx >= MAX_GRID_POSITIONS)
   {
      Print("[GRID] Position array full — cannot add");
      return false;
   }

   group.positions[idx].ticket     = ticket;
   group.positions[idx].open_price = fill_price;
   group.positions[idx].volume     = InpLotSize;
   group.positions[idx].open_time  = TimeCurrent();
   group.positions[idx].active     = true;
   group.position_count++;

   group.grid_level++;
   group.last_grid_price = fill_price;

   // Recalculate average entry
   RecalcAvgEntry(group);

   PrintFormat("[GRID] Grid add #%d: %s %.2f lots @ %.2f | avg=%.2f | total_lots=%.2f",
               group.grid_level, DirToString(group.direction),
               InpLotSize, fill_price, group.avg_entry, group.total_lots);

   // Update TP on ALL positions to group TP (avg + offset)
   UpdateAllPositionsTP(group);

   return true;
}

//+------------------------------------------------------------------+
//| Check time decay — halt grid adds after configured hours         |
//+------------------------------------------------------------------+
void CheckTimeDecay(GridGroup &group)
{
   if(group.grid_adds_halted)
      return;

   if(group.first_entry_time == 0)
      return;

   long elapsed_sec = (long)(TimeCurrent() - group.first_entry_time);
   long stale_sec   = (long)InpStaleHours * 3600;

   if(elapsed_sec >= stale_sec)
   {
      group.grid_adds_halted = true;
      group.status = GRID_STALE;
      PrintFormat("[GRID] Time decay: grid adds HALTED after %d hours (basket_pnl=$%.2f)",
                  InpStaleHours, group.basket_pnl);
   }
}

//+------------------------------------------------------------------+
//| Sync grid group with MT5 — detect externally closed positions    |
//+------------------------------------------------------------------+
void SyncGridWithBroker(GridGroup &group)
{
   int active_count = 0;

   for(int i = 0; i < group.position_count; i++)
   {
      if(!group.positions[i].active)
         continue;

      // Check if position still exists in MT5
      if(!PositionSelectByTicket(group.positions[i].ticket))
      {
         // Position was closed externally (TP hit by broker, manual close, etc)
         PrintFormat("[GRID] Position %I64u closed externally", group.positions[i].ticket);
         group.positions[i].active = false;
      }
      else
      {
         active_count++;
      }
   }

   // If ALL positions closed externally, mark group as closed
   if(active_count == 0 && (group.status == GRID_ACTIVE || group.status == GRID_STALE))
   {
      PrintFormat("[GRID] All positions closed externally — marking group CLOSED (was %s)",
                  group.status == GRID_STALE ? "STALE" : "ACTIVE");
      group.status = GRID_CLOSED;
   }
}

//+------------------------------------------------------------------+
//| Scan for orphaned positions — MT5 positions with our magic       |
//| number that are NOT tracked in g_grid.                           |
//|                                                                  |
//| ONLY called when grid has been INACTIVE for 120+ seconds         |
//| (caller enforces this). This prevents false positives during     |
//| normal grid lifecycle transitions.                               |
//|                                                                  |
//| Strategy: close orphans ONLY if loss exceeds uncle point.        |
//| If loss is under uncle, leave them — broker-side TP may still    |
//| trigger and recover the trade. No re-adoption (causes timing     |
//| issues with stale uncle tightening).                             |
//+------------------------------------------------------------------+
int ScanForOrphans(const GridGroup &group)
{
   int orphans_found = 0;
   int orphans_closed = 0;
   int total = PositionsTotal();

   // Collect orphan tickets and their combined P&L
   ulong orphan_tickets[];
   ArrayResize(orphan_tickets, 0);
   double total_orphan_pnl = 0;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MAGIC_GRID) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      // Check if tracked in current grid (should be empty since caller checks INACTIVE)
      bool tracked = false;
      if(group.status == GRID_ACTIVE || group.status == GRID_STALE)
      {
         for(int j = 0; j < group.position_count; j++)
         {
            if(group.positions[j].ticket == ticket && group.positions[j].active)
            {
               tracked = true;
               break;
            }
         }
      }

      if(!tracked)
      {
         int sz = ArraySize(orphan_tickets);
         ArrayResize(orphan_tickets, sz + 1);
         orphan_tickets[sz] = ticket;
         orphans_found++;

         if(PositionSelectByTicket(ticket))
            total_orphan_pnl += PositionGetDouble(POSITION_PROFIT)
                              + PositionGetDouble(POSITION_SWAP);
      }
   }

   if(orphans_found == 0)
      return 0;

   // Uncle threshold (use base uncle, no stale tightening for orphans)
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double pct_loss = equity * InpGlobalMaxLossPct / 100.0;
   double max_loss = MathMin(InpGlobalMaxLoss, pct_loss);

   if(total_orphan_pnl <= -max_loss)
   {
      // Orphan loss exceeds uncle — close them to prevent further bleeding
      PrintFormat("[ORPHAN] %d position(s) with loss=$%.2f exceeds uncle=$%.2f — CLOSING",
                  orphans_found, total_orphan_pnl, max_loss);

      for(int i = ArraySize(orphan_tickets) - 1; i >= 0; i--)
      {
         if(PositionSelectByTicket(orphan_tickets[i]))
         {
            PrintFormat("[ORPHAN] Closing #%I64u: %s %.2f lots @ %.2f | pnl=$%.2f",
                        orphan_tickets[i],
                        PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL",
                        PositionGetDouble(POSITION_VOLUME),
                        PositionGetDouble(POSITION_PRICE_OPEN),
                        PositionGetDouble(POSITION_PROFIT));
         }
         if(ClosePosition(orphan_tickets[i]))
            orphans_closed++;
      }
      PrintFormat("[ORPHAN] Closed %d orphan(s)", orphans_closed);
   }
   else
   {
      // Loss under uncle — leave them. Broker TP may still trigger recovery.
      static datetime last_orphan_log = 0;
      if(TimeCurrent() - last_orphan_log > 300)
      {
         PrintFormat("[ORPHAN] %d position(s) floating $%.2f (under uncle $%.2f) — leaving for broker TP",
                     orphans_found, total_orphan_pnl, max_loss);
         last_orphan_log = TimeCurrent();
      }
   }

   return orphans_closed;
}

//+------------------------------------------------------------------+
//| Main grid management — call every tick or every second           |
//| Returns: close reason if group was closed, CLOSE_NONE otherwise  |
//+------------------------------------------------------------------+
ENUM_CLOSE_REASON ManageGrid(GridGroup &group)
{
   if(group.status != GRID_ACTIVE && group.status != GRID_STALE)
      return CLOSE_NONE;

   // --- Sync with broker (detect external closes) ---
   SyncGridWithBroker(group);

   if(group.status == GRID_CLOSED)
      return CLOSE_MANUAL;  // Was closed externally

   // --- Calculate current basket P&L ---
   CalcBasketPnL(group);

   // --- Check uncle point (highest priority) ---
   if(IsUnclePointHit(group))
   {
      PrintFormat("[GRID] UNCLE POINT HIT! basket_pnl=$%.2f max_loss=$%.2f",
                  group.basket_pnl, GetEffectiveMaxLoss(group));
      CloseAllGridPositions(group, CLOSE_UNCLE);
      return CLOSE_UNCLE;
   }

   // --- Check group TP ---
   // Note: Broker also has hard TP set on each position, so it may close them
   // before we get here. SyncGridWithBroker (above) handles that case.
   // This manual check is belt-and-suspenders for edge cases where broker TP
   // didn't fire on all positions simultaneously.
   // Guard: count active positions first — if broker already closed most/all,
   // skip the manual close to avoid OrderSend errors on dead tickets.
   int active_count = 0;
   for(int i = 0; i < group.position_count; i++)
      if(group.positions[i].active) active_count++;

   if(active_count > 0 && IsGroupTPHit(group))
   {
      string tp_type = (group.grid_level == 0) ? "INITIAL" : "GROUP";
      PrintFormat("[GRID] %s TP HIT! basket_pnl=$%.2f group_tp=%.2f active=%d",
                  tp_type, group.basket_pnl, group.group_tp, active_count);

      ENUM_CLOSE_REASON reason = (group.grid_level == 0) ? CLOSE_TP_INITIAL : CLOSE_TP_GROUP;
      CloseAllGridPositions(group, reason);
      return reason;
   }

   // --- Check time decay ---
   CheckTimeDecay(group);

   // --- Check if grid add needed ---
   double current_price = (group.direction == SIGNAL_BUY) ?
                          SymbolInfoDouble(_Symbol, SYMBOL_BID) :
                          SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(ShouldAddGridLevel(group, current_price))
   {
      AddGridLevel(group);
   }

   return CLOSE_NONE;
}

//+------------------------------------------------------------------+
//| Recover grid state from existing MT5 positions (after restart)   |
//+------------------------------------------------------------------+
void RecoverGridFromPositions(GridGroup &group)
{
   group.Reset();

   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MAGIC_GRID) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)    continue;

      double price    = PositionGetDouble(POSITION_PRICE_OPEN);
      double lots     = PositionGetDouble(POSITION_VOLUME);
      long   pos_type = PositionGetInteger(POSITION_TYPE);
      datetime open_t = (datetime)PositionGetInteger(POSITION_TIME);

      // Determine direction from first position
      if(group.position_count == 0)
      {
         group.direction       = (pos_type == POSITION_TYPE_BUY) ? SIGNAL_BUY : SIGNAL_SELL;
         group.first_entry_time= open_t;
         group.status          = GRID_ACTIVE;
      }

      int idx = group.position_count;
      if(idx >= MAX_GRID_POSITIONS)
         break;

      group.positions[idx].ticket     = ticket;
      group.positions[idx].open_price = price;
      group.positions[idx].volume     = lots;
      group.positions[idx].open_time  = open_t;
      group.positions[idx].active     = true;
      group.position_count++;

      // Track earliest open time
      if(open_t < group.first_entry_time)
         group.first_entry_time = open_t;
   }

   if(group.position_count > 0)
   {
      group.grid_level = group.position_count - 1;
      RecalcAvgEntry(group);

      // Compute current ATR for grid spacing reference
      group.atr_at_entry = ComputeATR(InpATR_Period, InpATR_Timeframe);
      group.grid_spacing = group.atr_at_entry * InpGrid_ATR_Mult;

      // Find last grid price (lowest for BUY, highest for SELL)
      group.last_grid_price = group.positions[0].open_price;
      for(int i = 1; i < group.position_count; i++)
      {
         if(group.direction == SIGNAL_BUY)
         {
            if(group.positions[i].open_price < group.last_grid_price)
               group.last_grid_price = group.positions[i].open_price;
         }
         else
         {
            if(group.positions[i].open_price > group.last_grid_price)
               group.last_grid_price = group.positions[i].open_price;
         }
      }

      // Set group TP
      if(group.grid_level > 0)
         UpdateAllPositionsTP(group);
      else
      {
         // Single position — keep existing TP from MT5
         if(PositionSelectByTicket(group.positions[0].ticket))
            group.group_tp = PositionGetDouble(POSITION_TP);
      }

      // Check time decay
      long elapsed = (long)(TimeCurrent() - group.first_entry_time);
      if(elapsed >= (long)InpStaleHours * 3600)
      {
         group.grid_adds_halted = true;
         group.status = GRID_STALE;
      }

      PrintFormat("[RECOVER] Found %d positions | %s | avg=%.2f | lots=%.2f | grid_level=%d | spacing=%.2f",
                  group.position_count, DirToString(group.direction),
                  group.avg_entry, group.total_lots, group.grid_level,
                  group.grid_spacing);
   }
}

#endif
