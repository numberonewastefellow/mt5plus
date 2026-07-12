/* ============================================================================
   LIVE TICK CHART  —  XauOrderPad
   TradingView Lightweight Charts (vendored). Self-contained module: it does NOT
   touch app.js. It taps the SAME /ws state stream app.js already opens (by
   wrapping the WebSocket constructor) so there is no second socket.

   DISABLED BY DEFAULT. A checkbox (#chartEnable) turns it on/off. When OFF the
   chart is fully torn down — no chart object, no ResizeObserver, no /ws message
   listener, no demo timer, no redraws — so it costs effectively nothing. The
   only thing always present is the thin WebSocket wrapper, which (when disabled)
   does nothing but pass the socket through.

   When ON the feed is optimized: theme colors are read ONCE (not per tick),
   intra-second redraws are throttled to ~5/sec, and updates pause automatically
   while the browser tab is hidden.

   Load order (index.html): vendor -> chart.js -> app.js. chart.js must run
   BEFORE app.js so the WebSocket wrapper is installed before app.js connects.
   ========================================================================== */
(function () {
  'use strict';

  const KEY = 'xop.chartEnabled';     // '1' = on, anything else = off (default off)
  let enabled = false;                // resolved in init()

  /* ============================================================ chart engine */
  const ChartFeed = (() => {
    const WINDOW_S = 120;             // rolling window length (seconds)
    const DRAW_MS = 200;              // throttle intra-second redraws (~5/sec)
    let chart = null, priceSeries = null, volSeries = null, ro = null;
    let priceBuf = [], volBuf = [], lastT = 0, prevPrice = null, lastDraw = 0;
    let colBuy = '', colSell = '', colLine = '', colDim = '', colBorder = '', colFaint = '';

    function readColors() {
      const cs = getComputedStyle(document.body);
      const g = (n, fb) => { const v = cs.getPropertyValue(n).trim(); return v || fb; };
      colBuy    = g('--buy-line', 'rgba(25,201,138,.5)');
      colSell   = g('--sell-line', 'rgba(255,77,79,.5)');
      colLine   = g('--buy', '#19c98a');
      colDim    = g('--text-dim', '#8b99a7');
      colBorder = g('--border', '#1d2832');
      colFaint  = g('--text-faint', '#56646f');
    }

    function create() {
      if (chart) return true;
      const el = document.getElementById('chartWrap');
      if (!el || typeof LightweightCharts === 'undefined') return false;
      readColors();
      chart = LightweightCharts.createChart(el, {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: colDim, fontSize: 11 },
        grid: { vertLines: { color: colBorder }, horzLines: { color: colBorder } },
        rightPriceScale: { borderColor: colBorder },
        timeScale: { borderColor: colBorder, timeVisible: true, secondsVisible: true, rightOffset: 3 },
        crosshair: { mode: 0 },
        handleScroll: false, handleScale: false,
        width: Math.max(1, el.clientWidth), height: Math.max(1, el.clientHeight),
      });
      priceSeries = chart.addLineSeries({ color: colLine, lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
      volSeries = chart.addHistogramSeries({ priceScaleId: 'vol', color: colFaint, priceFormat: { type: 'volume' } });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      ro = new ResizeObserver(() => {
        const e = document.getElementById('chartWrap');
        if (chart && e && e.clientWidth > 0) chart.resize(e.clientWidth, e.clientHeight);
      });
      ro.observe(el);
      return true;
    }

    function destroy() {
      if (ro) { ro.disconnect(); ro = null; }
      if (chart) { try { chart.remove(); } catch (e) {} chart = null; }
      priceSeries = volSeries = null;
      priceBuf = []; volBuf = []; lastT = 0; prevPrice = null; lastDraw = 0;
    }

    function trim() {
      const cutoff = lastT - WINDOW_S;
      let changed = false;
      while (priceBuf.length && priceBuf[0].time < cutoff) { priceBuf.shift(); changed = true; }
      while (volBuf.length   && volBuf[0].time   < cutoff) { volBuf.shift();   changed = true; }
      if (changed) { priceSeries.setData(priceBuf); volSeries.setData(volBuf); }
    }

    function drawLast(price) {
      lastDraw = Date.now();
      const up = prevPrice == null ? true : price >= prevPrice;
      const i = priceBuf.length - 1;
      priceSeries.update(priceBuf[i]);
      volSeries.update({ time: volBuf[i].time, value: volBuf[i].value, color: up ? colBuy : colSell });
      prevPrice = price;
    }

    return {
      isLive() { return chart != null; },
      create, destroy,
      push(t, price, volume) {
        if (!chart && !create()) return;
        t = Math.floor(t || 0); if (!t) return;
        price = +price; if (!isFinite(price)) return;
        volume = Math.max(0, +volume || 0);
        if (t < lastT) return;                            // never go backwards (LWC rule)
        if (t !== lastT || !priceBuf.length) {            // new second -> append + draw now
          lastT = t;
          priceBuf.push({ time: t, value: price });
          volBuf.push({ time: t, value: volume });
          trim();
          drawLast(price);
        } else {                                          // same second -> accumulate, throttle redraw
          priceBuf[priceBuf.length - 1].value = price;    // latest price wins
          volBuf[volBuf.length - 1].value += volume;      // sum this second's tick volume
          if (Date.now() - lastDraw >= DRAW_MS) drawLast(price);
        }
      },
      refit() {
        if (!chart) return;
        const e = document.getElementById('chartWrap');
        if (e && e.clientWidth > 0) { chart.resize(e.clientWidth, e.clientHeight); chart.timeScale().fitContent(); }
      },
    };
  })();

  /* ============================================== /ws tap (no second socket)
     The wrapper is ALWAYS installed (app.js connects very early). But the
     message listener is attached only while enabled, and detached on disable —
     so when the chart is off there are zero per-frame chart callbacks. */
  let wsSeen = false;          // did app.js open a /ws socket? (false => demo feed)
  let lastRealMs = 0;
  let lastBid = null;          // for tick-volume = count of price changes / second
  const sockets = new Set();
  const NativeWS = window.WebSocket;

  function onWsMessage(ev) {
    if (!enabled || document.hidden) return;   // paused while disabled or tab hidden
    try {
      const s = JSON.parse(ev.data);
      if (s && s.bid != null) {
        lastRealMs = Date.now();
        // MT5 tick.volume is 0 for FX/metal quote ticks (no real trade volume on
        // a quote); use it only when the broker actually reports it, else fall
        // back to TICK volume = number of price changes per bar (what MT5 calls
        // "tick volume").
        const changed = (lastBid === null) ? true : (s.bid !== lastBid);
        lastBid = s.bid;
        const vol = (s.volume && s.volume > 0) ? s.volume : (changed ? 1 : 0);
        ChartFeed.push(s.tick_time || s.ts || (Date.now() / 1000), s.bid, vol);
      }
    } catch (e) { /* ignore non-JSON frames */ }
  }

  function PatchedWS(url, protocols) {
    const ws = protocols !== undefined ? new NativeWS(url, protocols) : new NativeWS(url);
    if (String(url || '').indexOf('/ws') !== -1) {
      wsSeen = true;
      sockets.add(ws);
      ws.addEventListener('close', () => sockets.delete(ws));
      if (enabled) ws.addEventListener('message', onWsMessage);   // attach only when on
    }
    return ws;
  }
  PatchedWS.prototype = NativeWS.prototype;
  PatchedWS.CONNECTING = NativeWS.CONNECTING; PatchedWS.OPEN = NativeWS.OPEN;
  PatchedWS.CLOSING = NativeWS.CLOSING; PatchedWS.CLOSED = NativeWS.CLOSED;
  window.WebSocket = PatchedWS;

  function attachAll() { sockets.forEach(ws => { ws.removeEventListener('message', onWsMessage); ws.addEventListener('message', onWsMessage); }); }
  function detachAll() { sockets.forEach(ws => ws.removeEventListener('message', onWsMessage)); }

  /* ------------------------------------------- demo / standalone fallback
     Only runs while enabled AND no real /ws feed is flowing — drives the chart
     with a synthetic random walk so the UI is verifiable without MT5. */
  let synthTimer = null;
  function startSynthetic() {
    if (synthTimer) return;
    let mid = 2400, base = 2400;
    synthTimer = setInterval(() => {
      if (!enabled || document.hidden) return;
      if (wsSeen && Date.now() - lastRealMs < 1500) return;   // real feed active
      mid += (base - mid) * 0.002 + (Math.random() - 0.5) * 0.6;
      ChartFeed.push(Math.floor(Date.now() / 1000), mid, 1 + Math.floor(Math.random() * 9));
    }, 250);
  }
  function stopSynthetic() { if (synthTimer) { clearInterval(synthTimer); synthTimer = null; } }

  /* ----------------------------------------------------------- enable / disable */
  function applyEnabled(on, persist) {
    enabled = !!on;
    if (persist) localStorage.setItem(KEY, enabled ? '1' : '0');

    const pane = document.getElementById('rightpane');
    const cb = document.getElementById('chartEnable');
    if (cb) cb.checked = enabled;
    if (pane) pane.classList.toggle('chart-off', !enabled);

    if (enabled) {
      attachAll();
      if (ChartFeed.create()) requestAnimationFrame(() => ChartFeed.refit());
      startSynthetic();
    } else {
      detachAll();
      stopSynthetic();
      ChartFeed.destroy();
    }
  }

  function wireUI() {
    const cb = document.getElementById('chartEnable');
    enabled = localStorage.getItem(KEY) === '1';    // default OFF
    applyEnabled(enabled, false);
    if (cb) cb.addEventListener('change', () => applyEnabled(cb.checked, true));

    // Re-fit when returning to a visible tab (the feed itself auto-resumes).
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && enabled) requestAnimationFrame(() => ChartFeed.refit());
    });
  }

  if (document.readyState !== 'loading') wireUI();
  else document.addEventListener('DOMContentLoaded', wireUI);
})();
