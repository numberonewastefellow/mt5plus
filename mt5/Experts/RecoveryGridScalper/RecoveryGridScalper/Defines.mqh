//+------------------------------------------------------------------+
//| Defines.mqh - constants, enums and panel object names            |
//|                                                                  |
//| RecoveryGridScalper: a single-direction averaging grid with NO    |
//| stop loss. The operator picks the side; the EA runs the cycle.    |
//| DEMO ONLY - see the risk section in ..\README.md.                 |
//+------------------------------------------------------------------+
#property strict

// Magic number. Unique to this EA so it never touches another strategy's
// positions - every position query in this EA filters on it.
#define RGS_MAGIC                 532040

// XAUUSD: 0.01 lot = 1 troy oz, so a $1.00/oz price move = $1.00 P&L on
// 0.01 lot. This is why the P&L on screen equals the price movement.
#define RGS_OZ_PER_LOT            100.0
#define RGS_MIN_LOT_OZ            1.0     // 0.01 lot x 100 oz

// Engine state. ADD and CLOSE are transitions, not resting states.
enum ENUM_RGS_STATE
  {
   RGS_IDLE = 0,      // no basket; waiting for the operator to click a side
   RGS_RUNNING,       // a basket is open and being managed
   RGS_HALTED         // daily loss kill or a safety guard fired; no new cycles
  };

enum ENUM_RGS_CLOSE_REASON
  {
   RGS_CLOSE_TARGET = 0,  // net P&L reached the cycle's fixed $ target
   RGS_CLOSE_GIVEBACK,    // retraced ExitGivebackPct% of balance from the basket's peak
   RGS_CLOSE_MANUAL,      // operator pressed CLOSE ALL
   RGS_CLOSE_KILL,        // daily loss kill
   RGS_CLOSE_DEINIT,      // EA removed with a basket open
   RGS_CLOSE_EXTERNAL,    // the basket vanished without the EA closing it: a broker
                          // stop-out, or a manual close from the Trade tab
   RGS_CLOSE_QUICK        // the time-decayed $/oz floor - see QuickExitPerOz()
  };

//+------------------------------------------------------------------+
//| HOW THE GRID ADDS.                                               |
//|                                                                  |
//| GRID  - add only while the basket is UNDERWATER, on adverse       |
//|         movement. This averages down to recover a loss and is     |
//|         what the video operator did: 33 of 34 clean add events    |
//|         (97%) happened with the basket in loss.                   |
//|                                                                  |
//| TREND - add on movement in EITHER direction. The operator picks   |
//|         the side, so a favourable run is also a reason to add:    |
//|         more ounces on a move that is already working. Same step, |
//|         same cooldown, same depth cap, same margin guard.         |
//|                                                                  |
//| TREND is NOT what the video did - it is a deliberate departure,   |
//| being tested because direction here is a human decision rather    |
//| than the algorithm's. Expect it to enlarge BOTH tails.            |
//+------------------------------------------------------------------+
enum ENUM_RGS_ADD_MODE
  {
   RGS_MODE_TREND = 0,    // add on any move >= step, either direction
   RGS_MODE_GRID          // add only while underwater, on adverse moves
  };

//--- The mode has to appear in the LOG, not just on the panel. A cycle's mode cannot be
//--- recovered from its fills after the fact: when price whipsaws inside the cooldown, GRID
//--- and TREND produce the same adds, and reading a tick window instead of the logged bid/ask
//--- gives the wrong answer (that mistake was made on 2026-09-08 and had to be retracted).
string RGS_AddModeName(const ENUM_RGS_ADD_MODE m)
  {
   return(m==RGS_MODE_GRID ? "GRID" : "TREND");
  }

// Panel object names. Prefixed so OnDeinit can delete exactly ours and
// leave any other chart objects alone.
#define RGS_PFX                   "RGS_"
#define RGS_BTN_BUY               RGS_PFX "btn_buy"
#define RGS_BTN_SELL              RGS_PFX "btn_sell"
#define RGS_BTN_CLOSE             RGS_PFX "btn_close"
#define RGS_BTN_PAUSE             RGS_PFX "btn_pause"
#define RGS_EDT_LOT               RGS_PFX "edt_lot"
#define RGS_LBL_TITLE             RGS_PFX "lbl_title"
#define RGS_LBL_STATE             RGS_PFX "lbl_state"
#define RGS_LBL_CYCLE             RGS_PFX "lbl_cycle"
#define RGS_LBL_BASKET            RGS_PFX "lbl_basket"
#define RGS_LBL_ACCOUNT           RGS_PFX "lbl_account"
#define RGS_LBL_TRAIL             RGS_PFX "lbl_trail"
#define RGS_LBL_MODE              RGS_PFX "lbl_mode"
#define RGS_LBL_PREV              RGS_PFX "lbl_prev"
#define RGS_LBL_LOT               RGS_PFX "lbl_lot"
#define RGS_LBL_WARN              RGS_PFX "lbl_warn"
#define RGS_BG                    RGS_PFX "bg"

// Panel geometry
#define RGS_X                     12
#define RGS_Y                     22
#define RGS_W                     236
#define RGS_ROW                   19
//+------------------------------------------------------------------+
