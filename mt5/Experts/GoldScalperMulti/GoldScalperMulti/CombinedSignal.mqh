//+------------------------------------------------------------------+
//| CombinedSignal.mqh — Aggregates signals from all strategies      |
//| Mirrors strategy_engine.py get_confluence() pattern              |
//+------------------------------------------------------------------+
#ifndef COMBINED_SIGNAL_MQH
#define COMBINED_SIGNAL_MQH

#include "Defines.mqh"
#include "Structs.mqh"

//+------------------------------------------------------------------+
//| Evaluate combined signal from individual strategies              |
//+------------------------------------------------------------------+
StrategySignal EvaluateCombined(StrategySignal &signals[], int count)
{
   StrategySignal sig;
   sig.Reset();
   sig.strategy = STRAT_COMBINED;

   int buy_count  = 0, sell_count  = 0;
   int buy_score  = 0, sell_score  = 0;
   string buy_reasons  = "";
   string sell_reasons = "";

   for(int i = 0; i < count; i++)
   {
      if(!signals[i].is_valid || signals[i].confidence <= 0)
         continue;

      if(signals[i].direction == SIGNAL_BUY)
      {
         buy_count++;
         buy_score += signals[i].confidence;
         if(StringLen(buy_reasons) > 0) buy_reasons += " + ";
         buy_reasons += StrategyName(signals[i].strategy);
      }
      else if(signals[i].direction == SIGNAL_SELL)
      {
         sell_count++;
         sell_score += signals[i].confidence;
         if(StringLen(sell_reasons) > 0) sell_reasons += " + ";
         sell_reasons += StrategyName(signals[i].strategy);
      }
   }

   // Determine dominant direction
   ENUM_SIGNAL_DIR direction = SIGNAL_NONE;
   int total_score  = 0;
   int agree_count  = 0;
   string reasons   = "";

   if(buy_count >= InpCombined_MinAgree && buy_score >= InpCombined_Threshold)
   {
      direction   = SIGNAL_BUY;
      total_score = buy_score;
      agree_count = buy_count;
      reasons     = buy_reasons;
   }
   else if(sell_count >= InpCombined_MinAgree && sell_score >= InpCombined_Threshold)
   {
      direction   = SIGNAL_SELL;
      total_score = sell_score;
      agree_count = sell_count;
      reasons     = sell_reasons;
   }

   if(direction == SIGNAL_NONE)
      return sig;

   // Combined confidence = average of contributing strategies, capped at 100
   int combined_confidence = MathMin(100, total_score / MathMax(1, agree_count));

   sig.direction  = direction;
   sig.confidence = combined_confidence;
   sig.timestamp  = TimeCurrent();
   sig.is_valid   = true;
   sig.reason     = StringFormat("Combined [%s] score=%d agree=%d/3",
                                 reasons, total_score, agree_count);

   return sig;
}

#endif
