//+------------------------------------------------------------------+
//| VPINStrategy.mqh — VPIN Flow Toxicity for Gold                   |
//| Ported from strategies/vpin_flow.py                              |
//|                                                                  |
//| Classifies each tick as buy/sell using TICK_FLAG or Lee-Ready,   |
//| accumulates into volume buckets, computes VPIN z-score.          |
//+------------------------------------------------------------------+
#ifndef VPIN_STRATEGY_MQH
#define VPIN_STRATEGY_MQH

#include "Defines.mqh"
#include "Structs.mqh"

//+------------------------------------------------------------------+
//| Add a completed bucket to ring buffer                            |
//+------------------------------------------------------------------+
void VPIN_AddBucket(VPINState &state, double buy_vol, double sell_vol, double total_vol)
{
   int idx = state.write_idx % MAX_BUCKETS;
   state.completed[idx].buy_volume   = buy_vol;
   state.completed[idx].sell_volume  = sell_vol;
   state.completed[idx].total_volume = total_vol;

   double imbalance = 0;
   if(total_vol > 0)
      imbalance = MathAbs(buy_vol - sell_vol) / total_vol;
   state.completed[idx].imbalance = imbalance;

   state.write_idx++;
   if(state.count < MAX_BUCKETS)
      state.count++;
}

//+------------------------------------------------------------------+
//| Get bucket at position (0 = oldest available)                    |
//+------------------------------------------------------------------+
bool VPIN_GetBucket(const VPINState &state, int pos, VPINBucket &bucket)
{
   if(pos < 0 || pos >= state.count)
      return false;

   int start_idx;
   if(state.count < MAX_BUCKETS)
      start_idx = pos;
   else
      start_idx = (state.write_idx + pos) % MAX_BUCKETS;

   bucket = state.completed[start_idx];
   return true;
}

//+------------------------------------------------------------------+
//| Auto-detect if tick flags work on this broker                    |
//+------------------------------------------------------------------+
void VPIN_TestTickFlags(VPINState &state, const MqlTick &tick)
{
   if(state.tick_flags_tested >= 100) return; // already decided

   state.tick_flags_tested++;
   if(((tick.flags & TICK_FLAG_BUY) != 0) || ((tick.flags & TICK_FLAG_SELL) != 0))
      state.tick_flags_found++;

   // After 100 ticks, decide
   if(state.tick_flags_tested >= 100)
   {
      // If less than 10% of ticks have buy/sell flags, broker doesn't support them
      state.tick_flags_work = (state.tick_flags_found > 10);
      if(!state.tick_flags_work)
         PrintFormat("[VPIN] Tick flags not supported by broker — using Lee-Ready classification (%d/%d)",
                     state.tick_flags_found, state.tick_flags_tested);
      else
         PrintFormat("[VPIN] Tick flags active — %d/%d ticks have buy/sell flags",
                     state.tick_flags_found, state.tick_flags_tested);
   }
}

//+------------------------------------------------------------------+
//| Process one tick — classify and accumulate into buckets          |
//+------------------------------------------------------------------+
StrategySignal ProcessVPINTick(VPINState &state)
{
   StrategySignal sig;
   sig.Reset();
   sig.strategy = STRAT_VPIN;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return sig;

   double price = tick.last;
   if(price <= 0)
      price = (tick.bid + tick.ask) / 2.0; // CFD fallback (no last price)

   // Auto-detect tick flag support
   if(state.tick_flags_tested < 100)
      VPIN_TestTickFlags(state, tick);

   // --- Classify tick as buy or sell ---
   bool is_buy  = false;
   bool is_sell = false;

   bool use_flags = InpVPIN_UseTickFlags && state.tick_flags_work;

   if(use_flags)
   {
      // Method 1: Broker tick flags (most accurate)
      if((tick.flags & TICK_FLAG_BUY) != 0)  is_buy  = true;
      if((tick.flags & TICK_FLAG_SELL) != 0) is_sell = true;
   }
   else
   {
      // Method 2: Lee-Ready algorithm (compare to previous tick)
      if(state.prev_tick_set)
      {
         if(price > state.prev_tick_price)       is_buy  = true;
         else if(price < state.prev_tick_price)  is_sell = true;
         // If equal (zero-tick), skip classification
      }
   }

   state.prev_tick_price = price;
   state.prev_tick_set   = true;

   if(!is_buy && !is_sell)
      return sig; // zero-tick, skip

   // --- Accumulate into current bucket ---
   if(is_buy)  state.current_buy  += 1.0;
   if(is_sell) state.current_sell += 1.0;
   state.current_vol += 1.0;

   // --- Check if bucket is full ---
   double bucket_size = (double)InpVPIN_BucketSize;
   while(state.current_vol >= bucket_size)
   {
      double ratio = bucket_size / state.current_vol;
      double bucket_buy  = state.current_buy * ratio;
      double bucket_sell = state.current_sell * ratio;

      VPIN_AddBucket(state, bucket_buy, bucket_sell, bucket_size);

      // Carry over remainder
      double remainder = 1.0 - ratio;
      state.current_buy  *= remainder;
      state.current_sell *= remainder;
      state.current_vol  -= bucket_size;
   }

   // --- VPIN calculation ---
   if(state.count < InpVPIN_MinBuckets)
      return sig;

   // Get last `window` buckets
   int window = MathMin(InpVPIN_Window, state.count);
   int start_pos = state.count - window;

   // Calculate VPIN = mean of recent imbalances
   double sum_imbalance = 0;
   double sum_buy = 0, sum_sell = 0;
   VPINBucket b;

   for(int i = start_pos; i < state.count; i++)
   {
      if(!VPIN_GetBucket(state, i, b)) continue;
      sum_imbalance += b.imbalance;
      sum_buy       += b.buy_volume;
      sum_sell      += b.sell_volume;
   }

   double vpin = sum_imbalance / (double)window;

   // Z-score against all historical buckets
   double all_sum = 0, all_sq_sum = 0;
   for(int i = 0; i < state.count; i++)
   {
      if(!VPIN_GetBucket(state, i, b)) continue;
      all_sum    += b.imbalance;
      all_sq_sum += b.imbalance * b.imbalance;
   }

   double all_mean = all_sum / (double)state.count;
   double variance = (all_sq_sum / (double)state.count) - (all_mean * all_mean);
   if(variance <= 0)
      return sig;

   double std_dev = MathSqrt(variance);
   double z_score = (vpin - all_mean) / std_dev;

   if(z_score < InpVPIN_ZThreshold)
      return sig;

   // --- Direction from recent flow dominance ---
   // Require minimum 55% dominance to avoid noise (51/49 is meaningless)
   double total_flow = sum_buy + sum_sell;
   if(total_flow <= 0) return sig;

   double buy_pct  = sum_buy / total_flow;
   double sell_pct = sum_sell / total_flow;
   double min_dominance = 0.55; // 55% minimum

   ENUM_SIGNAL_DIR direction = SIGNAL_NONE;
   if(sell_pct >= min_dominance)
      direction = SIGNAL_SELL;
   else if(buy_pct >= min_dominance)
      direction = SIGNAL_BUY;
   else
      return sig; // ambiguous flow, skip

   // --- Confidence ---
   int confidence = 50;
   // Strongly elevated
   if(z_score > InpVPIN_ZThreshold + 0.5)
      confidence += 10;
   // Price momentum aligned (check last 30 seconds of price movement)
   double close_now  = iClose(_Symbol, PERIOD_M1, 0);
   double close_prev = iClose(_Symbol, PERIOD_M1, 1);
   if(close_now > 0 && close_prev > 0)
   {
      double momentum = close_now - close_prev;
      if((direction == SIGNAL_BUY && momentum > 0) ||
         (direction == SIGNAL_SELL && momentum < 0))
         confidence += 10;
   }
   // Volume above average (current bar vs 20-bar average)
   long current_vol_bar = iVolume(_Symbol, PERIOD_M1, 0);
   double avg_vol = 0;
   for(int i = 1; i <= 20; i++)
      avg_vol += (double)iVolume(_Symbol, PERIOD_M1, i);
   avg_vol /= 20.0;
   if(current_vol_bar > avg_vol * 1.3)
      confidence += 10;

   // --- Build signal ---
   sig.direction  = direction;
   sig.confidence = MathMin(confidence, 100);
   sig.timestamp  = TimeCurrent();
   sig.is_valid   = true;
   sig.reason     = StringFormat("VPIN=%.3f z=%+.2f buckets=%d buy=%.0f sell=%.0f",
                                 vpin, z_score, state.count, sum_buy, sum_sell);

   return sig;
}

//+------------------------------------------------------------------+
//| Reset VPIN state (called on daily reset)                         |
//+------------------------------------------------------------------+
void ResetVPINState(VPINState &state)
{
   state.Reset();
}

#endif
