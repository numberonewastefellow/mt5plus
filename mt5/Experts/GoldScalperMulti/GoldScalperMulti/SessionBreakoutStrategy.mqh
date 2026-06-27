//+------------------------------------------------------------------+
//| SessionBreakoutStrategy.mqh — Asian range breakout for Gold      |
//|                                                                  |
//| Builds Asian session range (high/low), then trades breakouts     |
//| during London and NY sessions with volume confirmation.          |
//+------------------------------------------------------------------+
#ifndef SESSION_BREAKOUT_MQH
#define SESSION_BREAKOUT_MQH

#include "Defines.mqh"
#include "Structs.mqh"
#include "Utils.mqh"

//+------------------------------------------------------------------+
//| Build or update Asian session range from M15 candles             |
//+------------------------------------------------------------------+
void UpdateAsianRange(SessionState &state)
{
   MqlDateTime dt;
   TimeCurrent(dt);

   // Get today's Asian session candles
   MqlDateTime start_dt = dt;
   start_dt.hour = InpAsianStart_Hour;
   start_dt.min  = 0;
   start_dt.sec  = 0;
   datetime start_time = StructToTime(start_dt);

   MqlDateTime end_dt = dt;
   end_dt.hour = InpAsianEnd_Hour;
   end_dt.min  = 0;
   end_dt.sec  = 0;
   datetime end_time = StructToTime(end_dt);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, PERIOD_M15, start_time, end_time, rates);

   if(copied <= 0) return;

   double high = 0, low = 999999;
   double vol_sum = 0;

   for(int i = 0; i < copied; i++)
   {
      if(rates[i].high > high) high = rates[i].high;
      if(rates[i].low  < low)  low  = rates[i].low;
      vol_sum += (double)rates[i].tick_volume;
   }

   state.asian_high       = high;
   state.asian_low        = low;
   state.avg_tick_volume  = vol_sum / (double)copied;
   state.vol_sum          = vol_sum;
   state.vol_count        = copied;
}

//+------------------------------------------------------------------+
//| Process Session Breakout — called on each new M1 bar             |
//+------------------------------------------------------------------+
StrategySignal ProcessBreakout(SessionState &state)
{
   StrategySignal sig;
   sig.Reset();
   sig.strategy = STRAT_BREAKOUT;

   // Daily reset check
   MqlDateTime dt;
   TimeCurrent(dt);

   datetime today_date;
   MqlDateTime today_dt = dt;
   today_dt.hour = 0;
   today_dt.min  = 0;
   today_dt.sec  = 0;
   today_date = StructToTime(today_dt);

   if(state.last_range_date != today_date)
   {
      state.Reset();
      state.last_range_date = today_date;
   }

   // Phase 1: During Asian session, build the range
   if(InAsianSession())
   {
      UpdateAsianRange(state);
      return sig; // No signals during Asian session
   }

   // Mark Asian range as complete once Asian ends
   int hour, minute;
   GetBrokerTime(hour, minute);
   if(hour >= InpAsianEnd_Hour && !state.asian_range_set)
   {
      UpdateAsianRange(state); // Final update
      state.asian_range_set = true;
      if(state.asian_high > 0 && state.asian_low < 999999)
      {
         PrintFormat("[BREAKOUT] Asian range set: High=%.2f Low=%.2f Range=%.2f",
                     state.asian_high, state.asian_low,
                     state.asian_high - state.asian_low);
      }
   }

   if(!state.asian_range_set)
      return sig;

   // Validate range
   double range = state.asian_high - state.asian_low;
   if(range <= 0 || range > 50.0) // Sanity check: gold shouldn't have >$50 Asian range
      return sig;

   // Check if we're in an active session
   bool in_london = InLondonSession();
   bool in_ny     = InNYSession();
   if(!in_london && !in_ny)
      return sig;

   // Current price
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(bid <= 0)
      return sig;

   // Volume spike check (current M1 bar vs Asian average)
   long current_vol  = iVolume(_Symbol, PERIOD_M1, 0);
   bool vol_spike    = (state.avg_tick_volume > 0 &&
                        current_vol > state.avg_tick_volume * InpBreakout_VolSpike);

   // --- Detect breakout ---
   ENUM_SIGNAL_DIR direction = SIGNAL_NONE;
   string reason = "";

   // BUY breakout: above Asian high + buffer
   if(bid > state.asian_high + InpBreakout_Buffer)
   {
      // Check session dedup
      if(in_london && state.london_breakout_fired)
         { /* skip */ }
      else if(in_ny && !in_london && state.ny_breakout_fired)
         { /* skip */ }
      else
      {
         direction = SIGNAL_BUY;
         if(in_london) state.london_breakout_fired = true;
         if(in_ny)     state.ny_breakout_fired     = true;
         reason = StringFormat("BUY breakout above Asian high %.2f + buffer %.2f (bid=%.2f, range=%.2f)",
                               state.asian_high, InpBreakout_Buffer, bid, range);
      }
   }
   // SELL breakout: below Asian low - buffer
   else if(bid < state.asian_low - InpBreakout_Buffer)
   {
      if(in_london && state.london_breakout_fired)
         { /* skip */ }
      else if(in_ny && !in_london && state.ny_breakout_fired)
         { /* skip */ }
      else
      {
         direction = SIGNAL_SELL;
         if(in_london) state.london_breakout_fired = true;
         if(in_ny)     state.ny_breakout_fired     = true;
         reason = StringFormat("SELL breakout below Asian low %.2f - buffer %.2f (bid=%.2f, range=%.2f)",
                               state.asian_low, InpBreakout_Buffer, bid, range);
      }
   }

   if(direction == SIGNAL_NONE)
      return sig;

   // --- Confidence ---
   int confidence = 50;

   if(vol_spike)
      confidence += 15;

   // Strong break (2x buffer distance)
   if(direction == SIGNAL_BUY && bid > state.asian_high + InpBreakout_Buffer * 2)
      confidence += 10;
   if(direction == SIGNAL_SELL && bid < state.asian_low - InpBreakout_Buffer * 2)
      confidence += 10;

   // London+NY overlap = best liquidity
   if(in_london && in_ny)
      confidence += 10;

   // Narrow Asian range (tighter consolidation = stronger breakout)
   if(range < 10.0) // Less than $10 Asian range
      confidence += 5;

   // --- Build signal ---
   sig.direction  = direction;
   sig.confidence = MathMin(confidence, 100);
   sig.timestamp  = TimeCurrent();
   sig.is_valid   = true;
   sig.reason     = reason;

   return sig;
}

//+------------------------------------------------------------------+
//| Reset session state                                              |
//+------------------------------------------------------------------+
void ResetSessionState(SessionState &state)
{
   state.Reset();
}

#endif
