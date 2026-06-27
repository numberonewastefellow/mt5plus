//+------------------------------------------------------------------+
//| Utils.mqh — Utility functions for Gold Breakout Grid EA          |
//+------------------------------------------------------------------+
#ifndef UTILS_MQH
#define UTILS_MQH

#include "Defines.mqh"

//+------------------------------------------------------------------+
//| Extract broker-time hour and minute from TimeCurrent()           |
//+------------------------------------------------------------------+
void GetBrokerTime(int &hour, int &minute)
{
   MqlDateTime dt;
   TimeCurrent(dt);
   hour   = dt.hour;
   minute = dt.min;
}

//+------------------------------------------------------------------+
//| Get current spread in dollars                                    |
//+------------------------------------------------------------------+
double GetSpreadDollars()
{
   return SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
}

//+------------------------------------------------------------------+
//| Check if spread exceeds maximum allowed                          |
//+------------------------------------------------------------------+
bool SpreadExceeded(double max_spread_dollars)
{
   return GetSpreadDollars() > max_spread_dollars;
}

//+------------------------------------------------------------------+
//| Session checks (using broker server time)                        |
//+------------------------------------------------------------------+
bool InAsianSession(int asian_start, int asian_end)
{
   int h, m;
   GetBrokerTime(h, m);
   return (h >= asian_start && h < asian_end);
}

bool InLondonSession(int london_start, int london_end)
{
   int h, m;
   GetBrokerTime(h, m);
   return (h >= london_start && h < london_end);
}

bool InNYSession(int ny_start, int ny_end)
{
   int h, m;
   GetBrokerTime(h, m);
   return (h >= ny_start && h < ny_end);
}

bool InTradingSession(int london_start, int london_end, int ny_start, int ny_end)
{
   return InLondonSession(london_start, london_end) || InNYSession(ny_start, ny_end);
}

//+------------------------------------------------------------------+
//| Check if today is a new trading day (for daily resets)           |
//+------------------------------------------------------------------+
bool IsNewTradingDay(datetime &last_date)
{
   MqlDateTime now, prev;
   TimeCurrent(now);
   TimeToStruct(last_date, prev);
   if(now.year != prev.year || now.mon != prev.mon || now.day != prev.day)
   {
      last_date = TimeCurrent();
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Compute ATR from built-in indicator                              |
//+------------------------------------------------------------------+
double ComputeATR(int period, ENUM_TIMEFRAMES tf)
{
   int handle = iATR(_Symbol, tf, period);
   if(handle == INVALID_HANDLE)
      return 0;

   double atr_buf[];
   ArraySetAsSeries(atr_buf, true);
   // Use bar [1] (completed) for stable value
   if(CopyBuffer(handle, 0, 1, 1, atr_buf) < 1)
   {
      IndicatorRelease(handle);
      return 0;
   }
   double val = atr_buf[0];
   IndicatorRelease(handle);
   return val;
}

//+------------------------------------------------------------------+
//| News blackout: NFP = first Friday, CPI = 10th-15th              |
//+------------------------------------------------------------------+
bool IsNewsBlackoutDay()
{
   MqlDateTime dt;
   TimeCurrent(dt);

   // NFP: first Friday of the month (day <= 7 and day_of_week == 5)
   if(dt.day <= 7 && dt.day_of_week == 5)
   {
      PrintFormat("[NEWS] NFP day detected (first Friday: %d/%d)", dt.mon, dt.day);
      return true;
   }

   // CPI: typically 10th-15th of month (Tuesday or Wednesday)
   if(dt.day >= 10 && dt.day <= 15 && (dt.day_of_week == 2 || dt.day_of_week == 3))
   {
      PrintFormat("[NEWS] Potential CPI day detected (%d/%d)", dt.mon, dt.day);
      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
//| Auto-detect order filling mode from broker                       |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode()
{
   long fill_policy = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fill_policy & SYMBOL_FILLING_FOK) != 0)
      return ORDER_FILLING_FOK;
   if((fill_policy & SYMBOL_FILLING_IOC) != 0)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Count open positions belonging to this EA                        |
//+------------------------------------------------------------------+
int CountOurPositions()
{
   int count = 0;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) == MAGIC_GRID &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Convert signal direction to string                               |
//+------------------------------------------------------------------+
string DirToString(ENUM_SIGNAL_DIR dir)
{
   switch(dir)
   {
      case SIGNAL_BUY:  return "BUY";
      case SIGNAL_SELL: return "SELL";
      default:          return "NONE";
   }
}

//+------------------------------------------------------------------+
//| Clamp value between min and max                                  |
//+------------------------------------------------------------------+
double Clamp(double value, double min_val, double max_val)
{
   if(value < min_val) return min_val;
   if(value > max_val) return max_val;
   return value;
}

//+------------------------------------------------------------------+
//| Check if it's Friday close-out time                              |
//+------------------------------------------------------------------+
bool IsFridayCloseTime(int close_hour)
{
   MqlDateTime dt;
   TimeCurrent(dt);
   return (dt.day_of_week == 5 && dt.hour >= close_hour);
}

//+------------------------------------------------------------------+
//| Calculate total floating P&L across ALL our open positions       |
//+------------------------------------------------------------------+
double CalcBasketFloatingPnL()
{
   double total = 0;
   int count = PositionsTotal();
   for(int i = 0; i < count; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MAGIC_GRID) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      total += PositionGetDouble(POSITION_PROFIT)
             + PositionGetDouble(POSITION_SWAP);
   }
   return total;
}

//+------------------------------------------------------------------+
//| Close the worst (most negative) open position                    |
//| Returns ticket of closed position, or 0 if none                  |
//+------------------------------------------------------------------+
ulong FindWorstPosition()
{
   ulong worst_ticket = 0;
   double worst_pnl = 0;
   int count = PositionsTotal();
   for(int i = 0; i < count; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MAGIC_GRID) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      double pnl = PositionGetDouble(POSITION_PROFIT)
                  + PositionGetDouble(POSITION_SWAP);
      if(pnl < worst_pnl)
      {
         worst_pnl = pnl;
         worst_ticket = ticket;
      }
   }
   return worst_ticket;
}

#endif
