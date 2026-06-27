//+------------------------------------------------------------------+
//|                                          XauTickAccumulator.mq5   |
//|  Simple live algo for XAUUSDm (Exness).                           |
//|                                                                   |
//|  Strategy:                                                        |
//|   - On each new M1 candle, capture the candle OPEN price.         |
//|   - While price is ABOVE that open, buy 1 order per second,       |
//|     up to MaxPositions (10) positions.                            |
//|   - Each order gets SL = entry - StopLossUSD, TP = entry + TPUSD. |
//|   - When MaxPositions is reached -> close ALL EA positions.       |
//|                                                                   |
//|  NOTE: StopLossUSD MUST be larger than the spread (~0.24 on       |
//|  XAUUSDm). A buy's SL must sit below the current bid, so any SL    |
//|  inside the spread (e.g. 0.05) is REJECTED with "Invalid stops"   |
//|  (retcode 10016) and NO order is placed. Default is 0.50.         |
//+------------------------------------------------------------------+
#property copyright "Generated with Claude Code"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Inputs (tunable in the attach dialog) ---------------------------
input double Lots             = 1.00;     // Lot size per order
input double StopLossUSD      = 0.50;     // SL distance below entry, in price ($) - MUST be > spread
input double TakeProfitUSD    = 1.00;     // TP distance above entry, in price ($)
input int    MaxPositions     = 10;       // Max simultaneous EA positions
input int    EntryIntervalSec = 1;        // Min seconds between entries
input ulong  MagicNumber      = 990011;   // EA magic number
input int    Slippage         = 100;      // Max deviation, in points

//--- Globals ---------------------------------------------------------
CTrade   trade;
datetime g_barTime  = 0;        // time of the M1 bar we last reacted to
double   g_barOpen  = 0.0;      // open price of the current M1 bar
datetime g_lastEntry = 0;       // time of the last entry placed

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(!SymbolSelect(_Symbol, true))
      Print("WARN: could not select symbol ", _Symbol);

   if(_Symbol != "XAUUSDm")
      Print("WARN: EA designed for XAUUSDm but attached to ", _Symbol);

   // Seed bar state so we don't fire on the partial current bar at startup.
   g_barTime = iTime(_Symbol, PERIOD_M1, 0);
   g_barOpen = iOpen(_Symbol, PERIOD_M1, 0);

   PrintFormat("XauTickAccumulator started on %s | Lots=%.2f SL=%.2f TP=%.2f Max=%d",
               _Symbol, Lots, StopLossUSD, TakeProfitUSD, MaxPositions);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   PrintFormat("XauTickAccumulator stopped (reason=%d)", reason);
}

//+------------------------------------------------------------------+
//| Count this EA's open positions on this symbol                    |
//+------------------------------------------------------------------+
int CountMyPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Close all of this EA's positions on this symbol                  |
//+------------------------------------------------------------------+
void CloseAllMyPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(!trade.PositionClose(ticket))
         PrintFormat("Close failed ticket=%I64u retcode=%d", ticket, trade.ResultRetcode());
   }
}

//+------------------------------------------------------------------+
//| Main tick handler                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1) New M1 bar -> start a fresh cycle, capture this candle's open.
   datetime curBar = iTime(_Symbol, PERIOD_M1, 0);
   if(curBar != g_barTime)
   {
      g_barTime   = curBar;
      g_barOpen   = iOpen(_Symbol, PERIOD_M1, 0);
      g_lastEntry = 0;   // allow an immediate entry on the new candle
   }

   // 2) How many EA positions are open right now?
   int n = CountMyPositions();

   // 3) Hard exit: reached the cap -> close everything, idle until next entry.
   if(n >= MaxPositions)
   {
      CloseAllMyPositions();
      return;
   }

   // 4) Entry: price above the candle open + 1s spacing since last entry.
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick)) return;

   bool aboveOpen = (tick.ask > g_barOpen);
   bool spaced    = (TimeCurrent() - g_lastEntry >= EntryIntervalSec);

   if(aboveOpen && spaced && n < MaxPositions)
   {
      double sl = NormalizeDouble(tick.ask - StopLossUSD, _Digits);
      double tp = NormalizeDouble(tick.ask + TakeProfitUSD, _Digits);

      if(trade.Buy(Lots, _Symbol, tick.ask, sl, tp, "xau-accum"))
      {
         g_lastEntry = TimeCurrent();
         PrintFormat("BUY #%d @ %.3f  SL=%.3f TP=%.3f (barOpen=%.3f)",
                     n + 1, tick.ask, sl, tp, g_barOpen);
      }
      else
      {
         PrintFormat("BUY failed retcode=%d (%s)", trade.ResultRetcode(),
                     trade.ResultRetcodeDescription());
      }
   }
}
//+------------------------------------------------------------------+
