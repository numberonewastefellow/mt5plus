//+------------------------------------------------------------------+
//| Structs.mqh — Data structures for Gold Breakout Grid EA          |
//+------------------------------------------------------------------+
#ifndef STRUCTS_MQH
#define STRUCTS_MQH

#include "Defines.mqh"

//+------------------------------------------------------------------+
//| Breakout signal from detector                                    |
//+------------------------------------------------------------------+
struct BreakoutSignal
{
   ENUM_SIGNAL_DIR direction;
   int             confidence;
   string          reason;
   double          asian_high;
   double          asian_low;
   double          asian_range;
   double          atr_value;
   double          tp_distance;     // ATR-based TP
   double          grid_spacing;    // ATR-based grid spacing
   bool            is_valid;
   datetime        timestamp;

   void Reset()
   {
      direction   = SIGNAL_NONE;
      confidence  = 0;
      reason      = "";
      asian_high  = 0;
      asian_low   = 0;
      asian_range = 0;
      atr_value   = 0;
      tp_distance = 0;
      grid_spacing= 0;
      is_valid    = false;
      timestamp   = 0;
   }
};

//+------------------------------------------------------------------+
//| Asian range state — built during Asian session                   |
//+------------------------------------------------------------------+
struct AsianRangeState
{
   double   high;
   double   low;
   bool     range_set;          // True after Asian session ends
   double   vol_sum;            // Cumulative tick volume
   int      vol_count;          // Number of bars counted
   double   avg_volume;         // Average tick volume
   bool     london_fired;       // One breakout per London session
   bool     ny_fired;           // One breakout per NY session
   datetime last_range_date;    // Date of last range build

   void Reset()
   {
      high           = 0;
      low            = 999999;
      range_set      = false;
      vol_sum        = 0;
      vol_count      = 0;
      avg_volume     = 0;
      london_fired   = false;
      ny_fired       = false;
      last_range_date= 0;
   }
};

//+------------------------------------------------------------------+
//| Single position within a grid group                              |
//+------------------------------------------------------------------+
struct GridPosition
{
   ulong    ticket;
   double   open_price;
   double   volume;
   datetime open_time;
   bool     active;        // Still open in MT5

   void Reset()
   {
      ticket     = 0;
      open_price = 0;
      volume     = 0;
      open_time  = 0;
      active     = false;
   }
};

//+------------------------------------------------------------------+
//| Grid group — all linked positions from one breakout              |
//+------------------------------------------------------------------+
struct GridGroup
{
   GridPosition    positions[MAX_GRID_POSITIONS];
   int             position_count;
   ENUM_SIGNAL_DIR direction;
   double          avg_entry;           // Volume-weighted average
   double          total_lots;
   double          group_tp;            // avg_entry ± offset
   double          basket_pnl;          // Current floating P&L ($)
   double          atr_at_entry;        // ATR(14) when grid opened
   double          grid_spacing;        // ATR × mult (fixed at entry)
   double          initial_tp_distance; // ATR-based TP for initial trade
   int             grid_level;          // 0=initial only, 1-4=adds
   double          last_grid_price;     // Price of most recent entry
   datetime        first_entry_time;
   ENUM_GRID_STATUS status;
   bool            grid_adds_halted;    // Time decay flag

   void Reset()
   {
      for(int i = 0; i < MAX_GRID_POSITIONS; i++)
         positions[i].Reset();
      position_count     = 0;
      direction          = SIGNAL_NONE;
      avg_entry          = 0;
      total_lots         = 0;
      group_tp           = 0;
      basket_pnl         = 0;
      atr_at_entry       = 0;
      grid_spacing       = 0;
      initial_tp_distance= 0;
      grid_level         = 0;
      last_grid_price    = 0;
      first_entry_time   = 0;
      status             = GRID_INACTIVE;
      grid_adds_halted   = false;
   }
};

//+------------------------------------------------------------------+
//| Daily statistics                                                 |
//+------------------------------------------------------------------+
struct DailyStats
{
   double   realized_pnl;
   int      grids_opened;     // Grid groups started today
   int      wins;
   int      losses;
   datetime last_reset_date;

   void Reset()
   {
      realized_pnl    = 0;
      grids_opened    = 0;
      wins            = 0;
      losses          = 0;
      last_reset_date = 0;
   }
};

#endif
