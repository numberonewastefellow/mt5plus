/* Strategy panel — self-contained, independent of app.js.
 *
 * Talks to the backend the strategy endpoints only:
 *   GET  /api/strategy/status   -> current status (polled ~1s while open, 3s idle)
 *   POST /api/strategy          -> {enabled?, ...params}  enable/disable + tune
 *   GET  /api/account/safety    -> demo/hedging gating for the banner
 *   POST /close                 -> flatten (per-ticket) is done server-side; here
 *                                  FLATTEN just disables + the backend kill path
 *                                  is the real safety. We disable then rely on the
 *                                  strategy's own close on next toggle.
 *
 * Kept deliberately separate so it never interferes with the main terminal JS. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const modal = $("strategyModal");
  if (!modal) return;

  const fields = {
    enabled: $("stratEnabled"), volume: $("stratVolume"), rvol_threshold: $("stratRvol"),
    sl_atr_mult: $("stratSlAtr"), tp_r: $("stratTpR"), max_hold_min: $("stratHold"),
    cooldown_min: $("stratCooldown"), max_concurrent: $("stratConcurrent"),
    max_daily_loss: $("stratMaxLoss"), vol_filter: $("stratVolFilter"),
  };
  let pollTimer = null;
  let open = false;
  let userTouched = false; // don't stomp inputs the user is editing

  function toast(msg, kind) {
    // Reuse the app's toast area if present; else console.
    const box = document.getElementById("toasts");
    if (!box) { console.log("[strategy]", msg); return; }
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // Routed through API.req so the x-token header is attached. /api/strategy/status is
  // token-checked server-side (it is part of the same account state /ws protects).
  async function jget(url) {
    const r = await API.req(url, {});
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }
  // Routed through API.post so the x-token header is attached. POST /api/strategy
  // is token-checked server-side; without this the Strategy panel would 401 the
  // moment API_TOKEN is set, in the same silent way the Buy button would.
  async function jpost(url, body) {
    const r = await API.post(url, body);
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.json();
  }

  function paramsFromForm() {
    return {
      volume: parseFloat(fields.volume.value),
      rvol_threshold: parseFloat(fields.rvol_threshold.value),
      sl_atr_mult: parseFloat(fields.sl_atr_mult.value),
      tp_r: parseFloat(fields.tp_r.value),
      max_hold_min: parseFloat(fields.max_hold_min.value),
      cooldown_min: parseFloat(fields.cooldown_min.value),
      max_concurrent: parseInt(fields.max_concurrent.value, 10),
      max_daily_loss: parseFloat(fields.max_daily_loss.value),
      vol_filter: !!fields.vol_filter.checked,
    };
  }

  function fillForm(p) {
    if (userTouched || !p) return;
    fields.volume.value = p.volume;
    fields.rvol_threshold.value = p.rvol_threshold;
    fields.sl_atr_mult.value = p.sl_atr_mult;
    fields.tp_r.value = p.tp_r;
    fields.max_hold_min.value = p.max_hold_min;
    fields.cooldown_min.value = p.cooldown_min;
    fields.max_concurrent.value = p.max_concurrent;
    fields.max_daily_loss.value = p.max_daily_loss;
    fields.vol_filter.checked = !!p.vol_filter;
  }

  function renderStatus(s) {
    if (!s) return;
    fields.enabled.checked = !!s.enabled;
    fillForm(s.params);
    $("stState").textContent = s.state || (s.enabled ? "armed" : "disabled");
    $("stActive").textContent = s.active_straddles ?? 0;
    $("stSignals").textContent = s.signals_today ?? 0;
    $("stRvol").textContent = (s.last_rvol ?? 0).toFixed
      ? (s.last_rvol).toFixed(1) : s.last_rvol;
    $("stCooldown").textContent = (s.cooldown_left_s || 0) + "s";
    $("stRealized").textContent = (s.realized_today != null ? s.realized_today : "—");
    const err = $("stError");
    if (s.error) { err.hidden = false; err.textContent = "⚠ " + s.error; }
    else err.hidden = true;
    // top-bar dot: green when running, red when killed/error
    const dot = $("stratDot");
    if (dot) {
      dot.hidden = !(s.enabled || s.killed);
      dot.className = "strat-dot " + (s.killed ? "killed" : s.enabled ? "on" : "");
    }
  }

  async function refreshSafety() {
    const banner = $("stratBanner");
    try {
      const a = await jget("/api/account/safety");
      const hedging = a.margin_mode === 2;
      if (!a.connected) { banner.className = "autotest-banner loading"; banner.textContent = "not connected"; }
      else if (!a.is_demo) { banner.className = "autotest-banner real"; banner.textContent = "REAL account — strategy is blocked (demo only)"; }
      else if (!hedging) { banner.className = "autotest-banner real"; banner.textContent = "NETTING account — straddle needs hedging"; }
      else { banner.className = "autotest-banner demo"; banner.textContent = `DEMO · hedging · ${a.login} @ ${a.server} — OK to test`; }
      return a.is_demo && hedging;
    } catch (e) {
      banner.className = "autotest-banner loading"; banner.textContent = "account check failed";
      return false;
    }
  }

  async function poll() {
    try { renderStatus(await jget("/api/strategy/status")); } catch (e) { /* ignore */ }
  }

  function startPolling(fast) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, fast ? 1000 : 3000);
    poll();
  }

  // ---- apply / toggle ----
  async function apply(enabledOverride) {
    const body = paramsFromForm();
    if (enabledOverride !== undefined) body.enabled = enabledOverride;
    else body.enabled = !!fields.enabled.checked;
    if (body.enabled) {
      const ok = await refreshSafety();
      if (!ok) { toast("Strategy blocked: needs a DEMO + hedging account", "err"); fields.enabled.checked = false; return; }
      if (!confirm("Enable the automated straddle? It will place REAL orders on the demo account when a volume spike fires.")) {
        fields.enabled.checked = false; return;
      }
    }
    try {
      userTouched = false;
      renderStatus(await jpost("/api/strategy", body));
      toast(body.enabled ? "Strategy enabled" : "Strategy updated", "ok");
    } catch (e) {
      // The POST failed (401, network, backend guard) — so the server did NOT enable
      // the strategy. The checkbox, however, was already flipped by the user's own
      // `change` event. Leaving it ticked asserts the auto-straddle is armed when it
      // is not; the 3s poll would eventually correct it, but "eventually" is not good
      // enough for a control that places orders. Re-sync from the server now.
      toast("Strategy update failed: " + e.message, "err");
      try { renderStatus(await jget("/api/strategy/status")); }
      catch (_) { fields.enabled.checked = false; }   // can't ask? assume NOT enabled
    }
  }

  // ---- wire up ----
  function openModal() {
    open = true; userTouched = false;
    modal.hidden = false;
    refreshSafety();
    startPolling(true);
  }
  function closeModal() {
    open = false;
    modal.hidden = true;
    startPolling(false); // keep a slow poll so the top-bar dot stays live
  }

  $("strategyBtn").addEventListener("click", openModal);
  $("strategyClose").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  $("stratApply").addEventListener("click", () => apply());
  fields.enabled.addEventListener("change", () => apply(fields.enabled.checked));
  Object.values(fields).forEach((el) => {
    if (el && el !== fields.enabled) el.addEventListener("input", () => { userTouched = true; });
  });
  $("stratFlatten").addEventListener("click", async () => {
    // Disabling triggers no auto-close, so flatten explicitly by disabling AND
    // asking the backend kill path via a zero daily-loss toggle is overkill —
    // simplest: disable, then the user can also hit the terminal's close-all.
    fields.enabled.checked = false;
    await apply(false);
    toast("Strategy disabled. Use terminal Close-All to flatten open legs.", "");
  });

  // keep the top-bar dot alive from page load
  startPolling(false);
})();
