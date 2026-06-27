//+------------------------------------------------------------------+
//| VWAPStrategy.mqh — VWAP Scalper for Gold                         |
//| Ported from indicators.py compute_vwap / compute_vwap_bands      |
//|                                                                  |
//| Computes cumulative VWAP from session open with std dev bands.   |
//| BUY on bounce off lower band, SELL on rejection at upper band.   |
//+------------------------------------------------------------------+
#ifndef VWAP_STRATEGY_MQH
#define VWAP_STRATEGY_MQH

#include "Defines.mqh"
#include "Structs.mqh"

//+------------------------------------------------------------------+
//| Compute VWAP + bands from candle array                           |
//| Direct port of indicators.py compute_vwap()                      |
//+------------------------------------------------------------------+
void ComputeVWAP(MqlRates &rates[], int count,
                 double &out_vwap, double &out_std,
                 double &out_upper, double &out_lower,
                 double std_mult)
{
   out_vwap  = 0;
   out_std   = 0;
   out_upper = 0;
   out_lower = 0;

   if(count <= 0) return;

   double cum_tp_vol = 0;
   double cum_vol    = 0;

   // Pass 1: cumulative VWAP
   for(int i = 0; i < count; i++)
   {
      double tp  = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0) vol = 1.0; // fallback, same as Python
      cum_tp_vol += tp * vol;
      cum_vol    += vol;
   }

   if(cum_vol <= 0) return;
   out_vwap = cum_tp_vol / cum_vol;

   // Pass 2: volume-weighted standard deviation
   double cum_dev_sq = 0;
   double cum_vol2   = 0;
   for(int i = 0; i < count; i++)
   {
      double tp  = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0) vol = 1.0;
      double diff = tp - out_vwap;
      cum_dev_sq += diff * diff * vol;
      cum_vol2   += vol;
   }

   if(cum_vol2 > 0)
   {
      double variance = cum_dev_sq / cum_vol2;
      out_std = MathSqrt(variance);
   }

   out_upper = out_vwap + std_mult * out_std;
   out_lower = out_vwap - std_mult * out_std;
}

//+------------------------------------------------------------------+
//| Get session start time for today (Asian open = earliest)         |
//+------------------------------------------------------------------+
datetime GetSessionStart()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   dt.hour = InpAsianStart_Hour;
   dt.min  = 0;
   dt.sec  = 0;
   return StructToTime(dt);
}

//+------------------------------------------------------------------+
//| Process VWAP strategy — called on each new bar                   |
//+------------------------------------------------------------------+
StrategySignal ProcessVWAP(VWAPState &state)
{
   StrategySignal sig;
   sig.Reset();
   sig.strategy = STRAT_VWAP;

   // Get session candles
   datetime session_start = GetSessionStart();

   MqlRates rates[];
   ArraySetAsSeries(rates, false); // oldest first (chronological)
   int copied = CopyRates(_Symbol, InpVWAP_Timeframe, session_start, TimeCurrent(), rates);

   if(copied < InpVWAP_MinCandles)
      return sig;

   // Compute VWAP + bands
   double vwap, std_dev, upper, lower;
   ComputeVWAP(rates, copied, vwap, std_dev, upper, lower, InpVWAP_StdMultiplier);

   if(vwap <= 0 || std_dev <= 0)
      return sig;

   // Update state
   state.vwap        = vwap;
   state.std_dev     = std_dev;
   state.upper_band  = upper;
   state.lower_band  = lower;
   state.candle_count = copied;

   // Current and previous close
   double close      = rates[copied - 1].close;
   double prev_close = (copied >= 2) ? rates[copied - 2].close : 0;

   if(prev_close <= 0)
      return sig;

   // Track band touches
   if(prev_close <= lower)  state.was_below_lower = true;
   if(prev_close >= upper)  state.was_above_upper = true;

   // --- Mean-reversion signal detection ---
   ENUM_SIGNAL_DIR direction = SIGNAL_NONE;
   string reason = "";

   // BUY: price was below lower band and now bouncing up
   if(state.was_below_lower && close > lower && close > prev_close)
   {
      direction = SIGNAL_BUY;
      state.was_below_lower = false;
      reason = StringFormat("Price bounced off VWAP lower band (%.2f > %.2f, VWAP=%.2f)",
                            close, lower, vwap);
   }
   // SELL: price was above upper band and now reversing down
   else if(state.was_above_upper && close < upper && close < prev_close)
   {
      direction = SIGNAL_SELL;
      state.was_above_upper = false;
      reason = StringFormat("Price rejected at VWAP upper band (%.2f < %.2f, VWAP=%.2f)",
                            close, upper, vwap);
   }

   if(direction == SIGNAL_NONE)
      return sig;

   // --- Confidence ---
   int confidence = 50;

   // Bounce confirmation: price moved enough away from band
   if(direction == SIGNAL_BUY && close > lower + InpVWAP_BounceConfirm)
      confidence += 10;
   if(direction == SIGNAL_SELL && close < upper - InpVWAP_BounceConfirm)
      confidence += 10;

   // Volume spike on this bar
   long bar_vol  = rates[copied - 1].tick_volume;
   double avg_vol = 0;
   for(int i = MathMax(0, copied - 21); i < copied - 1; i++)
      avg_vol += (double)rates[i].tick_volume;
   int vol_bars = MathMin(20, copied - 1);
   if(vol_bars > 0) avg_vol /= vol_bars;
   if(bar_vol > avg_vol * 1.3)
      confidence += 10;

   // Price near VWAP midline (stronger mean-reversion)
   double dist_from_vwap = MathAbs(close - vwap);
   if(dist_from_vwap < std_dev * 0.5)
      confidence += 10;

   // --- Build signal ---
   sig.direction  = direction;
   sig.confidence = MathMin(confidence, 100);
   sig.timestamp  = TimeCurrent();
   sig.is_valid   = true;
   sig.reason     = reason;

   return sig;
}

//+------------------------------------------------------------------+
//| Reset VWAP state (called at session open)                        |
//+------------------------------------------------------------------+
void ResetVWAPState(VWAPState &state)
{
   state.Reset();
}

#endif
