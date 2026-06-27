//+------------------------------------------------------------------+
//| ChartVisuals.mqh — Dashboard, Asian range, grid levels           |
//+------------------------------------------------------------------+
#ifndef CHARTVISUALS_MQH
#define CHARTVISUALS_MQH

#include "Defines.mqh"
#include "Structs.mqh"
#include "Utils.mqh"
#include "TrendFilter.mqh"

//--- Object prefixes for cleanup
#define PFX_DASH  "GBG_D_"
#define PFX_RANGE "GBG_R_"
#define PFX_GRID  "GBG_G_"
#define PFX_SIG   "GBG_S_"
#define PFX_SESS  "GBG_SS_"

//--- Colors
#define CLR_BG         C'15,17,23'
#define CLR_TEXT        C'230,235,240'
#define CLR_DIM        C'120,130,145'
#define CLR_GREEN       clrLime
#define CLR_RED         clrOrangeRed
#define CLR_GOLD        clrGold
#define CLR_CYAN        clrDodgerBlue
#define CLR_WARN        clrYellow
#define CLR_RANGE_FILL  C'50,45,10'
#define CLR_GRID_LINE   C'0,180,255'
#define CLR_UNCLE       clrRed

//+------------------------------------------------------------------+
//| Delete all objects with given prefix                              |
//+------------------------------------------------------------------+
void DeleteByPrefix(string prefix)
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
//| Create/update a label (pixel-anchored)                           |
//+------------------------------------------------------------------+
void DrawLabel(string name, int x, int y, string text, color clr,
               int font_size = 9, int anchor = ANCHOR_LEFT_UPPER)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
}

//+------------------------------------------------------------------+
//| Create a horizontal price line                                   |
//+------------------------------------------------------------------+
void DrawHLine(string name, double price, color clr, int width = 1,
               int style = STYLE_SOLID, string tooltip = "")
{
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
   if(tooltip != "")
      ObjectSetString(0, name, OBJPROP_TOOLTIP, tooltip);
}

//+------------------------------------------------------------------+
//| Create a filled rectangle (for panel background)                 |
//+------------------------------------------------------------------+
void DrawPanelBG(string name, int x, int y, int width, int height, color clr)
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
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, C'40,45,55');
}

//+------------------------------------------------------------------+
//| Draw a signal arrow on the chart                                 |
//+------------------------------------------------------------------+
void DrawSignalArrow(ENUM_SIGNAL_DIR dir, double price, int confidence, string reason)
{
   string name = PFX_SIG + TimeToString(TimeCurrent(), TIME_SECONDS);
   int arrow_code = (dir == SIGNAL_BUY) ? 233 : 234;
   color clr      = (dir == SIGNAL_BUY) ? CLR_GREEN : CLR_RED;
   double offset  = (dir == SIGNAL_BUY) ? -2.0 : 2.0;

   ObjectCreate(0, name, OBJ_ARROW, 0, TimeCurrent(), price + offset);
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, arrow_code);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetString(0, name, OBJPROP_TOOLTIP,
                   StringFormat("%s %d%% — %s", DirToString(dir), confidence, reason));

   // Confidence label next to arrow
   string lbl = name + "_C";
   ObjectCreate(0, lbl, OBJ_TEXT, 0, TimeCurrent(), price + offset * 2);
   ObjectSetString(0, lbl, OBJPROP_TEXT, StringFormat("%d%%", confidence));
   ObjectSetInteger(0, lbl, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
   ObjectSetString(0, lbl, OBJPROP_FONT, "Consolas");
}

//+------------------------------------------------------------------+
//| Draw Asian range on chart (horizontal lines)                     |
//+------------------------------------------------------------------+
void DrawAsianRange(const AsianRangeState &state)
{
   DeleteByPrefix(PFX_RANGE);

   if(!state.range_set || state.high == 0)
      return;

   double buffer = InpBuffer;

   DrawHLine(PFX_RANGE + "H", state.high, CLR_GOLD, 1, STYLE_SOLID,
             StringFormat("Asian High: %.2f", state.high));
   DrawHLine(PFX_RANGE + "L", state.low, CLR_GOLD, 1, STYLE_SOLID,
             StringFormat("Asian Low: %.2f", state.low));
   DrawHLine(PFX_RANGE + "BUY", state.high + buffer, CLR_GREEN, 1, STYLE_DOT,
             StringFormat("BUY trigger: %.2f", state.high + buffer));
   DrawHLine(PFX_RANGE + "SELL", state.low - buffer, CLR_RED, 1, STYLE_DOT,
             StringFormat("SELL trigger: %.2f", state.low - buffer));

   // Midpoint
   double mid = (state.high + state.low) / 2.0;
   DrawHLine(PFX_RANGE + "MID", mid, CLR_DIM, 1, STYLE_DOT,
             StringFormat("Midpoint: %.2f", mid));
}

//+------------------------------------------------------------------+
//| Draw grid level lines on chart                                   |
//+------------------------------------------------------------------+
void DrawGridLevels(const GridGroup &group)
{
   DeleteByPrefix(PFX_GRID);

   if(group.status == GRID_INACTIVE || group.status == GRID_CLOSED)
      return;

   // Draw each position entry level
   for(int i = 0; i < group.position_count; i++)
   {
      if(!group.positions[i].active)
         continue;

      string name = PFX_GRID + "E" + IntegerToString(i);
      color clr   = (i == 0) ? CLR_CYAN : CLR_GRID_LINE;
      int width   = (i == 0) ? 2 : 1;
      string tip  = StringFormat("Grid #%d: %.2f (%.2f lots)", i,
                                 group.positions[i].open_price,
                                 group.positions[i].volume);
      DrawHLine(name, group.positions[i].open_price, clr, width, STYLE_SOLID, tip);
   }

   // Average entry line
   DrawHLine(PFX_GRID + "AVG", group.avg_entry, CLR_WARN, 2, STYLE_DASH,
             StringFormat("Avg Entry: %.2f", group.avg_entry));

   // Group TP line
   DrawHLine(PFX_GRID + "TP", group.group_tp, CLR_GREEN, 2, STYLE_DASH,
             StringFormat("Group TP: %.2f", group.group_tp));

   // Uncle point line (projected price where max loss hits)
   double contract   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double max_loss   = GetEffectiveMaxLoss(group);
   if(group.total_lots > 0 && contract > 0)
   {
      double uncle_dist = max_loss / (group.total_lots * contract);
      double uncle_price;
      if(group.direction == SIGNAL_BUY)
         uncle_price = group.avg_entry - uncle_dist;
      else
         uncle_price = group.avg_entry + uncle_dist;

      DrawHLine(PFX_GRID + "UNCLE", uncle_price, CLR_UNCLE, 1, STYLE_DOT,
                StringFormat("Uncle Point: %.2f (-$%.0f)", uncle_price, max_loss));
   }
}

//+------------------------------------------------------------------+
//| Draw session separator vertical lines                            |
//+------------------------------------------------------------------+
void DrawSessionSeparators()
{
   DeleteByPrefix(PFX_SESS);

   MqlDateTime dt;
   TimeCurrent(dt);

   // Today's London open
   dt.hour = InpLondonStart;
   dt.min  = 0;
   dt.sec  = 0;
   datetime london_t = StructToTime(dt);
   string lon_name = PFX_SESS + "LON";
   ObjectCreate(0, lon_name, OBJ_VLINE, 0, london_t, 0);
   ObjectSetInteger(0, lon_name, OBJPROP_COLOR, C'40,80,40');
   ObjectSetInteger(0, lon_name, OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, lon_name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, lon_name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, lon_name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, lon_name, OBJPROP_TOOLTIP, "London Open");

   // Today's NY open
   dt.hour = InpNYStart;
   datetime ny_t = StructToTime(dt);
   string ny_name = PFX_SESS + "NY";
   ObjectCreate(0, ny_name, OBJ_VLINE, 0, ny_t, 0);
   ObjectSetInteger(0, ny_name, OBJPROP_COLOR, C'40,40,80');
   ObjectSetInteger(0, ny_name, OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, ny_name, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, ny_name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, ny_name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, ny_name, OBJPROP_TOOLTIP, "NY Open");
}

//+------------------------------------------------------------------+
//| Main dashboard panel                                             |
//+------------------------------------------------------------------+
void DrawDashboard(const AsianRangeState &range_state,
                   const GridGroup &grid,
                   const DailyStats &daily)
{
   int x = 10, y = 20;
   int line_h = 16;
   int row = 0;

   // Panel background
   DrawPanelBG(PFX_DASH + "BG", x - 5, y - 5, 310, 380, CLR_BG);

   // Title + version + mode
   string mode = (InpMaxGridLevels == 0) ? "TP-ONLY" : "GRID";
   DrawLabel(PFX_DASH + "T0", x, y + line_h * row++,
             "GOLD BREAKOUT  v" + EA_VERSION + "  [" + mode + "]", CLR_GOLD, 11);
   DrawLabel(PFX_DASH + "T1", x, y + line_h * row++,
             "Build: " + TimeToString(__DATETIME__, TIME_DATE|TIME_SECONDS), CLR_DIM, 7);
   row++;

   // --- Triple EMA Trend ---
   ENUM_SIGNAL_DIR trend = GetTrendDirection();
   double ema_f = GetEMAFast();
   double ema_m = GetEMAMid();
   double ema_s = GetEMASlow();
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   color trend_clr = (trend == SIGNAL_BUY) ? CLR_GREEN :
                     (trend == SIGNAL_SELL) ? CLR_RED : CLR_WARN;
   string trend_str = (trend == SIGNAL_BUY)  ? "UPTREND (BUY only)" :
                      (trend == SIGNAL_SELL) ? "DOWNTREND (SELL only)" : "CHOPPY (blocked)";
   DrawLabel(PFX_DASH + "TR", x, y + line_h * row++,
             StringFormat("Trend: %s", trend_str), trend_clr, 9);
   DrawLabel(PFX_DASH + "TR2", x, y + line_h * row++,
             StringFormat("EMA: F=%.1f M=%.1f S=%.1f", ema_f, ema_m, ema_s), CLR_DIM, 8);

   // --- ATR + Fan Spread ---
   double atr = ComputeATR(InpATR_Period, InpATR_Timeframe);
   double fan_now  = GetFanSpreadNow();
   double fan_prev = GetFanSpreadPrev(InpFanLookback);
   bool fan_expanding = (trend == SIGNAL_BUY)  ? (fan_now >= fan_prev) :
                        (trend == SIGNAL_SELL) ? (fan_now <= fan_prev) : true;
   color fan_clr = fan_expanding ? CLR_CYAN : CLR_RED;
   DrawLabel(PFX_DASH + "ATR", x, y + line_h * row++,
             StringFormat("ATR=$%.2f  TP=$%.2f",
                          atr, Clamp(atr * InpTP_ATR_Mult, InpTP_Min, InpTP_Max)),
             CLR_CYAN, 9);
   DrawLabel(PFX_DASH + "FAN", x, y + line_h * row++,
             StringFormat("Fan: %.1f>%.1f %s",
                          fan_prev, fan_now,
                          fan_expanding ? "[EXPANDING]" : "[NARROWING - BLOCKED]"),
             fan_clr, 9);

   // --- Asian Range ---
   if(range_state.range_set)
   {
      DrawLabel(PFX_DASH + "AR", x, y + line_h * row++,
                StringFormat("Asian: %.2f / %.2f  Range=$%.2f  Vol=%.0f",
                             range_state.high, range_state.low,
                             range_state.high - range_state.low,
                             range_state.avg_volume),
                CLR_GOLD, 9);
   }
   else
   {
      int h, m;
      GetBrokerTime(h, m);
      bool in_asian = (h >= InpAsianStart && h < InpAsianEnd);
      string range_msg = in_asian ? "Building..." :
                         (range_state.high > 0) ? "Finalizing..." : "Waiting for Asian session";
      DrawLabel(PFX_DASH + "AR", x, y + line_h * row++,
                StringFormat("Asian: %s", range_msg), CLR_DIM, 9);
   }

   // --- Spread ---
   double spread = GetSpreadDollars();
   color spread_clr = (spread > InpMaxSpread) ? CLR_RED :
                      (spread > InpMaxSpread * 0.7) ? CLR_WARN : CLR_GREEN;
   DrawLabel(PFX_DASH + "SP", x, y + line_h * row++,
             StringFormat("Spread: $%.3f %s", spread,
                          (spread > InpMaxSpread) ? "[WIDE]" : "[OK]"),
             spread_clr, 9);

   // --- News ---
   if(InpNewsFilter)
   {
      bool news_day = IsNewsBlackoutDay();
      DrawLabel(PFX_DASH + "NW", x, y + line_h * row++,
                news_day ? "News: BLACKOUT (no new entries)" : "News: Clear",
                news_day ? CLR_RED : CLR_DIM, 9);
   }

   // --- Friday status ---
   if(InpFridayCloseHour > 0)
   {
      bool fri_close = IsFridayCloseTime(InpFridayCloseHour);
      DrawLabel(PFX_DASH + "FRI", x, y + line_h * row++,
                fri_close ? "Friday: CLOSING (no new trades)" : "Friday: Normal",
                fri_close ? CLR_RED : CLR_DIM, 9);
   }

   row++;

   // --- Position Status ---
   int pos_count = grid.position_count;  // Set by OnTimer from CountOurPositions()
   DrawLabel(PFX_DASH + "GH", x, y + line_h * row++, "--- POSITIONS ---", CLR_TEXT, 9);

   color basket_clr = (grid.basket_pnl >= 0) ? CLR_GREEN : CLR_RED;
   DrawLabel(PFX_DASH + "GS", x, y + line_h * row++,
             StringFormat("Open: %d/%d  Basket: $%.2f",
                          pos_count, InpMaxOpenPositions, grid.basket_pnl),
             basket_clr, 9);

   // Show uncle thresholds
   DrawLabel(PFX_DASH + "G1", x, y + line_h * row++,
             StringFormat("Uncle: pos=$%.0f  global=$%.0f | %s",
                          InpPerPositionUncle, InpGlobalMaxLoss,
                          InpMaxGridLevels == 0 ? "TP-Only" : "Grid"),
             CLR_DIM, 9);

   // Legacy grid display (only if grid mode is enabled)
   if(InpMaxGridLevels > 0 && (grid.status == GRID_ACTIVE || grid.status == GRID_STALE))
   {
      string stale_tag = grid.grid_adds_halted ? " [STALE]" : "";
      color g_pnl_clr = (grid.basket_pnl >= 0) ? CLR_GREEN : CLR_RED;

      DrawLabel(PFX_DASH + "GL1", x, y + line_h * row++,
                StringFormat("%s | Level %d/%d | %.2f lots%s",
                             DirToString(grid.direction), grid.grid_level,
                             InpMaxGridLevels, grid.total_lots, stale_tag),
                CLR_TEXT, 9);

      DrawLabel(PFX_DASH + "GL2", x, y + line_h * row++,
                StringFormat("Avg: %.2f  TP: %.2f", grid.avg_entry, grid.group_tp),
                CLR_CYAN, 9);

      DrawLabel(PFX_DASH + "GL3", x, y + line_h * row++,
                StringFormat("Basket P&L: $%.2f / -$%.0f uncle",
                             grid.basket_pnl, GetEffectiveMaxLoss(grid)),
                g_pnl_clr, 9);

      // Time open
      long elapsed_min = (long)(TimeCurrent() - grid.first_entry_time) / 60;
      long hours = elapsed_min / 60;
      long mins  = elapsed_min % 60;
      DrawLabel(PFX_DASH + "G4", x, y + line_h * row++,
                StringFormat("Open: %dh %dm  Stale at: %dh",
                             hours, mins, InpStaleHours),
                (hours >= InpStaleHours) ? CLR_WARN : CLR_DIM, 9);

      // Position details
      for(int i = 0; i < grid.position_count && i < 6; i++)
      {
         if(!grid.positions[i].active) continue;
         double pos_pnl = 0;
         double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
         if(grid.direction == SIGNAL_BUY)
            pos_pnl = (bid - grid.positions[i].open_price) * grid.positions[i].volume * contract;
         else
            pos_pnl = (grid.positions[i].open_price - SymbolInfoDouble(_Symbol, SYMBOL_ASK))
                      * grid.positions[i].volume * contract;

         color p_clr = (pos_pnl >= 0) ? CLR_GREEN : CLR_RED;
         DrawLabel(PFX_DASH + "P" + IntegerToString(i), x + 10, y + line_h * row++,
                   StringFormat("#%d: %.2f x%.2f  $%.2f",
                                i, grid.positions[i].open_price,
                                grid.positions[i].volume, pos_pnl),
                   p_clr, 8);
      }
   }

   row++;

   // --- Daily Stats ---
   DrawLabel(PFX_DASH + "DH", x, y + line_h * row++, "--- DAILY STATS ---", CLR_TEXT, 9);
   color pnl_clr = (daily.realized_pnl >= 0) ? CLR_GREEN : CLR_RED;
   DrawLabel(PFX_DASH + "D1", x, y + line_h * row++,
             StringFormat("P&L: $%.2f  Grids: %d/%d  W:%d L:%d",
                          daily.realized_pnl, daily.grids_opened,
                          InpMaxDailyTrades, daily.wins, daily.losses),
             pnl_clr, 9);

   // --- Account ---
   double acct_equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double free_pct    = (acct_equity > 0) ? (free_margin / acct_equity * 100.0) : 0;
   color margin_clr   = (free_pct > 70) ? CLR_GREEN :
                        (free_pct > 50) ? CLR_WARN : CLR_RED;
   DrawLabel(PFX_DASH + "D2", x, y + line_h * row++,
             StringFormat("Equity: $%.0f  Free: $%.0f (%.0f%%)",
                          acct_equity, free_margin, free_pct),
             margin_clr, 9);
}

//+------------------------------------------------------------------+
//| Cleanup all chart objects                                        |
//+------------------------------------------------------------------+
void CleanupChartVisuals()
{
   DeleteByPrefix(PFX_DASH);
   DeleteByPrefix(PFX_RANGE);
   DeleteByPrefix(PFX_GRID);
   DeleteByPrefix(PFX_SIG);
   DeleteByPrefix(PFX_SESS);
}

#endif
