//+------------------------------------------------------------------+
//| Defines.mqh — Constants, enums, magic number                     |
//| Gold Scalper Multi-Strategy EA for Exness XAUUSDm                |
//+------------------------------------------------------------------+
#ifndef DEFINES_MQH
#define DEFINES_MQH

#define MAGIC_NUMBER       20260327
#define MAX_BUCKETS        200
#define MAX_MANAGED_TRADES 20

enum ENUM_SIGNAL_DIR
{
   SIGNAL_NONE =  0,
   SIGNAL_BUY  =  1,
   SIGNAL_SELL = -1
};

enum ENUM_STRATEGY
{
   STRAT_VPIN     = 0,
   STRAT_VWAP     = 1,
   STRAT_BREAKOUT = 2,
   STRAT_COMBINED = 3
};

#endif
