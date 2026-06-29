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

  async order(req){
    // req = {symbol, side:'buy'|'sell', volume, type:'market'|'limit', price, sl, tp}
    if(this.demo) return demoOrder(req);
    const r = await fetch(this.base+'/order', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(req)});
    if(!r.ok) throw new Error((await r.json()).detail || 'order rejected');
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

async function placeOrder(side, isMarketKey){
  if(!API.demo && !liveHealthy){
    toast('fail','Not ready', liveMsg||'terminal/trading unavailable'); beep('fail');
    logLine(side.toUpperCase(), `Blocked — ${liveMsg||'not ready'}`, false);
    return;
  }
  syncFormFromInputs();
  // NOTE: armed direction is sticky — it changes ONLY via B/S (arm()), never by firing.
  const type = isMarketKey ? 'market' : state.type;
  const vol  = state.lot;
  flashAct(side);

  // LIMIT → pending entry (free; pendings are deliberate setups, not direction-locked)
  if(type==='limit'){
    if(!(parseFloat(state.limit)>0)){
      toast('fail','Limit price required','Enter a price or press M for market'); beep('fail');
      logLine(side.toUpperCase(), `Limit ${vol} rejected — no price`, false); return;
    }
    return openOrder(side, vol, 'limit');
  }

  // MARKET with netting ON → the ARMED direction is the only side you may OPEN.
  //   • key that matches armed  → ENTRY (open / add)
  //   • key opposite to armed   → EXIT only: close armed-side positions (FIFO, capped).
  //     If there's nothing on the armed side to close → BLOCK (no reverse). Re-arm to flip.
  if(S.netting !== false){
    const A = state.armed;
    if(side === A) return openOrder(side, vol, 'market');     // entry / add in armed dir

    const openArmed = state.positions
      .filter(p=>p.state==='open' && p.side===A && p.symbol===state.symbol)
      .sort((a,b)=>a.openTime-b.openTime);                    // FIFO: oldest first (this symbol)
    const armedVol = +openArmed.reduce((s,p)=>s+p.volume,0).toFixed(2);
    if(armedVol <= 1e-9){
      const ln = A==='buy' ? 'long' : 'short';
      toast('fail', `No ${ln} to close`, `${A.toUpperCase()} armed — press ${A==='buy'?'S':'B'} to trade the other way`);
      beep('fail'); flashBlock(side);
      logLine(side.toUpperCase(), `Blocked — no ${ln} to close (${A.toUpperCase()} armed)`, false);
      return;
    }
    return reduceOpposite(side, vol, openArmed, armedVol);    // close armed-side only
  }

  // netting OFF → hedging: open freely in either direction
  return openOrder(side, vol, 'market');
}

/* open a brand-new position (market fill or pending limit) */
async function openOrder(side, vol, type){
  const req = {
    symbol:state.symbol, side, volume:vol, type,
    price: type==='limit'? parseFloat(state.limit) : (side==='buy'?state.ask:state.bid),
    sl: parseFloat(state.sl)||0, tp: parseFloat(state.tp)||0,
  };
  try{
    const res = await API.order(req);
    const pos = state.positions.find(p=>p.ticket===res.ticket);
    if(pos){ pos.slPrice=sltpPrice(side, state.sl,'sl'); pos.tpPrice=sltpPrice(side, state.tp,'tp'); }
    const verb = type==='limit'?'Limit set':'Filled';
    logLine(side.toUpperCase(), `${verb} ${vol} ${state.symbol} @ ${fmt(req.price)}`+
            (req.sl?` · SL ${req.sl}p`:'')+(req.tp?` · TP ${req.tp}p`:''), true);
    toast('ok', `${side==='buy'?'BUY':'SELL'} ${type==='limit'?'pending':'filled'}`,
          `#${res.ticket} ${vol} @ ${fmt(req.price)}`);
    beep(side);
    renderPositions(); renderMetrics(); saveBook();
  }catch(err){
    logLine(side.toUpperCase(), `${vol} ${state.symbol} — ${err.message}`, false);
    toast('fail','Order rejected', err.message); beep('fail');
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
  // modal open → Esc closes, otherwise let inputs work
  if(!$('#settingsModal').hidden){
    if(e.key==='Escape'){ e.preventDefault(); closeSettings(); }
    return;
  }
  if(isTyping()){
    if(e.key==='Enter'||e.key==='Escape'){ e.preventDefault(); document.activeElement.blur(); syncFormFromInputs(); }
    return;
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

/* ===================== BOOT (must be last) =====================
   Invoke init() only after every module-level declaration above is initialized
   — notably `let liveHealthy/liveMsg`. Booting earlier put them in the temporal
   dead zone and threw on the live (API.demo=false) path. */
if(document.readyState!=='loading') init();
else document.addEventListener('DOMContentLoaded', init);
