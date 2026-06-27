//+------------------------------------------------------------------+
//| BreakoutDetector.mqh — Asian range build + London/NY breakout    |
//|                                                                  |
//| Phase 1: During Asian session, track high/low/volume             |
//| Phase 2: After Asian close, detect breakouts in London/NY        |
//| Applies: trend filter, volume spike, confidence scoring          |
//+------------------------------------------------------------------+
#ifndef BREAKOUTDETECTOR_MQH
#define BREAKOUTDETECTOR_MQH

#include "Defines.mqh"
#include "Structs.mqh"
#include "Utils.mqh"
#include "TrendFilter.mqh"

// Inputs are declared in GoldBreakoutGrid.mq5 and available here
// via include ordering (this file is included after input declarations)

//+------------------------------------------------------------------+
//| Build Asian range from M15 bars during Asian session             |
//+------------------------------------------------------------------+
void UpdateAsianRange(AsianRangeState &state,
                      int asian_start, int asian_end)
{
   // Calculate how many M15 bars span the Asian session
   int session_hours = asian_end - asian_start;
   int bars_needed   = session_hours * 4 + 4;  // extra buffer

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_M15, 0, bars_needed, rates);
   if(copied < 4)
      return;

   // Find bars within Asian session today
   MqlDateTime now_dt;
   TimeCurrent(now_dt);

   double high = 0;
   double low  = 999999;
   double vol_sum = 0;
   int    vol_count = 0;

   for(int i = 0; i < copied; i++)
   {
      MqlDateTime bar_dt;
      TimeToStruct(rates[i].time, bar_dt);

      // Only today's bars
      if(bar_dt.day != now_dt.day || bar_dt.mon != now_dt.mon)
         continue;

      // Only Asian session hours
      if(bar_dt.hour < asian_start || bar_dt.hour >= asian_end)
         continue;

      if(rates[i].high > high) high = rates[i].high;
      if(rates[i].low  < low)  low  = rates[i].low;
      vol_sum += (double)rates[i].tick_volume;
      vol_count++;
   }

   if(vol_count > 0)
   {
      state.high       = high;
      state.low        = low;
      state.vol_sum    = vol_sum;
      state.vol_count  = vol_count;
      state.avg_volume = vol_sum / vol_count;
   }
}

//+------------------------------------------------------------------+
//| Main breakout detection — call once per M1 bar                   |
//+------------------------------------------------------------------+
BreakoutSignal DetectBreakout(AsianRangeState &state)
{
   BreakoutSignal sig;
   sig.Reset();

   int hour, minute;
   GetBrokerTime(hour, minute);

   // --- Daily reset ---
   datetime today = TimeCurrent();
   if(IsNewTradingDay(state.last_range_date))
   {
      state.Reset();
      state.last_range_date = today;
      PrintFormat("[BREAKOUT] New day — range reset");
   }

   // --- Phase 1: Asian session — build range, no signals ---
   if(InAsianSession(InpAsianStart, InpAsianEnd))
   {
      UpdateAsianRange(state, InpAsianStart, InpAsianEnd);
      return sig;  // No signals during Asian
   }

   // --- Finalize range once Asian ends ---
   if(!state.range_set && hour >= InpAsianEnd)
   {
      UpdateAsianRange(state, InpAsianStart, InpAsianEnd);

      if(state.high > 0 && state.low < 999999 && state.high > state.low)
      {
         state.range_set = true;
         double range = state.high - state.low;
         PrintFormat("[BREAKOUT] Asian range set: %.2f / %.2f (range=$%.2f, avg_vol=%.0f)",
                     state.high, state.low, range, state.avg_volume);
      }
      else
      {
         return sig;  // Invalid range
      }
   }

   // Need a valid range to detect breakouts
   if(!state.range_set)
      return sig;

   double range = state.high - state.low;

   // Sanity: range between $0.50 and $80
   if(range < 0.50 || range > 80.0)
      return sig;

   // --- Phase 2: Breakout detection during London / NY ---
   bool in_london = InLondonSession(InpLondonStart, InpLondonEnd);
   bool in_ny     = InNYSession(InpNYStart, InpNYEnd);

   if(!in_london && !in_ny)
      return sig;  // Outside trading sessions

   // Dedup: one signal per session per direction
   if(in_london && !in_ny && state.london_fired)
      return sig;
   if(in_ny && !in_london && state.ny_fired)
      return sig;
   if(in_london && in_ny && state.london_fired && state.ny_fired)
      return sig;

   // --- Pre-filters ---
   // Spread filter
   if(SpreadExceeded(InpMaxSpread))
      return sig;

   // News blackout
   if(InpNewsFilter && IsNewsBlackoutDay())
      return sig;

   // --- Check price vs range ---
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double buy_trigger  = state.high + InpBuffer;
   double sell_trigger = state.low  - InpBuffer;

   ENUM_SIGNAL_DIR dir = SIGNAL_NONE;

   if(bid > buy_trigger)
      dir = SIGNAL_BUY;
   else if(bid < sell_trigger)
      dir = SIGNAL_SELL;
   else
      return sig;  // Price inside range

   // --- Trend filter (hard gate) ---
   if(!IsTrendAligned(dir))
   {
      static datetime last_trend_block = 0;
      if(TimeCurrent() - last_trend_block > 300)  // Log once per 5 min
      {
         PrintFormat("[BREAKOUT] %s blocked by 200 EMA trend filter (EMA=%.2f bid=%.2f)",
                     DirToString(dir), GetEMAValue(), bid);
         last_trend_block = TimeCurrent();
      }
      return sig;
   }

   // --- Compute ATR for dynamic TP and grid spacing ---
   double atr = ComputeATR(InpATR_Period, InpATR_Timeframe);
   if(atr <= 0)
   {
      Print("[BREAKOUT] ATR not available, skipping signal");
      return sig;
   }

   // --- Overextension filter (legacy, disabled by default in v4.20) ---
   double slow_ema = GetEMASlow();
   if(slow_ema > 0 && atr > 0 && InpMaxEMADistance > 0)
   {
      double ema_distance = MathAbs(bid - slow_ema) / atr;
      if(ema_distance > InpMaxEMADistance)
      {
         static datetime last_overext_block = 0;
         if(TimeCurrent() - last_overext_block > 300)
         {
            PrintFormat("[BREAKOUT] %s blocked — overextended %.1fx ATR from EMA (max=%.1f) bid=%.2f ema=%.2f atr=%.2f",
                        DirToString(dir), ema_distance, InpMaxEMADistance, bid, slow_ema, atr);
            last_overext_block = TimeCurrent();
         }
         return sig;
      }
   }

   // --- EMA Fan Spread filter (v4.20): skip if trend is exhausting ---
   // Measures Fast-Mid EMA spread direction over InpFanLookback H4 bars.
   // Fan narrowing = trend losing steam = inflection point = BLOCK.
   // Catches Mar 12 BUY (-$500, fan collapsing) while allowing
   // Feb 25 BUY (+$106, fan expanding) at similar price levels.
   if(InpFanLookback > 0 && !IsEMAFanExpanding(dir, InpFanLookback))
   {
      static datetime last_fan_block = 0;
      if(TimeCurrent() - last_fan_block > 300)
      {
         double sp_now  = GetFanSpreadNow();
         double sp_prev = GetFanSpreadPrev(InpFanLookback);
         PrintFormat("[BREAKOUT] %s blocked — EMA fan narrowing (spread: %.2f → %.2f, lookback=%d bars) bid=%.2f",
                     DirToString(dir), sp_prev, sp_now, InpFanLookback, bid);
         last_fan_block = TimeCurrent();
      }
      return sig;
   }

   // --- Volume check ---
   MqlRates current_bar[];
   ArraySetAsSeries(current_bar, true);
   double current_vol = 0;
   if(CopyRates(_Symbol, PERIOD_M15, 0, 1, current_bar) > 0)
      current_vol = (double)current_bar[0].tick_volume;

   bool vol_spike = (state.avg_volume > 0 && current_vol > state.avg_volume * InpVolSpike);

   // --- Confidence scoring ---
   int confidence = 50;  // Base
   string filters = "";

   // +10: Breakout confirmed
   confidence += 10;
   filters += "breakout ";

   // +10: Volume spike
   if(vol_spike)
   {
      confidence += 10;
      filters += "vol_spike ";
   }

   // +10: Strong break (2x buffer distance)
   double break_distance = (dir == SIGNAL_BUY) ?
                           (bid - state.high) : (state.low - bid);
   if(break_distance > InpBuffer * 2.0)
   {
      confidence += 10;
      filters += "strong_break ";
   }

   // +10: London+NY overlap
   if(in_london && in_ny)
   {
      confidence += 10;
      filters += "overlap ";
   }

   // +10: Tight Asian range (< $8)
   if(range < 8.0)
   {
      confidence += 10;
      filters += "tight_range ";
   }

   // +10: Trend aligned (already confirmed, add points)
   confidence += 10;
   filters += "trend_aligned ";

   // +5: Low spread (< $0.25)
   if(GetSpreadDollars() < 0.25)
   {
      confidence += 5;
      filters += "low_spread ";
   }

   // --- Minimum confidence gate ---
   if(confidence < InpMinConfidence)
      return sig;

   // --- Dynamic TP and grid spacing ---
   double tp_dist     = Clamp(atr * InpTP_ATR_Mult, InpTP_Min, InpTP_Max);
   double grid_space  = atr * InpGrid_ATR_Mult;

   // --- Mark session as fired ---
   if(in_london) state.london_fired = true;
   if(in_ny)     state.ny_fired     = true;

   // --- Build signal ---
   sig.direction    = dir;
   sig.confidence   = MathMin(confidence, 100);
   sig.asian_high   = state.high;
   sig.asian_low    = state.low;
   sig.asian_range  = range;
   sig.atr_value    = atr;
   sig.tp_distance  = tp_dist;
   sig.grid_spacing = grid_space;
   sig.is_valid     = true;
   sig.timestamp    = TimeCurrent();

   sig.reason = StringFormat("%s break $%.2f | range=$%.2f | ATR=$%.2f | TP=$%.2f | grid=$%.2f | conf=%d [%s]",
                             DirToString(dir), break_distance, range, atr,
                             tp_dist, grid_space, confidence, filters);

   PrintFormat("[BREAKOUT] SIGNAL: %s", sig.reason);

   return sig;
}

#endif
