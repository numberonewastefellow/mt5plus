//+------------------------------------------------------------------+
//| Panel.mqh - the click-to-trade dialog on the chart               |
//|                                                                  |
//| BUY / SELL start a cycle on that side. CLOSE ALL is unconditional |
//| and works in any state - it is the operator's brake and must      |
//| never be gated behind a condition. PAUSE stops NEW cycles without |
//| touching an open basket.                                         |
//|                                                                  |
//| The lot box shows the auto-tier lot for the current balance. Type |
//| a number over it to force that lot for the next cycle; clear it   |
//| (or enter 0) to go back to automatic.                             |
//+------------------------------------------------------------------+
#property strict
#include "Defines.mqh"

class CRgsPanel
  {
private:
   long              m_chart;
   double            m_max_lots;   // MaxTotalLots; 0 = not set, cycle line then shows depth cap only

   void              Btn(const string name,const int x,const int y,const int w,const int h,
                         const string text,const color bg,const color fg)
     {
      ObjectCreate(m_chart,name,OBJ_BUTTON,0,0,0);
      ObjectSetInteger(m_chart,name,OBJPROP_XDISTANCE,x);
      ObjectSetInteger(m_chart,name,OBJPROP_YDISTANCE,y);
      ObjectSetInteger(m_chart,name,OBJPROP_XSIZE,w);
      ObjectSetInteger(m_chart,name,OBJPROP_YSIZE,h);
      ObjectSetString (m_chart,name,OBJPROP_TEXT,text);
      ObjectSetInteger(m_chart,name,OBJPROP_BGCOLOR,bg);
      ObjectSetInteger(m_chart,name,OBJPROP_COLOR,fg);
      ObjectSetInteger(m_chart,name,OBJPROP_FONTSIZE,9);
      ObjectSetInteger(m_chart,name,OBJPROP_CORNER,CORNER_LEFT_UPPER);
      ObjectSetInteger(m_chart,name,OBJPROP_BORDER_COLOR,clrDimGray);
      ObjectSetInteger(m_chart,name,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(m_chart,name,OBJPROP_ZORDER,10);
     }

   void              Lbl(const string name,const int x,const int y,const string text,
                         const color fg,const int size=8)
     {
      ObjectCreate(m_chart,name,OBJ_LABEL,0,0,0);
      ObjectSetInteger(m_chart,name,OBJPROP_XDISTANCE,x);
      ObjectSetInteger(m_chart,name,OBJPROP_YDISTANCE,y);
      ObjectSetString (m_chart,name,OBJPROP_TEXT,text);
      ObjectSetInteger(m_chart,name,OBJPROP_COLOR,fg);
      ObjectSetInteger(m_chart,name,OBJPROP_FONTSIZE,size);
      ObjectSetString (m_chart,name,OBJPROP_FONT,"Consolas");
      ObjectSetInteger(m_chart,name,OBJPROP_CORNER,CORNER_LEFT_UPPER);
      ObjectSetInteger(m_chart,name,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(m_chart,name,OBJPROP_ZORDER,10);
     }

public:
   //--- MaxTotalLots, held so the cycle line can show the ceiling that actually binds. Set once
   //--- at Create; it is an input and never changes during a session.
   void              SetMaxLots(const double v) { m_max_lots=v; }

   void              Create(const long chart_id,const double tier_lot)
     {
      m_chart=chart_id;
      int x=RGS_X, y=RGS_Y;

      ObjectCreate(m_chart,RGS_BG,OBJ_RECTANGLE_LABEL,0,0,0);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_XDISTANCE,x-6);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_YDISTANCE,y-6);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_XSIZE,RGS_W);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_YSIZE,RGS_ROW*10+40);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_BGCOLOR,C'22,24,28');
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_BORDER_TYPE,BORDER_FLAT);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_COLOR,clrDimGray);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(m_chart,RGS_BG,OBJPROP_ZORDER,9);

      Lbl(RGS_LBL_TITLE,x,y,"RecoveryGridScalper  - DEMO ONLY",clrGold,9);
      y+=RGS_ROW+2;
      Btn(RGS_BTN_BUY  ,x    ,y,68,22,"BUY"      ,C'20,90,45' ,clrWhite);
      Btn(RGS_BTN_SELL ,x+72 ,y,68,22,"SELL"     ,C'130,40,40',clrWhite);
      Btn(RGS_BTN_CLOSE,x+144,y,80,22,"CLOSE ALL",C'70,70,80' ,clrWhite);
      y+=26;
      Lbl(RGS_LBL_LOT,x,y+4,"lot (0=auto):",clrSilver);
      ObjectCreate(m_chart,RGS_EDT_LOT,OBJ_EDIT,0,0,0);
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_XDISTANCE,x+86);
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_YDISTANCE,y);
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_XSIZE,54);
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_YSIZE,18);
      ObjectSetString (m_chart,RGS_EDT_LOT,OBJPROP_TEXT,"0");
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_BGCOLOR,C'40,44,50');
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_COLOR,clrWhite);
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_ALIGN,ALIGN_CENTER);
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_SELECTABLE,false);
      ObjectSetInteger(m_chart,RGS_EDT_LOT,OBJPROP_ZORDER,10);
      Btn(RGS_BTN_PAUSE,x+144,y-2,80,22,"PAUSE",C'70,70,80',clrWhite);
      y+=24;

      Lbl(RGS_LBL_STATE  ,x,y,               "state  : IDLE",clrWhite);
      Lbl(RGS_LBL_CYCLE  ,x,y+RGS_ROW,       "cycle  : -",clrSilver);
      Lbl(RGS_LBL_BASKET ,x,y+RGS_ROW*2,     "basket : -",clrSilver);
      Lbl(RGS_LBL_TRAIL  ,x,y+RGS_ROW*3,     "peak   : -",clrSilver);
      Lbl(RGS_LBL_ACCOUNT,x,y+RGS_ROW*4,     "acct   : -",clrSilver);
      Lbl(RGS_LBL_MODE   ,x,y+RGS_ROW*5,     "mode   : -",clrSilver);
      // " " and not "": MT5 renders an OBJ_LABEL with EMPTY text as its default
      // caption, the literal word "Label", which sat on the panel looking like a
      // bug. A single space is the empty state.
      Lbl(RGS_LBL_WARN   ,x,y+RGS_ROW*6,     " ",clrOrange);
      ChartRedraw(m_chart);
     }

   void              Destroy()
     {
      ObjectsDeleteAll(m_chart,RGS_PFX);
      ChartRedraw(m_chart);
     }

   //--- Read the override box. 0 / blank / unparseable => automatic.
   double            ManualLot() const
     {
      string s=ObjectGetString(m_chart,RGS_EDT_LOT,OBJPROP_TEXT);
      StringTrimLeft(s); StringTrimRight(s);
      if(StringLen(s)==0) return 0.0;
      double v=StringToDouble(s);
      return (v>0.0 ? v : 0.0);
     }

   void              SetPauseText(const bool paused)
     {
      ObjectSetString(m_chart,RGS_BTN_PAUSE,OBJPROP_TEXT,(paused?"RESUME":"PAUSE"));
      ObjectSetInteger(m_chart,RGS_BTN_PAUSE,OBJPROP_BGCOLOR,
                       (paused?C'150,110,20':C'70,70,80'));
     }

   //--- A button stays visually "pressed" after a click unless released.
   void              Release(const string name)
     {
      ObjectSetInteger(m_chart,name,OBJPROP_STATE,false);
     }

   void              Update(const string state,const int cycle_id,const bool is_buy,
                            const int n_open,const double total_lots,const double net_pnl,
                            const double target,const double tier_lot,const string warn,
                            const int depth_cap,const int group,const double best_pnl,
                            const double giveback,const double margin_per_lot,
                            const string add_mode,const double quick_per_oz)
     {
      ObjectSetString(m_chart,RGS_LBL_STATE,OBJPROP_TEXT,
                      StringFormat("state  : %s",state));
      // TWO ceilings, and the panel used to show only the generous one. `depth cap` is the risk
      // budget; MaxTotalLots permits `lot_cap` positions at this lot size, and that is usually
      // the smaller. Showing "cap 3 of 18 (lots)" stops the panel promising depth the EA will
      // never reach - on 2026-09-08 it read "cap 18" while the basket was frozen at 3.
      int lot_cap=(m_max_lots>0.0 && tier_lot>0.0 ? (int)MathFloor(m_max_lots/tier_lot+1e-9) : 0);
      string capstr=(lot_cap>0 && lot_cap<depth_cap
                     ? StringFormat("cap %d of %d (lots)",lot_cap,depth_cap)
                     : StringFormat("cap %d",depth_cap));
      ObjectSetString(m_chart,RGS_LBL_CYCLE,OBJPROP_TEXT,
                      StringFormat("cycle  : #%d %s  lot %.2f  batch %d  %s",
                                   cycle_id,(cycle_id>0?(is_buy?"BUY":"SELL"):"-"),
                                   tier_lot,group,capstr));
      // The target is a FIXED dollar amount, so the $/oz it implies falls as the basket grows.
      // Showing that number is the point: it is what the operator watches, and it is what the
      // old volume-scaled target held constant.
      double oz=total_lots*RGS_OZ_PER_LOT;
      ObjectSetString(m_chart,RGS_LBL_BASKET,OBJPROP_TEXT,
                      StringFormat("basket : %d/%d pos  net %+.2f / tgt %.2f  (%.3f $/oz)",
                                   n_open,depth_cap,net_pnl,target,
                                   (oz>0.0 ? target/oz : 0.0)));
      // The give-back arm fires at peak - giveback. Showing both makes it visible WHY a basket
      // closed below its high-water mark instead of looking like a missed target.
      // The quick arm is only meaningful once ARMED (the basket has been underwater past
      // QuickExitArmPct). QuickPerOz() returns 0 until then, so show "-" rather than a number
      // the engine will not act on. Shown in BOTH units: $/oz is what the operator reads off the
      // chart, the $ figure is what the basket must actually reach.
      ObjectSetString(m_chart,RGS_LBL_MODE,OBJPROP_TEXT,
                      quick_per_oz>0.0
                      ? StringFormat("mode   : %-5s   quick %.3f $/oz = %+.2f",
                                     add_mode,quick_per_oz,quick_per_oz*oz)
                      : StringFormat("mode   : %-5s   quick: not armed",add_mode));
      ObjectSetInteger(m_chart,RGS_LBL_MODE,OBJPROP_COLOR,
                       (quick_per_oz>0.0 ? clrAqua : clrSilver));
      ObjectSetString(m_chart,RGS_LBL_TRAIL,OBJPROP_TEXT,
                      StringFormat("peak   : %+.2f   exit if <= %+.2f",
                                   best_pnl,(best_pnl>0.0 ? best_pnl-giveback : 0.0)));
      ObjectSetInteger(m_chart,RGS_LBL_TRAIL,OBJPROP_COLOR,
                       (best_pnl>0.0 ? clrGold : clrDimGray));
      // margin/lot is LEARNED from this account's own fills, not from ACCOUNT_LEVERAGE or
      // OrderCalcMargin() - neither is trustworthy on every account (see GridEngine.mqh). Blank
      // until the first real fill calibrates it, rather than showing a misleading $0.00.
      ObjectSetString(m_chart,RGS_LBL_ACCOUNT,OBJPROP_TEXT,
                      StringFormat("acct   : bal %.2f  eq %.2f  free %.2f%s",
                                   AccountInfoDouble(ACCOUNT_BALANCE),
                                   AccountInfoDouble(ACCOUNT_EQUITY),
                                   AccountInfoDouble(ACCOUNT_MARGIN_FREE),
                                   (margin_per_lot>0.0 ? StringFormat("  margin/lot $%.2f",margin_per_lot) : "")));
      ObjectSetInteger(m_chart,RGS_LBL_BASKET,OBJPROP_COLOR,
                       (net_pnl>=0.0?clrLimeGreen:clrTomato));
      ObjectSetString(m_chart,RGS_LBL_WARN,OBJPROP_TEXT,(warn=="" ? " " : warn));
      ChartRedraw(m_chart);
     }
  };
//+------------------------------------------------------------------+
