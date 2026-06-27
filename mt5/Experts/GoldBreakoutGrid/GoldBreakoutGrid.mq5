//+------------------------------------------------------------------+
//| GoldBreakoutGrid.mq5                                             |
//| Gold Breakout EA v4.20 — TP-Only + Inflection Point Filter       |
//|                                                                  |
//| Strategy (v4.20 "Fan Filter"):                                   |
//|   1. Build Asian range during quiet session                      |
//|   2. Triple EMA (10/30/50 H4) trend filter                      |
//|   3. EMA Fan Spread filter (blocks trend exhaustion entries)     |
//|   4. Breakout entry with ATR-based TP                            |
//|   5. NO grid DCA — hold losers, allow new independent trades     |
//|   6. Per-position uncle ($500) + global cap ($1500)              |
//|   7. Friday close-out before weekend                             |
//|                                                                  |
//| v4.20: MinConfidence 75, EMA fan spread replaces overextension.  |
//| Fan spread catches inflection points (Mar 12 -$500) while        |
//| preserving momentum entries (Feb 25 +$106).                      |
//+------------------------------------------------------------------+
#property copyright "Option Chain Dashboard"
#property version   "4.20"
#property strict
#property description "Gold Breakout v4.20 — TP-Only, Fan Spread Filter"
#property description "Triple EMA + EMA Fan Spread (inflection point blocker)"
#property description "Hold losers, allow new trades | XAUUSDm 1:20"

// EA_VERSION defined in Defines.mqh

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
//--- Trend Filter (Triple EMA Crossover)
input group "=== Trend Filter (Triple EMA) ==="
input int             InpEMA_Fast       = 10;         // Fast EMA period (recent trend)
input int             InpEMA_Mid        = 30;         // Medium EMA period (medium trend)
input int             InpEMA_Slow       = 50;         // Slow EMA period (macro trend)
input ENUM_TIMEFRAMES InpEMA_Timeframe  = PERIOD_H4;  // EMA timeframe (all three)
input double          InpMaxEMADistance  = 0;          // Max ATR distance from Slow EMA (0=disabled, replaced by fan spread)
input int             InpFanLookback    = 5;          // EMA fan spread lookback (H4 bars, ~20hrs)

//--- Session Times (Broker server time)
input group "=== Session Times (Broker Time) ==="
input int    InpAsianStart     = 2;       // Asian session start hour
input int    InpAsianEnd       = 9;       // Asian session end hour
input int    InpLondonStart    = 10;      // London session start hour
input int    InpLondonEnd      = 14;      // London session end hour
input int    InpNYStart        = 15;      // NY session start hour
input int    InpNYEnd          = 20;      // NY session end hour

//--- Breakout Entry
input group "=== Breakout Entry ==="
input double InpBuffer         = 0.50;    // Breakout buffer above/below range ($)
input double InpVolSpike       = 1.2;     // Volume spike multiplier vs Asian avg
input int    InpMinConfidence  = 75;      // Min confidence score to auto-trade

//--- Dynamic TP (ATR-Based)
input group "=== Dynamic TP (ATR-Based) ==="
input int             InpATR_Period    = 14;         // ATR period
input ENUM_TIMEFRAMES InpATR_Timeframe = PERIOD_H4;  // ATR timeframe
input double InpTP_ATR_Mult    = 0.3;     // TP = ATR x this multiplier
input double InpTP_Min         = 3.0;     // Min TP distance ($)
input double InpTP_Max         = 15.0;    // Max TP distance ($)

//--- Position Management (v4.00 — no grid, multi-position)
input group "=== Position Management ==="
input double InpLotSize          = 0.01;   // Lot size per trade
input int    InpMaxOpenPositions = 5;      // Max simultaneous open positions

//--- Safety Net
input group "=== Safety Net ==="
input double InpPerPositionUncle = 500.0;  // Per-position uncle ($) — each trade lives or dies alone
input double InpGlobalMaxLoss    = 1500.0; // Global max loss across ALL positions ($) — emergency cap
input double InpGlobalMaxLossPct = 30.0;   // Global max loss (% of equity) — emergency cap

//--- Risk Limits
input group "=== Risk Limits ==="
input int    InpMaxDailyTrades   = 2;      // Max new entries per day
input double InpMaxDailyLoss     = 500.0;  // Max daily realized loss ($)

//--- Filters
input group "=== Filters ==="
input double InpMaxSpread      = 0.40;     // Max spread to enter ($)
input bool   InpNewsFilter     = true;     // Skip NFP/CPI days
input int    InpSlippage       = 50;       // Max slippage (points)
input int    InpFridayCloseHour = 0;       // Close all positions on Friday at this hour (0=disabled)

//--- Legacy (kept for compatibility, not used in v4.00)
input group "=== Grid Legacy (disabled in v4) ==="
input int    InpMaxGridLevels  = 0;        // Grid levels (0=disabled, TP-only mode)
input double InpGrid_ATR_Mult  = 0.7;     // Grid spacing (unused when levels=0)
input double InpMaxTotalLots   = 0.01;    // Max lots (single position)
input double InpGroupTPOffset  = 1.50;    // Group TP offset (unused)
input double InpSafetySL       = 0;       // Per-position SL (0=none)
input int    InpStaleHours     = 72;      // Stale hours (unused)
input double InpStaleTightenPct = 60.0;   // Stale tighten (unused)
input double InpMinFreeMarginPct = 50.0;  // Min margin (unused)
input int    InpMaxConcurrentGrids = 1;   // Concurrent grids (unused)

//+------------------------------------------------------------------+
//| Includes                                                         |
//+------------------------------------------------------------------+
#include "GoldBreakoutGrid/Defines.mqh"
#include "GoldBreakoutGrid/Structs.mqh"
#include "GoldBreakoutGrid/Utils.mqh"
#include "GoldBreakoutGrid/TrendFilter.mqh"
#include "GoldBreakoutGrid/TradeExecutor.mqh"
#include "GoldBreakoutGrid/GridManager.mqh"
#include "GoldBreakoutGrid/BreakoutDetector.mqh"
#include "GoldBreakoutGrid/ChartVisuals.mqh"

//+------------------------------------------------------------------+
//| Global state                                                     |
//+------------------------------------------------------------------+
AsianRangeState  g_range;
GridGroup        g_grid;        // Kept for dashboard compatibility
DailyStats       g_daily;
BreakoutSignal   g_last_signal;

// Throttle
datetime         g_last_entry_check = 0;
datetime         g_last_m1_bar      = 0;
datetime         g_last_grid_active = 0;

//+------------------------------------------------------------------+
//| Close ALL our positions (for Friday close / emergency)           |
//+------------------------------------------------------------------+
int CloseAllOurPositions()
{
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MAGIC_GRID) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(ClosePosition(ticket))
         closed++;
   }
   return closed;
}

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(StringFind(_Symbol, "XAUUSD") < 0 && StringFind(_Symbol, "Gold") < 0)
      PrintFormat("[INIT] WARNING: EA designed for XAUUSDm gold. Current: %s", _Symbol);

   double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double spread   = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   PrintFormat("[INIT] Symbol=%s Contract=%.0f Spread=$%.3f Lot=%.2f",
               _Symbol, contract, spread, InpLotSize);
   PrintFormat("[INIT] Triple EMA: Fast=%d Mid=%d Slow=%d on %s | Fan lookback=%d bars",
               InpEMA_Fast, InpEMA_Mid, InpEMA_Slow, EnumToString(InpEMA_Timeframe),
               InpFanLookback);
   PrintFormat("[INIT] TP: ATR(%d)x%.1f [min=$%.1f max=$%.1f] | Grid=%s",
               InpATR_Period, InpTP_ATR_Mult, InpTP_Min, InpTP_Max,
               InpMaxGridLevels == 0 ? "DISABLED (TP-only)" : "ENABLED");
   PrintFormat("[INIT] Uncle: per-pos=$%.0f | global=$%.0f or %.1f%% | MaxPos=%d | FridayClose=%d",
               InpPerPositionUncle, InpGlobalMaxLoss, InpGlobalMaxLossPct,
               InpMaxOpenPositions, InpFridayCloseHour);

   if(!InitTrendFilter(InpEMA_Fast, InpEMA_Mid, InpEMA_Slow, InpEMA_Timeframe))
      Print("[INIT] Trend filter init failed — continuing without trend gate");

   g_range.Reset();
   g_grid.Reset();
   g_daily.Reset();
   g_daily.last_reset_date = TimeCurrent();
   g_last_signal.Reset();

   // Recover existing positions (after restart)
   RecoverGridFromPositions(g_grid);

   EventSetTimer(1);

   DrawDashboard(g_range, g_grid, g_daily);
   DrawSessionSeparators();
   ChartRedraw(0);

   Print("[INIT] Gold Breakout EA v4.20 started (TP-only, fan spread filter)");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   DeinitTrendFilter();
   CleanupChartVisuals();
   PrintFormat("[DEINIT] Daily P&L=$%.2f  Trades=%d  W=%d  L=%d",
               g_daily.realized_pnl, g_daily.grids_opened,
               g_daily.wins, g_daily.losses);
}

//+------------------------------------------------------------------+
//| Tick handler                                                     |
//+------------------------------------------------------------------+
void OnTick()
{
   // === PRIORITY 0: Friday close-out ===
   if(InpFridayCloseHour > 0 && IsFridayCloseTime(InpFridayCloseHour))
   {
      int open = CountOurPositions();
      if(open > 0)
      {
         PrintFormat("[FRIDAY] Closing %d positions before weekend", open);
         int closed = CloseAllOurPositions();
         PrintFormat("[FRIDAY] Closed %d positions", closed);
         g_grid.Reset();
      }
      return;  // Don't open new trades on Friday after close hour
   }

   // === PRIORITY 1: Per-position uncle + global cap — check every tick ===
   int open_count = CountOurPositions();
   if(open_count > 0)
   {
      double basket_pnl = CalcBasketFloatingPnL();

      // --- Per-position uncle: each trade lives or dies on its own ---
      if(InpPerPositionUncle > 0)
      {
         int total = PositionsTotal();
         for(int i = total - 1; i >= 0; i--)
         {
            ulong ticket = PositionGetTicket(i);
            if(ticket == 0) continue;
            if(PositionGetInteger(POSITION_MAGIC) != MAGIC_GRID) continue;
            if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

            double pos_pnl = PositionGetDouble(POSITION_PROFIT)
                           + PositionGetDouble(POSITION_SWAP);

            if(pos_pnl <= -InpPerPositionUncle)
            {
               PrintFormat("[UNCLE] Position #%I64u pnl=$%.2f exceeds per-position uncle -$%.0f — closing",
                           ticket, pos_pnl, InpPerPositionUncle);
               ClosePosition(ticket);
            }
         }
      }

      // --- Global emergency cap: total across ALL positions ---
      basket_pnl = CalcBasketFloatingPnL();  // Recalculate after per-position closes
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double pct_cap = equity * InpGlobalMaxLossPct / 100.0;
      double global_cap = MathMin(InpGlobalMaxLoss, pct_cap);

      if(basket_pnl <= -global_cap)
      {
         ulong worst = FindWorstPosition();
         if(worst > 0)
         {
            double worst_pnl = 0;
            if(PositionSelectByTicket(worst))
               worst_pnl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
            PrintFormat("[UNCLE] Global basket $%.2f exceeds cap -$%.0f — closing worst #%I64u ($%.2f)",
                        basket_pnl, global_cap, worst, worst_pnl);
            ClosePosition(worst);
         }
      }

      // Update dashboard display
      g_grid.basket_pnl = basket_pnl;
   }

   // === PRIORITY 2: Manage active grid (if grid levels > 0, legacy mode) ===
   if(InpMaxGridLevels > 0 && (g_grid.status == GRID_ACTIVE || g_grid.status == GRID_STALE))
   {
      g_last_grid_active = TimeCurrent();
      ENUM_CLOSE_REASON result = ManageGrid(g_grid);
      if(result != CLOSE_NONE)
      {
         double pnl = g_grid.basket_pnl;
         g_daily.realized_pnl += pnl;
         if(pnl >= 0) g_daily.wins++;
         else g_daily.losses++;
         PrintFormat("[MAIN] Grid closed: reason=%d pnl=$%.2f", (int)result, pnl);
         g_grid.Reset();
      }
   }

   // === PRIORITY 3: Entry decisions — throttle to once per second ===
   if(TimeCurrent() - g_last_entry_check < 1)
      return;
   g_last_entry_check = TimeCurrent();

   // New M1 bar only
   datetime current_m1 = iTime(_Symbol, PERIOD_M1, 0);
   if(current_m1 == g_last_m1_bar)
      return;
   g_last_m1_bar = current_m1;

   // --- v4.00: Check position count instead of grid status ---
   if(CountOurPositions() >= InpMaxOpenPositions)
      return;  // Max positions reached

   // Skip if daily limits reached
   if(g_daily.grids_opened >= InpMaxDailyTrades)
      return;

   // Skip if daily loss limit
   if(g_daily.realized_pnl <= -InpMaxDailyLoss)
      return;

   // --- Detect breakout signal ---
   BreakoutSignal sig = DetectBreakout(g_range);

   if(!sig.is_valid)
      return;

   g_last_signal = sig;

   // --- Execute entry ---
   string comment = StringFormat("%s %s %d",
                                 EA_COMMENT_PREFIX,
                                 DirToString(sig.direction),
                                 sig.confidence);

   ulong ticket = PlaceMarketOrder(sig.direction, InpLotSize,
                                   sig.tp_distance, 0, comment);  // No SL, TP only

   if(ticket == 0)
   {
      Print("[MAIN] Entry order FAILED");
      return;
   }

   double fill_price = 0;
   if(PositionSelectByTicket(ticket))
      fill_price = PositionGetDouble(POSITION_PRICE_OPEN);
   if(fill_price == 0)
      fill_price = (sig.direction == SIGNAL_BUY) ?
                   SymbolInfoDouble(_Symbol, SYMBOL_ASK) :
                   SymbolInfoDouble(_Symbol, SYMBOL_BID);

   g_daily.grids_opened++;

   DrawSignalArrow(sig.direction, fill_price, sig.confidence, sig.reason);

   PrintFormat("[MAIN] Entry: %s %.2f lots @ %.2f | TP=%.2f | conf=%d | open=%d/%d",
               DirToString(sig.direction), InpLotSize, fill_price,
               (sig.direction == SIGNAL_BUY) ? fill_price + sig.tp_distance :
               fill_price - sig.tp_distance,
               sig.confidence, CountOurPositions(), InpMaxOpenPositions);
}

//+------------------------------------------------------------------+
//| Timer handler — periodic tasks (1 second interval)               |
//+------------------------------------------------------------------+
void OnTimer()
{
   // --- Daily reset ---
   if(IsNewTradingDay(g_daily.last_reset_date))
   {
      PrintFormat("[DAILY] Reset — yesterday: P&L=$%.2f Trades=%d W=%d L=%d",
                  g_daily.realized_pnl, g_daily.grids_opened,
                  g_daily.wins, g_daily.losses);
      g_daily.realized_pnl = 0;
      g_daily.grids_opened = 0;
      g_daily.wins   = 0;
      g_daily.losses = 0;

      g_range.Reset();
      g_range.last_range_date = TimeCurrent();
   }

   // --- Chart visuals every 5 seconds ---
   static int timer_ticks = 0;
   timer_ticks++;

   if(timer_ticks % 5 == 0)
   {
      // Update g_grid for dashboard display
      g_grid.position_count = CountOurPositions();
      g_grid.basket_pnl = CalcBasketFloatingPnL();

      DrawDashboard(g_range, g_grid, g_daily);
      DrawAsianRange(g_range);
      DrawSessionSeparators();
      ChartRedraw(0);
   }

   // --- Status log every 5 minutes ---
   if(timer_ticks % 300 == 0)
   {
      double atr = ComputeATR(InpATR_Period, InpATR_Timeframe);
      ENUM_SIGNAL_DIR trend = GetTrendDirection();
      double basket = CalcBasketFloatingPnL();
      PrintFormat("[STATUS] Trend=%s ATR=$%.2f | Positions=%d/%d | Basket=$%.2f | Daily=$%.2f T:%d W:%d L:%d",
                  DirToString(trend), atr,
                  CountOurPositions(), InpMaxOpenPositions, basket,
                  g_daily.realized_pnl, g_daily.grids_opened,
                  g_daily.wins, g_daily.losses);
   }
}

//+------------------------------------------------------------------+
//| Trade transaction handler                                        |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      ulong deal = trans.deal;
      if(deal > 0 && HistoryDealSelect(deal))
      {
         long magic = HistoryDealGetInteger(deal, DEAL_MAGIC);
         if(magic == MAGIC_GRID)
         {
            double price  = HistoryDealGetDouble(deal, DEAL_PRICE);
            double volume = HistoryDealGetDouble(deal, DEAL_VOLUME);
            double profit = HistoryDealGetDouble(deal, DEAL_PROFIT);
            string cmt    = HistoryDealGetString(deal, DEAL_COMMENT);

            // Track closed trades for daily stats
            long deal_entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
            if(deal_entry == DEAL_ENTRY_OUT || deal_entry == DEAL_ENTRY_INOUT)
            {
               g_daily.realized_pnl += profit;
               if(profit >= 0)
                  g_daily.wins++;
               else
                  g_daily.losses++;
            }

            PrintFormat("[DEAL] %.2f @ %.2f profit=$%.2f %s",
                        volume, price, profit, cmt);
         }
      }
   }
}
//+------------------------------------------------------------------+
