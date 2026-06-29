/* ============================================================================
   MT5 TERMINAL — app.js  (pure vanilla, no deps)
   ----------------------------------------------------------------------------
   ▸ Wire YOUR FastAPI backend in the API block below. Set API.demo = false and
     fill in the fetch() calls. Until then a built-in demo engine keeps the
     terminal fully live so you can review the UX.
   ============================================================================ */

'use strict';

/* ============================== SYMBOL UNIVERSE ============================ */
const SYMBOLS = {
  EURUSD: { base:1.08540, digits:5, point:0.00001, tickValue:1.0,  vol:0.00004 },
  GBPUSD: { base:1.27310, digits:5, point:0.00001, tickValue:1.0,  vol:0.00005 },
  USDJPY: { base:157.420, digits:3, point:0.001,   tickValue:0.67, vol:0.006   },
  XAUUSD: { base:2348.60, digits:2, point:0.01,    tickValue:1.0,  vol:0.06    },
  US30:   { base:39120.0, digits:1, point:0.1,     tickValue:0.1,  vol:1.2     },
  BTCUSD: { base:62140.0, digits:1, point:0.1,     tickValue:0.01, vol:6.0     },
};

/* ================================ SETTINGS ================================ */
const DEFAULTS = {
  defaultLot:0.01, lotStep:0.01, defaultSl:0, defaultTp:0, maxLot:50,
  sound:true, confirmClose:false, autoSltp:false, netting:true,
  theme:'dark-terminal', symbol:'XAUUSD', tf:15,
};
const THEMES = [
  { id:'dark-terminal', name:'Terminal' },
  { id:'dark-minimal',  name:'Minimal'  },
  { id:'light',         name:'Light'    },
];

function loadSettings(){
  try { return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem('mt5term')||'{}')); }
  catch(e){ return Object.assign({}, DEFAULTS); }
}
function saveSettings(){ localStorage.setItem('mt5term', JSON.stringify(S)); }
let S = loadSettings();

/* ================================== STATE ================================= */
const state = {
  symbol:S.symbol, tf:S.tf, armed:'buy', type:'market',
  lot:S.defaultLot, sl:'', tp:'', limit:'',
  bid:0, ask:0, lastMid:0,
  positions:[],            // {ticket, symbol, side, volume, type, state, entry, limit, sl, tp, slPrice, tpPrice, openTime}
  armedBySym:{},           // per-symbol armed direction
  ticket:1000,
  balance:25000, dailyRealized:0, wins:0, losses:0,
};

/* live price book — every symbol is priced continuously so background P&L,
   pending fills and SL/TP stay correct even while you watch another symbol. */
const prices = {};   // symbol -> {mid, bid, ask}
function seedPrices(){
  Object.keys(SYMBOLS).forEach(s=>{ const c=SYMBOLS[s]; prices[s]={mid:c.base, bid:c.base, ask:c.base}; });
}
function spreadPts(s){ return s==='XAUUSD'?20 : s==='BTCUSD'?40 : s==='US30'?30 : 8; }

/* ============================================================================
   API ADAPTER  —  replace demo bodies with your FastAPI endpoints.
   Every method returns a Promise. The UI only talks to this object.
   ========================================================================== */
const API = {
  base:'',          // same origin — served by the FastAPI backend
  demo:false,       // LIVE: wired to the XauOrderPad backend

  // Set to TRUE by AutoTest.start() and back to FALSE in AutoTest._finish().
  // Causes every /order POST to carry `auto_test:true` so the backend's
  // demo-only guard can refuse live-account requests in defense-in-depth.
  // Manual orders never see this flag and behave unchanged.
  autoTestActive: false,

  async order(req){
    // req = {symbol, side:'buy'|'sell', volume, type:'market'|'limit', price, sl, tp}
    if(this.demo) return demoOrder(req);
    // Bug #2 fix: inject `auto_test:true` ONLY while a run is active.
    // Without this the backend 403 demo-guard was completely dead code.
    const body = this.autoTestActive ? { ...req, auto_test:true } : req;
    const r = await fetch(this.base+'/order', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    if(!r.ok){
      // Backend returns a STRUCTURED 400 detail: {message, retcode, comment}
      // (old string-detail kept as a fallback for any legacy proxy in front).
      // We throw an Error whose .message preserves the human-readable string
      // (so existing toast/log code works unchanged) AND attach .retcode /
      // .comment as properties so AutoTest's burst loop can build a per-retcode
      // histogram without text-parsing.
      let detail = null;
      try { detail = (await r.json()).detail; } catch(_e) {}
      let message = 'order rejected', retcode = null, comment = null;
      if(typeof detail === 'string'){
        message = detail;
      } else if(detail && typeof detail === 'object'){
        message = detail.message || message;
        retcode = (detail.retcode != null) ? Number(detail.retcode) : null;
        comment = detail.comment || null;
      }
      const err = new Error(message);
      err.retcode = retcode;
      err.comment = comment;
      err.httpStatus = r.status;
      throw err;
    }
    return r.json();   // expect {ticket, price}
  },
  async closePosition(ticket){
    if(this.demo) return demoClose(ticket);
    const r = await fetch(this.base+'/close', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ticket})});
    if(!r.ok) throw new Error('close failed');
    return r.json();
  },
  async reducePosition(ticket, volume){
    // partial close — in MT5 this is an opposite deal of `volume` against the ticket
    if(this.demo) return demoReduce(ticket, volume);
    const r = await fetch(this.base+'/close', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ticket, volume})});
    if(!r.ok) throw new Error('partial close failed');
    return r.json();
  },
  async closeAll(symbol){
    if(this.demo) return demoCloseAll();
    const r = await fetch(this.base+'/close_all', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({symbol})});
    if(!r.ok) throw new Error('close-all failed');
    return r.json();
  },
  // The demo provides ticks locally. For live data, poll or open a WebSocket and
  // call applyTick({symbol, bid, ask}) + applyPositions([...]) yourself.
};

/* ================================ HELPERS ================================= */
const $  = s => document.querySelector(s);
const cfg = () => SYMBOLS[state.symbol];
const fmt = (p, d=cfg().digits) => Number(p).toFixed(d);
const fmtS = (p, sym) => Number(p).toFixed(SYMBOLS[sym].digits);   // format for a specific symbol
const money = v => (v>=0?'+':'−') + Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
function pnlOf(pos){
  if(pos.state!=='open') return 0;
  if(pos.liveProfit!=null) return pos.liveProfit;   // LIVE: use broker P/L
  const c = SYMBOLS[pos.symbol], px = prices[pos.symbol];
  if(!px) return 0;
  const price = pos.side==='buy' ? px.bid : px.ask;             // exit at opposite side
  const dir = pos.side==='buy' ? 1 : -1;
  return ((price - pos.entry)/c.point) * dir * c.tickValue * pos.volume;
}

/* ============================== PERSISTENCE (demo) ======================== */
function saveBook(){
  if(!API.demo) return;   // live mode: positions come from your backend, don't cache
  try{ localStorage.setItem('mt5book', JSON.stringify({
    positions:state.positions, ticket:state.ticket, balance:state.balance,
    dailyRealized:state.dailyRealized, wins:state.wins, losses:state.losses,
    armedBySym:state.armedBySym,
  })); }catch(e){}
}
function loadBook(){
  if(!API.demo) return;
  try{
    const b=JSON.parse(localStorage.getItem('mt5book')||'null'); if(!b) return;
    state.positions   = Array.isArray(b.positions)? b.positions : [];
    state.ticket      = b.ticket ?? state.ticket;
    state.balance     = b.balance ?? state.balance;
    state.dailyRealized = b.dailyRealized || 0;
    state.wins=b.wins||0; state.losses=b.losses||0;
    state.armedBySym  = b.armedBySym || {};
  }catch(e){}
}

/* ============================== AUDIO (fills) ============================= */
let actx;
function beep(kind){
  if(!S.sound) return;
  try{
    actx = actx || new (window.AudioContext||window.webkitAudioContext)();
    const o=actx.createOscillator(), g=actx.createGain();
    o.connect(g); g.connect(actx.destination);
    o.frequency.value = kind==='fail'?180 : kind==='sell'?440 : 660;
    o.type='sine';
    g.gain.setValueAtTime(0.0001, actx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.16, actx.currentTime+0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, actx.currentTime+0.16);
    o.start(); o.stop(actx.currentTime+0.17);
  }catch(e){}
}

/* ============================== NOTIFICATIONS ============================= */
function toast(kind, title, body){
  const el=document.createElement('div');
  el.className='toast '+kind;
  el.innerHTML = `<div class="toast-title">${kind==='ok'?'✓':kind==='fail'?'✕':'›'} ${title}</div>`+
                 (body?`<div class="toast-body">${body}</div>`:'');
  $('#toasts').appendChild(el);
  setTimeout(()=>{ el.classList.add('out'); setTimeout(()=>el.remove(),200); }, 2600);
}
function logLine(tag, msg, ok){
  const t=new Date();
  const el=document.createElement('div');
  el.className='log-line';
  const cls = tag==='BUY'?'buy':tag==='SELL'?'sell':tag==='INFO'?'info':'close';
  el.innerHTML =
    `<span class="log-time">${t.toLocaleTimeString('en-GB')}</span>`+
    `<span class="log-tag ${cls}">${tag}</span>`+
    `<span class="log-msg">${msg}</span>`+
    `<span class="log-status ${ok?'ok':'fail'}">${ok?'FILLED':'REJECTED'}</span>`;
  const body=$('#logBody'); body.prepend(el);
  while(body.children.length>120) body.lastChild.remove();
}

/* ================================ DEMO ENGINE ============================= */
function demoOrder(req){
  return new Promise((resolve,reject)=>{
    setTimeout(()=>{
      if(req.volume<=0) return reject(new Error('invalid volume'));
      const px = prices[req.symbol] || prices[state.symbol];
      const fillPrice = req.type==='limit' ? req.price
                        : (req.side==='buy'? px.ask : px.bid);
      const pos = {
        ticket:++state.ticket, symbol:req.symbol, side:req.side, volume:req.volume, type:req.type,
        state:req.type==='limit'?'pending':'open',
        entry:fillPrice, limit:req.type==='limit'?req.price:null,
        sl:req.sl||0, tp:req.tp||0, openTime:Date.now(),
      };
      state.positions.unshift(pos);
      resolve({ticket:pos.ticket, price:fillPrice, state:pos.state});
    }, 55); // simulated round-trip
  });
}
function demoClose(ticket){
  return new Promise(resolve=>setTimeout(()=>{
    const i=state.positions.findIndex(p=>p.ticket===ticket);
    if(i>-1){ realize(state.positions[i]); state.positions.splice(i,1); }
    resolve({ticket});
  },40));
}
function demoReduce(ticket, volume){
  // partially close `volume` lots of a ticket: realize that share of PnL, shrink it
  return new Promise(resolve=>setTimeout(()=>{
    const p=state.positions.find(x=>x.ticket===ticket);
    if(p){
      const portion = Math.min(1, volume/p.volume);
      const part = pnlOf(p)*portion;
      state.dailyRealized += part; state.balance += part;
      p.volume = +(p.volume - volume).toFixed(2);
      if(p.volume <= 1e-9){ const i=state.positions.indexOf(p); if(i>-1) state.positions.splice(i,1); }
    }
    resolve({ticket, volume});
  },40));
}
function demoCloseAll(symbol){
  // close only the given symbol's book (defaults to active symbol)
  return new Promise(resolve=>setTimeout(()=>{
    const sym = symbol || state.symbol;
    const keep=[]; let n=0;
    state.positions.forEach(p=>{ if(p.symbol===sym){ realize(p); n++; } else keep.push(p); });
    state.positions=keep;
    resolve({closed:n});
  },40));
}
function realize(pos){
  if(pos.state!=='open') return;             // pending cancelled, no PnL
  const p=pnlOf(pos);
  state.dailyRealized+=p; state.balance+=p;
  if(p>=0) state.wins++; else state.losses++;
}

/* price random walk (ALL symbols) + pending fills + SL/TP auto-close per symbol */
function tickEngine(){
  // walk every symbol so background books stay live
  Object.keys(SYMBOLS).forEach(s=>{
    const c=SYMBOLS[s], px=prices[s];
    px.mid += (c.base-px.mid)*0.0015 + (Math.random()-0.5)*2*c.vol;   // mean-reverting
    const half=spreadPts(s)*c.point/2;
    px.bid=px.mid-half; px.ask=px.mid+half;
  });
  const prevBid=state.bid;
  const a=prices[state.symbol];
  state.bid=a.bid; state.ask=a.ask; state.lastMid=a.mid;

  let mutated=false;
  // pending fills — each pending evaluated against ITS OWN symbol price
  state.positions.forEach(p=>{
    if(p.state==='pending'){
      const px=prices[p.symbol];
      const hit = p.side==='buy' ? px.ask<=p.limit : px.bid>=p.limit;
      if(hit){ p.state='open'; p.entry=p.limit; p.openTime=Date.now(); mutated=true;
        logLine(p.side.toUpperCase(), `${p.symbol} limit #${p.ticket} filled ${p.volume} @ ${fmtS(p.entry,p.symbol)}`, true);
        toast('ok','Limit filled', `${p.symbol} #${p.ticket} ${p.side.toUpperCase()} ${p.volume}`); beep(p.side);
      }
    }
  });
  // SL / TP auto-close — each position vs its own symbol (works in background too)
  for(let i=state.positions.length-1;i>=0;i--){
    const p=state.positions[i]; if(p.state!=='open') continue;
    const px=prices[p.symbol];
    const exit = p.side==='buy'?px.bid:px.ask;
    let hitSL=false, hitTP=false;
    if(p.sl){ hitSL = p.side==='buy'? exit<=p.slPrice : exit>=p.slPrice; }
    if(p.tp){ hitTP = p.side==='buy'? exit>=p.tpPrice : exit<=p.tpPrice; }
    if(hitSL||hitTP){
      realize(p); state.positions.splice(i,1); mutated=true;
      logLine('CLOSE', `${p.symbol} #${p.ticket} ${hitTP?'take-profit':'stop-loss'} hit @ ${fmtS(exit,p.symbol)}`, true);
      toast(hitTP?'ok':'fail', hitTP?'Take profit':'Stop loss', `${p.symbol} #${p.ticket} @ ${fmtS(exit,p.symbol)}`);
      beep(hitTP?'buy':'fail');
    }
  }
  if(mutated){ renderPositions(); renderMetrics(); saveBook(); }
  renderQuote(prevBid);
}

/* ============================== ORDER ACTIONS ============================= */
function syncFormFromInputs(){
  state.lot   = clampLot(parseFloat($('#lotInput').value)||0);
  state.sl    = $('#slInput').value.trim();
  state.tp    = $('#tpInput').value.trim();
  state.limit = $('#limitPrice').value.trim();
}
function clampLot(v){
  const step=S.lotStep||0.01;
  v=Math.max(step, Math.min(S.maxLot, v));
  return Math.round(v/step)*step;
}
function sltpPrice(side, points, kind){
  // kind 'sl' or 'tp'; returns absolute price or 0
  const pts=parseFloat(points); if(!pts) return 0;
  const c=cfg(), ref = side==='buy'?state.ask:state.bid, off=pts*c.point;
  if(kind==='sl') return side==='buy'? ref-off : ref+off;
  return side==='buy'? ref+off : ref-off;
}

/* Place an order via the keyboard or click path.
 *
 * Returns `{ok, ticket?, error?}` — NEVER `undefined`. Auto-Test's burst loop
 * depends on this shape to count failures correctly; the previous habit of
 * silently returning early on bad health or "no opposite to close" was the
 * source of bug #1 (test always showed PASS because catch-blocks never fired).
 *
 * `override` is forwarded to openOrder. Auto-Test passes
 *   { volume: cfg.lot, sl: 0, tp: 0 }
 * so the burst is fully decoupled from whatever the user has typed in the
 * lot / SL / TP form inputs (bug #3). The reduce-opposite path is NOT used
 * during Auto-Test (the burst always arms first then fires the same side),
 * so override only needs to flow through the open-side branches.
 */
async function placeOrder(side, isMarketKey, override){
  if(!API.demo && !liveHealthy){
    toast('fail','Not ready', liveMsg||'terminal/trading unavailable'); beep('fail');
    logLine(side.toUpperCase(), `Blocked — ${liveMsg||'not ready'}`, false);
    return { ok:false, error: liveMsg || 'not ready' };
  }
  syncFormFromInputs();
  // NOTE: armed direction is sticky — it changes ONLY via B/S (arm()), never by firing.
  const type = isMarketKey ? 'market' : state.type;
  const vol  = (override && 'volume' in override) ? override.volume : state.lot;
  flashAct(side);

  // LIMIT → pending entry (free; pendings are deliberate setups, not direction-locked)
  if(type==='limit'){
    if(!(parseFloat(state.limit)>0)){
      toast('fail','Limit price required','Enter a price or press M for market'); beep('fail');
      logLine(side.toUpperCase(), `Limit ${vol} rejected — no price`, false);
      return { ok:false, error:'limit price required' };
    }
    return openOrder(side, vol, 'limit', override);
  }

  // MARKET with netting ON → the ARMED direction is the only side you may OPEN.
  //   • key that matches armed  → ENTRY (open / add)
  //   • key opposite to armed   → EXIT only: close armed-side positions (FIFO, capped).
  //     If there's nothing on the armed side to close → BLOCK (no reverse). Re-arm to flip.
  if(S.netting !== false){
    const A = state.armed;
    if(side === A) return openOrder(side, vol, 'market', override);  // entry / add in armed dir

    const openArmed = state.positions
      .filter(p=>p.state==='open' && p.side===A && p.symbol===state.symbol)
      .sort((a,b)=>a.openTime-b.openTime);                    // FIFO: oldest first (this symbol)
    const armedVol = +openArmed.reduce((s,p)=>s+p.volume,0).toFixed(2);
    if(armedVol <= 1e-9){
      const ln = A==='buy' ? 'long' : 'short';
      toast('fail', `No ${ln} to close`, `${A.toUpperCase()} armed — press ${A==='buy'?'S':'B'} to trade the other way`);
      beep('fail'); flashBlock(side);
      logLine(side.toUpperCase(), `Blocked — no ${ln} to close (${A.toUpperCase()} armed)`, false);
      return { ok:false, error:`no ${ln} to close` };
    }
    // reduceOpposite does its own logging + still returns undefined; treat as "best-effort ok"
    await reduceOpposite(side, vol, openArmed, armedVol);
    return { ok:true, reduced:true };
  }

  // netting OFF → hedging: open freely in either direction
  return openOrder(side, vol, 'market', override);
}

/* Open a brand-new position (market fill or pending limit).
 *
 * Returns `{ok, ticket?, price?, error?}` — NEVER `undefined`. This shape is
 * required by the Auto-Test burst loop (which counts results), and is also
 * useful for any future caller that needs to react to per-order outcomes.
 *
 * The optional `override` lets callers (Auto-Test) force volume / SL / TP
 * without mutating the on-screen form inputs. When `override` is omitted,
 * the previous form-driven behaviour is preserved exactly.
 *
 *   override = { volume?:number, sl?:number, tp?:number }
 *
 * Side effects (toast / beep / logLine / renderPositions / saveBook) are
 * unchanged — wrapping callers shouldn't have to know they happened.
 */
async function openOrder(side, vol, type, override){
  const useSl = override && 'sl' in override ? override.sl : (parseFloat(state.sl) || 0);
  const useTp = override && 'tp' in override ? override.tp : (parseFloat(state.tp) || 0);
  const useVol = override && 'volume' in override ? override.volume : vol;
  const req = {
    symbol:state.symbol, side, volume:useVol, type,
    price: type==='limit'? parseFloat(state.limit) : (side==='buy'?state.ask:state.bid),
    sl: useSl, tp: useTp,
  };
  try{
    const res = await API.order(req);
    const pos = state.positions.find(p=>p.ticket===res.ticket);
    if(pos){
      // Only re-write SL/TP on the local position object when the form supplied them
      // (override path passes 0/0 deliberately and should leave them at 0).
      if(!override){
        pos.slPrice=sltpPrice(side, state.sl,'sl');
        pos.tpPrice=sltpPrice(side, state.tp,'tp');
      }
    }
    const verb = type==='limit'?'Limit set':'Filled';
    logLine(side.toUpperCase(), `${verb} ${useVol} ${state.symbol} @ ${fmt(req.price)}`+
            (req.sl?` · SL ${req.sl}`:'')+(req.tp?` · TP ${req.tp}`:''), true);
    toast('ok', `${side==='buy'?'BUY':'SELL'} ${type==='limit'?'pending':'filled'}`,
          `#${res.ticket} ${useVol} @ ${fmt(req.price)}`);
    beep(side);
    renderPositions(); renderMetrics(); saveBook();
    return { ok:true, ticket:res.ticket, price:res.price };
  }catch(err){
    // Preserve structured fields that API.order attached to the Error
    // (.retcode / .comment from the backend's 400 detail dict). The
    // Auto-Test burst loop uses these to populate the per-retcode histogram
    // in the audit JSON without any text parsing of err.message.
    logLine(side.toUpperCase(), `${useVol} ${state.symbol} — ${err.message}`, false);
    toast('fail','Order rejected', err.message); beep('fail');
    return {
      ok: false,
      error: err.message,
      retcode: (err && err.retcode != null) ? err.retcode : null,
      comment: (err && err.comment) || null,
    };
  }
}

/* close existing opposite positions FIFO, capped at open volume — never reverses */
async function reduceOpposite(side, vol, openOpp, oppVol){
  const closingName = side==='buy' ? 'short' : 'long';   // the side we're closing
  let toClose = Math.min(vol, oppVol);                   // cap → no over-close, no flip
  const capped = vol > oppVol + 1e-9;
  let closedVol = 0, fullClosed = 0;
  try{
    for(const pos of openOpp){
      if(toClose <= 1e-9) break;
      if(pos.volume <= toClose + 1e-9){
        await API.closePosition(pos.ticket);             // whole ticket (counts W/L)
        closedVol += pos.volume; fullClosed++; toClose -= pos.volume;
      }else{
        await API.reducePosition(pos.ticket, +toClose.toFixed(2)); // partial close
        closedVol += toClose; toClose = 0;
      }
    }
    closedVol = +closedVol.toFixed(2);
    logLine('CLOSE',
      `${side.toUpperCase()} signal closed ${closedVol} ${closingName}`+
      (capped ? ` · capped at open size` : ''), true);
    toast('info', `Closed ${closedVol} ${closingName}`,
      capped ? `Flat — re-arm to trade the other way` : `${closingName} reduced`);
    beep(side);
    renderPositions(); renderMetrics(); saveBook();
  }catch(err){
    toast('fail','Reduce failed', err.message); beep('fail');
  }
}

async function closeOne(ticket){
  try{ await API.closePosition(ticket);
    logLine('CLOSE', `Position #${ticket} closed`, true);
    toast('info','Position closed', `#${ticket}`);
    renderPositions(); renderMetrics(); saveBook();
  }catch(e){ toast('fail','Close failed', e.message); beep('fail'); }
}
async function closeLast(){
  const open=state.positions.filter(p=>p.state==='open' && p.symbol===state.symbol);
  if(!open.length){ toast('info','Nothing to close',`No open ${state.symbol} positions`); return; }
  closeOne(open[0].ticket);
}
async function closeAll(){
  // closes the ACTIVE symbol's book (focused trading). Other symbols stay open —
  // jump to them via the book bar to flatten there.
  const n=state.positions.filter(p=>p.symbol===state.symbol).length;
  if(!n){ toast('info','Nothing to close',`No ${state.symbol} positions`); return; }
  if(S.confirmClose && !confirm(`Close all ${n} ${state.symbol} position(s)?`)) return;
  try{ const r=await API.closeAll(state.symbol);
    logLine('CLOSE', `Closed all ${r.closed??n} ${state.symbol} position(s)`, true);
    toast('info','Closed all', `${state.symbol} flattened (${r.closed??n})`);
    renderPositions(); renderMetrics(); saveBook();
  }catch(e){ toast('fail','Close-all failed', e.message); beep('fail'); }
}

/* ============================== FORM CONTROLS ============================= */
function arm(side){
  state.armed=side; state.armedBySym[state.symbol]=side;
  renderArmed(); saveBook();
}
function setLot(v){ state.lot=clampLot(v); $('#lotInput').value=state.lot.toFixed(2); }
function lotStep(dir){ setLot(state.lot + dir*(S.lotStep||0.01)); pop($('#lotInput')); }
function setLotDigit(d){ setLot((d||10)*(S.lotStep||0.01)); pop($('#lotInput')); }
function setType(t){
  state.type=t;
  $('#orderForm').dataset.type=t;
  $('#typeMarket').classList.toggle('active', t==='market');
  $('#typeLimit').classList.toggle('active', t==='limit');
  $('#limitWrap').hidden = t!=='limit';
  if(t==='limit'){
    if(!$('#limitPrice').value) $('#limitPrice').value=fmt(state.lastMid);
    $('#buyMain').textContent='BUY LIMIT'; $('#sellMain').textContent='SELL LIMIT';
    $('#buySub').textContent='@ price'; $('#sellSub').textContent='@ price';
    $('#buyBtn').classList.remove('exit','disabled');
    $('#sellBtn').classList.remove('exit','disabled');
    $('#placeHint').innerHTML='Click <b>BUY</b>/<b>SELL</b> to place a pending limit @ your price';
  }else{
    renderActionRoles();
  }
}
function toggleType(){ setType(state.type==='market'?'limit':'market'); }
function pop(el){ el.animate?.([{transform:'scale(1.06)'},{transform:'scale(1)'}],{duration:120}); }
function flashAct(side){
  const b=$(side==='buy'?'#buyBtn':'#sellBtn');
  b.animate?.([{filter:'brightness(1.8)'},{filter:'brightness(1)'}],{duration:200});
}
function flashBlock(side){
  const b=$(side==='buy'?'#buyBtn':'#sellBtn');
  b.animate?.([{transform:'translateX(0)'},{transform:'translateX(-4px)'},{transform:'translateX(4px)'},{transform:'translateX(0)'}],{duration:200});
}

/* Decide what each action button means right now, given the armed direction.
   Armed side = the only side you may OPEN. The other button becomes CLOSE-only
   (disabled when there's nothing on the armed side to close). */
function renderActionRoles(){
  if(state.type==='limit') return;               // limit labels handled by setType
  const A=state.armed, hedging=S.netting===false;
  const here = p => p.state==='open' && p.symbol===state.symbol;
  const lv=+state.positions.filter(p=>here(p)&&p.side==='buy').reduce((s,p)=>s+p.volume,0).toFixed(2);
  const sv=+state.positions.filter(p=>here(p)&&p.side==='sell').reduce((s,p)=>s+p.volume,0).toFixed(2);
  const set=(id,main,sub,exit,enabled)=>{
    const b=$(id);
    b.querySelector('.act-main').textContent=main;
    b.querySelector('.act-sub').textContent=sub;
    b.classList.toggle('exit', !!exit);
    b.classList.toggle('disabled', !!exit && !enabled);
  };
  if(hedging){
    set('#buyBtn','BUY',fmt(state.ask),false,true);
    set('#sellBtn','SELL',fmt(state.bid),false,true);
    $('#placeHint').innerHTML='Hedging · <span class="hint-key">␣</span> buy · <span class="hint-key">⌫</span> sell — both directions open';
    return;
  }
  if(A==='buy'){
    set('#buyBtn','BUY',fmt(state.ask),false,true);
    set('#sellBtn','CLOSE', lv? `✕ ${lv.toFixed(2)} long` : 'no long', true, lv>1e-9);
    $('#placeHint').innerHTML='<b>BUY armed</b> · <span class="hint-key">␣</span> add long · <span class="hint-key">⌫</span> close longs · <span class="hint-key">S</span> to flip';
  }else{
    set('#sellBtn','SELL',fmt(state.bid),false,true);
    set('#buyBtn','CLOSE', sv? `✕ ${sv.toFixed(2)} short` : 'no short', true, sv>1e-9);
    $('#placeHint').innerHTML='<b>SELL armed</b> · <span class="hint-key">⌫</span> add short · <span class="hint-key">␣</span> close shorts · <span class="hint-key">B</span> to flip';
  }
}

/* ================================ RENDER ================================= */
function renderArmed(){
  const f=$('#orderForm'); f.dataset.armed=state.armed;
  $('#armedText').textContent = state.armed==='buy' ? 'LONG · BUY ARMED' : 'SHORT · SELL ARMED';
  renderActionRoles();
}
function renderQuote(prevBid){
  $('#bidPrice').textContent=fmt(state.bid);
  $('#askPrice').textContent=fmt(state.ask);
  const c=cfg();
  $('#spread').textContent=Math.round((state.ask-state.bid)/c.point);
  // tick flash
  if(prevBid){
    const up=state.bid>prevBid;
    [$('#bidPrice'),$('#askPrice')].forEach(el=>{
      el.classList.remove('up','down'); void el.offsetWidth;
      el.classList.add(up?'up':'down');
    });
  }
  // live sl/tp previews
  $('#slPreview').textContent = state.sl? fmt(sltpPrice(state.armed,state.sl,'sl')) : '—';
  $('#tpPreview').textContent = state.tp? fmt(sltpPrice(state.armed,state.tp,'tp')) : '—';
  renderActionRoles();    // refresh entry price + exit counts each tick
  renderPnlOnly();
}
function renderPositions(){
  const tb=$('#positionsBody'); tb.innerHTML='';
  const list = state.positions.filter(p=>p.symbol===state.symbol);   // active symbol only
  $('#posEmpty').style.display = list.length?'none':'flex';
  $('#posEmpty').textContent = `No ${state.symbol} positions — arm a side and fire.`;
  list.forEach(p=>{
    const tr=document.createElement('tr'); tr.dataset.ticket=p.ticket;
    const cur = p.state==='pending' ? '—' : fmt(p.side==='buy'?state.bid:state.ask);
    const pnl = pnlOf(p);
    const pillCls = p.state==='pending'?'pending':p.side;
    const pillTxt = p.state==='pending'?`${p.side.toUpperCase()} LMT`:p.side.toUpperCase();
    tr.innerHTML=
      `<td><span class="side-pill ${pillCls}">${pillTxt}</span></td>`+
      `<td class="r">${p.volume.toFixed(2)}</td>`+
      `<td class="r">${fmt(p.entry)}</td>`+
      `<td class="r">${cur}</td>`+
      `<td class="r">${p.sl?p.sl:'—'}</td>`+
      `<td class="r">${p.tp?p.tp:'—'}</td>`+
      `<td class="r pnl-cell ${pnl>=0?'pos':'neg'}">${p.state==='pending'?'—':money(pnl)}</td>`+
      `<td class="r"><button class="close-x" data-close="${p.ticket}">✕</button></td>`;
    tb.appendChild(tr);
  });
  renderSymBanner();
}
function renderPnlOnly(){
  // account floating P&L = ALL symbols; positions-panel total = active symbol only
  let acct=0, openAll=0, lotsAll=0, symTotal=0;
  state.positions.forEach(p=>{
    const pnl=pnlOf(p); acct+=pnl;
    if(p.state==='open'){ openAll++; lotsAll+=p.volume; }
    if(p.symbol===state.symbol){
      if(p.state!=='pending') symTotal+=pnl;
      const tr=$(`#positionsBody tr[data-ticket="${p.ticket}"]`);
      if(tr && p.state!=='pending'){
        tr.children[3].textContent=fmt(p.side==='buy'?state.bid:state.ask);
        const cell=tr.children[6]; cell.textContent=money(pnl);
        cell.className='r pnl-cell '+(pnl>=0?'pos':'neg');
      }
    }
  });
  $('#posTotal').textContent=money(symTotal);
  $('#posTotal').className='num '+(symTotal>=0?'pos':'neg');
  const card=$('#floatCard');
  $('#floatingPnl').textContent=money(acct);
  $('#floatingPnl').className='metric-value big '+(acct>=0?'pos':'neg');
  card.classList.toggle('pos', acct>=0); card.classList.toggle('neg', acct<0);
  $('#equity').textContent=(state.balance+acct).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  $('#openCount').textContent=openAll;
  $('#lotSum').textContent=lotsAll.toFixed(2);
  updateSymBannerPnl();
}
function renderMetrics(){
  $('#dailyPnl').textContent=money(state.dailyRealized);
  $('#dailyPnl').className='metric-value '+(state.dailyRealized>=0?'pos':'neg');
  $('#winLoss').textContent=`${state.wins} / ${state.losses}`;
  const tot=state.wins+state.losses;
  $('#winRate').textContent=(tot?Math.round(state.wins/tot*100):0)+'% wr';
  $('#balance').textContent=state.balance.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  renderPnlOnly();
  saveBook();
}

/* ===================== CROSS-SYMBOL "BOOK" BANNER ======================== */
function bookBySymbol(){
  const by={};
  state.positions.forEach(p=>{
    if(p.state!=='open' && p.state!=='pending') return;
    (by[p.symbol] = by[p.symbol] || {n:0, pnl:0}).n++;
    by[p.symbol].pnl += pnlOf(p);
  });
  return by;
}
let _bookSig='';
function renderSymBanner(){
  const bar=$('#symBar'), wrap=$('#topstrip');
  const by=bookBySymbol();
  const syms=Object.keys(by).sort();
  const sig=syms.join(',')+'|'+state.symbol;
  if(sig!==_bookSig){
    _bookSig=sig;
    if(!syms.length){ bar.innerHTML=''; wrap.classList.remove('has-book'); }
    else{
      wrap.classList.add('has-book');
      bar.innerHTML='<span class="sym-bar-label">BOOK</span>'+syms.map(s=>{
        const act=s===state.symbol;
        return `<button class="sym-chip ${act?'active':''}" data-sym="${s}">`+
               `<span class="sc-name">${s}</span>`+
               `<span class="sc-n">${by[s].n}</span>`+
               `<span class="sc-pnl" data-pnl="${s}">0.00</span></button>`;
      }).join('');
    }
  }
  updateSymBannerPnl(by);
}
function updateSymBannerPnl(by){
  by = by || bookBySymbol();
  const bar=$('#symBar'); if(!bar) return;
  Object.keys(by).forEach(s=>{
    const el=bar.querySelector(`[data-pnl="${s}"]`);
    if(el){ el.textContent=money(by[s].pnl); el.className='sc-pnl '+(by[s].pnl>=0?'pos':'neg'); }
  });
}

/* ================================ CLOCK + CANDLE ========================= */
function tickClock(){
  const now=new Date();
  $('#clock').textContent=now.toLocaleTimeString('en-GB');
  // candle timer (uses UTC wall clock, aligned to timeframe)
  const tf=state.tf, sec=now.getUTCHours()*3600+now.getUTCMinutes()*60+now.getUTCSeconds();
  const period=tf*60, elapsed=sec%period, left=period-elapsed;
  $('#candleElapsed').textContent=mmss(elapsed);
  $('#candleLeft').textContent=mmss(left);
  $('#candleFill').style.width=(elapsed/period*100).toFixed(1)+'%';
}
const mmss=s=>String(Math.floor(s/60)).padStart(2,'0')+':'+String(Math.floor(s%60)).padStart(2,'0');

/* ================================ THEME ================================= */
function applyTheme(id){
  document.documentElement.dataset.theme=id;
  S.theme=id; saveSettings();
  $('#themeName').textContent=THEMES.find(t=>t.id===id).name;
}
function cycleTheme(){
  const i=THEMES.findIndex(t=>t.id===S.theme);
  applyTheme(THEMES[(i+1)%THEMES.length].id);
  toast('info','Theme', THEMES.find(t=>t.id===S.theme).name);
}

/* ================================ SYMBOL / TF =========================== */
function setSymbol(sym){
  state.symbol=sym; S.symbol=sym; saveSettings();
  const px=prices[sym] || (prices[sym]={mid:SYMBOLS[sym].base, bid:SYMBOLS[sym].base, ask:SYMBOLS[sym].base});
  state.lastMid=px.mid; state.bid=px.bid; state.ask=px.ask;   // pick up this symbol's live book
  state.armed = state.armedBySym[sym] || 'buy';               // per-symbol armed direction
  if(state.type==='limit') $('#limitPrice').value=fmt(px.mid);
  renderArmed();
  renderQuote(); renderPositions(); renderMetrics();
}
function setTf(tf){
  state.tf=tf; S.tf=tf; saveSettings();
  const map={1:'M1',5:'M5',15:'M15',30:'M30',60:'H1',240:'H4'};
  $('#candleTfLabel').textContent=map[tf]||('M'+tf);
  tickClock();
}

/* ================================ SETTINGS UI =========================== */
function openSettings(){
  $('#setDefaultLot').value=S.defaultLot;
  $('#setLotStep').value=S.lotStep;
  $('#setDefaultSl').value=S.defaultSl;
  $('#setDefaultTp').value=S.defaultTp;
  $('#setMaxLot').value=S.maxLot;
  $('#setSound').checked=S.sound;
  $('#setConfirmClose').checked=S.confirmClose;
  $('#setAutoSltp').checked=S.autoSltp;
  $('#setNetting').checked=S.netting!==false;
  $('#settingsModal').hidden=false;
}
function closeSettings(){ $('#settingsModal').hidden=true; }
function applySettings(){
  S.defaultLot=parseFloat($('#setDefaultLot').value)||0.10;
  S.lotStep=parseFloat($('#setLotStep').value)||0.01;
  S.defaultSl=parseInt($('#setDefaultSl').value)||0;
  S.defaultTp=parseInt($('#setDefaultTp').value)||0;
  S.maxLot=parseFloat($('#setMaxLot').value)||50;
  S.sound=$('#setSound').checked;
  S.confirmClose=$('#setConfirmClose').checked;
  S.autoSltp=$('#setAutoSltp').checked;
  S.netting=$('#setNetting').checked;
  saveSettings();
  // apply defaults to live form
  setLot(S.defaultLot);
  if(S.autoSltp){ $('#slInput').value=S.defaultSl||''; $('#tpInput').value=S.defaultTp||''; }
  closeSettings();
  toast('ok','Settings saved','Defaults applied');
}

/* ================================ KEYBOARD ============================== */
const isTyping = () => {
  const a=document.activeElement, t=a&&a.tagName;
  return t==='INPUT'||t==='SELECT'||t==='TEXTAREA';
};
window.addEventListener('keydown', e=>{
  // settings modal open → Esc closes settings, otherwise let inputs work
  if(!$('#settingsModal').hidden){
    if(e.key==='Escape'){ e.preventDefault(); closeSettings(); }
    return;
  }
  // auto-test modal open → its own field inputs already swallow; Esc closes it
  if($('#autoTestModal') && !$('#autoTestModal').hidden){
    if(e.key==='Escape'){ e.preventDefault(); $('#autoTestModal').hidden = true; }
    return;
  }
  if(isTyping()){
    if(e.key==='Enter'||e.key==='Escape'){ e.preventDefault(); document.activeElement.blur(); syncFormFromInputs(); }
    return;
  }
  // While AutoTest is running, swallow all order-affecting hotkeys to prevent
  // the user from racing the burst engine. Esc is special: it both ABORTS the
  // run AND triggers an immediate closeAll (emergency flatten). Theme / settings
  // / lot-adjust hotkeys stay live — they don't touch the broker.
  const autoTestRunning = (typeof AutoTest !== 'undefined') && AutoTest.st.phase !== 'idle';
  if(autoTestRunning){
    if(e.key==='Escape'){
      e.preventDefault();
      AutoTest.stop('user pressed Esc');
      closeAll();
      return;
    }
    if([' ','Backspace','Delete','Enter','b','B','s','S'].includes(e.key)){
      e.preventDefault();
      toast('info','Auto-Test active','Hotkeys disabled — STOP from the panel to regain control');
      return;
    }
  }
  switch(e.key){
    case ' ':        e.preventDefault(); placeOrder('buy', true);  break; // SPACE = buy now
    case 'Backspace':e.preventDefault(); placeOrder('sell', true); break; // ⌫ = sell now
    case 'Escape':   e.preventDefault(); closeAll(); break;
    case 'Delete':   e.preventDefault(); closeLast(); break;
    case 'b': case 'B': e.preventDefault(); arm('buy'); break;
    case 's': case 'S': e.preventDefault(); arm('sell'); break;
    case 'm': case 'M': e.preventDefault(); toggleType(); break;
    case 't': case 'T': e.preventDefault(); cycleTheme(); break;
    case '+': case '=': e.preventDefault(); lotStep(+1); break;
    case '-': case '_': e.preventDefault(); lotStep(-1); break;
    default:
      if(/^[0-9]$/.test(e.key)){ e.preventDefault(); setLotDigit(parseInt(e.key,10)); }
  }
}, {capture:false});

/* ============================ WIRE DOM EVENTS =========================== */
function bind(){
  // symbol / tf
  const sel=$('#symbolSelect');
  Object.keys(SYMBOLS).forEach(s=>{
    const o=document.createElement('option'); o.value=s; o.textContent=s; sel.appendChild(o);
  });
  sel.value=state.symbol;
  sel.addEventListener('change', ()=>setSymbol(sel.value));
  $('#tfSelect').value=String(state.tf);
  $('#tfSelect').addEventListener('change', e=>setTf(parseInt(e.target.value)));

  // form
  $('#lotInput').addEventListener('change', ()=>setLot(parseFloat($('#lotInput').value)||S.lotStep));
  $('#lotUp').addEventListener('click', ()=>lotStep(+1));
  $('#lotDown').addEventListener('click', ()=>lotStep(-1));
  $('#typeMarket').addEventListener('click', ()=>setType('market'));
  $('#typeLimit').addEventListener('click', ()=>setType('limit'));
  $('#buyBtn').addEventListener('click', ()=>placeOrder('buy', false));   // button respects Market/Limit toggle
  $('#sellBtn').addEventListener('click', ()=>placeOrder('sell', false));
  $('#bidCell').addEventListener('click', ()=>arm('sell'));
  $('#askCell').addEventListener('click', ()=>arm('buy'));
  ['#slInput','#tpInput','#limitPrice'].forEach(s=>$(s).addEventListener('input', syncFormFromInputs));

  // positions close (delegated)
  $('#positionsBody').addEventListener('click', e=>{
    const t=e.target.dataset.close; if(t) closeOne(parseInt(t));
  });

  // book bar — click a symbol chip to jump to that symbol's book
  $('#symBar').addEventListener('click', e=>{
    const chip=e.target.closest('.sym-chip'); if(!chip) return;
    const s=chip.dataset.sym;
    $('#symbolSelect').value=s; setSymbol(s);
  });

  // topbar
  $('#themeBtn').addEventListener('click', cycleTheme);
  $('#settingsBtn').addEventListener('click', openSettings);
  $('#settingsClose').addEventListener('click', closeSettings);
  $('#settingsSave').addEventListener('click', applySettings);
  $('#settingsReset').addEventListener('click', ()=>{ S=Object.assign({},DEFAULTS); saveSettings(); openSettings(); toast('info','Reset','Defaults restored'); });
  $('#settingsModal').addEventListener('click', e=>{ if(e.target.id==='settingsModal') closeSettings(); });
  document.querySelectorAll('.mtab').forEach(b=>b.addEventListener('click', ()=>{
    document.querySelectorAll('.mtab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.tabpane').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    document.querySelector(`.tabpane[data-pane="${b.dataset.tab}"]`).classList.add('active');
  }));
}

/* ================================ BOOT ================================= */
function init(){
  seedPrices();
  if(API.demo){
    loadBook();               // restore demo positions / stats from last session
  }else{
    state.symbol='XAUUSD'; S.symbol='XAUUSD';   // single-symbol lock
  }
  applyTheme(S.theme);
  bind();
  bindAutoTest();                  // wire the AUTO-TEST modal buttons (open / close / start / stop / download)
  setSymbol(state.symbol);
  setTf(state.tf);
  setLot(S.defaultLot);
  if(S.autoSltp){ $('#slInput').value=S.defaultSl||''; $('#tpInput').value=S.defaultTp||''; }
  renderArmed();
  renderPositions();
  renderMetrics();
  logLine('INFO', `Terminal ${API.demo?'DEMO feed':'LIVE feed'} · arm with B/S, fire with Space/⌫`, true);

  // loops
  if(API.demo){
    setInterval(tickEngine, 120);  // demo only: random walk + client SL/TP + pending fills
  }else{
    lockSingleSymbol();
    startLive();                   // real prices/positions over WebSocket
    fetchAccountSafety();          // initial DEMO/REAL badge probe (canonical source)
  }
  setInterval(tickClock, 250);     // clock + candle
  tickClock();
}

// NOTE: init() is invoked at the very END of this file, after every module-level
// `let` (liveHealthy/liveMsg) is initialized. Calling it here put those lets in
// the temporal dead zone and crashed the live (API.demo=false) path.

/* ============================================================================
   LIVE-FEED HOOKS — call these from your WebSocket / poll loop when API.demo=false
   ========================================================================== */
window.applyTick = ({bid, ask})=>{ const pb=state.bid; state.bid=bid; state.ask=ask; renderQuote(pb); };
window.applyPositions = (list)=>{ state.positions=list; renderPositions(); renderMetrics(); };
window.applyAccount = ({balance, dailyRealized, wins, losses})=>{
  if(balance!=null) state.balance=balance;
  if(dailyRealized!=null) state.dailyRealized=dailyRealized;
  if(wins!=null) state.wins=wins; if(losses!=null) state.losses=losses;
  renderMetrics();
};

/* ============================================================================
   LIVE BACKEND CLIENT (XauOrderPad)  —  used when API.demo === false
   Connects to /ws, maps the backend state into the UI's shapes, and gates
   trading on backend health. The broker (MT5) owns SL/TP & fills, so the demo
   tick-engine is NOT started in live mode.
   ========================================================================== */
let liveHealthy = true;     // demo defaults to tradable
let liveMsg = '';

function lockSingleSymbol(){
  const sel=$('#symbolSelect');
  if(sel){ sel.value='XAUUSD'; sel.disabled=true; sel.title='Single-symbol build (XAUUSD)'; }
}

function setHealth(ok, msg){
  liveHealthy = ok; liveMsg = ok ? '' : (msg||'not ready');
  document.body.classList.toggle('unhealthy', !ok);
  let b=document.getElementById('healthBanner');
  if(!b){
    b=document.createElement('div'); b.id='healthBanner';
    b.style.cssText='position:fixed;left:0;right:0;top:0;z-index:9999;padding:6px 10px;'+
      'text-align:center;font:600 13px system-ui,sans-serif;background:#7a1f1f;color:#fff';
    document.body.appendChild(b);
  }
  b.style.display = ok ? 'none' : 'block';
  if(!ok) b.textContent = '⚠ '+liveMsg+' — trading disabled';
}

/* Map a backend position (from worker._poll_state) into the shape the UI's
 * renderers expect. We deliberately preserve `magic` so the Auto-Test sync
 * verifier (reconcileAutoTest) can detect "foreign" positions — i.e.
 * positions on the same symbol opened by something other than this app
 * (e.g. user clicking BUY in the MT5 desktop terminal, or another EA). */
function mapPos(p, isPending){
  const sym = state.symbol, pt = (SYMBOLS[sym] && SYMBOLS[sym].point) || 0.001;
  const entry = p.price_open;
  const slP = p.sl || 0, tpP = p.tp || 0;
  return {
    ticket: p.ticket, symbol: sym,
    side: String(p.side||'').toLowerCase(),
    volume: p.volume, type: isPending ? 'limit' : 'market',
    state: isPending ? 'pending' : 'open',
    entry, limit: isPending ? p.price_open : null,
    sl: slP ? Math.round(Math.abs(entry - slP)/pt) : 0,
    tp: tpP ? Math.round(Math.abs(entry - tpP)/pt) : 0,
    slPrice: slP || 0, tpPrice: tpP || 0,
    openTime: (p.time||0)*1000,
    liveProfit: isPending ? null : (p.profit!=null ? p.profit : null),
    magic: (p.magic != null) ? p.magic : null,   // for reconcileAutoTest foreign-magic check
  };
}

function onState(s){
  const sym = state.symbol;
  if(SYMBOLS[sym]){
    if(s.digits!=null) SYMBOLS[sym].digits = s.digits;
    if(s.point!=null)  SYMBOLS[sym].point  = s.point;
  }
  if(prices[sym] && s.bid!=null && s.ask!=null){
    prices[sym].bid=s.bid; prices[sym].ask=s.ask; prices[sym].mid=(s.bid+s.ask)/2;
  }
  if(s.bid!=null && s.ask!=null) window.applyTick({bid:s.bid, ask:s.ask});

  const open = (s.positions||[]).map(p=>mapPos(p,false));
  const pend = (s.orders||[]).map(p=>mapPos(p,true));
  window.applyPositions(open.concat(pend));

  const acc = s.account||{};
  window.applyAccount({balance:acc.balance, dailyRealized:acc.daily_realized,
                       wins:acc.wins, losses:acc.losses});

  setHealth(!!s.healthy, s.error || (s.connected ? 'trading not allowed' : 'terminal offline'));

  // ---- Auto-Test hooks (run on every /ws frame) -----------------------------
  // We attach BOTH callbacks here so that:
  //  (a) the AutoTest engine sees demo/healthy changes mid-run and can self-abort
  //  (b) reconcileAutoTest updates the SYNC chip + maintains the placed-ticket
  //      ledger and foreign-magic detection (runs always — chip is informative
  //      outside a run too)
  // No second WebSocket is opened. This is the SINGLE consumer of /ws.
  try { if(typeof AutoTest !== 'undefined') AutoTest._onState(s); } catch(e){ console.warn('AutoTest._onState', e); }
  try { if(typeof reconcileAutoTest === 'function') reconcileAutoTest(s); } catch(e){ console.warn('reconcileAutoTest', e); }
  try { if(typeof updateAccountBadgeFromState === 'function') updateAccountBadgeFromState(s); } catch(e){}
}

function startLive(){
  const proto = location.protocol==='https:' ? 'wss' : 'ws';
  let ws;
  const connect = ()=>{
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = ev=>{ try{ onState(JSON.parse(ev.data)); }catch(e){} };
    ws.onclose = ()=>{ setHealth(false,'disconnected from server'); setTimeout(connect, 1000); };
    ws.onerror = ()=>{ try{ ws.close(); }catch(e){} };
  };
  setHealth(false,'connecting…');
  connect();
}

/* ============================================================================
   AUTO-TEST ENGINE  (demo-only burst tester + sync verifier)
   ============================================================================
   Purpose: drive the EXACT live order-placement path used by manual hotkeys
   (placeOrder / closeAll), in BOTH directions (LONG = buy-armed burst,
   SHORT = sell-armed burst), and verify every order is acknowledged by the
   broker via the live /ws feed. Designed to surface bugs in the order path
   BEFORE the user trades real money.

   ─── Safety: three layers, none can be bypassed ──────────────────────────
   (1) START button is disabled until /api/account/safety reports is_demo=true
   (2) AutoTest._verifyDemo() re-checks before every sub-cycle; flips abort if
       the account is no longer demo
   (3) Server /order returns 403 for auto_test=true on non-demo accounts —
       survives JS bugs / console exec / forged curl / mid-session login swap

   ─── Bug-fix cross-references (see plan file) ────────────────────────────
   #1 telemetry double-count → counters live ONLY in _burst's .then handler.
       openOrder/placeOrder now return {ok, ticket, error} so .then sees real
       success/failure. No external onResult hook.
   #2 dead backend guard → API.autoTestActive flag (set true by start(), false
       by _finish()) causes API.order to inject auto_test:true.
   #3 lot/SL/TP override → _burst builds {volume:cfg.lot, sl:0, tp:0} and
       threads it through placeOrder → openOrder; form inputs are ignored.
   #4 not-flat → _runCycle returns false if _waitForBrokerFlat times out, and
       start() exits on a false return BEFORE Cycle B can start. Final closeAll
       always runs in the finally block.
   #5 sync tautology → reconcileAutoTest uses (a) our own placedTickets ledger
       (a Map<ticket,{placedAt,confirmed,latencyMs}>) cross-checked against the
       feed, and (b) foreign-magic detection (positions with a magic other
       than config.MAGIC). The old uiTickets-vs-brokerTickets comparison is
       gone (it was always equal — UI is sourced from feed).
   #6 single WebSocket → we hook the existing onState(s) above. There is no
       second WebSocket anywhere in this file.
   ============================================================================ */

const AUTOTEST_MAGIC = 532026;            // mirrors config.MAGIC in the backend

const AutoTest = {
  /* User-configurable knobs (snapshot at start()). Persisted to localStorage
   * via _readCfgFromForm / _writeCfgToForm so a refresh keeps your last setup.*/
  cfg: {
    rate: 20,             // orders/sec target
    burstCount: 100,      // orders per sub-cycle
    restSec: 5,           // seconds between A_CLOSE and B_OPEN
    lot: 0.01,            // lot size — overrides form
    triggerMode: 'wall_minute',  // 'wall_minute' | 'tf_bar_start' | 'immediate'
    maxOrdersPerRun: 500, // hard cap on total orders (both cycles combined)
    consecFailKill: 10,   // consecutive failures → abort + closeAll
    inFlightCap: 32,      // max pending sends; prevents broker pile-up
  },

  /* Live state — reset on every start() */
  st: {
    phase: 'idle',        // idle | wait_trigger | A_OPEN | A_REST | A_CLOSE | B_OPEN | B_REST | B_CLOSE | done
    cycle: null,          // 'LONG' | 'SHORT' | null
    sent: 0, ok: 0, failed: 0,
    consecFail: 0, inFlight: 0,
    achievedRate: 0,
    abort: null,          // null | 'reason string'
    healthyMissCount: 0,
    placedTickets: new Map(),   // ticket → {placedAt, side, confirmed, latencyMs, lost, requested_price, fill_price, slippage}
    cycleStats: [],       // appended at the end of each sub-cycle for the audit log
    runId: null,
    startedAt: null,
    // Audit fields: starting/ending balance & realised P&L (snapshotted from /ws state.account).
    startAccount: null,   // {balance, equity, daily_realized}  at start()
    endAccount: null,     // {balance, equity, daily_realized}  at _finish()
    // Per-cycle retcode histogram: {cycleLabel: {retcode: count}}.
    // Populated by _burst's failure handler from err.retcode on the thrown Error.
    retcodeHistogram: { LONG: {}, SHORT: {} },
    // Per-cycle slippage observations: arrays of |fill - requested|.
    slippages: { LONG: [], SHORT: [] },
    // burst timing — for achieved-rate display
    _t0: 0, _sentT0: 0,
    // direction-sanity tracking for the burst phase
    _prevNetLots: null,
    _wrongDirFrames: 0,
  },

  /* Public: invoked from the AUTO-TEST modal's START button. */
  async start(){
    if(this.st.phase !== 'idle') return;                    // idempotent
    this._resetState();
    this._readCfgFromForm();

    const safety = await this._verifyDemo();
    if(!safety.is_demo){ return this._fail(`account is NOT demo (login=${safety.login}, mode=${safety.trade_mode})`); }

    const pre = this._preflight();
    if(!pre.ok){ return this._fail(`preflight: ${pre.reason}`); }

    if(!await this._confirmDialog(safety)) return;          // user cancelled

    this._lockManualKeys(true);
    this._lockSymbolSelect(true);
    API.autoTestActive = true;                              // bug #2: backend now sees auto_test
    this.st.startedAt = new Date().toISOString();
    this.st.runId = this._mkRunId();
    // Snapshot starting account state from the most recent /ws frame.
    // Used by _buildAudit to compute realized_pnl + balance_delta for the run.
    this.st.startAccount = this._snapshotAccount();
    logLine('AUTO-TEST', `run ${this.st.runId} START · rate=${this.cfg.rate}/s × ${this.cfg.burstCount} × 2 cycles · lot=${this.cfg.lot} · start_balance=${this.st.startAccount?.balance ?? '?'}`, true);

    // UX fix: close the modal so the user can see the main UI (positions,
    // floating P&L, price ticker) while the run executes. The compact
    // topbar pill provides at-a-glance status + a STOP button without
    // taking over the screen. Clicking the pill re-opens the modal.
    closeAutoTestModal();
    showRunPill();

    try {
      await this._waitForTrigger();
      if(this.st.abort) return;

      const aOk = await this._runCycle('LONG',  'buy');
      if(!aOk || this.st.abort) return;                     // HARD ABORT — no Cycle B

      const safety2 = await this._verifyDemo();
      if(!safety2.is_demo){ this.stop('account changed to non-demo mid-run'); return; }

      const bOk = await this._runCycle('SHORT', 'sell');
      if(!bOk || this.st.abort) return;
    } finally {
      try { await closeAll(); } catch(e){}                  // final safety flatten — never throws
      API.autoTestActive = false;
      this._lockManualKeys(false);
      this._lockSymbolSelect(false);
      this._finish();
    }
  },

  /* Public: STOP button or emergency Esc. Sets abort; the running phase exits
   * at its next checkpoint, finally{} flattens, _finish renders verdict. */
  stop(reason){
    if(this.st.phase === 'idle') return;
    this.st.abort = reason || 'user stop';
    logLine('AUTO-TEST', `STOP requested: ${this.st.abort}`, false);
  },

  /* Run one sub-cycle. Returns true ONLY if it reached flat — caller MUST
   * abort the run on a false return (bug #4: never proceed into B on non-flat). */
  async _runCycle(label, side){
    this.st.cycle = label;
    arm(side);

    await this._burst(side);
    if(this.st.abort) return false;

    this.st.phase = label === 'LONG' ? 'A_REST' : 'B_REST';
    await this._sleep(this.cfg.restSec * 1000);
    if(this.st.abort) return false;

    this.st.phase = label === 'LONG' ? 'A_CLOSE' : 'B_CLOSE';
    try { await closeAll(); } catch(e){ /* tolerated; flat-check below decides */ }

    const flat = await this._waitForBrokerFlat(10000);
    if(!flat){
      this.stop(`failed to flatten after ${label}_CLOSE within 10s`);
      this.st.cycleStats.push({label, sent:this.st.sent, ok:this.st.ok, failed:this.st.failed, flat:false});
      return false;
    }
    this.st.cycleStats.push({label, sent:this.st.sent, ok:this.st.ok, failed:this.st.failed, flat:true});
    return true;
  },

  /* Fire the burst. SINGLE counting site (bug #1) — increments st.ok/st.failed
   * from the {ok,…} return shape only here. No onResult hook anywhere else. */
  async _burst(side){
    this.st.phase = side==='buy' ? 'A_OPEN' : 'B_OPEN';
    const interval = 1000 / Math.max(1, this.cfg.rate);
    const override = { volume: this.cfg.lot, sl: 0, tp: 0 };   // bug #3 fix
    this.st._t0 = performance.now();
    this.st._sentT0 = this.st.sent;
    this.st._prevNetLots = null;
    this.st._wrongDirFrames = 0;

    let done = 0;
    while (done < this.cfg.burstCount &&
           this.st.sent < this.cfg.maxOrdersPerRun &&
           this.st.consecFail < this.cfg.consecFailKill &&
           !this.st.abort) {
      if (this.st.inFlight >= this.cfg.inFlightCap) {
        await this._sleep(5);
        continue;
      }
      this.st.inFlight++; this.st.sent++; done++;
      // Snapshot the reference price RIGHT BEFORE firing — used downstream
      // to compute slippage when the broker reports the actual fill price.
      const requestedPrice = (side === 'buy') ? state.ask : state.bid;
      const cycleLabel = this.st.cycle;   // 'LONG' | 'SHORT'

      // Fire-and-track: do NOT await, so the loop maintains its tick rate.
      // `placeOrder` was refactored to ALWAYS resolve (never reject) — the
      // failure path returns {ok:false, error, retcode, comment}. So all
      // counting happens in .then; .catch is reserved for defensive logging
      // of any unexpected JS error in the callback itself.
      placeOrder(side, true, override).then(r => {
        if (r && r.ok) {
          this.st.ok++;
          this.st.consecFail = 0;
          if (r.ticket != null) {
            // Slippage = |fill_price − requested_price|. r.price is the
            // broker-reported fill price; if null we record null and skip
            // the slippage aggregation for this order (rare path).
            const fillPrice = (r.price != null) ? Number(r.price) : null;
            const slippage = (fillPrice != null && requestedPrice != null)
              ? Math.abs(fillPrice - requestedPrice) : null;
            this.st.placedTickets.set(r.ticket, {
              placedAt: performance.now(),
              side, confirmed: false, latencyMs: null, lost: false,
              requested_price: requestedPrice,
              fill_price: fillPrice,
              slippage: slippage,
            });
            if (slippage != null && cycleLabel && this.st.slippages[cycleLabel]) {
              this.st.slippages[cycleLabel].push(slippage);
            }
          }
        } else {
          // Failure path: bucket the broker retcode into the per-cycle
          // histogram for the audit JSON. `r.retcode` comes from API.order's
          // structured Error → openOrder's catch block (see app.js).
          this.st.failed++;
          this.st.consecFail++;
          const rc = (r && r.retcode != null) ? String(r.retcode) : 'unknown';
          if (cycleLabel && this.st.retcodeHistogram[cycleLabel]) {
            const h = this.st.retcodeHistogram[cycleLabel];
            h[rc] = (h[rc] || 0) + 1;
          }
        }
      }).catch(err => {
        // Defensive: if the .then callback itself threw (logic bug), still
        // count the failure so the run can't silently look successful.
        console.warn('AutoTest _burst .then crashed', err);
        this.st.failed++; this.st.consecFail++;
      }).finally(() => {
        this.st.inFlight--;
        this._updateAchievedRate();
        this._refreshStatusGrid();
      });

      await this._sleep(interval);
    }

    // Drain in-flight requests so we don't enter REST with sends still landing.
    const drainStart = performance.now();
    while (this.st.inFlight > 0 && performance.now() - drainStart < 3000) {
      await this._sleep(20);
    }

    if (this.st.consecFail >= this.cfg.consecFailKill) {
      this.stop(`kill: ${this.st.consecFail} consecutive failures`);
    }
  },

  /* Called from the EXISTING onState(s) — no second WebSocket. */
  _onState(s){
    if(this.st.phase === 'idle') return;
    if(s && s.account && s.account.is_demo === false){
      this.stop('account became non-demo mid-run');
      return;
    }
    if(!s || !s.healthy){
      this.st.healthyMissCount++;
      if(this.st.healthyMissCount > 3) this.stop('healthy=false for >3 frames');
    } else {
      this.st.healthyMissCount = 0;
    }
    // Direction sanity during BURST phase: net_lots must move in armed-side
    // direction (positive for LONG, negative for SHORT). Two consecutive
    // wrong-sign deltas during burst = critical routing bug → abort.
    if((this.st.phase === 'A_OPEN' || this.st.phase === 'B_OPEN') && s && s.net_lots != null){
      const expectedSign = (this.st.phase === 'A_OPEN') ? +1 : -1;
      if(this.st._prevNetLots != null){
        const delta = s.net_lots - this.st._prevNetLots;
        if(delta !== 0 && Math.sign(delta) !== expectedSign){
          this.st._wrongDirFrames++;
          if(this.st._wrongDirFrames >= 2){
            this.stop(`wrong-direction net_lots during ${this.st.phase} (expected ${expectedSign>0?'+':'-'})`);
          }
        } else if(delta !== 0){
          this.st._wrongDirFrames = 0;
        }
      }
      this.st._prevNetLots = s.net_lots;
    } else {
      this.st._prevNetLots = null;
    }
  },

  /* Wait until the broker reports zero positions for the active symbol.
   * Polls UI state.positions (which is already the live feed via onState).
   * Returns true if flat reached; false on timeout. */
  async _waitForBrokerFlat(timeoutMs){
    const start = performance.now();
    while (performance.now() - start < timeoutMs) {
      const open = state.positions.filter(p => p.state === 'open' && p.symbol === state.symbol);
      if(open.length === 0) return true;
      if(this.st.abort) return false;
      await this._sleep(75);  // ~ one /ws frame at POLL_HZ=15
    }
    return false;
  },

  /* Wait for the configured trigger before firing the first burst. */
  async _waitForTrigger(){
    this.st.phase = 'wait_trigger';
    if(this.cfg.triggerMode === 'immediate') return;
    if(this.cfg.triggerMode === 'wall_minute'){
      // Sleep until the next whole minute (local clock). Within ±100ms is fine.
      const ms = 60_000 - (Date.now() % 60_000);
      logLine('AUTO-TEST', `waiting ${(ms/1000).toFixed(1)}s to next :00 minute`, true);
      await this._sleep(ms);
      return;
    }
    if(this.cfg.triggerMode === 'tf_bar_start'){
      // Wait for the candle "elapsed" indicator to wrap from large → near-zero.
      // The candle update loop runs every ~250ms (tickClock) and writes #candleElapsed.
      const el = document.getElementById('candleElapsed');
      let prev = -1;
      const giveUpAt = performance.now() + 16 * 60 * 1000;  // worst case: M15 = 15min
      while(performance.now() < giveUpAt && !this.st.abort){
        const txt = el ? el.textContent || '00:00' : '00:00';
        const [mm, ss] = txt.split(':').map(n => parseInt(n,10) || 0);
        const elapsedSec = mm * 60 + ss;
        if(prev >= 0 && prev > 5 && elapsedSec <= 1) return;   // wrapped → new bar
        prev = elapsedSec;
        await this._sleep(200);
      }
    }
  },

  /* Hit /api/account/safety to confirm DEMO status. Returns the safety object. */
  async _verifyDemo(){
    try {
      const r = await fetch('/api/account/safety', {cache:'no-store'});
      if(!r.ok) return { is_demo:false, login:'?', server:'?', trade_mode:-1, healthy:false };
      return await r.json();
    } catch(e){
      return { is_demo:false, login:'?', server:'?', trade_mode:-1, healthy:false };
    }
  },

  /* Synchronous gates that must all pass before the START button does anything.
   * Returns {ok:bool, reason:string}. */
  _preflight(){
    if(!liveHealthy) return {ok:false, reason:`broker not healthy: ${liveMsg||'unknown'}`};
    if(!(state.bid > 0) || !(state.ask > 0)) return {ok:false, reason:'no quote (bid/ask = 0)'};
    const spread = state.ask - state.bid;
    if(spread > 1.0) return {ok:false, reason:`spread too wide (${spread.toFixed(3)})`};
    if(!(this.cfg.lot > 0)) return {ok:false, reason:'lot must be > 0'};
    // pending orders check — refuse to start if there are existing pendings on the symbol
    const pending = state.positions.filter(p => p.state === 'pending' && p.symbol === state.symbol);
    if(pending.length > 0) return {ok:false, reason:`cancel the ${pending.length} pending order(s) on ${state.symbol} first`};
    return {ok:true, reason:''};
  },

  /* User-facing confirm dialog. Last chance to say no. */
  async _confirmDialog(safety){
    const total = this.cfg.burstCount * 2;
    const msg = `About to place LIVE DEMO orders on ${safety.login}@${safety.server}.\n\n` +
                `~${total} orders (${this.cfg.burstCount} × 2 cycles), lot ${this.cfg.lot} each, rate ${this.cfg.rate}/s.\n\n` +
                `Continue?`;
    return window.confirm(msg);
  },

  /* Block / unblock manual order hotkeys via a flag (the keydown handler reads
   * AutoTest.st.phase directly, so this is just for symmetry / future toggles). */
  _lockManualKeys(_on){ /* keydown handler reads st.phase directly */ },

  /* Disable the symbol selector for the duration of a run so a mid-run
   * symbol change can't orphan positions opened in the original symbol. */
  _lockSymbolSelect(on){
    const sel = $('#symbolSelect');
    if(!sel) return;
    if(on){ sel.dataset.prevDisabled = sel.disabled ? '1':'0'; sel.disabled = true; }
    else  { sel.disabled = sel.dataset.prevDisabled === '1'; }
  },

  /* Compute orders-per-second over the current burst window for the UI. */
  _updateAchievedRate(){
    const elapsed = (performance.now() - this.st._t0) / 1000;
    if(elapsed > 0.05){
      this.st.achievedRate = (this.st.sent - this.st._sentT0) / elapsed;
    }
  },

  /* Render the live status grid in the modal AND the compact topbar pill.
   * Cheap; called from .finally on every order and once more in _finish().
   * The pill mirrors the most important numbers (phase + sent/total) so the
   * user can monitor progress without re-opening the modal. */
  _refreshStatusGrid(){
    const set = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v; };
    set('atPhase', this.st.phase);
    set('atCycle', this.st.cycle || '—');
    set('atSent', String(this.st.sent));
    set('atOk', String(this.st.ok));
    set('atFailed', String(this.st.failed));
    set('atAchRate', this.st.achievedRate.toFixed(1));
    let confirmed = 0, lost = 0;
    for(const r of this.st.placedTickets.values()){
      if(r.confirmed && !r.lost) confirmed++;
      if(r.lost) lost++;
    }
    set('atConfirmed', String(confirmed));
    set('atLost', String(lost));

    // Topbar pill mirrors phase + progress. Two-cycle total = burstCount × 2.
    const totalTarget = (this.cfg.burstCount || 0) * 2;
    set('atRunPhase', this.st.phase);
    set('atRunProgress', `${this.st.sent}/${totalTarget}`);
  },

  _resetState(){
    this.st = {
      phase: 'idle', cycle: null,
      sent: 0, ok: 0, failed: 0,
      consecFail: 0, inFlight: 0, achievedRate: 0,
      abort: null, healthyMissCount: 0,
      placedTickets: new Map(),
      cycleStats: [],
      runId: null, startedAt: null,
      // Audit-only fields
      startAccount: null, endAccount: null,
      retcodeHistogram: { LONG: {}, SHORT: {} },
      slippages: { LONG: [], SHORT: [] },
      // Internal timing / direction state
      _t0: 0, _sentT0: 0,
      _prevNetLots: null, _wrongDirFrames: 0,
    };
  },

  /* Pull the most recent account snapshot from the latest /ws state. The
   * webui's existing `state` global keeps the live broker numbers; we copy
   * the three fields we need so they don't change under us mid-run. Returns
   * `null` if no /ws frame has arrived yet (test will degrade gracefully). */
  _snapshotAccount(){
    if(typeof state === 'undefined') return null;
    return {
      balance:        (typeof state.balance        === 'number') ? state.balance        : null,
      // `state.equity` is not stored on the global state object by the existing
      // applyAccount — but `floating_pl + balance` reconstructs equity well
      // enough for audit. We capture floating separately below.
      floating_pl:    (typeof state.floating_pl    === 'number') ? state.floating_pl    : null,
      daily_realized: (typeof state.dailyRealized  === 'number') ? state.dailyRealized  : null,
      wins:           (typeof state.wins           === 'number') ? state.wins           : null,
      losses:         (typeof state.losses         === 'number') ? state.losses         : null,
      ts: new Date().toISOString(),
    };
  },

  _mkRunId(){
    const d = new Date();
    const z = n => String(n).padStart(2,'0');
    return `${d.getUTCFullYear()}${z(d.getUTCMonth()+1)}${z(d.getUTCDate())}-${z(d.getUTCHours())}${z(d.getUTCMinutes())}${z(d.getUTCSeconds())}`;
  },

  /* Read modal inputs → cfg. Persisted to localStorage so refresh keeps last setup. */
  _readCfgFromForm(){
    const num = (id, dflt) => { const el = $(id); const v = el ? parseFloat(el.value) : NaN; return Number.isFinite(v) ? v : dflt; };
    this.cfg.rate            = num('#atRate', 20);
    this.cfg.burstCount      = num('#atBurstCount', 100);
    this.cfg.restSec         = num('#atRestSec', 5);
    this.cfg.lot             = num('#atLot', 0.01);
    this.cfg.maxOrdersPerRun = num('#atMaxOrders', 500);
    this.cfg.consecFailKill  = num('#atConsecFailKill', 10);
    this.cfg.inFlightCap     = num('#atInFlightCap', 32);
    const sel = $('#atTriggerMode'); if(sel) this.cfg.triggerMode = sel.value;
    try { localStorage.setItem('mt5autotest', JSON.stringify(this.cfg)); } catch(e){}
  },

  /* Write persisted cfg → modal inputs (called on modal-open). */
  _writeCfgToForm(){
    try {
      const saved = JSON.parse(localStorage.getItem('mt5autotest') || 'null');
      if(saved) Object.assign(this.cfg, saved);
    } catch(e){}
    const set = (id, v) => { const el = $(id); if(el) el.value = v; };
    set('#atRate', this.cfg.rate);
    set('#atBurstCount', this.cfg.burstCount);
    set('#atRestSec', this.cfg.restSec);
    set('#atLot', this.cfg.lot);
    set('#atMaxOrders', this.cfg.maxOrdersPerRun);
    set('#atConsecFailKill', this.cfg.consecFailKill);
    set('#atInFlightCap', this.cfg.inFlightCap);
    const sel = $('#atTriggerMode'); if(sel) sel.value = this.cfg.triggerMode;
  },

  /* Compute final verdict (PASS / REVIEW / FAIL), render it, refresh status
   * grid one more time so the Phase cell catches up to "idle" (fixing the
   * "phase still shows B_OPEN after done" bug), flash the topbar pill in the
   * verdict colour, then auto-open the modal so the user sees the verdict
   * block + audit-download button immediately — no risk of missing the result.
   * On FAIL also raises the persistent top-of-page banner that survives
   * modal close + page reloads until explicitly dismissed. */
  _finish(){
    this.st.phase = 'idle';
    this.st.cycle = null;
    // Snapshot ending account state for the audit (paired with startAccount).
    this.st.endAccount = this._snapshotAccount();
    const verdict = this._computeVerdict();
    const summary = this._renderVerdict(verdict);
    this._lastAudit = this._buildAudit(verdict);
    const dl = $('#atDownloadAudit'); if(dl) dl.hidden = false;
    // Refresh grid + pill AFTER the verdict is computed so Phase reads "idle".
    this._refreshStatusGrid();
    logLine('AUTO-TEST',
            `run ${this.st.runId} DONE — verdict=${verdict.label} · sent=${this.st.sent} ok=${this.st.ok} fail=${this.st.failed}`
            + (this._lastAudit.realized_pnl != null
               ? ` · realized=${this._lastAudit.realized_pnl.toFixed(2)}` : ''),
            verdict.label === 'PASS');
    if(summary) toast(verdict.label === 'PASS' ? 'ok' : 'fail', `Auto-Test ${verdict.label}`, summary);

    // Persistent FAIL banner — only on FAIL. Survives modal close + reload.
    if(verdict.label === 'FAIL'){
      setFailBanner(`${this.st.runId} — ${verdict.reason}`);
    }

    // Flash the pill in verdict colour + auto-open modal after a brief delay
    // so the user catches the colour change but isn't slammed with the modal.
    setVerdictPill(verdict.label);
    setTimeout(() => { openAutoTestModal(); }, 900);
  },

  _fail(reason){
    logLine('AUTO-TEST', `refused to start: ${reason}`, false);
    toast('fail', 'Auto-Test refused', reason);
    this.st.phase = 'idle';
    API.autoTestActive = false;
  },

  /* Bundle deterministic PASS / REVIEW / FAIL based on confirmed criteria. */
  _computeVerdict(){
    const sent = this.st.sent, ok = this.st.ok, failed = this.st.failed;
    let confirmed = 0, lost = 0, late = 0;
    for(const r of this.st.placedTickets.values()){
      if(r.lost) lost++;
      else if(r.confirmed) confirmed++;
    }
    const ratio = sent > 0 ? ok / sent : 0;
    const rateOk = sent === 0 ? false : Math.abs(this.st.achievedRate - this.cfg.rate) / Math.max(1, this.cfg.rate) <= 0.20;
    const allFlat = this.st.cycleStats.every(c => c.flat);
    if(this.st.abort && !allFlat) return {label:'FAIL', reason: this.st.abort};
    if(lost > 0) return {label:'FAIL', reason:`${lost} tickets never appeared in feed`};
    if(!allFlat) return {label:'FAIL', reason:'one or more sub-cycles did not reach flat'};
    if(ratio >= 0.95 && rateOk) return {label:'PASS', reason:'all checks green'};
    return {label:'REVIEW', reason:`ok/sent=${(ratio*100).toFixed(0)}% achievedRate=${this.st.achievedRate.toFixed(1)}/s`};
  },

  _renderVerdict(v){
    const el = $('#atVerdict'); if(!el) return v.reason;
    el.hidden = false;
    el.className = 'autotest-verdict ' + (v.label === 'PASS' ? 'pass' : v.label === 'REVIEW' ? 'review' : 'fail');
    const text = `${v.label}: ${v.reason}\nsent=${this.st.sent} ok=${this.st.ok} failed=${this.st.failed} achieved=${this.st.achievedRate.toFixed(1)}/s`;
    el.textContent = text;
    return v.reason;
  },

  /* Assemble the per-run audit JSON. Captures:
   *   - identity + verdict
   *   - config snapshot
   *   - totals + per-cycle stats
   *   - start/end account snapshots (from /ws state.account)
   *   - realized_pnl + balance_delta derived from those snapshots
   *   - per-cycle slippage mean/max/p95 (from this.st.slippages)
   *   - per-cycle retcodes_histogram (from this.st.retcodeHistogram)
   *
   * Output is intentionally pandas-friendly: top-level keys are scalars or
   * small arrays of objects; deeply nested structures are avoided. */
  _buildAudit(verdict){
    const sa = this.st.startAccount, ea = this.st.endAccount;
    const realized = (sa && ea && sa.daily_realized != null && ea.daily_realized != null)
                     ? Number((ea.daily_realized - sa.daily_realized).toFixed(2)) : null;
    const balDelta = (sa && ea && sa.balance != null && ea.balance != null)
                     ? Number((ea.balance - sa.balance).toFixed(2)) : null;
    // Enrich each cycleStats entry with slippage stats + retcode histogram
    // (these were tracked per-cycle in this.st, joined here by label).
    const cycles = this.st.cycleStats.map(c => {
      const slips = this.st.slippages[c.label] || [];
      const hist  = this.st.retcodeHistogram[c.label] || {};
      return {
        ...c,
        slippage_n:    slips.length,
        slippage_mean: _stat_mean(slips),
        slippage_max:  _stat_max(slips),
        slippage_p95:  _stat_p95(slips),
        retcodes_histogram: { ...hist },
      };
    });
    return {
      run_id: this.st.runId,
      started_at: this.st.startedAt,
      ended_at: new Date().toISOString(),
      verdict: verdict.label,
      verdict_reason: verdict.reason,
      config: { ...this.cfg },
      totals: {
        sent: this.st.sent, ok: this.st.ok, failed: this.st.failed,
        achievedRate: this.st.achievedRate,
      },
      // Account snapshots taken from the live /ws feed at start and finish.
      // realized_pnl = closed-deal P&L of the run; balance_delta = net effect
      // on broker balance (includes commission & swap, hence the small drift
      // vs realized_pnl on accounts that charge those).
      start_account:  sa,
      end_account:    ea,
      realized_pnl:   realized,
      balance_delta:  balDelta,
      cycles,
    };
  },

  _sleep(ms){ return new Promise(res => setTimeout(res, ms)); },
};

/* ============================================================================
   SYNC VERIFIER — reconcileAutoTest(state)
   Called from onState(s). Does two distinct jobs:
     A) Reconciles AutoTest's placedTickets ledger against the live feed:
        marks confirmed (latency < 1s = OK, 1-5s = pending, >5s = LOST)
     B) Detects "foreign" positions on the symbol (magic != AUTOTEST_MAGIC) —
        these are positions opened by something OTHER than this app (user
        clicking BUY in MT5, another EA, etc.). Warning-only, not a test FAIL.
   The SYNC chip in the topbar reflects the worst of the two states.
   ============================================================================ */
function reconcileAutoTest(s){
  const positions = (s && s.positions) || [];
  const feedTickets = new Set(positions.map(p => p.ticket));

  // --- Foreign-magic detection (runs always) ----------------------------------
  let foreignCount = 0;
  for(const p of positions){
    if(p.magic != null && Number(p.magic) !== AUTOTEST_MAGIC) foreignCount++;
  }

  // --- Placed-ticket ledger (only meaningful during a run) -------------------
  let pendingCount = 0, lateCount = 0, lostCount = 0;
  const now = performance.now();
  if(AutoTest.st.phase !== 'idle'){
    for(const [ticket, rec] of AutoTest.st.placedTickets){
      if(rec.confirmed) continue;
      const age = now - rec.placedAt;
      if(feedTickets.has(ticket)){
        rec.confirmed = true; rec.latencyMs = age;
      } else if(age > 5000){
        rec.confirmed = true; rec.lost = true; lostCount++;
      } else if(age > 1000){
        lateCount++;
      } else {
        pendingCount++;
      }
    }
  }

  // --- Chip state: worst-of wins ---------------------------------------------
  // priority: fail (lost) > pending (late) > external > ok
  //
  // The topbar chip and the modal SYNC cell use SLIGHTLY different value
  // strings to avoid the "SYNCSYNC OK" duplication bug: the topbar chip
  // has no separate label, so it shows "SYNC OK" / "LOST n" / etc; the
  // modal cell already has a "SYNC" label next to it, so the value drops
  // the "SYNC " prefix and shows just "OK" / "LOST n" / "PENDING n" /
  // "EXTERNAL n".
  let cls = 'ok', chipText = 'SYNC OK', cellText = 'OK';
  if(lostCount > 0){
    cls = 'fail';     chipText = `LOST ${lostCount}`;     cellText = `LOST ${lostCount}`;
  } else if(lateCount > 0){
    cls = 'pending';  chipText = `PENDING ${lateCount}`;  cellText = `PENDING ${lateCount}`;
  } else if(foreignCount > 0){
    cls = 'external'; chipText = `EXTERNAL ${foreignCount}`; cellText = `EXTERNAL ${foreignCount}`;
  }
  const chip = document.getElementById('syncChip');
  const txt  = document.getElementById('syncText');
  if(chip) chip.className = 'sync ' + cls;
  if(txt)  txt.textContent = chipText;

  // Modal status cell — value only, no "SYNC " prefix (label is rendered separately).
  const ms = document.getElementById('atSync');
  if(ms) ms.textContent = cellText;
}

/* ============================================================================
   ACCOUNT BADGE  —  driven by /api/account/safety (and refreshed from /ws state)
   ============================================================================ */
function setAccountBadge(safety){
  const txt = document.getElementById('connText');
  if(!txt) return;
  txt.classList.remove('loading','demo','real','contest');
  if(!safety || safety.trade_mode == null){
    txt.classList.add('loading');
    txt.textContent = '…';
    return;
  }
  const login = safety.login != null ? `#${safety.login}` : '';
  if(safety.is_demo){
    txt.classList.add('demo');
    txt.textContent = `DEMO ${login}`;
  } else if(safety.trade_mode === 1){
    txt.classList.add('contest');
    txt.textContent = `CONTEST ${login}`;
  } else {
    txt.classList.add('real');
    txt.textContent = `REAL — TEST BLOCKED`;
  }
  // Update Auto-Test modal banner + account block (if modal exists / open).
  const banner = document.getElementById('autoTestBanner');
  if(banner){
    banner.classList.remove('loading','demo','real','contest');
    if(safety.is_demo){
      banner.classList.add('demo');
      banner.textContent = `Demo account verified: ${login}@${safety.server || '?'} · Auto-Test allowed`;
    } else {
      banner.classList.add(safety.trade_mode === 1 ? 'contest' : 'real');
      banner.textContent = `Account is NOT demo — Auto-Test REFUSED`;
    }
  }
  const set = (id,v) => { const el = document.getElementById(id); if(el) el.textContent = v; };
  set('atLogin',  safety.login   != null ? safety.login   : '—');
  set('atServer', safety.server  != null ? safety.server  : '—');
  set('atMargin', safety.margin_mode === 0 ? 'NETTING' :
                  safety.margin_mode === 2 ? 'HEDGING' : '—');
  set('atHealthy', safety.healthy ? 'YES' : 'NO');
  // START button is gated on DEMO + healthy
  const startBtn = document.getElementById('atStart');
  if(startBtn) startBtn.disabled = !(safety.is_demo && safety.healthy);
}

/* Refresh badge from the /ws frame so it stays current without re-fetching. */
function updateAccountBadgeFromState(s){
  if(!s || !s.account) return;
  const a = s.account;
  setAccountBadge({
    login: a.login, server: a.server,
    trade_mode: a.trade_mode,
    is_demo: a.is_demo,
    margin_mode: a.margin_mode,
    healthy: !!s.healthy,
  });
}

/* Fetched explicitly on page load + Auto-Test modal open (canonical source). */
async function fetchAccountSafety(){
  try {
    const r = await fetch('/api/account/safety', {cache:'no-store'});
    if(!r.ok) return null;
    const s = await r.json();
    setAccountBadge(s);
    return s;
  } catch(e){
    setAccountBadge(null);
    return null;
  }
}

/* Small numeric stats used by AutoTest._buildAudit for slippage aggregation.
 * Defined at module level so the audit assembler stays self-contained. */
function _stat_mean(arr){
  if(!arr || !arr.length) return null;
  let s = 0; for(const v of arr) s += v;
  return Number((s / arr.length).toFixed(6));
}
function _stat_max(arr){
  if(!arr || !arr.length) return null;
  let m = -Infinity; for(const v of arr) if(v > m) m = v;
  return Number(m.toFixed(6));
}
function _stat_p95(arr){
  if(!arr || !arr.length) return null;
  const sorted = [...arr].sort((a,b) => a-b);
  const idx = Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95));
  return Number(sorted[idx].toFixed(6));
}

/* ============================================================================
   FAIL banner  —  persistent across page reloads via localStorage
   ============================================================================
   Shown when AutoTest._finish() computes a FAIL verdict. It survives:
   - the modal being closed
   - the topbar pill auto-dismissing
   - a full page reload (state stored in localStorage["mt5_fail_banner"])
   so the operator cannot dismiss the warning by accident. Cleared only by
   the user clicking the DISMISS button on the banner itself.
   PASS / REVIEW verdicts do NOT use this banner.
*/
const FAIL_BANNER_KEY = 'mt5_fail_banner';

function setFailBanner(text){
  const banner = document.getElementById('atFailBanner');
  const txt    = document.getElementById('atFailBannerText');
  if(!banner || !txt) return;
  const message = `AUTO-TEST FAILED — ${text}`;
  txt.textContent = message;
  banner.hidden = false;
  try { localStorage.setItem(FAIL_BANNER_KEY, message); } catch(_e) {}
}
function clearFailBanner(){
  const banner = document.getElementById('atFailBanner');
  if(banner) banner.hidden = true;
  try { localStorage.removeItem(FAIL_BANNER_KEY); } catch(_e) {}
}
function restoreFailBannerFromStorage(){
  let saved = null;
  try { saved = localStorage.getItem(FAIL_BANNER_KEY); } catch(_e) {}
  if(!saved) return;
  const banner = document.getElementById('atFailBanner');
  const txt    = document.getElementById('atFailBannerText');
  if(banner && txt){
    txt.textContent = saved;
    banner.hidden = false;
  }
}

/* ============================================================================
   AUTO-TEST MODAL WIRING  —  open/close/start/stop/download buttons + pill
   ============================================================================ */
function openAutoTestModal(){
  const m = $('#autoTestModal'); if(!m) return;
  m.hidden = false;
  AutoTest._writeCfgToForm();
  AutoTest._refreshStatusGrid();
  fetchAccountSafety();          // refresh badge + gate
}
function closeAutoTestModal(){
  const m = $('#autoTestModal'); if(!m) return;
  m.hidden = true;
  // Closing via the X must behave like the backdrop click: once the run is
  // idle (finished/stopped), dismiss the topbar pill too so it doesn't linger
  // with a dead "STOP" button. While a run is still active the pill stays so
  // the user can monitor progress / re-open the modal.
  if(typeof AutoTest !== 'undefined' && AutoTest.st.phase === 'idle') hideRunPill();
}

/* Pill helpers — control the compact topbar status indicator that's
 * visible while a run is active. Hidden by default (HTML has `hidden`). */
function showRunPill(){
  const p = document.getElementById('atRunPill'); if(!p) return;
  // Reset to default running colour (clear any previous verdict class).
  p.classList.remove('pass','review','fail','ping');
  p.hidden = false;
  // Force a refresh of the pill text immediately so it doesn't show stale data.
  AutoTest._refreshStatusGrid();
}
function hideRunPill(){
  const p = document.getElementById('atRunPill'); if(!p) return;
  p.hidden = true;
  p.classList.remove('pass','review','fail','ping');
}

/* Flip the pill into a verdict colour + brief "ping" animation. Called from
 * AutoTest._finish(). The pill stays visible behind the modal that the
 * finish handler auto-opens; it's hidden when the modal is closed. */
function setVerdictPill(verdictLabel){
  const p = document.getElementById('atRunPill'); if(!p) return;
  p.classList.remove('pass','review','fail');
  const cls = verdictLabel === 'PASS' ? 'pass'
            : verdictLabel === 'REVIEW' ? 'review' : 'fail';
  p.classList.add(cls);
  // Trigger the ping animation (remove + re-add for repeat-ability).
  p.classList.remove('ping'); void p.offsetWidth; p.classList.add('ping');
  // The phase cell already reads "idle"; surface the verdict label on the pill.
  const phaseEl = document.getElementById('atRunPhase');
  if(phaseEl) phaseEl.textContent = verdictLabel;
}

function downloadAuditJson(){
  const audit = AutoTest._lastAudit;
  if(!audit){ toast('info','No audit','Run an Auto-Test first'); return; }
  const blob = new Blob([JSON.stringify(audit, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `autotest-${audit.run_id}.json`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function bindAutoTest(){
  const on = (id, ev, fn) => { const el = document.getElementById(id); if(el) el.addEventListener(ev, fn); };
  on('autoTestBtn',     'click', openAutoTestModal);
  on('autoTestClose',   'click', closeAutoTestModal);
  on('atStart',         'click', () => AutoTest.start());
  on('atStop',          'click', () => { AutoTest.stop('user clicked STOP'); closeAll(); });
  on('atDownloadAudit', 'click', downloadAuditJson);
  const modal = document.getElementById('autoTestModal');
  if(modal) modal.addEventListener('click', e => {
    if(e.target.id === 'autoTestModal'){
      closeAutoTestModal();
      // Closing the modal after a finished run also dismisses the pill so
      // the topbar returns to its normal state. While a run is still active,
      // the pill stays so the user can re-open via clicking it.
      if(AutoTest.st.phase === 'idle') hideRunPill();
    }
  });

  // Pill: clicking anywhere except the STOP button re-opens the modal.
  // STOP button gets its own handler that aborts the run AND flattens.
  const pill = document.getElementById('atRunPill');
  if(pill) pill.addEventListener('click', e => {
    if(e.target && e.target.id === 'atRunStop') return; // STOP handler below
    openAutoTestModal();
  });
  on('atRunStop', 'click', e => {
    e.stopPropagation();           // don't bubble to the pill click → open modal
    AutoTest.stop('user clicked pill STOP');
    closeAll();
  });

  // FAIL banner controls (persistent across reloads via localStorage).
  on('atFailBannerView',    'click', () => { openAutoTestModal(); });
  on('atFailBannerDismiss', 'click', () => { clearFailBanner(); });
  // Restore the banner if a previous run's FAIL was never dismissed.
  restoreFailBannerFromStorage();
}

/* ===================== BOOT (must be last) =====================
   Invoke init() only after every module-level declaration above is initialized
   — notably `let liveHealthy/liveMsg`. Booting earlier put them in the temporal
   dead zone and threw on the live (API.demo=false) path. */
if(document.readyState!=='loading') init();
else document.addEventListener('DOMContentLoaded', init);
