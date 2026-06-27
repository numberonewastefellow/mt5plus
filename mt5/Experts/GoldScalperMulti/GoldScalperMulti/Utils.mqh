//+------------------------------------------------------------------+
//| Utils.mqh — Spread filter, session checks, filling mode, logging |
//+------------------------------------------------------------------+
#ifndef UTILS_MQH
#define UTILS_MQH

#include "Defines.mqh"

//+------------------------------------------------------------------+
//| Spread filter — block entry when spread too wide                 |
//+------------------------------------------------------------------+
bool SpreadExceeded()
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double spread = ask - bid;
   return (spread > InpMaxSpreadDollars);
}

//+------------------------------------------------------------------+
//| Get current broker hour/minute from TimeCurrent                  |
//+------------------------------------------------------------------+
void GetBrokerTime(int &hour, int &minute)
{
   MqlDateTime dt;
   TimeCurrent(dt);
   hour   = dt.hour;
   minute = dt.min;
}

//+------------------------------------------------------------------+
//| Check if currently in London+NY overlap session                  |
//+------------------------------------------------------------------+
bool InOverlapSession()
{
   int hour, minute;
   GetBrokerTime(hour, minute);
   return (hour >= InpOverlapStart_Hour && hour < InpOverlapEnd_Hour);
}

//+------------------------------------------------------------------+
//| Check if in London session                                       |
//+------------------------------------------------------------------+
bool InLondonSession()
{
   int hour, minute;
   GetBrokerTime(hour, minute);
   return (hour >= InpLondonStart_Hour && hour < InpLondonEnd_Hour);
}

//+------------------------------------------------------------------+
//| Check if in New York session                                     |
//+------------------------------------------------------------------+
bool InNYSession()
{
   int hour, minute;
   GetBrokerTime(hour, minute);
   int ny_start_total = InpNYStart_Hour * 60 + InpNYStart_Min;
   int now_total      = hour * 60 + minute;
   int ny_end_total   = InpNYEnd_Hour * 60;
   return (now_total >= ny_start_total && now_total < ny_end_total);
}

//+------------------------------------------------------------------+
//| Check if in Asian session                                        |
//+------------------------------------------------------------------+
bool InAsianSession()
{
   int hour, minute;
   GetBrokerTime(hour, minute);
   return (hour >= InpAsianStart_Hour && hour < InpAsianEnd_Hour);
}

//+------------------------------------------------------------------+
//| Check if new trading day (compare dates)                         |
//+------------------------------------------------------------------+
bool IsNewTradingDay(datetime &last_date)
{
   MqlDateTime now_dt, last_dt;
   TimeCurrent(now_dt);
   TimeToStruct(last_date, last_dt);

   if(now_dt.year != last_dt.year || now_dt.mon != last_dt.mon || now_dt.day != last_dt.day)
   {
      last_date = TimeCurrent();
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Get best filling mode for this broker/symbol                     |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode()
{
   long filling = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0)  return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) != 0)  return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Check if new SL is better (more protective) than current         |
//+------------------------------------------------------------------+
bool IsBetterSL(double new_sl, double current_sl, ENUM_POSITION_TYPE pos_type)
{
   if(current_sl == 0) return true;

   if(pos_type == POSITION_TYPE_BUY)
      return (new_sl > current_sl);   // BUY: higher SL is better
   else
      return (new_sl < current_sl);   // SELL: lower SL is better
}

//+------------------------------------------------------------------+
//| Strategy name from enum                                          |
//+------------------------------------------------------------------+
string StrategyName(ENUM_STRATEGY strat)
{
   switch(strat)
   {
      case STRAT_VPIN:     return "VPIN";
      case STRAT_VWAP:     return "VWAP";
      case STRAT_BREAKOUT: return "BREAKOUT";
      case STRAT_COMBINED: return "COMBINED";
      default:             return "UNKNOWN";
   }
}

//+------------------------------------------------------------------+
//| Count open positions with our magic number                       |
//+------------------------------------------------------------------+
int CountOpenTrades()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) == MAGIC_NUMBER &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Check if a specific strategy already has an open trade           |
//+------------------------------------------------------------------+
bool HasOpenTradeForStrategy(ENUM_STRATEGY strat)
{
   string comment_prefix = StrategyName(strat);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) == MAGIC_NUMBER &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
      {
         string comment = PositionGetString(POSITION_COMMENT);
         if(StringFind(comment, comment_prefix) >= 0)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Log strategy signal                                              |
//+------------------------------------------------------------------+
void LogSignal(const StrategySignal &sig)
{
   if(!sig.is_valid) return;
   string dir = (sig.direction == SIGNAL_BUY) ? "BUY" : "SELL";
   PrintFormat("[%s] %s signal | confidence=%d | %s",
              StrategyName(sig.strategy), dir, sig.confidence, sig.reason);
}

#endif
