//+------------------------------------------------------------------+
//| GoldScalperMulti.mq5                                             |
//| Gold Scalper Multi-Strategy EA for Exness XAUUSDm                |
//|                                                                  |
//| Combines VPIN Flow Toxicity, VWAP Scalper, Session Breakout      |
//| into a single EA with individual strategy toggles.               |
//|                                                                  |
//| Ported from Python option_chain_dashboard strategies:            |
//|   - strategies/vpin_flow.py → VPINStrategy.mqh                   |
//|   - indicators.py           → VWAPStrategy.mqh                   |
//|   - New                     → SessionBreakoutStrategy.mqh        |
//+------------------------------------------------------------------+
#property copyright "Option Chain Dashboard"
#property version   "1.00"
#property strict
#property description "Gold Scalper — VPIN + VWAP + Session Breakout"
#property description "Designed for XAUUSDm (Exness micro) — $5-$10 TP scalping"

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
//--- Strategy Toggles
input group "=== Strategy Toggles ==="
input bool   InpEnableVPIN          = true;    // Enable VPIN Flow Toxicity
input bool   InpEnableVWAP          = true;    // Enable VWAP Scalper
input bool   InpEnableBreakout      = true;    // Enable Session Breakout
input bool   InpEnableCombined      = true;    // Enable Combined Signal

//--- Asymmetric Hold Mode (tight TP, wide/no SL — hold losses, exit profits fast)
input group "=== Asymmetric Hold Mode ==="
input bool   InpAsymmetricMode      = false;   // Enable asymmetric hold (default OFF=scalp)
input double InpTP_Pct              = 0.15;    // TP as % of entry price (0.15%=~$4.5 gold)
input double InpSL_ATR_Mult         = 3.0;     // SL = N × ATR(14) (0=no stop loss)
input bool   InpAutoLotSize         = false;   // Auto calc lots from risk % (false=use fixed)
input double InpRiskPercent         = 1.0;     // Risk % of equity per trade (for auto lot)

//--- Trade Settings
input group "=== Trade Settings ==="
input double InpLotSize             = 0.01;    // Lot size per trade
input double InpTP1_Dollars         = 5.0;     // Take Profit 1 ($) — partial close
input double InpTP2_Dollars         = 10.0;    // Take Profit 2 ($) — full close
input double InpSafetySL_Dollars    = 75.0;    // Emergency safety SL ($) (0=no SL)
input int    InpMaxConcurrentTrades = 1;       // Max open trades
input int    InpMaxDailyTrades      = 2;       // Max trades per day (all modes)
input double InpMaxDailyLoss        = 50.0;    // Max daily loss ($)
input int    InpSlippage            = 50;      // Slippage (points)

//--- Spread Filter
input group "=== Spread Filter ==="
input double InpMaxSpreadDollars    = 0.40;    // Max spread to enter ($)

//--- VPIN Parameters
input group "=== VPIN Strategy ==="
input int    InpVPIN_BucketSize     = 500;     // Ticks per volume bucket
input int    InpVPIN_Window         = 20;      // Rolling window of buckets
input double InpVPIN_ZThreshold     = 1.8;     // Z-score threshold
input int    InpVPIN_MinBuckets     = 10;      // Min buckets before signal
input bool   InpVPIN_UseTickFlags   = true;    // Use tick flags (auto-fallback)

//--- VWAP Parameters
input group "=== VWAP Strategy ==="
input ENUM_TIMEFRAMES InpVWAP_Timeframe = PERIOD_M5; // Candle timeframe
input double InpVWAP_StdMultiplier  = 2.0;    // Std dev band multiplier
input int    InpVWAP_MinCandles     = 5;       // Min candles before signal
input double InpVWAP_BounceConfirm  = 0.50;   // Min bounce ($) to confirm

//--- Session Breakout Parameters
input group "=== Session Breakout ==="
input double InpBreakout_Buffer     = 0.50;    // Buffer above/below range ($)
input double InpBreakout_VolSpike   = 1.5;     // Volume spike multiplier
input int    InpAsianStart_Hour     = 2;       // Asian start (broker time)
input int    InpAsianEnd_Hour       = 9;       // Asian end
input int    InpLondonStart_Hour    = 10;      // London start
input int    InpLondonEnd_Hour      = 14;      // London end
input int    InpNYStart_Hour        = 15;      // NY start hour
input int    InpNYStart_Min         = 30;      // NY start minute
input int    InpNYEnd_Hour          = 20;      // NY end

//--- Combined Signal
input group "=== Combined Signal ==="
input int    InpCombined_Threshold  = 120;     // Sum of confidences to trigger
input int    InpCombined_MinAgree   = 2;       // Min strategies agreeing
input int    InpCombined_HighConf   = 70;      // High confidence threshold

//--- Session Filter
input group "=== Session Filter ==="
input bool   InpSessionFilter       = true;    // Only trade London+NY (skip Asian noise)
input int    InpOverlapStart_Hour   = 15;      // Overlap start (broker time)
input int    InpOverlapEnd_Hour     = 18;      // Overlap end

//+------------------------------------------------------------------+
//| Includes                                                         |
//+------------------------------------------------------------------+
#include "GoldScalperMulti/Defines.mqh"
#include "GoldScalperMulti/Structs.mqh"
#include "GoldScalperMulti/Utils.mqh"
#include "GoldScalperMulti/VPINStrategy.mqh"
#include "GoldScalperMulti/VWAPStrategy.mqh"
#include "GoldScalperMulti/SessionBreakoutStrategy.mqh"
#include "GoldScalperMulti/CombinedSignal.mqh"
#include "GoldScalperMulti/ChartVisuals.mqh"
#include "GoldScalperMulti/TradeManager.mqh"

//+------------------------------------------------------------------+
//| Global state                                                     |
//+------------------------------------------------------------------+
VPINState     g_vpin_state;
VWAPState     g_vwap_state;
SessionState  g_session_state;
ManagedTrade  g_trades[MAX_MANAGED_TRADES];
int           g_trade_count = 0;
DailyStats    g_daily;

// Throttle: last entry check time
datetime      g_last_entry_check = 0;
// New bar detection
datetime      g_last_vwap_bar    = 0;
datetime      g_last_m1_bar      = 0;
// Cached individual signals (updated per bar, read by combined)
StrategySignal g_vpin_signal;
StrategySignal g_vwap_signal;
StrategySignal g_breakout_signal;

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   // Validate symbol
   if(StringFind(_Symbol, "XAUUSD") < 0 && StringFind(_Symbol, "Gold") < 0)
   {
      PrintFormat("WARNING: EA calibrated for XAUUSDm gold. Current: %s", _Symbol);
   }

   // Log symbol info
   double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double spread   = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   int    digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   PrintFormat("[INIT] Symbol=%s Contract=%.0f Digits=%d Spread=%.3f Lot=%.2f",
               _Symbol, contract, digits, spread, InpLotSize);
   PrintFormat("[INIT] TP1=$%.1f TP2=$%.1f SafetySL=$%.1f MaxDaily=%d",
               InpTP1_Dollars, InpTP2_Dollars, InpSafetySL_Dollars,
               InpMaxDailyTrades);
   PrintFormat("[INIT] Strategies: VPIN=%s VWAP=%s Breakout=%s Combined=%s",
               InpEnableVPIN ? "ON" : "OFF",
               InpEnableVWAP ? "ON" : "OFF",
               InpEnableBreakout ? "ON" : "OFF",
               InpEnableCombined ? "ON" : "OFF");

   // Initialize states
   ResetVPINState(g_vpin_state);
   ResetVWAPState(g_vwap_state);
   ResetSessionState(g_session_state);
   g_daily.Reset();
   g_daily.last_reset_date = TimeCurrent();
   g_trade_count = 0;

   g_vpin_signal.Reset();
   g_vwap_signal.Reset();
   g_breakout_signal.Reset();

   // Timer for periodic tasks (daily reset, status logging)
   EventSetTimer(1);

   // Force initial dashboard draw so it's visible immediately
   DrawDashboard(g_vpin_state, g_vwap_state, g_session_state, g_daily, 0);
   DrawSessionSeparators();
   ChartRedraw(0);
   Print("[INIT] Chart visuals initialized — objects: ", ObjectsTotal(0));

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   CleanupChartVisuals();
   PrintFormat("[DEINIT] Daily PnL=%.2f Trades=%d W=%d L=%d",
               g_daily.realized_pnl, g_daily.trades_taken,
               g_daily.wins, g_daily.losses);
}

//+------------------------------------------------------------------+
//| Tick handler — runs on every tick                                |
//+------------------------------------------------------------------+
void OnTick()
{
   // --- VPIN: Process every tick ---
   if(InpEnableVPIN)
   {
      StrategySignal vpin_tick = ProcessVPINTick(g_vpin_state);
      if(vpin_tick.is_valid)
         g_vpin_signal = vpin_tick;  // Update cached signal
   }

   // --- Manage open trades every tick (trailing SL, partial close) ---
   ManageOpenTrades(g_trades, g_trade_count, g_daily);

   // --- Entry decisions: throttle to once per second ---
   if(TimeCurrent() - g_last_entry_check < 1)
      return;
   g_last_entry_check = TimeCurrent();

   // --- VWAP: recalculate on new bar ---
   if(InpEnableVWAP)
   {
      datetime current_bar = iTime(_Symbol, InpVWAP_Timeframe, 0);
      if(current_bar != g_last_vwap_bar)
      {
         g_last_vwap_bar = current_bar;
         g_vwap_signal = ProcessVWAP(g_vwap_state);
      }
   }

   // --- Session Breakout: check on new M1 bar ---
   if(InpEnableBreakout)
   {
      datetime current_m1 = iTime(_Symbol, PERIOD_M1, 0);
      if(current_m1 != g_last_m1_bar)
      {
         g_last_m1_bar = current_m1;
         g_breakout_signal = ProcessBreakout(g_session_state);
      }
   }

   // --- Combined Signal ---
   StrategySignal combined_signal;
   combined_signal.Reset();

   if(InpEnableCombined)
   {
      StrategySignal all[3];
      all[0] = g_vpin_signal;
      all[1] = g_vwap_signal;
      all[2] = g_breakout_signal;
      combined_signal = EvaluateCombined(all, 3);
   }

   // --- Execute trades ---
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(InpAsymmetricMode)
   {
      // ═══ ASYMMETRIC MODE: VPIN is confirmation only, not standalone ═══
      // Only VWAP and Breakout can open trades.
      // VPIN boosts confidence (+15) if it agrees, blocks if it disagrees.

      // Process VWAP signal with VPIN filter
      if(g_vwap_signal.is_valid && g_vwap_signal.direction != SIGNAL_NONE)
      {
         // VPIN confirms: same direction = boost, opposite = block
         if(g_vpin_signal.is_valid && g_vpin_signal.direction != SIGNAL_NONE)
         {
            if(g_vpin_signal.direction == g_vwap_signal.direction)
               g_vwap_signal.confidence = MathMin(100, g_vwap_signal.confidence + 15);
            else
            {
               // VPIN says opposite direction — skip this signal
               PrintFormat("[ASYM] VWAP %s blocked by VPIN %s",
                           g_vwap_signal.direction == SIGNAL_BUY ? "BUY" : "SELL",
                           g_vpin_signal.direction == SIGNAL_BUY ? "BUY" : "SELL");
               g_vwap_signal.Reset();
            }
         }

         if(g_vwap_signal.is_valid)
         {
            LogSignal(g_vwap_signal);
            DrawSignalArrow(g_vwap_signal, bid);
            PlaceTrade(g_vwap_signal, g_trades, g_trade_count, g_daily);
            g_vwap_signal.Reset();
         }
      }

      // Process Breakout signal with VPIN filter
      if(g_breakout_signal.is_valid && g_breakout_signal.direction != SIGNAL_NONE)
      {
         if(g_vpin_signal.is_valid && g_vpin_signal.direction != SIGNAL_NONE)
         {
            if(g_vpin_signal.direction == g_breakout_signal.direction)
               g_breakout_signal.confidence = MathMin(100, g_breakout_signal.confidence + 15);
            else
            {
               PrintFormat("[ASYM] Breakout %s blocked by VPIN %s",
                           g_breakout_signal.direction == SIGNAL_BUY ? "BUY" : "SELL",
                           g_vpin_signal.direction == SIGNAL_BUY ? "BUY" : "SELL");
               g_breakout_signal.Reset();
            }
         }

         if(g_breakout_signal.is_valid)
         {
            LogSignal(g_breakout_signal);
            DrawSignalArrow(g_breakout_signal, bid);
            PlaceTrade(g_breakout_signal, g_trades, g_trade_count, g_daily);
            g_breakout_signal.Reset();
         }
      }

      g_vpin_signal.Reset(); // VPIN consumed as filter, never trades alone
   }
   else
   {
      // ═══ SCALP MODE: all strategies trade independently ═══
      // Combined gets priority when enabled
      if(InpEnableCombined && combined_signal.is_valid && combined_signal.direction != SIGNAL_NONE)
      {
         LogSignal(combined_signal);
         DrawSignalArrow(combined_signal, bid);
         PlaceTrade(combined_signal, g_trades, g_trade_count, g_daily);
      }
      else
      {
         if(g_vpin_signal.is_valid && g_vpin_signal.direction != SIGNAL_NONE)
         {
            LogSignal(g_vpin_signal);
            DrawSignalArrow(g_vpin_signal, bid);
            PlaceTrade(g_vpin_signal, g_trades, g_trade_count, g_daily);
            g_vpin_signal.Reset();
         }
         if(g_vwap_signal.is_valid && g_vwap_signal.direction != SIGNAL_NONE)
         {
            LogSignal(g_vwap_signal);
            DrawSignalArrow(g_vwap_signal, bid);
            PlaceTrade(g_vwap_signal, g_trades, g_trade_count, g_daily);
            g_vwap_signal.Reset();
         }
         if(g_breakout_signal.is_valid && g_breakout_signal.direction != SIGNAL_NONE)
         {
            LogSignal(g_breakout_signal);
            DrawSignalArrow(g_breakout_signal, bid);
            PlaceTrade(g_breakout_signal, g_trades, g_trade_count, g_daily);
            g_breakout_signal.Reset();
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Timer handler — periodic tasks                                   |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Daily reset
   CheckDailyReset(g_daily);

   // Reset VWAP at session change (Asian open)
   int hour, minute;
   GetBrokerTime(hour, minute);
   if(hour == InpAsianStart_Hour && minute == 0)
   {
      static datetime last_vwap_reset = 0;
      if(TimeCurrent() - last_vwap_reset > 3600)
      {
         ResetVWAPState(g_vwap_state);
         last_vwap_reset = TimeCurrent();
         Print("[VWAP] Session reset at Asian open");
      }
   }

   // --- Chart visuals (update every 5 seconds) ---
   static int timer_ticks = 0;
   timer_ticks++;

   if(timer_ticks % 5 == 0)
   {
      // Always draw dashboard (pixel-based, always visible)
      DrawDashboard(g_vpin_state, g_vwap_state, g_session_state,
                    g_daily, CountOpenTrades());

      // Draw session separators (vertical lines)
      DrawSessionSeparators();

      // Draw VWAP bands on chart (draws from candle data directly)
      if(InpEnableVWAP)
         DrawVWAPBands(g_vwap_state, InpVWAP_Timeframe);

      // Draw Asian range
      if(InpEnableBreakout)
         DrawAsianRange(g_session_state);

      // Draw TP1 levels for active trades
      DrawTP1Levels(g_trades, g_trade_count);

      ChartRedraw(0);

      // Debug: log object count every 30 seconds
      if(timer_ticks % 30 == 0)
      {
         int obj_count = ObjectsTotal(0);
         PrintFormat("[CHART] Objects: %d | VWAP=%.2f candles=%d | Asian=%.2f/%.2f set=%s",
                     obj_count, g_vwap_state.vwap, g_vwap_state.candle_count,
                     g_session_state.asian_high, g_session_state.asian_low,
                     g_session_state.asian_range_set ? "YES" : "NO");
      }
   }

   // Status log every 5 minutes
   if(timer_ticks % 300 == 0)
   {
      PrintFormat("[STATUS] Daily PnL=%.2f Trades=%d Open=%d VPIN_buckets=%d VWAP=%.2f Asian=%.2f/%.2f",
                  g_daily.realized_pnl, g_daily.trades_taken,
                  CountOpenTrades(), g_vpin_state.count,
                  g_vwap_state.vwap,
                  g_session_state.asian_high, g_session_state.asian_low);
   }
}

//+------------------------------------------------------------------+
//| Trade transaction handler — track position closes                |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   // Log fills for our magic number
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      ulong deal = trans.deal;
      if(deal > 0)
      {
         if(HistoryDealSelect(deal))
         {
            long magic = HistoryDealGetInteger(deal, DEAL_MAGIC);
            if(magic == MAGIC_NUMBER)
            {
               double price  = HistoryDealGetDouble(deal, DEAL_PRICE);
               double volume = HistoryDealGetDouble(deal, DEAL_VOLUME);
               double profit = HistoryDealGetDouble(deal, DEAL_PROFIT);
               string comment = HistoryDealGetString(deal, DEAL_COMMENT);

               PrintFormat("[DEAL] price=%.2f vol=%.2f profit=%.2f %s",
                           price, volume, profit, comment);
            }
         }
      }
   }
}
//+------------------------------------------------------------------+
