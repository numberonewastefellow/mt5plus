//+------------------------------------------------------------------+
//| TradeManager.mqh — Hold-on-loss risk management                  |
//|                                                                  |
//| Philosophy: Quick TP ($5-$10), NO tight SL, hold losers.         |
//| Gold mean-reverts — let it recover instead of stopping out.      |
//| Emergency safety SL at $75 only (catastrophic protection).       |
//+------------------------------------------------------------------+
#ifndef TRADE_MANAGER_MQH
#define TRADE_MANAGER_MQH

#include "Defines.mqh"
#include "Structs.mqh"
#include "Utils.mqh"

//+------------------------------------------------------------------+
//| Calculate lot size from risk % and SL distance                   |
//+------------------------------------------------------------------+
double CalcLotSize(double sl_distance)
{
   if(sl_distance <= 0) return InpLotSize;

   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_amt = equity * (InpRiskPercent / 100.0);
   double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);

   if(contract <= 0) return InpLotSize;

   double lots    = risk_amt / (sl_distance * contract);
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = MathMin(0.10, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX));
   double step    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(step > 0)
      lots = MathFloor(lots / step) * step;

   lots = MathMax(min_lot, MathMin(lots, max_lot));
   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
//| Place a new trade                                                |
//+------------------------------------------------------------------+
bool PlaceTrade(StrategySignal &signal,
                ManagedTrade &trades[], int &trade_count,
                DailyStats &daily)
{
   // --- Pre-flight checks (ALL modes) ---
   if(daily.realized_pnl <= -InpMaxDailyLoss)
   {
      Print("[TRADE] Daily loss limit reached");
      return false;
   }

   // Daily trade limit — enforced in ALL modes
   if(daily.trades_taken >= InpMaxDailyTrades)
   {
      Print("[TRADE] Daily trade limit reached (", daily.trades_taken, "/", InpMaxDailyTrades, ")");
      return false;
   }

   if(CountOpenTrades() >= InpMaxConcurrentTrades)
      return false;

   if(SpreadExceeded())
   {
      Print("[TRADE] Spread too wide");
      return false;
   }

   // Session filter — only London/NY
   if(InpSessionFilter && !InOverlapSession() && !InLondonSession() && !InNYSession())
      return false;

   // No duplicate per strategy
   if(HasOpenTradeForStrategy(signal.strategy))
      return false;

   // --- Calculate price, SL, TP ---
   double price, sl, tp, tp1;
   double volume;

   if(InpAsymmetricMode)
   {
      // ═══ ASYMMETRIC MODE ═══
      double atr = ComputeATR(14, PERIOD_M15);

      if(signal.direction == SIGNAL_BUY)
      {
         price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         tp    = NormalizeDouble(price + (price * InpTP_Pct / 100.0), _Digits);
         if(InpSL_ATR_Mult > 0 && atr > 0)
            sl = NormalizeDouble(price - (atr * InpSL_ATR_Mult), _Digits);
         else
            sl = 0;
      }
      else
      {
         price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         tp    = NormalizeDouble(price - (price * InpTP_Pct / 100.0), _Digits);
         if(InpSL_ATR_Mult > 0 && atr > 0)
            sl = NormalizeDouble(price + (atr * InpSL_ATR_Mult), _Digits);
         else
            sl = 0;
      }
      tp1 = tp;

      if(InpAutoLotSize)
      {
         double sl_dist = (sl != 0) ? MathAbs(price - sl) :
                          ((atr > 0) ? atr * 3.0 : price * 0.005);
         volume = CalcLotSize(sl_dist);
      }
      else
         volume = InpLotSize;
   }
   else
   {
      // ═══ SCALP + HOLD MODE ═══
      // Quick TP ($5/$10), NO tight SL, hold losers
      volume = InpLotSize;

      if(signal.direction == SIGNAL_BUY)
      {
         price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         tp1   = NormalizeDouble(price + InpTP1_Dollars, _Digits);
         tp    = NormalizeDouble(price + InpTP2_Dollars, _Digits);

         // Safety SL only (emergency, not for regular exits)
         if(InpSafetySL_Dollars > 0)
            sl = NormalizeDouble(price - InpSafetySL_Dollars, _Digits);
         else
            sl = 0; // No SL at all — hold indefinitely
      }
      else
      {
         price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         tp1   = NormalizeDouble(price - InpTP1_Dollars, _Digits);
         tp    = NormalizeDouble(price - InpTP2_Dollars, _Digits);

         if(InpSafetySL_Dollars > 0)
            sl = NormalizeDouble(price + InpSafetySL_Dollars, _Digits);
         else
            sl = 0;
      }
   }

   // --- Send order ---
   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action    = TRADE_ACTION_DEAL;
   request.symbol    = _Symbol;
   request.volume    = volume;
   request.type      = (signal.direction == SIGNAL_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   request.price     = NormalizeDouble(price, _Digits);
   request.sl        = sl;
   request.tp        = tp;
   request.deviation = InpSlippage;
   request.magic     = MAGIC_NUMBER;

   string mode_tag = InpAsymmetricMode ? "ASYM" : "HOLD";
   request.comment   = mode_tag + " " + StrategyName(signal.strategy) + " " +
                       IntegerToString(signal.confidence);
   request.type_filling = GetFillingMode();

   if(!OrderSend(request, result))
   {
      PrintFormat("[TRADE] OrderSend failed: %d — %s", result.retcode, result.comment);
      return false;
   }

   if(result.retcode != TRADE_RETCODE_DONE && result.retcode != TRADE_RETCODE_PLACED)
   {
      PrintFormat("[TRADE] Order not filled: retcode=%d %s", result.retcode, result.comment);
      return false;
   }

   // Track locally
   if(trade_count < MAX_MANAGED_TRADES)
   {
      trades[trade_count].Reset();
      trades[trade_count].ticket       = result.order;
      trades[trade_count].strategy_id  = (int)signal.strategy;
      trades[trade_count].open_price   = result.price > 0 ? result.price : price;
      trades[trade_count].initial_sl   = sl;
      trades[trade_count].initial_tp1  = tp1;
      trades[trade_count].initial_tp2  = tp;
      trades[trade_count].lots         = volume;
      trades[trade_count].open_time    = TimeCurrent();
      trades[trade_count].active       = true;
      trade_count++;
   }

   daily.trades_taken++;

   string dir_str = (signal.direction == SIGNAL_BUY) ? "BUY" : "SELL";
   string sl_str  = (sl > 0) ? StringFormat("%.2f", sl) : "NONE";
   PrintFormat("[TRADE] %s %s %s %.2f lots @ %.2f | SL=%s TP1=%.2f TP2=%.2f | %s",
              mode_tag, dir_str, _Symbol, volume,
              result.price > 0 ? result.price : price,
              sl_str, tp1, tp, signal.reason);

   return true;
}

//+------------------------------------------------------------------+
//| Partial close helper                                             |
//+------------------------------------------------------------------+
bool PartialClose(ulong ticket, double lots_to_close)
{
   if(!PositionSelectByTicket(ticket))
      return false;

   ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action   = TRADE_ACTION_DEAL;
   request.position = ticket;
   request.symbol   = _Symbol;
   request.volume   = NormalizeDouble(lots_to_close, 2);

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

   request.deviation    = InpSlippage;
   request.magic        = MAGIC_NUMBER;
   request.type_filling = GetFillingMode();

   if(!OrderSend(request, result))
   {
      PrintFormat("[TRADE] Partial close failed: %d — %s", result.retcode, result.comment);
      return false;
   }

   return (result.retcode == TRADE_RETCODE_DONE);
}

//+------------------------------------------------------------------+
//| Modify SL on open position                                       |
//+------------------------------------------------------------------+
bool MoveSL(ulong ticket, double new_sl)
{
   if(!PositionSelectByTicket(ticket))
      return false;

   double current_tp = PositionGetDouble(POSITION_TP);

   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action   = TRADE_ACTION_SLTP;
   request.position = ticket;
   request.symbol   = _Symbol;
   request.sl       = NormalizeDouble(new_sl, _Digits);
   request.tp       = current_tp;

   if(!OrderSend(request, result))
      return false;

   return (result.retcode == TRADE_RETCODE_DONE);
}

//+------------------------------------------------------------------+
//| Manage open trades                                               |
//| Scalp+Hold mode: only partial close at TP1, NO trailing, NO BE   |
//| Asymmetric mode: no management at all (broker handles TP)        |
//+------------------------------------------------------------------+
void ManageOpenTrades(ManagedTrade &trades[], int &trade_count, DailyStats &daily)
{
   for(int i = trade_count - 1; i >= 0; i--)
   {
      if(!trades[i].active) continue;

      // Check if position still exists
      if(!PositionSelectByTicket(trades[i].ticket))
      {
         // Position closed (TP hit or safety SL or external close)
         double deal_profit = 0;

         if(HistorySelectByPosition(trades[i].ticket))
         {
            int total = HistoryDealsTotal();
            for(int d = total - 1; d >= 0; d--)
            {
               ulong deal_ticket = HistoryDealGetTicket(d);
               if(deal_ticket > 0)
                  deal_profit += HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
            }
         }

         daily.realized_pnl += deal_profit;
         if(deal_profit >= 0) daily.wins++;
         else                 daily.losses++;

         double close_price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         DrawTradeCloseMarker(close_price, deal_profit, deal_profit >= 0);

         PrintFormat("[TRADE] Position %d closed | PnL=%.2f | Daily=%.2f",
                     trades[i].ticket, deal_profit, daily.realized_pnl);

         trades[i].active = false;
         continue;
      }

      // Asymmetric mode: no trade management — broker handles TP, hold losses
      if(InpAsymmetricMode)
         continue;

      // ═══ SCALP+HOLD: Only partial close at TP1 — NO trailing, NO breakeven ═══
      // We HOLD losers and let gold recover. Only take partial profit at TP1.

      double current_price = PositionGetDouble(POSITION_PRICE_CURRENT);
      double open_price    = PositionGetDouble(POSITION_PRICE_OPEN);
      ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double lots_remaining = PositionGetDouble(POSITION_VOLUME);

      double price_diff;
      if(pos_type == POSITION_TYPE_BUY)
         price_diff = current_price - open_price;
      else
         price_diff = open_price - current_price;

      // --- TP1: Partial close at $5 profit (take half off the table) ---
      if(!trades[i].tp1_hit && price_diff >= InpTP1_Dollars)
      {
         double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
         double half_lots = NormalizeDouble(lots_remaining / 2.0, 2);

         if(half_lots >= min_lot)
         {
            if(PartialClose(trades[i].ticket, half_lots))
            {
               trades[i].tp1_hit = true;
               PrintFormat("[TRADE] TP1 hit — closed %.2f lots, holding rest for TP2", half_lots);
            }
         }
         else
            trades[i].tp1_hit = true; // lot too small to split
      }

      // NO trailing stop — we hold losers
      // NO breakeven move — we don't modify SL
      // TP2 is set on broker side — auto-closes when hit
      // Safety SL ($75) is set on broker side — emergency only
   }
}

//+------------------------------------------------------------------+
//| Daily reset                                                      |
//+------------------------------------------------------------------+
void CheckDailyReset(DailyStats &daily)
{
   if(IsNewTradingDay(daily.last_reset_date))
   {
      PrintFormat("[DAILY] Previous day: PnL=%.2f Trades=%d W=%d L=%d",
                  daily.realized_pnl, daily.trades_taken, daily.wins, daily.losses);
      daily.realized_pnl = 0;
      daily.trades_taken = 0;
      daily.wins         = 0;
      daily.losses       = 0;
   }
}

#endif
