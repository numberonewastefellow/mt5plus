//+------------------------------------------------------------------+
//| TradeExec.mqh - order placement and close-all                    |
//|                                                                  |
//| Every order this EA sends carries RGS_MAGIC, and every close      |
//| filters on it, so the EA can never touch a position it did not    |
//| open - including positions the operator placed by hand.           |
//|                                                                  |
//| NO STOP LOSS AND NO TAKE PROFIT is set on any order. That is the  |
//| strategy, not an omission: the basket is managed as a whole and   |
//| closed together. It also means the broker will not protect this   |
//| account - only the EA's own guards will.                          |
//+------------------------------------------------------------------+
#property strict
#include <Trade\Trade.mqh>
#include "Defines.mqh"

class CRgsExec
  {
private:
   CTrade            m_trade;
   string            m_sym;

public:
   void              Init(const string sym,const int slippage)
     {
      m_sym=sym;
      m_trade.SetExpertMagicNumber(RGS_MAGIC);
      m_trade.SetDeviationInPoints(slippage);
      m_trade.SetTypeFillingBySymbol(sym);
      m_trade.LogLevel(LOG_LEVEL_ERRORS);
     }

   int               LastRetcode() { return (int)m_trade.ResultRetcode(); }
   double            LastPrice()   { return m_trade.ResultPrice(); }
   ulong             LastTicket()  { return m_trade.ResultOrder(); }

   //--- Open ONE position at market. The batch loop lives in the engine so
   //--- that every fill can be logged individually.
   bool              Open(const bool is_buy,const double lot,const string comment)
     {
      bool ok = is_buy ? m_trade.Buy(lot,m_sym,0.0,0.0,0.0,comment)
                       : m_trade.Sell(lot,m_sym,0.0,0.0,0.0,comment);
      if(!ok)
         PrintFormat("RGS OPEN FAILED %s %.2f: retcode=%d %s",
                     (is_buy?"BUY":"SELL"),lot,LastRetcode(),m_trade.ResultRetcodeDescription());
      return ok;
     }

   //--- Close every position this EA owns. Returns how many closed.
   //--- Iterates by ticket and re-scans, because the index shifts as
   //--- positions disappear underneath the loop.
   int               CloseAll(int &failed)
     {
      int closed=0; failed=0;
      for(int pass=0; pass<3; pass++)
        {
         bool any=false;
         for(int i=PositionsTotal()-1;i>=0;i--)
           {
            ulong tk=PositionGetTicket(i);
            if(tk==0) continue;
            if(!PositionSelectByTicket(tk)) continue;
            if(PositionGetInteger(POSITION_MAGIC)!=RGS_MAGIC) continue;
            if(PositionGetString(POSITION_SYMBOL)!=m_sym) continue;
            any=true;
            if(m_trade.PositionClose(tk)) closed++;
            else
              {
               failed++;
               PrintFormat("RGS CLOSE FAILED ticket=%I64u retcode=%d %s",
                           tk,LastRetcode(),m_trade.ResultRetcodeDescription());
              }
           }
         if(!any) break;
        }
      return closed;
     }
  };
//+------------------------------------------------------------------+
