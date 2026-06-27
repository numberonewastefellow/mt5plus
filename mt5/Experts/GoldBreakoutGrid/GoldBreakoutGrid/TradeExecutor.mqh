//+------------------------------------------------------------------+
//| TradeExecutor.mqh — Order placement, modification, close         |
//+------------------------------------------------------------------+
#ifndef TRADEEXECUTOR_MQH
#define TRADEEXECUTOR_MQH

#include "Defines.mqh"
#include "Structs.mqh"
#include "Utils.mqh"

// Inputs are declared in GoldBreakoutGrid.mq5 and available here
// via include ordering (this file is included after input declarations)

//+------------------------------------------------------------------+
//| Place a market order — returns ticket or 0 on failure            |
//+------------------------------------------------------------------+
ulong PlaceMarketOrder(ENUM_SIGNAL_DIR dir, double lots, double tp,
                       double safety_sl, string comment)
{
   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   request.action    = TRADE_ACTION_DEAL;
   request.symbol    = _Symbol;
   request.magic     = MAGIC_GRID;
   request.deviation = InpSlippage;
   request.type_filling = GetFillingMode();
   request.comment   = comment;

   if(dir == SIGNAL_BUY)
   {
      request.type   = ORDER_TYPE_BUY;
      request.price  = ask;
      request.volume = lots;
      request.tp     = (tp > 0) ? ask + tp : 0;
      request.sl     = (safety_sl > 0) ? ask - safety_sl : 0;
   }
   else if(dir == SIGNAL_SELL)
   {
      request.type   = ORDER_TYPE_SELL;
      request.price  = bid;
      request.volume = lots;
      request.tp     = (tp > 0) ? bid - tp : 0;
      request.sl     = (safety_sl > 0) ? bid + safety_sl : 0;
   }
   else
      return 0;

   if(!OrderSend(request, result))
   {
      PrintFormat("[TRADE] OrderSend FAILED: %d — %s (lots=%.2f tp=%.2f sl=%.2f)",
                  result.retcode, result.comment, lots,
                  request.tp, request.sl);
      return 0;
   }

   if(result.retcode != TRADE_RETCODE_DONE && result.retcode != TRADE_RETCODE_PLACED)
   {
      PrintFormat("[TRADE] Order rejected: %d — %s", result.retcode, result.comment);
      return 0;
   }

   PrintFormat("[TRADE] %s %.2f lots @ %.2f | TP=%.2f SL=%.2f | ticket=%I64u | %s",
               DirToString(dir), lots, result.price,
               request.tp, request.sl, result.order, comment);

   return result.order;
}

//+------------------------------------------------------------------+
//| Modify TP (and optionally SL) on an existing position            |
//+------------------------------------------------------------------+
bool ModifyPositionTP(ulong ticket, double new_tp, double new_sl = 0)
{
   if(!PositionSelectByTicket(ticket))
      return false;

   double current_sl = PositionGetDouble(POSITION_SL);
   double current_tp = PositionGetDouble(POSITION_TP);

   // Use current SL if not overriding
   if(new_sl == 0)
      new_sl = current_sl;

   // Skip if nothing changed
   if(MathAbs(new_tp - current_tp) < 0.01 && MathAbs(new_sl - current_sl) < 0.01)
      return true;

   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action   = TRADE_ACTION_SLTP;
   request.symbol   = _Symbol;
   request.position = ticket;
   request.sl       = new_sl;
   request.tp       = new_tp;

   if(!OrderSend(request, result))
   {
      PrintFormat("[TRADE] ModifyTP FAILED ticket=%I64u: %d — %s",
                  ticket, result.retcode, result.comment);
      return false;
   }

   if(result.retcode != TRADE_RETCODE_DONE)
   {
      PrintFormat("[TRADE] ModifyTP rejected ticket=%I64u: %d", ticket, result.retcode);
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Close a single position at market                                |
//+------------------------------------------------------------------+
bool ClosePosition(ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return false;

   long pos_type = PositionGetInteger(POSITION_TYPE);
   double volume = PositionGetDouble(POSITION_VOLUME);

   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action    = TRADE_ACTION_DEAL;
   request.symbol    = _Symbol;
   request.position  = ticket;
   request.magic     = MAGIC_GRID;
   request.deviation = InpSlippage;
   request.volume    = volume;
   request.type_filling = GetFillingMode();

   if(pos_type == POSITION_TYPE_BUY)
   {
      request.type  = ORDER_TYPE_SELL;
      request.price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   }
   else
   {
      request.type  = ORDER_TYPE_BUY;
      request.price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   }

   request.comment = EA_COMMENT_PREFIX + " close";

   if(!OrderSend(request, result))
   {
      PrintFormat("[TRADE] Close FAILED ticket=%I64u: %d — %s",
                  ticket, result.retcode, result.comment);
      return false;
   }

   if(result.retcode != TRADE_RETCODE_DONE)
   {
      PrintFormat("[TRADE] Close rejected ticket=%I64u: %d", ticket, result.retcode);
      return false;
   }

   PrintFormat("[TRADE] Closed ticket=%I64u @ %.2f", ticket, result.price);
   return true;
}

//+------------------------------------------------------------------+
//| Close all positions in a grid group                              |
//+------------------------------------------------------------------+
int CloseAllGridPositions(GridGroup &group, ENUM_CLOSE_REASON reason)
{
   int closed = 0;
   string reason_str;
   switch(reason)
   {
      case CLOSE_TP_INITIAL: reason_str = "TP_INITIAL"; break;
      case CLOSE_TP_GROUP:   reason_str = "TP_GROUP";   break;
      case CLOSE_UNCLE:      reason_str = "UNCLE";      break;
      case CLOSE_STALE:      reason_str = "STALE";      break;
      case CLOSE_DAILY_LOSS: reason_str = "DAILY_LOSS"; break;
      default:               reason_str = "MANUAL";     break;
   }

   PrintFormat("[GRID] Closing grid group (%d positions) — reason: %s | basket_pnl=$%.2f",
               group.position_count, reason_str, group.basket_pnl);

   for(int i = 0; i < group.position_count; i++)
   {
      if(!group.positions[i].active)
         continue;
      if(ClosePosition(group.positions[i].ticket))
      {
         group.positions[i].active = false;
         closed++;
      }
   }

   group.status = GRID_CLOSED;
   PrintFormat("[GRID] Closed %d/%d positions", closed, group.position_count);
   return closed;
}

#endif
