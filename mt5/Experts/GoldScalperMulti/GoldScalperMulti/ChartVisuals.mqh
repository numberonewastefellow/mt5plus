//+------------------------------------------------------------------+
//| ChartVisuals.mqh — Professional chart overlays                   |
//|                                                                  |
//| Draws: VWAP bands (O(n) incremental), Asian range rectangle,     |
//| session separators, ATR channel, VPIN gauge, signal arrows,      |
//| trade P&L markers, dashboard panel with labels.                  |
//+------------------------------------------------------------------+
#ifndef CHART_VISUALS_MQH
#define CHART_VISUALS_MQH

#include "Defines.mqh"
#include "Structs.mqh"

//--- Color scheme (professional dark-chart palette)
#define CLR_VWAP           C'30,144,255'     // dodger blue (bright)
#define CLR_VWAP_UPPER     C'135,206,250'    // light sky blue
#define CLR_VWAP_LOWER     C'135,206,250'
#define CLR_VWAP_FILL_BULL C'20,60,20'       // faint green when price > VWAP
#define CLR_VWAP_FILL_BEAR C'60,20,20'       // faint red when price < VWAP
#define CLR_ASIAN_HIGH     C'255,215,0'      // gold
#define CLR_ASIAN_LOW      C'255,215,0'
#define CLR_ASIAN_FILL     C'25,23,5'        // very subtle gold tint
#define CLR_SESSION_LONDON C'60,60,60'       // subtle gray
#define CLR_SESSION_NY     C'50,50,70'       // subtle blue-gray
#define CLR_TP1            clrLime
#define CLR_BUY_ARROW      clrLime
#define CLR_SELL_ARROW     clrOrangeRed
#define CLR_TRADE_WIN      C'0,200,100'
#define CLR_TRADE_LOSS     C'220,50,50'
#define CLR_ATR_BAND       C'80,80,80'       // subtle ATR channel
#define CLR_VPIN_BAR_LOW   C'60,60,60'
#define CLR_VPIN_BAR_MED   C'180,180,50'
#define CLR_VPIN_BAR_HIGH  C'220,50,80'
#define CLR_LABEL_BG       C'20,22,28'
#define CLR_LABEL_TEXT      C'230,235,240'    // bright white text
#define CLR_LABEL_DIM      C'160,170,180'    // readable gray
#define CLR_LABEL_GREEN    C'80,230,140'     // vivid green
#define CLR_LABEL_RED      C'255,90,90'      // vivid red
#define CLR_LABEL_BLUE     C'120,190,255'    // vivid blue
#define CLR_LABEL_GOLD     C'255,220,50'     // bright gold

//--- Object name prefixes
#define PFX_VWAP    "GS_VW_"
#define PFX_ASIAN   "GS_AS_"
#define PFX_SIGNAL  "GS_SIG_"
#define PFX_TP1     "GS_TP1_"
#define PFX_SESS    "GS_SS_"
#define PFX_ATR     "GS_ATR_"
#define PFX_PANEL   "GS_PNL_"
#define PFX_TRADE   "GS_TRD_"

//+------------------------------------------------------------------+
//| Delete all objects with given prefix                              |
//+------------------------------------------------------------------+
void DeleteObjectsByPrefix(string prefix)
{
   int total = ObjectsTotal(0);
   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| Create/update horizontal line                                    |
//+------------------------------------------------------------------+
void DrawHLine(string name, double price, color clr, int width,
               ENUM_LINE_STYLE style, string tooltip)
{
   if(price <= 0) return;
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);
}

//+------------------------------------------------------------------+
//| Create/update trend line segment                                 |
//+------------------------------------------------------------------+
void DrawSegment(string name, datetime t1, double p1, datetime t2, double p2,
                 color clr, int width, ENUM_LINE_STYLE style)
{
   if(p1 <= 0 || p2 <= 0) return;
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
   }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, t1);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 0, p1);
   ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 1, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
}

//+------------------------------------------------------------------+
//| Create/update label object for dashboard                         |
//+------------------------------------------------------------------+
void DrawLabel(string name, int x, int y, string text, color clr,
               int font_size, ENUM_ANCHOR_POINT anchor = ANCHOR_LEFT_UPPER)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_ZORDER, 10); // above panel background
      ObjectSetInteger(0, name, OBJPROP_BACK, false);
   }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
}

//+------------------------------------------------------------------+
//| Create/update rectangle (for Asian range fill)                   |
//+------------------------------------------------------------------+
void DrawRectangle(string name, datetime t1, double p1, datetime t2, double p2,
                   color clr, bool fill)
{
   if(p1 <= 0 || p2 <= 0) return;
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, name, OBJPROP_TIME, 0, t1);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 0, p1);
   ObjectSetInteger(0, name, OBJPROP_TIME, 1, t2);
   ObjectSetDouble(0, name, OBJPROP_PRICE, 1, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FILL, fill);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
}

//+------------------------------------------------------------------+
//| Compute ATR for N periods                                        |
//+------------------------------------------------------------------+
double ComputeATR(int period, ENUM_TIMEFRAMES tf = PERIOD_M5)
{
   double atr_buf[];
   int handle = iATR(_Symbol, tf, period);
   if(handle == INVALID_HANDLE) return 0;
   if(CopyBuffer(handle, 0, 0, 1, atr_buf) <= 0) { IndicatorRelease(handle); return 0; }
   double val = atr_buf[0];
   IndicatorRelease(handle);
   return val;
}

//+------------------------------------------------------------------+
//| Draw VWAP + bands — O(n) incremental computation                 |
//+------------------------------------------------------------------+
void DrawVWAPBands(const VWAPState &state, ENUM_TIMEFRAMES tf)
{
   // Draw VWAP from chart candles directly — doesn't depend on strategy state
   MqlDateTime dt;
   TimeCurrent(dt);
   dt.hour = InpAsianStart_Hour;
   dt.min = 0; dt.sec = 0;
   datetime session_start = StructToTime(dt);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, tf, session_start, TimeCurrent(), rates);
   if(copied < 2) return;

   int max_draw = MathMin(copied, 150);
   int start = MathMax(0, copied - max_draw);

   DeleteObjectsByPrefix(PFX_VWAP);

   // O(n) incremental VWAP + Welford online std dev
   double cum_tp_vol = 0, cum_vol = 0;
   // Warm up from bar 0 to start (don't draw, just accumulate)
   for(int i = 0; i < start; i++)
   {
      double tp = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0) vol = 1.0;
      cum_tp_vol += tp * vol;
      cum_vol += vol;
   }

   double prev_vwap = 0, prev_upper = 0, prev_lower = 0;
   datetime prev_time = 0;

   // Incremental Welford for std dev
   double w_mean = 0, w_S = 0, w_sumW = 0;
   // Warm up Welford from 0 to start
   for(int i = 0; i < start; i++)
   {
      double tp = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0) vol = 1.0;
      double old_sumW = w_sumW;
      w_sumW += vol;
      double delta = tp - w_mean;
      double R = delta * vol / w_sumW;
      w_mean += R;
      w_S += old_sumW * delta * R;
   }

   for(int i = start; i < copied; i++)
   {
      double tp = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
      double vol = (double)rates[i].tick_volume;
      if(vol <= 0) vol = 1.0;

      cum_tp_vol += tp * vol;
      cum_vol += vol;
      double vwap = cum_tp_vol / cum_vol;

      // Welford update for weighted std dev
      double old_sumW = w_sumW;
      w_sumW += vol;
      double delta = tp - w_mean;
      double R = delta * vol / w_sumW;
      w_mean += R;
      w_S += old_sumW * delta * R;

      double std_val = (w_sumW > 0) ? MathSqrt(w_S / w_sumW) : 0;
      double upper = vwap + InpVWAP_StdMultiplier * std_val;
      double lower = vwap - InpVWAP_StdMultiplier * std_val;

      if(prev_vwap > 0 && prev_time > 0)
      {
         string idx = IntegerToString(i);
         // VWAP center line (thick blue)
         DrawSegment(PFX_VWAP + "M" + idx, prev_time, prev_vwap, rates[i].time, vwap,
                     CLR_VWAP, 2, STYLE_SOLID);
         // Upper band (thin dotted)
         DrawSegment(PFX_VWAP + "U" + idx, prev_time, prev_upper, rates[i].time, upper,
                     CLR_VWAP_UPPER, 1, STYLE_DOT);
         // Lower band (thin dotted)
         DrawSegment(PFX_VWAP + "L" + idx, prev_time, prev_lower, rates[i].time, lower,
                     CLR_VWAP_LOWER, 1, STYLE_DOT);
      }

      prev_vwap = vwap; prev_upper = upper; prev_lower = lower;
      prev_time = rates[i].time;
   }

   // Extended VWAP/bands into future (ray right from last point)
   if(prev_vwap > 0)
   {
      DrawHLine(PFX_VWAP + "NOW_M", prev_vwap, CLR_VWAP, 1, STYLE_DOT,
                StringFormat("VWAP: %.2f", prev_vwap));
      DrawHLine(PFX_VWAP + "NOW_U", prev_upper, CLR_VWAP_UPPER, 1, STYLE_DOT,
                StringFormat("Upper +%d\x03C3: %.2f", (int)InpVWAP_StdMultiplier, prev_upper));
      DrawHLine(PFX_VWAP + "NOW_L", prev_lower, CLR_VWAP_LOWER, 1, STYLE_DOT,
                StringFormat("Lower -%d\x03C3: %.2f", (int)InpVWAP_StdMultiplier, prev_lower));
   }
}

//+------------------------------------------------------------------+
//| Draw Asian range as filled rectangle + lines                     |
//+------------------------------------------------------------------+
void DrawAsianRange(SessionState &state)
{
   // If strategy hasn't set the range yet, compute from chart candles
   if(!state.asian_range_set || state.asian_high <= 0 || state.asian_low >= 999999)
   {
      MqlDateTime dt;
      TimeCurrent(dt);
      MqlDateTime as_s = dt;
      as_s.hour = InpAsianStart_Hour; as_s.min = 0; as_s.sec = 0;
      MqlDateTime as_e = dt;
      as_e.hour = InpAsianEnd_Hour; as_e.min = 0; as_e.sec = 0;

      MqlRates ar[];
      ArraySetAsSeries(ar, false);
      int n = CopyRates(_Symbol, PERIOD_M15, StructToTime(as_s), StructToTime(as_e), ar);
      if(n <= 0) return;

      double h = 0, l = 999999;
      for(int i = 0; i < n; i++)
      {
         if(ar[i].high > h) h = ar[i].high;
         if(ar[i].low < l)  l = ar[i].low;
      }
      if(h <= 0 || l >= 999999) return;

      state.asian_high = h;
      state.asian_low = l;
      state.asian_range_set = true;
   }

   // Get Asian session time bounds for rectangle
   MqlDateTime dt;
   TimeCurrent(dt);

   MqlDateTime as_start = dt;
   as_start.hour = InpAsianStart_Hour; as_start.min = 0; as_start.sec = 0;
   MqlDateTime as_end = dt;
   as_end.hour = InpAsianEnd_Hour; as_end.min = 0; as_end.sec = 0;

   // Filled rectangle for Asian range
   DrawRectangle(PFX_ASIAN + "RECT",
                 StructToTime(as_start), state.asian_high,
                 StructToTime(as_end), state.asian_low,
                 CLR_ASIAN_FILL, true);

   // Horizontal lines extending beyond Asian session
   double range = state.asian_high - state.asian_low;
   DrawHLine(PFX_ASIAN + "HIGH", state.asian_high, CLR_ASIAN_HIGH, 2, STYLE_DASH,
             StringFormat("Asian High: %.2f", state.asian_high));
   DrawHLine(PFX_ASIAN + "LOW", state.asian_low, CLR_ASIAN_LOW, 2, STYLE_DASH,
             StringFormat("Asian Low: %.2f  (range $%.2f)", state.asian_low, range));

   // Breakout trigger levels (buffer lines)
   DrawHLine(PFX_ASIAN + "BUF_H", state.asian_high + InpBreakout_Buffer,
             CLR_ASIAN_HIGH, 1, STYLE_DOT,
             StringFormat("BUY trigger: %.2f (+$%.2f buffer)",
                          state.asian_high + InpBreakout_Buffer, InpBreakout_Buffer));
   DrawHLine(PFX_ASIAN + "BUF_L", state.asian_low - InpBreakout_Buffer,
             CLR_ASIAN_LOW, 1, STYLE_DOT,
             StringFormat("SELL trigger: %.2f (-$%.2f buffer)",
                          state.asian_low - InpBreakout_Buffer, InpBreakout_Buffer));

   // Range midpoint (useful for reversion targets)
   double mid = (state.asian_high + state.asian_low) / 2.0;
   DrawHLine(PFX_ASIAN + "MID", mid, C'80,75,30', 1, STYLE_DOT,
             StringFormat("Asian Mid: %.2f", mid));
}

//+------------------------------------------------------------------+
//| Draw session separator vertical lines                            |
//+------------------------------------------------------------------+
void DrawSessionSeparators()
{
   MqlDateTime dt;
   TimeCurrent(dt);

   // London open
   MqlDateTime lon = dt;
   lon.hour = InpLondonStart_Hour; lon.min = 0; lon.sec = 0;
   datetime lon_time = StructToTime(lon);
   string lon_name = PFX_SESS + "LON";
   if(ObjectFind(0, lon_name) < 0)
   {
      ObjectCreate(0, lon_name, OBJ_VLINE, 0, lon_time, 0);
      ObjectSetInteger(0, lon_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lon_name, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, lon_name, OBJPROP_TIME, 0, lon_time);
   ObjectSetInteger(0, lon_name, OBJPROP_COLOR, CLR_SESSION_LONDON);
   ObjectSetInteger(0, lon_name, OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, lon_name, OBJPROP_BACK, true);
   ObjectSetString(0, lon_name, OBJPROP_TOOLTIP, "London Open");

   // NY open
   MqlDateTime ny = dt;
   ny.hour = InpNYStart_Hour; ny.min = InpNYStart_Min; ny.sec = 0;
   datetime ny_time = StructToTime(ny);
   string ny_name = PFX_SESS + "NY";
   if(ObjectFind(0, ny_name) < 0)
   {
      ObjectCreate(0, ny_name, OBJ_VLINE, 0, ny_time, 0);
      ObjectSetInteger(0, ny_name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, ny_name, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, ny_name, OBJPROP_TIME, 0, ny_time);
   ObjectSetInteger(0, ny_name, OBJPROP_COLOR, CLR_SESSION_NY);
   ObjectSetInteger(0, ny_name, OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, ny_name, OBJPROP_BACK, true);
   ObjectSetString(0, ny_name, OBJPROP_TOOLTIP, "New York Open");

   // Session labels
   DrawLabel(PFX_SESS + "LON_LBL", 0, 0, " LONDON", CLR_SESSION_LONDON, 8);
   ObjectSetInteger(0, PFX_SESS + "LON_LBL", OBJPROP_CORNER, CORNER_LEFT_UPPER);
   // Position label near the vertical line using time/price anchor
   if(ObjectFind(0, PFX_SESS + "LON_TXT") < 0)
      ObjectCreate(0, PFX_SESS + "LON_TXT", OBJ_TEXT, 0, lon_time, SymbolInfoDouble(_Symbol, SYMBOL_BID));
   ObjectSetInteger(0, PFX_SESS + "LON_TXT", OBJPROP_TIME, 0, lon_time);
   ObjectSetDouble(0, PFX_SESS + "LON_TXT", OBJPROP_PRICE, 0, SymbolInfoDouble(_Symbol, SYMBOL_BID) + 2);
   ObjectSetString(0, PFX_SESS + "LON_TXT", OBJPROP_TEXT, "  LON");
   ObjectSetInteger(0, PFX_SESS + "LON_TXT", OBJPROP_COLOR, CLR_SESSION_LONDON);
   ObjectSetString(0, PFX_SESS + "LON_TXT", OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, PFX_SESS + "LON_TXT", OBJPROP_FONTSIZE, 7);

   if(ObjectFind(0, PFX_SESS + "NY_TXT") < 0)
      ObjectCreate(0, PFX_SESS + "NY_TXT", OBJ_TEXT, 0, ny_time, SymbolInfoDouble(_Symbol, SYMBOL_BID));
   ObjectSetInteger(0, PFX_SESS + "NY_TXT", OBJPROP_TIME, 0, ny_time);
   ObjectSetDouble(0, PFX_SESS + "NY_TXT", OBJPROP_PRICE, 0, SymbolInfoDouble(_Symbol, SYMBOL_BID) + 2);
   ObjectSetString(0, PFX_SESS + "NY_TXT", OBJPROP_TEXT, "  NY");
   ObjectSetInteger(0, PFX_SESS + "NY_TXT", OBJPROP_COLOR, CLR_SESSION_NY);
   ObjectSetString(0, PFX_SESS + "NY_TXT", OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, PFX_SESS + "NY_TXT", OBJPROP_FONTSIZE, 7);
}

//+------------------------------------------------------------------+
//| Draw TP1 levels for active trades                                |
//+------------------------------------------------------------------+
void DrawTP1Levels(const ManagedTrade &trades[], int trade_count)
{
   DeleteObjectsByPrefix(PFX_TP1);
   for(int i = 0; i < trade_count; i++)
   {
      if(!trades[i].active || trades[i].tp1_hit) continue;
      string name = PFX_TP1 + IntegerToString(i);
      DrawHLine(name, trades[i].initial_tp1, CLR_TP1, 1, STYLE_DASHDOT,
                StringFormat("TP1 ($%.0f): %.2f", InpTP1_Dollars, trades[i].initial_tp1));
   }
}

//+------------------------------------------------------------------+
//| Draw signal arrow with offset for visibility                     |
//+------------------------------------------------------------------+
void DrawSignalArrow(const StrategySignal &signal, double price)
{
   if(!signal.is_valid || signal.direction == SIGNAL_NONE || price <= 0) return;

   static int arrow_count = 0;
   arrow_count++;
   string name = PFX_SIGNAL + IntegerToString(arrow_count);

   int arrow_code;
   color clr;
   double offset;

   if(signal.direction == SIGNAL_BUY)
   {
      arrow_code = 233;
      clr = CLR_BUY_ARROW;
      offset = -1.0; // below price
   }
   else
   {
      arrow_code = 234;
      clr = CLR_SELL_ARROW;
      offset = 1.0;  // above price
   }

   ObjectCreate(0, name, OBJ_ARROW, 0, TimeCurrent(), price + offset);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, arrow_code);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetString(0, name, OBJPROP_TOOLTIP,
                   StringFormat("%s %s [%d%%]\n%s",
                                StrategyName(signal.strategy),
                                signal.direction == SIGNAL_BUY ? "BUY" : "SELL",
                                signal.confidence, signal.reason));

   // Price label next to arrow
   string lbl_name = PFX_SIGNAL + "L" + IntegerToString(arrow_count);
   if(ObjectFind(0, lbl_name) < 0)
      ObjectCreate(0, lbl_name, OBJ_TEXT, 0, TimeCurrent(), price + offset * 2);
   ObjectSetInteger(0, lbl_name, OBJPROP_TIME, 0, TimeCurrent());
   ObjectSetDouble(0, lbl_name, OBJPROP_PRICE, 0, price + offset * 2.5);
   ObjectSetString(0, lbl_name, OBJPROP_TEXT,
                   StringFormat(" %s %d%%", StrategyName(signal.strategy), signal.confidence));
   ObjectSetInteger(0, lbl_name, OBJPROP_COLOR, clr);
   ObjectSetString(0, lbl_name, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, lbl_name, OBJPROP_FONTSIZE, 7);
}

//+------------------------------------------------------------------+
//| Draw trade close marker with P&L label                           |
//+------------------------------------------------------------------+
void DrawTradeCloseMarker(double price, double pnl, bool is_win)
{
   if(price <= 0) return;

   static int close_count = 0;
   close_count++;
   string name = PFX_TRADE + IntegerToString(close_count);
   color clr = is_win ? CLR_TRADE_WIN : CLR_TRADE_LOSS;

   // X marker for close
   ObjectCreate(0, name, OBJ_ARROW, 0, TimeCurrent(), price);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, 251); // X mark
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);

   // P&L label
   string lbl = PFX_TRADE + "L" + IntegerToString(close_count);
   if(ObjectFind(0, lbl) < 0)
      ObjectCreate(0, lbl, OBJ_TEXT, 0, TimeCurrent(), price);
   ObjectSetInteger(0, lbl, OBJPROP_TIME, 0, TimeCurrent());
   ObjectSetDouble(0, lbl, OBJPROP_PRICE, 0, price + (is_win ? 1.5 : -1.5));
   ObjectSetString(0, lbl, OBJPROP_TEXT, StringFormat(" $%+.2f", pnl));
   ObjectSetInteger(0, lbl, OBJPROP_COLOR, clr);
   ObjectSetString(0, lbl, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
}

//+------------------------------------------------------------------+
//| Draw professional dashboard panel (label-based, not Comment)     |
//+------------------------------------------------------------------+
void DrawPanelBackground(string name, int x, int y, int width, int height, color bg, int border_width = 1)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, width);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, height);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, name, OBJPROP_COLOR, C'60,65,75');
   ObjectSetInteger(0, name, OBJPROP_WIDTH, border_width);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, 0);  // behind labels
}

void DrawDashboard(const VPINState &vpin, const VWAPState &vwap,
                   const SessionState &sess, const DailyStats &daily,
                   int open_trades)
{
   int x = 12, y = 22;
   int line_h = 16;
   int row = 0;
   int pad = 8;    // padding inside panel
   int panel_w = 310;
   int panel_rows = 11; // estimate, will adjust
   int panel_h = pad * 2 + line_h * panel_rows;

   // Solid dark background panel
   DrawPanelBackground(PFX_PANEL + "BG", x, y, panel_w, panel_h, C'15,17,23', 1);

   // Offset text inside panel
   x += pad;
   y += pad;

   // Title + Mode badge
   if(InpAsymmetricMode)
   {
      DrawLabel(PFX_PANEL + "T", x, y + line_h * row, "GOLD ASYMMETRIC HOLD", CLR_LABEL_GOLD, 11);
      row++;
      DrawLabel(PFX_PANEL + "TM", x, y + line_h * row,
                StringFormat("TP:%.2f%%  SL:%.1fxATR  Risk:%.1f%%  Max:%d/day",
                             InpTP_Pct, InpSL_ATR_Mult, InpRiskPercent, InpMaxDailyTrades),
                CLR_LABEL_DIM, 8);
   }
   else
   {
      DrawLabel(PFX_PANEL + "T", x, y + line_h * row, "GOLD SCALPER", CLR_LABEL_TEXT, 11);
      row++;
      DrawLabel(PFX_PANEL + "TM", x, y + line_h * row,
                StringFormat("TP1:$%.0f  TP2:$%.0f  SafetySL:$%.0f  Max:%d/day",
                             InpTP1_Dollars, InpTP2_Dollars, InpSafetySL_Dollars, InpMaxDailyTrades),
                CLR_LABEL_DIM, 8);
   }
   row++;

   // Separator
   DrawLabel(PFX_PANEL + "S1", x, y + line_h * row,
             "________________________________", C'40,44,52', 8);
   row++;

   // Daily P&L
   color pnl_clr = daily.realized_pnl >= 0 ? CLR_LABEL_GREEN : CLR_LABEL_RED;
   DrawLabel(PFX_PANEL + "PL", x, y + line_h * row,
             StringFormat("P&L: $%+.2f", daily.realized_pnl), pnl_clr, 10);
   row++;

   // Win/Loss + daily trade count
   int total_trades = daily.wins + daily.losses;
   double win_rate = total_trades > 0 ? (double)daily.wins / total_trades * 100.0 : 0;
   if(InpAsymmetricMode)
   {
      int remaining = InpMaxDailyTrades - daily.trades_taken;
      color rem_clr = remaining <= 0 ? CLR_LABEL_RED : CLR_LABEL_DIM;
      DrawLabel(PFX_PANEL + "WL", x, y + line_h * row,
                StringFormat("W:%d L:%d (%.0f%%) | Trades: %d/%d",
                             daily.wins, daily.losses, win_rate,
                             daily.trades_taken, InpMaxDailyTrades),
                rem_clr, 9);
   }
   else
   {
      DrawLabel(PFX_PANEL + "WL", x, y + line_h * row,
                StringFormat("W:%d  L:%d  (%.0f%%)", daily.wins, daily.losses, win_rate),
                CLR_LABEL_DIM, 9);
   }
   row++;

   // Open trades
   DrawLabel(PFX_PANEL + "OT", x, y + line_h * row,
             StringFormat("Open: %d/%d  |  Trades: %d",
                          open_trades, InpMaxConcurrentTrades, daily.trades_taken),
             CLR_LABEL_DIM, 9);
   row++;

   // Separator
   DrawLabel(PFX_PANEL + "S2", x, y + line_h * row,
             "________________________________", C'40,44,52', 8);
   row++;

   // ATR
   double atr = ComputeATR(14, PERIOD_M5);
   DrawLabel(PFX_PANEL + "ATR", x, y + line_h * row,
             StringFormat("ATR(14): $%.2f", atr), CLR_LABEL_DIM, 9);
   row++;

   // VPIN status with visual gauge
   if(InpEnableVPIN)
   {
      string vpin_status;
      color vpin_clr;
      if(vpin.count < InpVPIN_MinBuckets)
      {
         vpin_status = StringFormat("VPIN: warming %d/%d", vpin.count, InpVPIN_MinBuckets);
         vpin_clr = CLR_LABEL_DIM;
      }
      else
      {
         // Show bucket fill as visual bar
         string bar = "";
         int fill_pct = MathMin(100, (int)((double)vpin.count / MAX_BUCKETS * 100));
         int blocks = fill_pct / 10;
         for(int b = 0; b < 10; b++)
            bar += (b < blocks) ? "\x2588" : "\x2591"; // filled/empty block
         vpin_status = StringFormat("VPIN: %s %d bkt", bar, vpin.count);
         vpin_clr = CLR_LABEL_BLUE;
      }
      DrawLabel(PFX_PANEL + "VP", x, y + line_h * row, vpin_status, vpin_clr, 9);
      row++;
   }

   // VWAP
   if(InpEnableVWAP)
   {
      if(vwap.vwap > 0)
      {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         string pos = (bid > vwap.upper_band) ? " [ABOVE]" :
                      (bid < vwap.lower_band) ? " [BELOW]" : " [INSIDE]";
         color vc = (bid > vwap.upper_band) ? CLR_LABEL_RED :
                    (bid < vwap.lower_band) ? CLR_LABEL_GREEN : CLR_LABEL_BLUE;
         DrawLabel(PFX_PANEL + "VW", x, y + line_h * row,
                   StringFormat("VWAP: %.2f%s", vwap.vwap, pos), vc, 9);
      }
      else
         DrawLabel(PFX_PANEL + "VW", x, y + line_h * row, "VWAP: computing...", CLR_LABEL_DIM, 9);
      row++;
   }

   // Session Breakout
   if(InpEnableBreakout)
   {
      if(sess.asian_range_set)
      {
         double range = sess.asian_high - sess.asian_low;
         DrawLabel(PFX_PANEL + "BR", x, y + line_h * row,
                   StringFormat("Asian: %.1f-%.1f ($%.1f)",
                                sess.asian_low, sess.asian_high, range),
                   CLR_LABEL_GOLD, 9);
      }
      else
         DrawLabel(PFX_PANEL + "BR", x, y + line_h * row, "Asian: building...", CLR_LABEL_DIM, 9);
      row++;
   }

   // Spread
   double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   color sp_clr = spread > InpMaxSpreadDollars ? CLR_LABEL_RED : CLR_LABEL_DIM;
   DrawLabel(PFX_PANEL + "SP", x, y + line_h * row,
             StringFormat("Spread: $%.3f%s", spread, spread > InpMaxSpreadDollars ? " WIDE" : ""),
             sp_clr, 9);
   row++;

   // Daily loss limit remaining
   double loss_remaining = InpMaxDailyLoss + daily.realized_pnl;
   color lr_clr = loss_remaining < InpMaxDailyLoss * 0.3 ? CLR_LABEL_RED : CLR_LABEL_DIM;
   DrawLabel(PFX_PANEL + "DL", x, y + line_h * row,
             StringFormat("Loss budget: $%.2f", MathMax(0, loss_remaining)), lr_clr, 9);
}

//+------------------------------------------------------------------+
//| Clean up all chart objects                                       |
//+------------------------------------------------------------------+
void CleanupChartVisuals()
{
   DeleteObjectsByPrefix(PFX_VWAP);
   DeleteObjectsByPrefix(PFX_ASIAN);
   DeleteObjectsByPrefix(PFX_SIGNAL);
   DeleteObjectsByPrefix(PFX_TP1);
   DeleteObjectsByPrefix(PFX_SESS);
   DeleteObjectsByPrefix(PFX_ATR);
   DeleteObjectsByPrefix(PFX_PANEL);
   DeleteObjectsByPrefix(PFX_TRADE);
   Comment("");
}

#endif
