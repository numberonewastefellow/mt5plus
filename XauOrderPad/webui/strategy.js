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
      needsHedging: false,
      fields: {
        side: ["ldSide", str],
        trigger: ["ldTrigger", num],
        volume: ["ldVolume", num],
        target: ["ldTarget", num],
        retrace: ["ldRetrace", num],
        max_positions: ["ldMaxPos", int],
        entry_mode: ["ldEntryMode", str],
        entry_step: ["ldStep", num],
        entry_gap_ms: ["ldGapMs", int],
        hard_sl: ["ldHardSl", num],
        max_daily_loss: ["ldMaxLoss", num],
        paper: ["ldPaper", bool],
      },
      status: {
        state: ["ldState", (v, s) => v || (s.enabled ? "armed" : "disabled")],
        open_positions: ["ldOpen", (v) => v ?? 0],
        ladders_done: ["ldDone", (v) => v ?? 0],
        paper_pl_per_oz: ["ldPaperPl", (v) => (v ?? 0).toFixed(3)],
        paper_trades: ["ldPaperTrades", (v) => v ?? 0],
        spread: ["ldSpread", (v) => (v ? v.toFixed(3) + "/oz" : "—")],
      },
      errorEl: "ldError",
      warnEl: "ldWarn",
    },
  ];

  let pollTimer = null;
  let userTouched = false;     // don't stomp inputs the user is mid-edit
  let last = {};               // last /api/strategies payload
  let paperWasOn = {};         // per engine: was PAPER on at last render?

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
    if (eng.id === "ladder") renderSpreadHint(s);
    paperWasOn[eng.id] = s.params ? !!s.params.paper : true;
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
    const id = tab && tab.id === "tabLadder" ? "ladder" : "straddle";
    return ENGINES.find((e) => e.id === id);
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
      if (!confirm(
        "Turn PAPER MODE OFF?\n\n" +
        "The ladder will place REAL orders on the demo account from the next trigger.\n\n" +
        "Measured on 37,500 real ticks: with no directional edge this loses ~1 spread " +
        "(0.24/oz) per trade, and the ladder multiplies that cost. Only your trigger " +
        "can beat it — and paper mode is how you find out whether it does.")) {
        $("ldPaper").checked = true;
        return;
      }
    }

    // 2) Enabling at all.
    if (body.enabled) {
      const safe = await refreshSafety();
      if (!safe.demo) {
        toast("Blocked: needs a DEMO account", "err");
        $(eng.enabledEl).checked = false; return;
      }
      if (eng.needsHedging && !safe.hedging) {
        toast("Blocked: this strategy needs a hedging account", "err");
        $(eng.enabledEl).checked = false; return;
      }
      const live = eng.id === "ladder" && body.paper === false;
      if (!confirm(`Enable ${eng.label}?` + (live
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

  for (const eng of ENGINES) {
    const en = $(eng.enabledEl);
    if (en) en.addEventListener("change", () => apply(eng, en.checked));
    for (const [, [id]] of Object.entries(eng.fields)) {
      const el = $(id);
      if (el) el.addEventListener("input", () => {
        userTouched = true;
        if (eng.id === "ladder") renderSpreadHint(last.ladder || {});
      });
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
