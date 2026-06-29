/* ============================================================================
   LIVE TICK CHART  —  XauOrderPad
   TradingView Lightweight Charts (vendored). Self-contained module: it does NOT
   touch app.js. It taps the SAME /ws state stream app.js already opens (by
   wrapping the WebSocket constructor) so there is no second socket and no edit
   to the live-feed code. Price is drawn as a line, tick volume as a histogram
   beneath it, on a rolling ~2-minute window.

   Load order (see index.html): vendor → chart.js → app.js. chart.js must run
   BEFORE app.js so the WebSocket wrapper is installed before app.js connects.
   (app.js boots via init() during deferred-script execution, so the wrapper has
   to be in place first.)
   ========================================================================== */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ chart */
  const ChartFeed = (() => {
    const WINDOW_S = 120;                 // rolling window length (seconds)
    let chart = null, priceSeries = null, volSeries = null, ro = null;
    let priceBuf = [], volBuf = [];       // {time,value}, ascending integer second
    let lastT = 0, prevPrice = null, inited = false;

    const cssVar = (name, fb) => {
      const v = getComputedStyle(document.body).getPropertyValue(name).trim();
      return v || fb;
    };
    const volColor = up => up
      ? cssVar('--buy-line', 'rgba(25,201,138,.5)')
      : cssVar('--sell-line', 'rgba(255,77,79,.5)');

    function ensure() {
      if (inited) return chart != null;
      inited = true;
      const el = document.getElementById('chartWrap');
      if (!el || typeof LightweightCharts === 'undefined') { inited = false; return false; }
      chart = LightweightCharts.createChart(el, {
        layout: { background: { type: 'solid', color: 'transparent' },
                  textColor: cssVar('--text-dim', '#8b99a7'), fontSize: 11 },
        grid: { vertLines: { color: cssVar('--border', '#1d2832') },
                horzLines: { color: cssVar('--border', '#1d2832') } },
        rightPriceScale: { borderColor: cssVar('--border', '#1d2832') },
        timeScale: { borderColor: cssVar('--border', '#1d2832'),
                     timeVisible: true, secondsVisible: true, rightOffset: 3 },
        crosshair: { mode: 0 },
        handleScroll: false, handleScale: false,
        width: Math.max(1, el.clientWidth), height: Math.max(1, el.clientHeight),
      });
      priceSeries = chart.addLineSeries({ color: cssVar('--buy', '#19c98a'),
        lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
      volSeries = chart.addHistogramSeries({ priceScaleId: 'vol',
        color: cssVar('--text-faint', '#56646f'), priceFormat: { type: 'volume' } });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

      ro = new ResizeObserver(() => {
        const e = document.getElementById('chartWrap');
        if (chart && e && e.clientWidth > 0) chart.resize(e.clientWidth, e.clientHeight);
      });
      ro.observe(el);
      return true;
    }

    function trim() {
      const cutoff = lastT - WINDOW_S;
      let changed = false;
      while (priceBuf.length && priceBuf[0].time < cutoff) { priceBuf.shift(); changed = true; }
      while (volBuf.length   && volBuf[0].time   < cutoff) { volBuf.shift();   changed = true; }
      if (changed) { priceSeries.setData(priceBuf); volSeries.setData(volBuf); }
    }

    return {
      push(t, price, volume) {
        if (!ensure()) return;
        t = Math.floor(t || 0); if (!t) return;
        price = +price; if (!isFinite(price)) return;
        volume = Math.max(0, +volume || 0);
        if (t < lastT) return;                      // never go backwards (LWC rule)
        const up = prevPrice == null ? true : price >= prevPrice;
        if (t === lastT && priceBuf.length) {
          priceBuf[priceBuf.length - 1].value = price;     // overwrite this second's price
          volBuf[volBuf.length - 1].value += volume;       // accumulate this second's volume
          priceSeries.update({ time: t, value: price });
          volSeries.update({ time: t, value: volBuf[volBuf.length - 1].value, color: volColor(up) });
        } else {
          lastT = t;
          priceBuf.push({ time: t, value: price });
          volBuf.push({ time: t, value: volume });
          priceSeries.update({ time: t, value: price });
          volSeries.update({ time: t, value: volume, color: volColor(up) });
          trim();
        }
        prevPrice = price;
      },
      // Re-fit after expand / theme change so the canvas matches its container.
      refit() {
        if (!ensure() || !chart) return;
        const e = document.getElementById('chartWrap');
        if (e && e.clientWidth > 0) { chart.resize(e.clientWidth, e.clientHeight); chart.timeScale().fitContent(); }
      },
    };
  })();

  /* ---------------------------------------------------- /ws tap (no 2nd socket)
     Wrap the WebSocket constructor so we observe the very frames app.js already
     receives on /ws. We only attach an extra 'message' listener; app.js keeps
     its own ws.onmessage handler untouched (both fire). */
  let wsSeen = false;          // did app.js ever open a /ws socket? (false => demo)
  let lastRealMs = 0;
  let lastBid = null;          // for tick-volume = count of price changes / second
  const NativeWS = window.WebSocket;
  function PatchedWS(url, protocols) {
    const ws = protocols !== undefined ? new NativeWS(url, protocols) : new NativeWS(url);
    if (String(url || '').indexOf('/ws') !== -1) {
      wsSeen = true;
      ws.addEventListener('message', ev => {
        try {
          const s = JSON.parse(ev.data);
          if (s && s.bid != null) {
            lastRealMs = Date.now();
            // MT5's tick.volume is 0 for FX/metal quote ticks (no real trade
            // volume on a quote), so prefer it only when the broker actually
            // reports it; otherwise fall back to TICK volume = number of price
            // changes in the bar (this is exactly what MT5 calls "tick volume").
            const changed = (lastBid === null) ? true : (s.bid !== lastBid);
            lastBid = s.bid;
            const vol = (s.volume && s.volume > 0) ? s.volume : (changed ? 1 : 0);
            ChartFeed.push(s.tick_time || s.ts || (Date.now() / 1000), s.bid, vol);
          }
        } catch (e) { /* ignore non-JSON frames */ }
      });
    }
    return ws;
  }
  PatchedWS.prototype = NativeWS.prototype;
  PatchedWS.CONNECTING = NativeWS.CONNECTING; PatchedWS.OPEN = NativeWS.OPEN;
  PatchedWS.CLOSING = NativeWS.CLOSING; PatchedWS.CLOSED = NativeWS.CLOSED;
  window.WebSocket = PatchedWS;

  /* --------------------------------------------- demo / standalone fallback
     If no /ws socket is opened within 3s, the page is running the demo feed
     (app.js never calls startLive()) — drive the chart with a synthetic random
     walk so the UI is verifiable without MT5. Stops the moment real frames flow. */
  function maybeStartSynthetic() {
    if (wsSeen) return;                         // live mode — real ticks will arrive
    let mid = 2400, base = 2400;
    setInterval(() => {
      if (Date.now() - lastRealMs < 1500) return;   // real feed active → stay quiet
      mid += (base - mid) * 0.002 + (Math.random() - 0.5) * 0.6;
      ChartFeed.push(Math.floor(Date.now() / 1000), mid, 1 + Math.floor(Math.random() * 9));
    }, 250);
  }

  /* ----------------------------------------------------- collapse / expand
     Collapsed → positions table takes the full width (see .rightpane.collapsed
     in styles.css). Choice persists across reloads. */
  function wireUI() {
    const pane = document.getElementById('rightpane');
    const btn = document.getElementById('chartToggle');
    if (pane && btn) {
      const KEY = 'xop.chartCollapsed';
      const setGlyph = () => { btn.textContent = pane.classList.contains('collapsed') ? '⮞' : '⮜'; };
      if (localStorage.getItem(KEY) === '1') pane.classList.add('collapsed');
      setGlyph();
      if (!pane.classList.contains('collapsed')) requestAnimationFrame(() => ChartFeed.refit());
      btn.addEventListener('click', () => {
        const collapsed = pane.classList.toggle('collapsed');
        localStorage.setItem(KEY, collapsed ? '1' : '0');
        setGlyph();
        if (!collapsed) requestAnimationFrame(() => ChartFeed.refit());
      });
    }
    setTimeout(maybeStartSynthetic, 3000);
  }

  if (document.readyState !== 'loading') wireUI();
  else document.addEventListener('DOMContentLoaded', wireUI);
})();
