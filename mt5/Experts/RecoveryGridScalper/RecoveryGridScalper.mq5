//+------------------------------------------------------------------+
//|                                        RecoveryGridScalper.mq5   |
//|                                                                  |
//|  A single-direction averaging grid with NO STOP LOSS.            |
//|  The OPERATOR picks the side by clicking BUY or SELL on the       |
//|  chart panel; the EA then runs the cycle - opens a batch, adds    |
//|  while underwater, and closes everything on the target.           |
//|                                                                  |
//|  *** DEMO ONLY ***                                               |
//|                                                                  |
//|  This is an averaging martingale that bets available equity and   |
//|  never cuts a loser. Its shape is many small wins then one        |
//|  adverse run that takes the account. Replay over 13 real sessions |
//|  (analysis/video_ocr/, D:\llm\ios\xausd_video_out\REPLAY_RUNS.md) |
//|  found NO configuration that survived, and a $1 account died      |
//|  within 1-3 cycles in all nine tested. The guards below are       |
//|  damage-limiters, not a fix.                                      |
//|                                                                  |
//|  Spec + reference cycles: RecoveryGridScalper\README.md           |
//+------------------------------------------------------------------+
#property copyright "mt5plus"
#property version   "1.00"
#property strict
#property description "Manual-direction recovery grid. DEMO ONLY. No stop loss."

#include "RecoveryGridScalper/Defines.mqh"
#include "RecoveryGridScalper/Structs.mqh"
#include "RecoveryGridScalper/Utils.mqh"
#include "RecoveryGridScalper/TradeExec.mqh"
#include "RecoveryGridScalper/Logger.mqh"
#include "RecoveryGridScalper/Panel.mqh"
#include "RecoveryGridScalper/GridEngine.mqh"

//--- exit -----------------------------------------------------------
//
// BOTH ARMS ARE FRACTIONS OF BALANCE, never a dollar amount and never a price distance.
// That is what makes the rule scale-free: it fitted a $1.47 account and a $1,181 one.
// The previous "$/oz per 0.01 lot" target was proportional to open volume and therefore
// demanded the same price move no matter how deep the basket had gone - which cancelled
// the recovery entirely. See CloseTarget() in GridEngine.mqh.
input group "Exit"
input double ExitTargetPct   = 28.9;  // Close-all target, % of the balance at cycle open
input double ExitGivebackPct = 15.0;  // Or: retrace this % of balance from the basket's peak
//--- sizing ---------------------------------------------------------
//
// The batch and the depth cap are DERIVED FROM BALANCE (Utils.mqh), not set here. The
// operator's own run opened ONE position at $0.30-$0.99 and grew the count with the account
// before the lot moved; a hardcoded batch of 3 put three times that exposure on a $1 account.
input group "Sizing"
input bool   AutoLot        = true;   // Lot from the balance tier table
input double ManualLotInput = 0.0;    // >0 forces this lot (panel box overrides)
//--- add throttle (BOTH must pass) ----------------------------------
input group  "Add throttle"
input double AddStepUSD     = 0.05;   // Adverse move before the next add (measured 0.037-0.070)
input int    AddCooldownSec = 2;      // Minimum seconds between adds
//--- safety ---------------------------------------------------------
input group  "Safety"
input double MinFreeMargin  = 0.20;   // Stop adding below this free margin
input double MaxTotalLots   = 1.00;   // Hard cap on open volume
// A FRACTION OF BALANCE, like every other constant here - not a dollar amount. It was
// `DailyLossKill = 0.50`, a flat $0.50, which on a $98 account halted trading after losing
// 0.5% of it, and which commission alone could spend in two break-even cycles.
// OFF BY DEFAULT: the operator opts in with EnableDailyLossKill, rather than needing to know
// that setting the percentage to 0 is what turns it off.
input bool   EnableDailyLossKill = false; // Halt trading for the day after a loss (see below)
input double DailyLossKillPct    = 50.0;  // ...of this % of the day's opening balance, if enabled
input bool   DemoOnly       = true;   // Refuse to run on a live account
input bool   AutoRestart    = false;  // false = one click, one cycle
input int    Slippage       = 100;    // Max deviation, points
// ACCOUNT_LEVERAGE and OrderCalcMargin() are not trustworthy on every account - one tested
// account reported both as if a lot cost ~$0 margin, while the broker's server actually charged
// ~$22 for a 0.01 lot fill (measured from ACCOUNT_MARGIN_FREE before/after a real order). Left at
// 0, the EA LEARNS its real $-per-lot cost from its own fills instead of trusting either. Set this
// only if you already know the account's real figure and want to skip that learning step.
input double RealMarginPerLotUSD = 0.0; // 0 = auto-calibrate from live fills, >0 = manual override
//--- hotkeys ----------------------------------------------------------
//
// Chart-scoped only: CHARTEVENT_KEYDOWN reaches this EA's OnChartEvent only
// while THIS chart subwindow has keyboard focus, same as a mouse click on
// its panel already is. Plain letters (no modifier) sidestep every MT5
// built-in shortcut, which are all function keys, arrows, Page Up/Down,
// Home/End, +/-, Delete, Esc, or Ctrl+<letter> combos.
input group  "Hotkeys"
input bool   EnableHotkeys  = true;   // false = mouse-click only (original behavior)
input string HotkeyBuy      = "B";
input string HotkeySell     = "S";
input string HotkeyCloseAll = "P";

CRgsExec   g_exec;
CRgsLogger g_log;
CRgsPanel  g_panel;
CRgsEngine g_engine;
bool       g_paused=false;
bool       g_ready=false;
bool       g_lot_edit_active=false;
bool       g_hotkeys_armed=false;   // EnableHotkeys is an input (read-only); this is the live gate
int        g_vk_buy=0;
int        g_vk_sell=0;
int        g_vk_close=0;

//+------------------------------------------------------------------+
//| One letter -> its virtual-key code, or -1 if not exactly one letter |
//+------------------------------------------------------------------+
int RGS_KeyOf(string s)
  {
   StringTrimLeft(s); StringTrimRight(s);
   StringToUpper(s);
   if(StringLen(s)!=1) return(-1);
   return((int)StringGetCharacter(s,0));
  }

//+------------------------------------------------------------------+
//| Resolve the Hotkey* inputs to VK codes. Refuse rather than bind    |
//| something unintended: any bad or colliding letter disables all     |
//| hotkeys for this session instead of guessing what was meant.       |
//+------------------------------------------------------------------+
void ResolveHotkeys()
  {
   g_hotkeys_armed=false;
   if(!EnableHotkeys) return;
   g_vk_buy  =RGS_KeyOf(HotkeyBuy);
   g_vk_sell =RGS_KeyOf(HotkeySell);
   g_vk_close=RGS_KeyOf(HotkeyCloseAll);
   if(g_vk_buy<0 || g_vk_sell<0 || g_vk_close<0)
     {
      Print("RGS: HotkeyBuy/HotkeySell/HotkeyCloseAll must each be exactly one letter - "
            "hotkeys DISABLED for this session. Mouse clicks still work.");
      return;
     }
   if(g_vk_buy==g_vk_sell || g_vk_buy==g_vk_close || g_vk_sell==g_vk_close)
     {
      Print("RGS: HotkeyBuy/HotkeySell/HotkeyCloseAll must all be different letters - "
            "hotkeys DISABLED for this session. Mouse clicks still work.");
      return;
     }
   g_hotkeys_armed=true;
   PrintFormat("RGS: hotkeys armed - BUY [%s]  SELL [%s]  CLOSE ALL [%s]",
               HotkeyBuy,HotkeySell,HotkeyCloseAll);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   // --- DEMO GUARD. Checked before anything else can trade. -----------
   ENUM_ACCOUNT_TRADE_MODE mode=
      (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(DemoOnly && mode!=ACCOUNT_TRADE_MODE_DEMO)
     {
      Print("RGS REFUSING TO RUN: DemoOnly is on and this is not a demo account "
            "(ACCOUNT_TRADE_MODE=",EnumToString(mode),"). This strategy has no stop loss "
            "and no measured edge - do not point it at real money.");
      return(INIT_FAILED);
     }
   if(mode!=ACCOUNT_TRADE_MODE_DEMO)
      Print("RGS *** LIVE ACCOUNT AND DemoOnly IS OFF. This EA has no stop loss. ***");

   if(!SymbolSelect(_Symbol,true))
     {
      Print("RGS: cannot select symbol ",_Symbol);
      return(INIT_FAILED);
     }

   // A run id that changes on every attach. `cycle_id` restarts at 1 each time, so it cannot
   // identify a cycle on its own - (run_id, cycle_id) can.
   if(!g_log.Init(AccountInfoInteger(ACCOUNT_LOGIN),(long)TimeLocal()))
     {
      Print("RGS: logging failed to open - refusing to start. The log IS the point.");
      return(INIT_FAILED);
     }

   ResolveHotkeys();

   g_exec.Init(_Symbol,Slippage);
   double kill_pct=(EnableDailyLossKill ? DailyLossKillPct : 0.0);
   g_engine.Init(_Symbol,GetPointer(g_exec),GetPointer(g_log),
                 ExitTargetPct,ExitGivebackPct,AddStepUSD,AddCooldownSec,
                 MinFreeMargin,MaxTotalLots,kill_pct,AutoRestart,RealMarginPerLotUSD);

   double bal=AccountInfoDouble(ACCOUNT_BALANCE);
   g_panel.Create(ChartID(),AutoLot?RGS_TierLot(bal):ManualLotInput);
   if(ManualLotInput>0.0)
      ObjectSetString(ChartID(),RGS_EDT_LOT,OBJPROP_TEXT,DoubleToString(ManualLotInput,2));

   PrintFormat("RGS started on %s | exit %.1f%% of balance, or %.1f%% give-back from peak | "
               "batch %d and depth cap %d at this balance | add step %.3f + %ds | "
               "backstops: lots %.2f, free margin %.2f, kill %.2f",
               _Symbol,ExitTargetPct,ExitGivebackPct,
               RGS_GroupFor(bal,RGS_TierLot(bal)),RGS_MaxPositionsFor(bal,RGS_TierLot(bal)),
               AddStepUSD,AddCooldownSec,MaxTotalLots,MinFreeMargin,
               (kill_pct>0.0 ? kill_pct/100.0*bal : 0.0));
   RGS_WarnExposure(_Symbol,bal);
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("RGS: ALGO TRADING IS OFF in the terminal - the panel buttons will be "
            "logged but every order is rejected. Click the toolbar 'Algo Trading' button.");
   else if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
      Print("RGS: this EA is not allowed to trade - tick 'Allow Algo Trading' in its "
            "properties (right-click the chart -> Expert Advisors -> Properties).");
   g_log.Note(0,StringFormat("EA start bal=%.2f target_pct=%.1f giveback_pct=%.1f "
                             "batch=%d cap=%d step=%.3f cd=%d",
                             bal,ExitTargetPct,ExitGivebackPct,
                             RGS_GroupFor(bal,RGS_TierLot(bal)),
                             RGS_MaxPositionsFor(bal,RGS_TierLot(bal)),
                             AddStepUSD,AddCooldownSec));
   g_ready=true;
   EventSetTimer(1);          // panel refresh even on a quiet feed
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   // A basket left open when the EA is removed is NOT closed automatically:
   // silently flattening someone's book on a chart-change or recompile would
   // be worse than leaving it. It is logged loudly instead.
   int n; double tl,np; n=RGS_CountOpen(_Symbol,tl,np);
   if(n>0)
      PrintFormat("RGS *** REMOVED WITH %d POSITION(S) STILL OPEN (%.2f lots, net %+.2f). "
                  "They were NOT closed. Close them yourself or re-attach the EA. ***",
                  n,tl,np);
   g_log.Note(g_engine.CycleId(),StringFormat("EA stop reason=%d open=%d",reason,n));
   g_log.Deinit();
   g_panel.Destroy();
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(!g_ready) return;
   g_engine.OnTick();
   RefreshPanel();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   if(g_ready) RefreshPanel();
  }

//+------------------------------------------------------------------+
void RefreshPanel()
  {
   int n; double lots,net;
   g_engine.Snapshot(n,lots,net);
   string st="IDLE";
   if(g_engine.State()==RGS_RUNNING) st="RUNNING";
   if(g_engine.State()==RGS_HALTED)  st=(g_paused?"PAUSED":"HALTED (kill)");
   double bal=AccountInfoDouble(ACCOUNT_BALANCE);
   double tier=RGS_TierLot(bal);
   string warn="";
   if(n>0 && AccountInfoDouble(ACCOUNT_MARGIN_FREE)<MinFreeMargin) warn="free margin at floor";
   if(g_engine.State()==RGS_HALTED && !g_paused)
      warn=StringFormat("daily loss kill %.2f/%.2f - RESUME, or set EnableDailyLossKill=false",
                        -g_engine.DayRealised(),g_engine.KillAt());
   // Last, so it OVERRIDES the others: with trading disabled nothing else on the
   // panel matters. MT5 gives no hover cursor on chart objects, so a button whose
   // orders are all being rejected is indistinguishable from a dead button unless
   // the panel says why. TERMINAL_ = the toolbar's Algo Trading switch,
   // MQL_ = this EA's own "Allow Algo Trading" checkbox; either one blocks orders.
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
      warn="ALGO TRADING OFF - clicks will not trade";
   // While IDLE there is no cycle, so show what the CURRENT balance would size a cycle at -
   // otherwise the panel reads 0/0 and the operator cannot see what a click would do.
   int cap  =(g_engine.State()==RGS_RUNNING ? g_engine.DepthCap() : RGS_MaxPositionsFor(bal,tier));
   int grp  =(g_engine.State()==RGS_RUNNING ? g_engine.Group()    : RGS_GroupFor(bal,tier));
   double tg=(g_engine.State()==RGS_RUNNING ? g_engine.CloseTarget() : ExitTargetPct/100.0*bal);
   // The give-back MUST come from the engine, not be recomputed here. The engine measures it
   // against the cycle's OPENING balance; recomputing it from the LIVE balance gave a different
   // number, because commission is charged as the cycle opens (3 x 0.10 lot = $3.30 on
   // 2026-09-07 c2). The panel then printed "exit if <= -1.46" while the engine was using -1.95.
   // Small, but the panel is the only window into what the engine will do, and `tg` on the same
   // line already comes from the cycle - so the display was mixing two bases.
   double gb=(g_engine.State()==RGS_RUNNING ? g_engine.GiveBack() : ExitGivebackPct/100.0*bal);
   g_panel.Update(st,g_engine.CycleId(),g_engine.IsBuy(),n,lots,net,
                  tg,tier,warn,cap,grp,g_engine.BestPnl(),
                  gb,g_engine.MarginPerLot());
  }

//+------------------------------------------------------------------+
//| Actions shared by the mouse-click and hotkey paths, so neither     |
//| can drift from the other - both call exactly these.                |
//+------------------------------------------------------------------+
void DoStartCycle(const bool is_buy,const string origin)
  {
   double manual=g_panel.ManualLot();
   double lot=(manual>0.0 ? manual
                          : (AutoLot ? RGS_TierLot(AccountInfoDouble(ACCOUNT_BALANCE))
                                     : ManualLotInput));
   PrintFormat("RGS: operator %s %s, lot %.2f (%s)",
               origin,(is_buy?"BUY":"SELL"),lot,(manual>0.0?"manual override":"auto tier"));
   g_engine.StartCycle(is_buy,lot);
   RefreshPanel();
  }

void DoCloseAll(const string origin)
  {
   PrintFormat("RGS: operator %s CLOSE ALL.",origin);
   g_engine.CloseNow(RGS_CLOSE_MANUAL);
   RefreshPanel();
  }

void DoTogglePause(const string origin)
  {
   g_paused=!g_paused;
   g_engine.SetPaused(g_paused);
   g_panel.SetPauseText(g_paused);
   PrintFormat("RGS: %s %s (open positions are untouched)",origin,(g_paused?"PAUSED":"RESUMED"));
   RefreshPanel();
  }

//+------------------------------------------------------------------+
//| Panel clicks + hotkeys                                            |
//+------------------------------------------------------------------+
void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
  {
   if(id==CHARTEVENT_OBJECT_CLICK)
     {
      // Entering the lot box: latch so a hotkey letter typed as part of the
      // override number can never be read as a trade command (belt and
      // suspenders - see g_lot_edit_active declaration).
      if(sparam==RGS_EDT_LOT) { g_lot_edit_active=true; return; }

      if(sparam==RGS_BTN_BUY || sparam==RGS_BTN_SELL)
        {
         g_panel.Release(sparam);
         DoStartCycle(sparam==RGS_BTN_BUY,"clicked");
         return;
        }

      if(sparam==RGS_BTN_CLOSE)
        {
         g_panel.Release(sparam);
         DoCloseAll("clicked");
         return;
        }

      if(sparam==RGS_BTN_PAUSE)
        {
         g_panel.Release(sparam);
         DoTogglePause("clicked");
        }
      return;
     }

   if(id==CHARTEVENT_OBJECT_ENDEDIT)
     {
      if(sparam==RGS_EDT_LOT) g_lot_edit_active=false;
      return;
     }

   if(id==CHARTEVENT_KEYDOWN)
     {
      // Chart-scoped by construction: this only fires while THIS chart
      // subwindow has keyboard focus, same as the mouse click already is.
      if(!g_hotkeys_armed || g_lot_edit_active) return;
      int key=(int)lparam;
      if(key==g_vk_buy)   { DoStartCycle(true, "pressed hotkey"); return; }
      if(key==g_vk_sell)  { DoStartCycle(false,"pressed hotkey"); return; }
      if(key==g_vk_close) { DoCloseAll("pressed hotkey"); }
     }
  }
//+------------------------------------------------------------------+
