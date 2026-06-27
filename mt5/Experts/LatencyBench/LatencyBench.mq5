//+------------------------------------------------------------------+
//|                                                LatencyBench.mq5  |
//|     Measures tick inter-arrival time and (optional) order        |
//|     round-trip latency. Run on the VPS BEFORE deploying the      |
//|     real EA to confirm co-location is actually working.          |
//+------------------------------------------------------------------+
//| Measure-only by default (PlaceTestOrders=false). Logs percentile |
//| stats of tick gaps every ReportEverySec seconds.                 |
//|                                                                  |
//| Pass criteria (Exness + correctly co-located LD4 VPS):           |
//|   - p50 tick gap during London session: < ~100 ms                |
//|   - p99 tick gap during London session: < ~500 ms                |
//|                                                                  |
//| To measure full tick-to-fill round-trip, set PlaceTestOrders=true|
//| on a DEMO account (sends 0.01 lot market BUYs and immediately    |
//| closes them; never use on a live account).                       |
//+------------------------------------------------------------------+
#property copyright "User"
#property version   "1.00"
#property strict
#property description "Latency benchmark for the VPS. Measure-only by default."

input bool   PlaceTestOrders = false;   // DEMO ONLY. Sends round-trip test orders.
input double TestLotSize     = 0.01;
input int    TestEverySec    = 60;      // Interval between test orders
input int    ReportEverySec  = 30;
input ulong  MagicNumber     = 20260628;

//--- Tick-gap ring buffer
const int RING_MAX = 20000;
double    g_gaps_ms[];
int       g_gap_count   = 0;
ulong     g_last_tick_us= 0;

//--- Order round-trip tracking
ulong     g_pending_send_us = 0;     // 0 = no order in flight
ulong     g_rt_ms[];
int       g_rt_count    = 0;
datetime  g_last_test   = 0;
datetime  g_last_report = 0;

MqlTradeRequest g_req;
MqlTradeResult  g_res;
ENUM_ORDER_TYPE_FILLING g_filling = ORDER_FILLING_IOC;

//+------------------------------------------------------------------+
int OnInit()
{
    ArrayResize(g_gaps_ms, RING_MAX);
    ArrayResize(g_rt_ms,   RING_MAX);
    g_last_report = TimeCurrent();
    g_last_test   = TimeCurrent();

    int modes = (int)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
    if((modes & SYMBOL_FILLING_IOC) != 0)      g_filling = ORDER_FILLING_IOC;
    else if((modes & SYMBOL_FILLING_FOK) != 0) g_filling = ORDER_FILLING_FOK;
    else                                        g_filling = ORDER_FILLING_RETURN;

    ZeroMemory(g_req);
    g_req.action       = TRADE_ACTION_DEAL;
    g_req.symbol       = _Symbol;
    g_req.volume       = TestLotSize;
    g_req.deviation    = 100;
    g_req.magic        = MagicNumber;
    g_req.type_filling = g_filling;
    g_req.comment      = "latency-bench";

    PrintFormat("LatencyBench started. symbol=%s PlaceTestOrders=%s TestLot=%.2f filling=%d",
                _Symbol,
                PlaceTestOrders ? "TRUE (DEMO ONLY)" : "false",
                TestLotSize, g_filling);
    if(PlaceTestOrders && AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
    {
        Print("ABORT: PlaceTestOrders=true on a REAL account. Refusing to start.");
        return INIT_FAILED;
    }
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
    ulong now_us = GetMicrosecondCount();

    // 1) Tick-gap measurement
    if(g_last_tick_us > 0)
    {
        double gap_ms = (now_us - g_last_tick_us) / 1000.0;
        g_gaps_ms[g_gap_count % RING_MAX] = gap_ms;
        g_gap_count++;
    }
    g_last_tick_us = now_us;

    // 2) Periodic test order (DEMO ONLY)
    if(PlaceTestOrders && g_pending_send_us == 0)
    {
        datetime now = TimeCurrent();
        if(now - g_last_test >= TestEverySec)
        {
            send_test_order(now_us);
            g_last_test = now;
        }
    }

    // 3) Periodic report
    datetime now_t = TimeCurrent();
    if(now_t - g_last_report >= ReportEverySec)
    {
        report();
        g_last_report = now_t;
    }
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest&     request,
                        const MqlTradeResult&      result)
{
    if(!PlaceTestOrders) return;
    if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
    if((ulong)trans.deal == 0) return;
    if(!HistoryDealSelect(trans.deal)) return;
    if((ulong)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != MagicNumber) return;

    long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
    if(entry != DEAL_ENTRY_IN) return; // measure on the fill of the OPEN side only

    if(g_pending_send_us > 0)
    {
        double rt_ms = (GetMicrosecondCount() - g_pending_send_us) / 1000.0;
        g_rt_ms[g_rt_count % RING_MAX] = rt_ms;
        g_rt_count++;
        g_pending_send_us = 0;

        // Close the test position immediately
        ulong pos_ticket = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
        if(pos_ticket != 0) close_position(pos_ticket);
    }
}

//+------------------------------------------------------------------+
void send_test_order(ulong now_us)
{
    MqlTick tk;
    if(!SymbolInfoTick(_Symbol, tk)) return;
    g_req.type  = ORDER_TYPE_BUY;
    g_req.price = tk.ask;
    g_req.sl    = 0;
    g_req.tp    = 0;
    g_pending_send_us = GetMicrosecondCount();
    if(!OrderSend(g_req, g_res))   // SYNCHRONOUS for true round-trip measurement
    {
        PrintFormat("Test OrderSend failed: retcode=%d %s", g_res.retcode, g_res.comment);
        g_pending_send_us = 0;
    }
}

//+------------------------------------------------------------------+
void close_position(ulong pos_ticket)
{
    if(!PositionSelectByTicket(pos_ticket)) return;
    MqlTradeRequest req; ZeroMemory(req);
    MqlTradeResult  res;
    req.action       = TRADE_ACTION_DEAL;
    req.position     = pos_ticket;
    req.symbol       = _Symbol;
    req.volume       = PositionGetDouble(POSITION_VOLUME);
    req.type         = ORDER_TYPE_SELL;
    req.price        = SymbolInfoDouble(_Symbol, SYMBOL_BID);
    req.deviation    = 100;
    req.magic        = MagicNumber;
    req.type_filling = g_filling;
    OrderSend(req, res);
}

//+------------------------------------------------------------------+
void report()
{
    int n = (g_gap_count < RING_MAX) ? g_gap_count : RING_MAX;
    if(n >= 10)
    {
        double sorted[];
        ArrayResize(sorted, n);
        for(int i = 0; i < n; i++) sorted[i] = g_gaps_ms[i];
        ArraySort(sorted);
        PrintFormat("[tick-gaps] n=%d  p50=%.1fms  p90=%.1fms  p99=%.1fms  max=%.1fms",
                    n, sorted[n/2], sorted[(n*9)/10],
                    sorted[(n*99)/100], sorted[n-1]);
    }
    int m = (g_rt_count < RING_MAX) ? g_rt_count : RING_MAX;
    if(m >= 5)
    {
        double rt_sorted[];
        ArrayResize(rt_sorted, m);
        for(int i = 0; i < m; i++) rt_sorted[i] = g_rt_ms[i];
        ArraySort(rt_sorted);
        PrintFormat("[order-roundtrip] n=%d  p50=%.1fms  p90=%.1fms  p99=%.1fms  max=%.1fms",
                    m, rt_sorted[m/2], rt_sorted[(m*9)/10],
                    rt_sorted[(m*99)/100], rt_sorted[m-1]);
    }
}
//+------------------------------------------------------------------+
