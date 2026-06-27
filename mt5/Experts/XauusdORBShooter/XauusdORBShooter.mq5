//+------------------------------------------------------------------+
//|                                          XauusdORBShooter.mq5    |
//|         Low-latency tick-driven ORB scalper for XAUUSD on MT5    |
//+------------------------------------------------------------------+
//| Long-only. On every tick that breaches the prior closed candle's |
//| high (M1/M5/M15 selectable), fires a market BUY via              |
//| OrderSendAsync with fixed $-distance SL and $-distance TP, up to |
//| MaxStack concurrent positions. Halts trading for the day once    |
//| realized loss reaches MaxDayLossR.                                |
//|                                                                  |
//| Target: <150 ms tick-to-fill on Exness when EA runs on a VPS     |
//| co-located in the broker's datacenter (typically Equinix LD4).   |
//|                                                                  |
//| NOTE: news pause is OFF by design — flat the EA manually around  |
//| scheduled US releases (NFP, CPI, FOMC, etc).                     |
//+------------------------------------------------------------------+
#property copyright "User"
#property version   "1.00"
#property strict
#property description "ORB tick-shooter for XAUUSD. OrderSendAsync; pre-built request; cached ORB high; daily loss cap."

//--- Inputs ---------------------------------------------------------
input group "Strategy"
input ENUM_TIMEFRAMES OrbTimeframe = PERIOD_M5;  // ORB timeframe: M1 / M5 / M15
input double LotSize        = 1.0;               // Lot size per order
input double SL_Distance    = 0.05;              // SL distance below entry (price units)
input double TP_Distance    = 1.00;              // TP distance above entry (price units)
input int    MaxStack       = 10;                // Max concurrent open longs
input double MaxSpread      = 0.25;              // Skip entry if spread > this (price units)
input int    Deviation      = 50;                // Slippage tolerance (points)
input int    MinIntervalMs  = 10;                // Minimum ms between consecutive orders

input group "Risk"
input double MaxDayLossR    = 10.0;              // Halt trading after this many R lost today

input group "Execution"
input ulong  MagicNumber    = 20260627;
input string SymbolOverride = "";                // "" = chart symbol; else e.g. "XAUUSDm"

//--- Globals --------------------------------------------------------
string   g_symbol;
double   g_orb_high       = 0.0;
datetime g_last_bar_time  = 0;
ulong    g_last_order_ms  = 0;
double   g_day_pnl        = 0.0;
datetime g_day_anchor     = 0;
double   g_one_r_dollars  = 0.0;
ENUM_ORDER_TYPE_FILLING g_filling = ORDER_FILLING_IOC;

MqlTradeRequest g_req;
MqlTradeResult  g_res;

//+------------------------------------------------------------------+
int OnInit()
{
    g_symbol = (SymbolOverride == "") ? _Symbol : SymbolOverride;
    if(!SymbolSelect(g_symbol, true))
    {
        PrintFormat("FATAL: symbol not available: %s", g_symbol);
        return INIT_FAILED;
    }

    // Auto-detect filling mode (Exness Standard vs Raw/ECN behave differently)
    int modes = (int)SymbolInfoInteger(g_symbol, SYMBOL_FILLING_MODE);
    if((modes & SYMBOL_FILLING_IOC) != 0)      g_filling = ORDER_FILLING_IOC;
    else if((modes & SYMBOL_FILLING_FOK) != 0) g_filling = ORDER_FILLING_FOK;
    else                                        g_filling = ORDER_FILLING_RETURN;
    PrintFormat("Filling mode detected: %d (mask=%d)", g_filling, modes);

    // Pre-build the request (zero allocation in OnTick hot path)
    ZeroMemory(g_req);
    g_req.action       = TRADE_ACTION_DEAL;
    g_req.symbol       = g_symbol;
    g_req.volume       = LotSize;
    g_req.type         = ORDER_TYPE_BUY;
    g_req.deviation    = Deviation;
    g_req.magic        = MagicNumber;
    g_req.type_filling = g_filling;
    g_req.comment      = "ORB-shooter";

    // 1R in account currency = SL_Distance * LotSize * contract size
    double contract = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_CONTRACT_SIZE);
    g_one_r_dollars = SL_Distance * LotSize * contract;
    PrintFormat("1R per order = %.2f (SL=%.5f x Lot=%.2f x Contract=%.0f). MaxDayLossR=%.1f => stop at -%.2f",
                g_one_r_dollars, SL_Distance, LotSize, contract,
                MaxDayLossR, g_one_r_dollars * MaxDayLossR);

    g_day_anchor    = day_floor(TimeCurrent());
    g_day_pnl       = 0.0;
    g_last_bar_time = 0;
    g_last_order_ms = 0;
    refresh_orb_high();

    PrintFormat("XauusdORBShooter ready. symbol=%s timeframe=%s orb_high=%.5f maxstack=%d",
                g_symbol, EnumToString(OrbTimeframe), g_orb_high, MaxStack);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    PrintFormat("XauusdORBShooter shutdown. reason=%d day_pnl=%.2f", reason, g_day_pnl);
}

//+------------------------------------------------------------------+
void OnTick()
{
    // Day rollover
    datetime today = day_floor(TimeCurrent());
    if(today != g_day_anchor)
    {
        g_day_anchor = today;
        g_day_pnl    = 0.0;
    }

    // Day kill-switch (loss recorded as negative; halt when |loss| >= MaxDayLossR * 1R)
    if(g_one_r_dollars > 0.0 && (-g_day_pnl) >= MaxDayLossR * g_one_r_dollars) return;

    // Refresh cached ORB high on new bar
    datetime bar_time = iTime(g_symbol, OrbTimeframe, 0);
    if(bar_time != g_last_bar_time)
    {
        refresh_orb_high();
        g_last_bar_time = bar_time;
    }
    if(g_orb_high <= 0.0) return;

    // Quote + spread guard
    MqlTick tk;
    if(!SymbolInfoTick(g_symbol, tk)) return;
    if((tk.ask - tk.bid) > MaxSpread) return;

    // Breakout filter
    if(tk.ask <= g_orb_high) return;

    // Stack cap
    if(count_open_longs() >= MaxStack) return;

    // De-dup window
    ulong now_ms = GetMicrosecondCount() / 1000;
    if(now_ms - g_last_order_ms < (ulong)MinIntervalMs) return;

    // Fire
    g_req.price = tk.ask;
    g_req.sl    = NormalizeDouble(tk.ask - SL_Distance, _Digits);
    g_req.tp    = NormalizeDouble(tk.ask + TP_Distance, _Digits);

    if(OrderSendAsync(g_req, g_res))
    {
        g_last_order_ms = now_ms;
    }
    else
    {
        PrintFormat("OrderSendAsync rejected: retcode=%d comment=%s",
                    g_res.retcode, g_res.comment);
    }
}

//+------------------------------------------------------------------+
//| Track realized P&L for the daily kill-switch                     |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest&     request,
                        const MqlTradeResult&      result)
{
    if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
    ulong deal = trans.deal;
    if(deal == 0) return;
    if(!HistoryDealSelect(deal)) return;
    if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != MagicNumber) return;

    double profit = HistoryDealGetDouble(deal, DEAL_PROFIT)
                  + HistoryDealGetDouble(deal, DEAL_SWAP)
                  + HistoryDealGetDouble(deal, DEAL_COMMISSION);
    if(profit != 0.0) g_day_pnl += profit;
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
void refresh_orb_high()
{
    g_orb_high = iHigh(g_symbol, OrbTimeframe, 1); // prior CLOSED bar
}

int count_open_longs()
{
    int n = 0;
    int total = PositionsTotal();
    for(int i = 0; i < total; i++)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0) continue;
        if(PositionGetString(POSITION_SYMBOL) != g_symbol) continue;
        if((ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
        if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) n++;
    }
    return n;
}

datetime day_floor(datetime t)
{
    return t - (t % 86400);
}
//+------------------------------------------------------------------+
