/* Strategy panel — self-contained, independent of app.js.
 *
 *   GET  /api/strategies         -> {id: status, ...} for EVERY engine
 *   POST /api/strategy/{id}      -> {enabled?, ...params}  enable/disable + tune
 *   GET  /api/account/safety     -> demo/hedging gating for the banner
 *
 * Engines are fully independent server-side (each owns a magic number), so this
 * file treats them as a list of descriptors rather than special-casing either
 * one. Adding a third engine means adding one entry to ENGINES plus its markup.
 *
 * Kept deliberately separate from app.js so it never interferes with the main
 * terminal JS. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const modal = $("strategyModal");
  if (!modal) return;

  const num = (el) => parseFloat(el.value);
  const int = (el) => parseInt(el.value, 10);
  const bool = (el) => !!el.checked;
  const str = (el) => el.value;

  /* One descriptor per engine.
   *   fields   : param name -> [element id, reader]  (form -> POST body)
   *   status   : status key -> [element id, formatter]
   * `enabledEl` is separate because it is the one control with a confirm on it. */
  const ENGINES = [
    {
      id: "straddle",
      label: "Straddle",
      panel: "straddlePanel",
      enabledEl: "stratEnabled",
      needsHedging: true,
      fields: {
        volume: ["stratVolume", num],
        rvol_threshold: ["stratRvol", num],
        sl_atr_mult: ["stratSlAtr", num],
        tp_r: ["stratTpR", num],
        max_hold_min: ["stratHold", num],
        cooldown_min: ["stratCooldown", num],
        max_concurrent: ["stratConcurrent", int],
        max_daily_loss: ["stratMaxLoss", num],
        vol_filter: ["stratVolFilter", bool],
      },
      status: {
        state: ["stState", (v, s) => v || (s.enabled ? "armed" : "disabled")],
        active_straddles: ["stActive", (v) => v ?? 0],
        signals_today: ["stSignals", (v) => v ?? 0],
        last_rvol: ["stRvol", (v) => (v ?? 0).toFixed(1)],
        cooldown_left_s: ["stCooldown", (v) => (v || 0) + "s"],
      },
      errorEl: "stError",
    },
    {
      id: "ladder",
      label: "Ladder",
      panel: "ladderPanel",
      enabledEl: "ldEnabled",
      // Every rung is its own position with its own ticket and target; a netting
      // account would collapse them into one line and make that map fiction.
      needsHedging: true,
      fields: {
        side: ["ldSide", str],
        trigger: ["ldTrigger", num],
        volume: ["ldVolume", num],
        target: ["ldTarget", num],
        stop_mode: ["ldStopMode", str],
        retrace: ["ldRetrace", num],
        trail_activate: ["ldTrailActivate", num],
        floor_offset: ["ldFloorOffset", num],
        max_positions: ["ldMaxPos", int],
        max_lots: ["ldMaxLots", num],
        entry_mode: ["ldEntryMode", str],
        entry_step: ["ldStep", num],
        entry_gap_ms: ["ldGapMs", int],
        hard_sl: ["ldHardSl", num],
        max_daily_loss: ["ldMaxLoss", num],
        cooldown_s: ["ldCooldown", num],
        max_ladders_per_day: ["ldMaxLadders", int],
        paper: ["ldPaper", bool],
        auto_continue: ["ldAutoContinue", bool],
      },
      status: {
        state: ["ldState", (v, s) => v || (s.enabled ? "armed" : "disabled")],
        open_positions: ["ldOpen", (v) => v ?? 0],
        open_lots: ["ldOpenLots", (v) => (v ?? 0).toFixed(2)],
        ladders_done: ["ldDone", (v) => v ?? 0],
        ladders_today: ["ldToday", (v) => v ?? 0],
        paper_pl_per_oz: ["ldPaperPl", (v) => (v ?? 0).toFixed(3)],
        paper_trades: ["ldPaperTrades", (v) => v ?? 0],
        spread: ["ldSpread", (v) => (v ? v.toFixed(3) + "/oz" : "—")],
        // Derived server-side, both of them. The dialled stop is measured on the
        // entry-side price but realised on the exit side, so the operator gives up
        // stop + spread -- a difference that showed up live as a 0.30 setting
        // costing 0.34. Showing the raw dial alone was the misleading part.
        effective_stop: ["ldEffStop", (v) => (v ? v.toFixed(3) + "/oz" : "—")],
        floor_price: ["ldFloorPx", (v) => (v ? v.toFixed(3) : "—")],
      },
      errorEl: "ldError",
      warnEl: "ldWarn",
    },
    {
      id: "sladder",
      label: "Str·Ladder",
      panel: "sladderPanel",
      enabledEl: "slEnabled",
      // The opening straddle holds a long AND a short at once -- a netting account nets them
      // to zero, so the engine refuses to arm there (StrategyBase.needs_hedging).
      needsHedging: true,
      fields: {
        level: ["slLevel", num],
        sl: ["slSl", num],
        tp: ["slTp", num],
        gap: ["slGap", num],
        volume: ["slVolume", num],
        max_legs: ["slMaxLegs", int],
        max_lots: ["slMaxLots", num],
        cooldown_s: ["slCooldown", num],
        max_daily_loss: ["slMaxLoss", num],
        always_straddle: ["slAlwaysStraddle", bool],
      },
      status: {
        state: ["slState", (v, s) => v || (s.enabled ? "armed" : "disabled")],
        phase: ["slPhase", (v) => v || "—"],
        direction: ["slDir", (v) => (v ? v.toUpperCase() : "—")],
        next_entry: ["slNext", (v) => (v ? Number(v).toFixed(2) : "—")],
        legs_taken: ["slLegs", (v) => v ?? 0],
        open_legs: ["slOpen", (v) => v ?? 0],
        spread: ["slSpread", (v) => (v ? v.toFixed(3) + "/oz" : "—")],
      },
      errorEl: "slError",
    },
    {
      id: "rider",
      label: "Rider",
      panel: "riderPanel",
      enabledEl: "rdEnabled",
      needsHedging: false,
      fields: {
        thrust_mult: ["rdThrust", num],
        stop_units: ["rdStopUnits", str],
        sl: ["rdSl", num],
        trail: ["rdTrail", num],
        tp: ["rdTp", num],
        max_hold: ["rdMaxHold", int],
        atr_win: ["rdAtrWin", int],
        use_ny_hours: ["rdNy", bool],
        risk_frac: ["rdRisk", num],
        max_daily_loss: ["rdMaxLoss", num],
        auto_demo: ["rdAutoDemo", bool],
        auto_real: ["rdAutoReal", bool],
      },
      status: {
        state: ["rdState", (v, s) => v || (s.enabled ? "active" : "disabled")],
        execution: ["rdExec", (v) => v || "suggest"],
        live_ticket: ["rdLiveTicket", (v) => (v ? String(v) : "—")],
        live_stop: ["rdLiveStop", (v) => (v != null ? Number(v).toFixed(2) : "—")],
        in_paper_position: ["rdInPos", (v) => (v ? "yes" : "no")],
        paper_pnl_per_oz: ["rdPaperPl", (v) => (v ?? 0).toFixed(2)],
        // Accrued at the lot each trade was sized at -- NOT the running $/oz total
        // rescaled by whatever lot the current card happens to carry.
        paper_pnl_usd: ["rdPaperUsd", (v) => (v ?? 0).toFixed(2)],
        paper_trades: ["rdPaperTrades", (v) => v ?? 0],
      },
      errorEl: "rdError",
    },
  ];

  let pollTimer = null;
  let userTouched = false;     // don't stomp inputs the user is mid-edit
  let last = {};               // last /api/strategies payload
  let paperWasOn = {};         // per engine: was PAPER on at last render?
  // Edge-trigger for the manual-reload alert: chime only on the OFF->ON transition,
  // never every poll while it stays parked.
  let attnWasOn = {};
  // The rider's execution switches as the SERVER last reported them. Confirms fire on
  // the OFF->ON edge only, so re-saving the panel with auto already on does not
  // re-prompt (and, more importantly, a prompt cannot be trained into muscle memory).
  let autoWasOn = { demo: false, real: false };

  function toast(msg, kind) {
    const box = document.getElementById("toasts");
    if (!box) { console.log("[strategy]", msg); return; }
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // Routed through API.req/API.post so the x-token header is attached. These routes
  // are token-checked server-side; without it the panel would 401 the moment
  // API_TOKEN is set, in the same silent way the Buy button would.
  async function jget(url) {
    const r = await API.req(url, {});
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
  async function jpost(url, body) {
    const r = await API.post(url, body);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  // ---- form <-> status --------------------------------------------------
  function paramsFromForm(eng) {
    const out = {};
    for (const [k, [id, read]] of Object.entries(eng.fields)) {
      const el = $(id);
      if (el) out[k] = read(el);
    }
    return out;
  }

  function fillForm(eng, p) {
    if (userTouched || !p) return;
    for (const [k, [id]] of Object.entries(eng.fields)) {
      const el = $(id);
      if (!el || p[k] === undefined || p[k] === null) continue;
      if (el.type === "checkbox") el.checked = !!p[k];
      else el.value = p[k];
    }
  }

  function renderOne(eng, s) {
    if (!s) return;
    const en = $(eng.enabledEl);
    if (en) en.checked = !!s.enabled;
    fillForm(eng, s.params);

    for (const [k, [id, fmt]] of Object.entries(eng.status)) {
      const el = $(id);
      if (el) el.textContent = fmt(s[k], s);
    }

    const err = $(eng.errorEl);
    if (err) {
      if (s.error) { err.hidden = false; err.textContent = "⚠ " + s.error; }
      else err.hidden = true;
    }
    // The engine's own warning (e.g. max_positions > 1 multiplies the spread
    // cost). It is computed server-side from real measurements -- render it,
    // never swallow it.
    if (eng.warnEl) {
      const w = $(eng.warnEl);
      if (w) {
        if (s.warning) { w.hidden = false; w.textContent = "⚠ " + s.warning; }
        else w.hidden = true;
      }
    }
    if (eng.id === "ladder") { renderSpreadHint(s); renderStopMode(s); renderAttention(s); renderFireHint(s); }
    if (eng.id === "sladder") { renderSladderHints(s); renderSladderAttention(s); renderSladderFire(s); }
    if (eng.id === "rider") {
      renderRiderCard(s);
      renderStopUnits();
      autoWasOn = { demo: !!(s.params && s.params.auto_demo),
                    real: !!(s.params && s.params.auto_real) };
    }
    paperWasOn[eng.id] = s.params ? !!s.params.paper : true;
  }

  /* Rewrite the Stop/Trailing labels to match the selected UNITS.
     The two fields do not change, but what "6" MEANS in them does: $6.00 in fixed
     mode, 6 x ATR in atr mode -- and at a typical M5 ATR of ~$4.50 that is a $27
     stop, four times wider. A static "$/oz" label next to a multiplier is exactly
     the kind of unit mismatch that put a 10x-wrong stop on a live order once already. */
  function renderStopUnits() {
    const sel = $("rdStopUnits");
    if (!sel) return;
    const atr = sel.value === "atr";
    const sl = $("rdSlLabel"), tr = $("rdTrailLabel");
    if (sl) sl.textContent = atr ? "Stop (× ATR)" : "Stop ($/oz)";
    if (tr) tr.textContent = atr ? "Trailing (× ATR)" : "Trailing ($/oz)";
    // Sane bounds per mode: a 6 left over from $/oz means 6xATR here, so nudge the
    // step/max to make the multiplier reading obvious rather than silently accepted.
    for (const el of [$("rdSl"), $("rdTrail")]) {
      if (!el) continue;
      el.step = atr ? "0.25" : "0.5";
      el.max = atr ? "10" : "";
    }
  }
  { const el = $("rdStopUnits"); if (el) el.addEventListener("change", renderStopUnits); }

  /* The rider's live TRADE-NOW card. It is a SUGGESTION: shown only on an
     actionable 'enter' signal while enabled; the operator taps Place to send it
     through the normal /order path. The engine itself places nothing. */
  function renderRiderCard(s) {
    const card = $("rdCard"), idle = $("rdCardIdle");
    const c = (s && s.card) || {};
    /* `actionable` is DERIVED SERVER-SIDE (rider._actionable): the card was issued on
       the newest closed bar, the engine is armed, and it is not already placing for
       you. Do NOT re-derive it from c.kind here -- the card keeps kind:"enter" for the
       whole trade (up to ~2 h), so that test would leave this button offering a
       long-dead entry price, and Android would have to reimplement the same rule and
       could disagree with it. */
    const actionable = !!(s && s.actionable);
    if (card) card.hidden = !actionable;
    if (idle) idle.hidden = !!actionable;
    if (actionable) {
      const side = $("rdCardSide");
      side.textContent = (c.side || "").toUpperCase();
      side.style.color = c.side === "buy" ? "#2fa572" : "#e0664f";
      $("rdCardLot").textContent = c.lot;
      $("rdCardEntry").textContent = c.entry;
      $("rdCardSl").textContent = c.sl;
      $("rdCardTp").textContent = c.tp;
      $("rdCardReason").textContent = c.reason || "";
    } else if (idle) {
      idle.textContent = (s && s.live_ticket)
        ? `Riding a LIVE position (ticket ${s.live_ticket}, stop ${s.live_stop}). The engine trails it on every tick and exits at market.`
        : (s && (s.execution === "auto-demo" || s.execution === "AUTO-REAL"))
        ? `Auto-trading is ON (${s.execution}) — the next signal is placed for you, so there is nothing to tap.`
        : c.kind === "close"
        ? `Last exit: ${c.reason} (paper ${c.pnl_oz >= 0 ? "+" : ""}${c.pnl_oz}/oz). Waiting for the next thrust.`
        : (c.kind === "enter" && c.status)
          ? "That suggestion has expired (it was only good for its own bar). Waiting for the next thrust."
        : (s && s.in_paper_position)
          ? "In a paper position — a CLOSE card will appear on exit."
          : (c.note || "No trade signalled right now — waiting for a thrust in a high-vol regime.");
    }
  }

  /* Show the live spread beside the Target field, and turn the field red when the
     target sits inside it. The SERVER already refuses to arm in that case -- this
     just says WHY before Apply is pressed, rather than after. A target smaller than
     the spread is not a strategy, it is a fee. */
  function renderSpreadHint(s) {
    const hint = $("ldSpreadHint");
    const tgt = $("ldTarget");
    const spread = Number(s.spread || 0);
    if (!hint || !tgt) return;
    if (!spread) { hint.textContent = ""; tgt.classList.remove("bad"); return; }
    const t = parseFloat(tgt.value);
    hint.textContent = `— live spread ${spread.toFixed(2)}/oz`;
    const bad = !isNaN(t) && t <= spread;
    tgt.classList.toggle("bad", bad);
    hint.classList.toggle("bad", bad);
    if (bad) hint.textContent += ` → a winner still nets ${(t - spread).toFixed(2)}/oz`;
  }

  /* Only ONE stop is in play at a time, so only show the field that is. Both being
     visible invited the reading that they combine -- they do not: `stop_mode` picks
     one and the other is dead. Also flags a floor inside the spread, which the
     server refuses for the same reason it refuses a target inside the spread: the
     ladder is already marked past it the moment it arms, so it would flush on the
     first tick. */
  function renderStopMode(s) {
    const mode = ($("ldStopMode") || {}).value || "retrace";
    const row = (id) => { const el = $(id); return el && el.closest(".set-row"); };
    const rRow = row("ldRetrace"), fRow = row("ldFloorOffset");
    if (rRow) rRow.hidden = mode !== "retrace";
    if (fRow) fRow.hidden = mode !== "floor";

    const spread = Number(s.spread || 0);

    // retrace vs spread. This is the guard that actually bites in normal session -- the server
    // refuses when `retrace <= spread` (every rung stops out before it can move) -- yet it was the
    // ONE guard with no hint here, so the refusal only ever showed up as an error string after
    // Apply. Same shape as the target and floor hints.
    const rHint = $("ldRetraceHint"), rtr = $("ldRetrace");
    if (rHint && rtr) {
      if (mode !== "retrace" || !spread) { rHint.textContent = ""; rtr.classList.remove("bad"); }
      else {
        const v = parseFloat(rtr.value);
        const bad = !isNaN(v) && v <= spread;
        rHint.textContent = bad
          ? `— inside the ${spread.toFixed(2)}/oz spread → every rung stops out before it moves`
          : `— live spread ${spread.toFixed(2)}/oz`;
        rtr.classList.toggle("bad", bad);
        rHint.classList.toggle("bad", bad);
      }
    }

    const hint = $("ldFloorHint"), off = $("ldFloorOffset");
    if (!hint || !off) return;
    if (mode !== "floor" || !spread) { hint.textContent = ""; off.classList.remove("bad"); return; }
    const v = parseFloat(off.value);
    const bad = !isNaN(v) && v <= spread;
    hint.textContent = bad
      ? `— inside the ${spread.toFixed(2)}/oz spread → would flush on the first tick`
      : `— live spread ${spread.toFixed(2)}/oz`;
    off.classList.toggle("bad", bad);
    hint.classList.toggle("bad", bad);
  }

  /* A distinct double-chime for the manual-reload alert. Prefers app.js's `beep`
     (which honours the user's sound toggle); falls back to a self-contained tone so
     this file keeps working even loaded on its own. Never throws. */
  function chime() {
    try {
      if (typeof window.beep === "function") {
        window.beep("buy");
        setTimeout(() => { try { window.beep("buy"); } catch (e) {} }, 180);
        return;
      }
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      const ac = new AC();
      for (const t of [0, 0.18]) {
        const o = ac.createOscillator(), g = ac.createGain();
        o.connect(g); g.connect(ac.destination);
        o.frequency.value = 880; o.type = "sine";
        g.gain.setValueAtTime(0.0001, ac.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.14, ac.currentTime + t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + t + 0.15);
        o.start(ac.currentTime + t); o.stop(ac.currentTime + t + 0.16);
      }
    } catch (e) {}
  }

  /* Manual-reload alert. The engine rides ONE leg then PARKS -- it never re-enters
     itself. `needs_attention` (derived server-side, so both clients agree) lights this
     banner and chimes ONCE on the off->on edge, so the operator sets a new level rather
     than watch a trend walk away un-traded. */
  function renderAttention(s) {
    const el = $("ldAttention");
    if (!el) return;
    const on = !!s.needs_attention;
    if (on) {
      el.hidden = false;
      el.textContent = "🔔 " + (s.attention_reason ||
        "Ladder closed out and parked — set a new level to re-engage.");
      if (!attnWasOn.ladder) { chime(); toast("Ladder parked — set a new level to re-engage", "info"); }
    } else {
      el.hidden = true;
    }
    attnWasOn.ladder = on;
  }

  /* Warn, on the trigger field, when the CURRENT trigger is already on the crossed side
     -- a SELL level above the bid (or a BUY below the ask) arms INSTANTLY instead of
     waiting for the move. `would_fire_now` is derived server-side from the live quote. */
  function renderFireHint(s) {
    const el = $("ldFireHint");
    if (!el) return;
    if (s.would_fire_now) {
      el.textContent = s.enabled
        ? "⚠ already past — this level fires INSTANTLY, not on a move"
        : "⚠ already past — arms instantly the moment you enable";
      el.classList.add("bad");
    } else {
      el.textContent = "";
      el.classList.remove("bad");
    }
  }

  /* Straddle-ladder hints, mirroring the ladder's: redden sl/tp when either sits inside the
     live spread (the server refuses to arm then -- an sl inside the spread stops on the first
     tick, a tp inside it never nets a win), and warn on the level field when price already sits
     at it (arming would place the straddle immediately). All verdicts are server-derived; this
     only surfaces WHY before Apply, never recomputes the refusal. */
  function renderSladderHints(s) {
    const spread = Number(s.spread || 0);
    for (const [inp, hint] of [["slSl", "slSlHint"], ["slTp", "slTpHint"]]) {
      const el = $(inp), h = $(hint);
      if (!el || !h) continue;
      if (!spread) { h.textContent = ""; el.classList.remove("bad"); continue; }
      const v = parseFloat(el.value);
      const bad = !isNaN(v) && v <= spread;
      h.textContent = bad
        ? `— inside the ${spread.toFixed(2)}/oz spread → cannot win`
        : `— live spread ${spread.toFixed(2)}/oz`;
      el.classList.toggle("bad", bad);
      h.classList.toggle("bad", bad);
    }
  }
  function renderSladderFire(s) {
    const el = $("slFireHint");
    if (!el) return;
    if (s.would_fire_now) {
      el.textContent = s.enabled
        ? "⚠ price is AT the level — the straddle goes on now"
        : "⚠ price is at the level — arming places the straddle at once";
      el.classList.add("bad");
    } else { el.textContent = ""; el.classList.remove("bad"); }
  }
  function renderSladderAttention(s) {
    const el = $("slAttention");
    if (!el) return;
    const on = !!s.needs_attention;
    if (on) {
      el.hidden = false;
      el.textContent = "🔔 " + (s.attention_reason ||
        "Run parked — set a new level to continue the trend.");
      if (!attnWasOn.sladder) { chime(); toast("Straddle-ladder parked — set a new level", "info"); }
    } else el.hidden = true;
    attnWasOn.sladder = on;
  }

  function renderAll(all) {
    last = all || {};
    for (const eng of ENGINES) renderOne(eng, last[eng.id]);

    // Top-bar dot: lit if ANY engine is enabled, red if ANY is killed.
    const dot = $("stratDot");
    if (dot) {
      const vals = Object.values(last);
      const anyOn = vals.some((s) => s && s.enabled);
      const anyKilled = vals.some((s) => s && s.killed);
      dot.hidden = !(anyOn || anyKilled);
      dot.className = "strat-dot " + (anyKilled ? "killed" : anyOn ? "on" : "");
    }
  }

  async function refreshSafety() {
    const banner = $("stratBanner");
    try {
      const a = await jget("/api/account/safety");
      const hedging = a.margin_mode === 2;
      if (!a.connected) { banner.className = "autotest-banner loading"; banner.textContent = "not connected"; }
      else if (!a.is_demo) { banner.className = "autotest-banner real"; banner.textContent = "REAL account — strategies are blocked (demo only)"; }
      else { banner.className = "autotest-banner demo"; banner.textContent = `DEMO · ${hedging ? "hedging" : "NETTING"} · ${a.login} @ ${a.server}`; }
      return { demo: !!a.is_demo, hedging };
    } catch (e) {
      banner.className = "autotest-banner loading"; banner.textContent = "account check failed";
      return { demo: false, hedging: false };
    }
  }

  async function poll() {
    try { renderAll(await jget("/api/strategies")); } catch (e) { /* ignore */ }
  }
  function startPolling(fast) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, fast ? 1000 : 3000);
    poll();
  }

  // ---- apply ------------------------------------------------------------
  function currentEngine() {
    const tab = modal.querySelector(".strat-tab.on");
    const panel = tab && tab.dataset.panel;
    return ENGINES.find((e) => e.panel === panel) || ENGINES[0];
  }

  async function apply(eng, enabledOverride) {
    const body = paramsFromForm(eng);
    body.enabled = enabledOverride !== undefined
      ? enabledOverride : !!$(eng.enabledEl).checked;

    // Two SEPARATE confirms, deliberately.
    //
    // 1) Turning PAPER off is the moment this engine starts sending real orders.
    //    That is a different decision from "enable", and folding them into one
    //    dialog would let a user click through the enable prompt and arm a live
    //    trader without ever being asked about it.
    if (eng.id === "ladder" && body.paper === false && paperWasOn.ladder !== false) {
      const lots = Number((body.max_lots ?? 0));
      const uncapped = Number(body.max_positions ?? 1) === 0 && lots <= 0;
      if (!confirm(
        "Turn PAPER MODE OFF?\n\n" +
        "The ladder will place REAL orders on the demo account from the next trigger.\n\n" +
        "Measured on 37,500 real ticks: with no directional edge this loses ~1 spread " +
        "per trade, and the ladder multiplies that cost. Only your trigger " +
        "can beat it — and paper mode is how you find out whether it does.\n\n" +
        "Measured LIVE on 2026-07-21: 68 ladders and 79 trades in 25 minutes, " +
        "13.9% win rate, and the target was never once reached." +
        (uncapped
          ? "\n\nMax positions AND max lots are both 0 (uncapped). A slow grind adds " +
            "rungs faster than the target drains them, and the floor closes all of " +
            "them at once. Set a lot cap first."
          : ""))) {
        $("ldPaper").checked = true;
        return;
      }
    }

    // 1b) The rider's two execution switches. Confirm each ON transition separately,
    //     for the same reason as (1): "auto-trade on demo" and "auto-trade with real
    //     money" are different decisions and must be consented to individually.
    //     Turning either OFF is never confirmed -- never stand between a user and the
    //     safe direction.
    if (eng.id === "rider") {
      if (body.auto_demo === true && !autoWasOn.demo &&
        !confirm(
          "Turn ON auto-trading for DEMO accounts?\n\n" +
          "The rider will place orders BY ITSELF whenever it signals, with no tap. " +
          "A stop is attached at the broker and the trail runs server-side.\n\n" +
          "Demo money only — this is how you build the forward-test record.")) {
        $("rdAutoDemo").checked = false; return;
      }
      if (body.auto_real === true && !autoWasOn.real &&
        !confirm(
          "Turn ON auto-trading for REAL accounts?\n\n" +
          "THIS SPENDS REAL MONEY, with no confirmation per trade.\n\n" +
          "What is actually known: backtested +$0.585/oz over random, with a confidence " +
          "interval that INCLUDES ZERO, and it is regime-dependent — it needs volatility, " +
          "and a calm market bleeds. It is not a proven edge.\n\n" +
          "The only cap is the daily-loss kill-switch (currently " +
          `$${body.max_daily_loss ?? "?"}). Size follows risk_frac x equity, so a larger ` +
          "balance means a larger position.\n\n" +
          "This switch never resumes by itself after a restart.")) {
        $("rdAutoReal").checked = false; return;
      }
    }

    // 2) Enabling at all.
    if (body.enabled) {
      const safe = await refreshSafety();
      // The rider is the one engine that may run on a real account -- and only when the
      // operator has explicitly turned auto_real on (the server re-checks both).
      const realOk = eng.id === "rider" && body.auto_real === true;
      if (!safe.demo && !realOk) {
        toast("Blocked: needs a DEMO account", "err");
        $(eng.enabledEl).checked = false; return;
      }
      if (eng.needsHedging && !safe.hedging) {
        toast("Blocked: this strategy needs a hedging account", "err");
        $(eng.enabledEl).checked = false; return;
      }
      const live = eng.id === "ladder" && body.paper === false;
      // The rider's wording follows its EXECUTION switches, not a paper flag. Telling a
      // user "it will place REAL orders when it signals" while both switches are off
      // would be simply false -- and false safety warnings are how real ones get ignored.
      const riderNote = body.auto_real
        ? "\n\nAUTO-TRADE ON REAL IS ON — it will place orders with REAL MONEY, unattended."
        : body.auto_demo
          ? "\n\nAuto-trade on DEMO is on — it will place demo orders by itself when it signals."
          : "\n\nBoth auto-trade switches are OFF — it will only suggest; you tap Place.";
      if (!confirm(`Enable ${eng.label}?` + (eng.id === "rider"
        ? riderNote
        : live
        ? "\n\nPAPER MODE IS OFF — it will place REAL orders on the demo account."
        : eng.id === "ladder"
          ? "\n\nPaper mode is ON — it will log what it would do and place NO orders."
          : "\n\nIt will place REAL orders on the demo account when it signals."))) {
        $(eng.enabledEl).checked = false; return;
      }
    }

    try {
      userTouched = false;
      const s = await jpost("/api/strategy/" + eng.id, body);
      renderOne(eng, s);
      last[eng.id] = s;
      // The server may REFUSE to arm (e.g. target inside the spread) and hand back
      // enabled:false with a reason. Reporting "enabled" on the strength of a 200
      // would be a lie -- read the engine's own answer, not the request.
      if (body.enabled && !s.enabled) toast(s.error || "Refused", "err");
      else toast(body.enabled ? `${eng.label} enabled` : `${eng.label} updated`, "ok");
    } catch (e) {
      toast(`${eng.label} update failed: ` + e.message, "err");
    }
  }

  // ---- tabs -------------------------------------------------------------
  function showTab(panelId) {
    modal.querySelectorAll(".strat-tab").forEach((t) =>
      t.classList.toggle("on", t.dataset.panel === panelId));
    ENGINES.forEach((e) => { const p = $(e.panel); if (p) p.hidden = e.panel !== panelId; });
  }

  // ---- wiring -----------------------------------------------------------
  function openModal() {
    userTouched = false;
    modal.hidden = false;
    refreshSafety();
    startPolling(true);
  }
  function closeModal() {
    modal.hidden = true;
    startPolling(false);   // keep a slow poll so the top-bar dot stays live
  }

  $("strategyBtn").addEventListener("click", openModal);
  $("strategyClose").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  modal.querySelectorAll(".strat-tab").forEach((t) =>
    t.addEventListener("click", () => showTab(t.dataset.panel)));

  $("stratApply").addEventListener("click", () => apply(currentEngine()));

  // Rider PLACE: the ONE human tap that turns the current suggestion into a real
  // demo order, through the SAME /order path the Buy/Sell buttons use. The rider
  // engine itself never places anything — this handler is the only trigger.
  const rdPlace = $("rdPlace");
  if (rdPlace) rdPlace.addEventListener("click", async () => {
    const s = last.rider || {};
    const c = s.card || {};
    // Same server-derived gate the button's visibility uses, re-checked at tap time:
    // the card can expire between the render and the click.
    if (!s.actionable) { toast("No active trade suggestion right now", "err"); return; }
    const safe = await refreshSafety();
    if (!safe.demo) { toast("Blocked: needs a DEMO account", "err"); return; }
    // /order takes sl/tp as POINT DISTANCES, not prices: PlaceReq carries no
    // sl_tp_mode, so the worker falls back to config.SL_TP_MODE = "points".
    // Posting the card's absolute price here silently placed a ~$4 stop instead
    // of the intended $6 (and a $4 target instead of $50). Convert -- and refuse
    // to place at all if the point size is unreadable: a wrong-unit stop on a
    // live order is worse than no trade.
    let point = 0;
    try { point = Number((await jget("/api/state")).point) || 0; } catch (e) { point = 0; }
    if (!point) { toast("Cannot read symbol point size — not placing", "err"); return; }
    const slPts = Math.round(Math.abs(c.entry - c.sl) / point);
    const tpPts = Math.round(Math.abs(c.tp - c.entry) / point);
    if (!confirm(`Place ${(c.side || "").toUpperCase()} ${c.lot} lot at market?\n\n` +
      `SL ${(slPts * point).toFixed(2)} away  ·  TP ${(tpPts * point).toFixed(2)} away\n\n` +
      `Real order on the DEMO account — you are the trigger.`)) return;
    try {
      await jpost("/order", { side: c.side, volume: c.lot, type: "market",
                              sl: slPts, tp: tpPts, origin: "rider" });
      toast(`Placed ${(c.side || "").toUpperCase()} ${c.lot} @ market`, "ok");
    } catch (e) { toast("Order failed: " + e.message, "err"); }
  });

  for (const eng of ENGINES) {
    const en = $(eng.enabledEl);
    if (en) en.addEventListener("change", () => apply(eng, en.checked));
    for (const [, [id]] of Object.entries(eng.fields)) {
      const el = $(id);
      if (!el) continue;
      const touched = () => {
        userTouched = true;
        if (eng.id === "ladder") { renderSpreadHint(last.ladder || {}); renderStopMode(last.ladder || {}); }
        if (eng.id === "sladder") { renderSladderHints(last.sladder || {}); renderSladderFire(last.sladder || {}); }
      };
      el.addEventListener("input", touched);
      // A <select> fires "change", not "input", in every browser that matters. Wiring
      // only "input" left the stop-mode switch showing the wrong field until the next
      // poll overwrote it -- i.e. the operator picking "floor" still saw "retrace".
      if (el.tagName === "SELECT") el.addEventListener("change", touched);
    }
  }

  $("stratFlatten").addEventListener("click", async () => {
    const eng = currentEngine();
    $(eng.enabledEl).checked = false;
    await apply(eng, false);
    toast(`${eng.label} disabled. Use Close-All to flatten open legs.`, "");
  });

  startPolling(false);     // keep the top-bar dot alive from page load
})();
