//+------------------------------------------------------------------+
//| Structs.mqh — Data structures for Gold Scalper EA                |
//+------------------------------------------------------------------+
#ifndef STRUCTS_MQH
#define STRUCTS_MQH

#include "Defines.mqh"

//--- Strategy signal output
struct StrategySignal
{
   ENUM_STRATEGY    strategy;
   ENUM_SIGNAL_DIR  direction;
   int              confidence;    // 0-100
   string           reason;
   datetime         timestamp;
   bool             is_valid;

   void Reset()
   {
      strategy   = STRAT_VPIN;
      direction  = SIGNAL_NONE;
      confidence = 0;
      reason     = "";
      timestamp  = 0;
      is_valid   = false;
   }
};

//--- VPIN volume bucket
struct VPINBucket
{
   double buy_volume;
   double sell_volume;
   double total_volume;
   double imbalance;   // abs(buy - sell) / total
};

//--- VPIN persistent state
struct VPINState
{
   double     current_buy;
   double     current_sell;
   double     current_vol;
   VPINBucket completed[MAX_BUCKETS];   // ring buffer
   int        write_idx;                // next write position
   int        count;                    // total completed buckets
   double     prev_tick_price;
   bool       prev_tick_set;
   bool       tick_flags_work;          // auto-detect if broker sends buy/sell flags
   int        tick_flags_tested;        // how many ticks tested
   int        tick_flags_found;         // how many had buy/sell flags

   void Reset()
   {
      current_buy       = 0;
      current_sell      = 0;
      current_vol       = 0;
      write_idx         = 0;
      count             = 0;
      prev_tick_price   = 0;
      prev_tick_set     = false;
      tick_flags_work   = true;
      tick_flags_tested = 0;
      tick_flags_found  = 0;
   }
};

//--- VWAP persistent state (reset at session open)
struct VWAPState
{
   double   vwap;
   double   std_dev;
   double   upper_band;
   double   lower_band;
   int      candle_count;
   datetime session_start;
   double   prev_close;
   double   prev_prev_close;
   bool     was_below_lower;
   bool     was_above_upper;
   datetime last_bar_time;

   void Reset()
   {
      vwap            = 0;
      std_dev         = 0;
      upper_band      = 0;
      lower_band      = 0;
      candle_count    = 0;
      session_start   = 0;
      prev_close      = 0;
      prev_prev_close = 0;
      was_below_lower = false;
      was_above_upper = false;
      last_bar_time   = 0;
   }
};

//--- Session Breakout state
struct SessionState
{
   double   asian_high;
   double   asian_low;
   bool     asian_range_set;
   double   avg_tick_volume;
   double   vol_sum;
   int      vol_count;
   bool     london_breakout_fired;
   bool     ny_breakout_fired;
   datetime last_range_date;

   void Reset()
   {
      asian_high            = 0;
      asian_low             = 999999;
      asian_range_set       = false;
      avg_tick_volume       = 0;
      vol_sum               = 0;
      vol_count             = 0;
      london_breakout_fired = false;
      ny_breakout_fired     = false;
      last_range_date       = 0;
   }
};

//--- Managed trade for exit management
struct ManagedTrade
{
   ulong    ticket;
   int      strategy_id;
   double   open_price;
   double   initial_sl;
   double   initial_tp1;    // $5 level
   double   initial_tp2;    // $10 level
   double   lots;
   bool     tp1_hit;
   bool     breakeven_set;
   bool     trailing_active;
   double   highest_profit;
   datetime open_time;
   bool     active;

   void Reset()
   {
      ticket          = 0;
      strategy_id     = 0;
      open_price      = 0;
      initial_sl      = 0;
      initial_tp1     = 0;
      initial_tp2     = 0;
      lots            = 0;
      tp1_hit         = false;
      breakeven_set   = false;
      trailing_active = false;
      highest_profit  = 0;
      open_time       = 0;
      active          = false;
   }
};

//--- Daily PnL tracking
struct DailyStats
{
   double   realized_pnl;
   int      trades_taken;
   int      wins;
   int      losses;
   datetime last_reset_date;

   void Reset()
   {
      realized_pnl    = 0;
      trades_taken    = 0;
      wins            = 0;
      losses          = 0;
      last_reset_date = 0;
   }
};

#endif
