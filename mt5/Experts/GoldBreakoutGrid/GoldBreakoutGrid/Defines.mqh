//+------------------------------------------------------------------+
//| Defines.mqh — Constants & enums for Gold Breakout Grid EA        |
//+------------------------------------------------------------------+
#ifndef DEFINES_MQH
#define DEFINES_MQH

#define MAGIC_GRID            20260330       // Unique magic number
#define MAX_GRID_POSITIONS    6              // 1 initial + up to 5 grid adds
#define EA_COMMENT_PREFIX     "GBG"          // Comment tag for our orders
#define EA_VERSION            "4.20"

//--- Signal direction
enum ENUM_SIGNAL_DIR
{
   SIGNAL_NONE =  0,
   SIGNAL_BUY  =  1,
   SIGNAL_SELL = -1
};

//--- Grid group lifecycle
enum ENUM_GRID_STATUS
{
   GRID_INACTIVE = 0,   // No active grid
   GRID_ACTIVE   = 1,   // Grid running (initial or with adds)
   GRID_STALE    = 2,   // Grid adds halted (time decay)
   GRID_CLOSED   = 3    // Grid closed (TP / uncle / manual)
};

//--- Close reasons
enum ENUM_CLOSE_REASON
{
   CLOSE_NONE       = 0,
   CLOSE_TP_INITIAL = 1,  // Initial TP hit (no grid was needed)
   CLOSE_TP_GROUP   = 2,  // Group TP hit (grid recovery)
   CLOSE_UNCLE      = 3,  // Basket max loss
   CLOSE_STALE      = 4,  // Time decay forced close
   CLOSE_MANUAL     = 5,  // User closed
   CLOSE_DAILY_LOSS = 6   // Daily loss limit
};

#endif
