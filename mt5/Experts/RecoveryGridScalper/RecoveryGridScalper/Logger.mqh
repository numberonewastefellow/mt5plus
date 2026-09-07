//+------------------------------------------------------------------+
//| Logger.mqh - two CSVs: per TRADE and per CYCLE                   |
//|                                                                  |
//| No other EA in this repo writes files; they all Print() to the    |
//| Experts tab, which is lost when the terminal restarts and cannot  |
//| be analysed. These two files are the point of the exercise: they  |
//| record where a cycle started, how far it moved, and when it       |
//| closed - per trade AND per cycle.                                 |
//|                                                                  |
//| Written to <terminal>\MQL5\Files\ and FLUSHED after every row, so |
//| a crash or a margin call loses nothing.                           |
//+------------------------------------------------------------------+
#property strict
#include "Defines.mqh"
#include "Structs.mqh"

class CRgsLogger
  {
private:
   int               m_trades;
   int               m_cycles;
   string            m_ftrades;
   string            m_fcycles;
   long              m_run;            // this attach's id - see Init

   //--- ISO-8601 UTC, so the log lines up with the tick CSVs in analysis/tick_data/
   string            IsoUtc(const datetime t) const
     {
      MqlDateTime s; TimeToStruct(t,s);
      return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                          s.year,s.mon,s.day,s.hour,s.min,s.sec);
     }

   int               Open(const string fname,const string header)
     {
      // FILE_SHARE_READ so the file can be opened in Excel while the EA runs.
      bool exists=FileIsExist(fname);
      int h=FileOpen(fname,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_SHARE_READ,',');
      if(h==INVALID_HANDLE)
        {
         PrintFormat("RGS LOG: cannot open %s (err %d)",fname,GetLastError());
         return INVALID_HANDLE;
        }
      FileSeek(h,0,SEEK_END);
      if(!exists || FileSize(h)==0) { FileWrite(h,header); FileFlush(h); }
      return h;
     }

public:
                     CRgsLogger(): m_trades(INVALID_HANDLE), m_cycles(INVALID_HANDLE) {}

   bool              Init(const long login,const long run_id)
     {
      m_run=run_id;
      MqlDateTime s; TimeToStruct(TimeCurrent(),s);
      string stamp=StringFormat("%04d%02d%02d",s.year,s.mon,s.day);
      // _v2_ IS DELIBERATE. The schema gained columns and the old writer appended the wider
      // rows under the narrower header of an existing file, so every column after `lot` read
      // SHIFTED - which is how a cycle row came to look like it had a 0.315 target when that
      // number was really something else. A new filename means a new header, always.
      m_ftrades=StringFormat("RecoveryGrid_v2_trades_%I64d_%s.csv",login,stamp);
      m_fcycles=StringFormat("RecoveryGrid_v2_cycles_%I64d_%s.csv",login,stamp);

      // run_id first: `cycle_id` restarts at 1 on every re-attach, so on its own it cannot
      // identify a cycle. cycle_id 4 appeared in 8 different OPEN rows from 4 separate runs,
      // which made the two logs impossible to join. (run_id, cycle_id) is the real key.
      m_trades=Open(m_ftrades,
         "run_id,time_msc,iso_utc,cycle_id,event,ticket,side,lot,price,bid,ask,"
         "position_pnl,basket_pnl,n_open,total_lots,balance,equity,free_margin,retcode,comment");
      // `target` is written per cycle because it is FIXED at open and is the number every exit
      // is judged against; without it a row cannot be checked. `depth_cap`/`group` record the
      // sizing the balance implied, so a run can be reconciled against
      // analysis/video_ocr/lot_position_engine.py after the fact.
      // `net_broker` is the figure to trust: profit + swap + commission straight from deal
      // history. `realised` is only the balance step across the close, and reads ~0 when a
      // stop-out booked the loss before the EA noticed. `commission` is in neither the live
      // basket P&L nor `realised`, so it is logged on its own.
      m_cycles=Open(m_fcycles,
         "run_id,cycle_id,start_iso,end_iso,duration_s,direction,lot,group,depth_cap,target,"
         "batches,positions,total_lots,"
         "avg_entry,exit_price,best_pnl,worst_pnl,worst_equity,realised,commission,net_broker,"
         "balance_before,balance_after,close_reason");
      if(m_trades==INVALID_HANDLE || m_cycles==INVALID_HANDLE) return false;
      PrintFormat("RGS LOG run %I64d: %s | %s  (in MQL5\\Files)",m_run,m_ftrades,m_fcycles);
      return true;
     }

   void              Deinit()
     {
      if(m_trades!=INVALID_HANDLE) { FileClose(m_trades); m_trades=INVALID_HANDLE; }
      if(m_cycles!=INVALID_HANDLE) { FileClose(m_cycles); m_cycles=INVALID_HANDLE; }
     }

   //--- One row per fill and per close. `event` is OPEN / CLOSE / REJECT.
   void              Trade(const int cycle_id,const string event,const ulong ticket,
                           const bool is_buy,const double lot,const double price,
                           const double pos_pnl,const double basket_pnl,
                           const int n_open,const double total_lots,
                           const int retcode,const string comment)
     {
      if(m_trades==INVALID_HANDLE) return;
      FileWrite(m_trades,
                (string)m_run,
                (string)((long)TimeCurrent()*1000),
                IsoUtc(TimeCurrent()),
                (string)cycle_id, event, (string)ticket,
                (is_buy?"buy":"sell"),
                DoubleToString(lot,2), DoubleToString(price,3),
                DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_BID),3),
                DoubleToString(SymbolInfoDouble(_Symbol,SYMBOL_ASK),3),
                DoubleToString(pos_pnl,2), DoubleToString(basket_pnl,2),
                (string)n_open, DoubleToString(total_lots,2),
                DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2),
                DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2),
                DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2),
                (string)retcode, comment);
      FileFlush(m_trades);
     }

   //--- One row per completed cycle. `realised` must equal
   //--- balance_after - balance_before; that identity is the log's own check.
   void              Cycle(const SCycle &c,const double exit_price,const double realised,
                           const double balance_after,const string reason,
                           const double commission,const double net_broker)
     {
      if(m_cycles==INVALID_HANDLE) return;
      FileWrite(m_cycles,
                (string)m_run,
                (string)c.id, IsoUtc(c.t_open), IsoUtc(c.t_close),
                (string)(long)(c.t_close-c.t_open),
                (c.is_buy?"buy":"sell"),
                DoubleToString(c.lot,2),
                (string)c.group, (string)c.depth_cap, DoubleToString(c.target,3),
                (string)c.batches, (string)c.positions,
                DoubleToString(c.total_lots,2),
                DoubleToString(c.AvgEntry(),3), DoubleToString(exit_price,3),
                DoubleToString(c.best_pnl,2), DoubleToString(c.worst_pnl,2),
                DoubleToString(c.worst_equity,2), DoubleToString(realised,2),
                DoubleToString(commission,2), DoubleToString(net_broker,2),
                DoubleToString(c.balance_open,2), DoubleToString(balance_after,2),
                reason);
      FileFlush(m_cycles);
      PrintFormat("RGS CYCLE %d %s: %d/%d positions in %d batches of %d, %.2f lots, %ds, "
                  "peak %+.2f worst %.2f vs target %.3f, BROKER NET %+.2f (comm %+.2f), "
                  "balance %.2f -> %.2f (%s)",
                  c.id,(c.is_buy?"BUY":"SELL"),c.positions,c.depth_cap,c.batches,c.group,
                  c.total_lots,(int)(c.t_close-c.t_open),c.best_pnl,c.worst_pnl,c.target,
                  net_broker,commission,c.balance_open,balance_after,reason);
     }

   //--- A free-form note in the trade log, for state changes worth auditing.
   void              Note(const int cycle_id,const string what)
     {
      Trade(cycle_id,"NOTE",0,true,0.0,0.0,0.0,0.0,0,0.0,0,what);
     }
  };
//+------------------------------------------------------------------+
