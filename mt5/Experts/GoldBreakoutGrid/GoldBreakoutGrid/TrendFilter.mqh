//+------------------------------------------------------------------+
//| TrendFilter.mqh — Triple EMA Crossover trend gate               |
//|                                                                  |
//| Fast(10) > Mid(30) > Slow(50) = UPTREND  → BUY only             |
//| Fast(10) < Mid(30) < Slow(50) = DOWNTREND → SELL only            |
//| Not aligned                   = CHOPPY   → Block all signals     |
//|                                                                  |
//| Replaces single EMA which had a "flat zone" problem:             |
//| price near single EMA allowed wrong-direction trades.            |
//| Triple alignment is much harder to fool during pullbacks.        |
//+------------------------------------------------------------------+
#ifndef TRENDFILTER_MQH
#define TRENDFILTER_MQH

#include "Defines.mqh"

//--- Persistent EMA indicator handles
int g_ema_fast_handle = INVALID_HANDLE;
int g_ema_mid_handle  = INVALID_HANDLE;
int g_ema_slow_handle = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Initialize the 3 EMA indicators (call in OnInit)                 |
//+------------------------------------------------------------------+
bool InitTrendFilter(int fast_period, int mid_period, int slow_period,
                     ENUM_TIMEFRAMES tf)
{
   g_ema_fast_handle = iMA(_Symbol, tf, fast_period, 0, MODE_EMA, PRICE_CLOSE);
   g_ema_mid_handle  = iMA(_Symbol, tf, mid_period, 0, MODE_EMA, PRICE_CLOSE);
   g_ema_slow_handle = iMA(_Symbol, tf, slow_period, 0, MODE_EMA, PRICE_CLOSE);

   if(g_ema_fast_handle == INVALID_HANDLE ||
      g_ema_mid_handle  == INVALID_HANDLE ||
      g_ema_slow_handle == INVALID_HANDLE)
   {
      PrintFormat("[TREND] Failed to create Triple EMA(%d/%d/%d) on %s",
                  fast_period, mid_period, slow_period, EnumToString(tf));
      return false;
   }

   PrintFormat("[TREND] Triple EMA(%d/%d/%d) on %s initialized",
               fast_period, mid_period, slow_period, EnumToString(tf));
   return true;
}

//+------------------------------------------------------------------+
//| Release all EMA indicators (call in OnDeinit)                    |
//+------------------------------------------------------------------+
void DeinitTrendFilter()
{
   if(g_ema_fast_handle != INVALID_HANDLE)
   {  IndicatorRelease(g_ema_fast_handle); g_ema_fast_handle = INVALID_HANDLE; }
   if(g_ema_mid_handle != INVALID_HANDLE)
   {  IndicatorRelease(g_ema_mid_handle);  g_ema_mid_handle = INVALID_HANDLE;  }
   if(g_ema_slow_handle != INVALID_HANDLE)
   {  IndicatorRelease(g_ema_slow_handle); g_ema_slow_handle = INVALID_HANDLE; }
}

//+------------------------------------------------------------------+
//| Read EMA value at a specific bar shift                           |
//+------------------------------------------------------------------+
double ReadEMAAtBar(int handle, int shift)
{
   if(handle == INVALID_HANDLE)
      return 0;
   double buf[];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(handle, 0, shift, 1, buf) < 1)
      return 0;
   return buf[0];
}

//+------------------------------------------------------------------+
//| Read a single EMA value (bar[1] = last completed bar)            |
//+------------------------------------------------------------------+
double _ReadEMA(int handle)
{
   return ReadEMAAtBar(handle, 1);
}

//+------------------------------------------------------------------+
//| Get all 3 EMA values                                             |
//+------------------------------------------------------------------+
double GetEMAFast()  { return _ReadEMA(g_ema_fast_handle); }
double GetEMAMid()   { return _ReadEMA(g_ema_mid_handle);  }
double GetEMASlow()  { return _ReadEMA(g_ema_slow_handle); }

// Backward compat: return slow EMA (used by dashboard for display)
double GetEMAValue() { return GetEMASlow(); }

//+------------------------------------------------------------------+
//| Determine allowed trade direction using Triple EMA alignment     |
//|                                                                  |
//| Fast > Mid > Slow = UPTREND   → SIGNAL_BUY                      |
//| Fast < Mid < Slow = DOWNTREND → SIGNAL_SELL                      |
//| Not aligned       = CHOPPY    → SIGNAL_NONE (block all)          |
//+------------------------------------------------------------------+
ENUM_SIGNAL_DIR GetTrendDirection()
{
   double fast = GetEMAFast();
   double mid  = GetEMAMid();
   double slow = GetEMASlow();

   // All 3 must be valid
   if(fast <= 0 || mid <= 0 || slow <= 0)
      return SIGNAL_NONE;

   // Triple alignment check
   if(fast > mid && mid > slow)
      return SIGNAL_BUY;   // Strong uptrend: Fast > Mid > Slow

   if(fast < mid && mid < slow)
      return SIGNAL_SELL;  // Strong downtrend: Fast < Mid < Slow

   return SIGNAL_NONE;     // Not aligned = choppy, block all signals
}

//+------------------------------------------------------------------+
//| Check if a signal direction is allowed by trend filter           |
//| SIGNAL_NONE = choppy → block (unlike v2.x which allowed both)    |
//+------------------------------------------------------------------+
bool IsTrendAligned(ENUM_SIGNAL_DIR signal_dir)
{
   ENUM_SIGNAL_DIR trend = GetTrendDirection();
   if(trend == SIGNAL_NONE)
      return false;   // Choppy / EMAs not aligned — block ALL signals
   return (signal_dir == trend);
}

//+------------------------------------------------------------------+
//| EMA Fan Spread Direction — detect trend exhaustion               |
//|                                                                  |
//| Measures if Fast EMA is pulling AWAY from Mid (healthy trend)    |
//| or COLLAPSING toward Mid (trend exhaustion / inflection).        |
//|                                                                  |
//| For BUY:  spread narrowing = Fast falling toward Mid = BLOCK     |
//| For SELL: spread narrowing = Fast rising toward Mid  = BLOCK     |
//|                                                                  |
//| Returns: true if fan is expanding (healthy), false if narrowing  |
//+------------------------------------------------------------------+
bool IsEMAFanExpanding(ENUM_SIGNAL_DIR dir, int lookback)
{
   double fast_now  = ReadEMAAtBar(g_ema_fast_handle, 1);
   double mid_now   = ReadEMAAtBar(g_ema_mid_handle, 1);
   double fast_prev = ReadEMAAtBar(g_ema_fast_handle, lookback);
   double mid_prev  = ReadEMAAtBar(g_ema_mid_handle, lookback);

   if(fast_now <= 0 || mid_now <= 0 || fast_prev <= 0 || mid_prev <= 0)
      return true;  // Data not available, allow trade

   double spread_now  = fast_now - mid_now;
   double spread_prev = fast_prev - mid_prev;

   if(dir == SIGNAL_BUY)
      return (spread_now >= spread_prev);   // BUY: spread must be widening (or stable)

   if(dir == SIGNAL_SELL)
      return (spread_now <= spread_prev);   // SELL: spread must be widening negative (or stable)

   return true;
}

//+------------------------------------------------------------------+
//| Get current fan spread values for dashboard display              |
//+------------------------------------------------------------------+
double GetFanSpreadNow(int lookback_unused = 0)
{
   double fast = ReadEMAAtBar(g_ema_fast_handle, 1);
   double mid  = ReadEMAAtBar(g_ema_mid_handle, 1);
   if(fast <= 0 || mid <= 0) return 0;
   return fast - mid;
}

double GetFanSpreadPrev(int lookback)
{
   double fast = ReadEMAAtBar(g_ema_fast_handle, lookback);
   double mid  = ReadEMAAtBar(g_ema_mid_handle, lookback);
   if(fast <= 0 || mid <= 0) return 0;
   return fast - mid;
}

#endif
