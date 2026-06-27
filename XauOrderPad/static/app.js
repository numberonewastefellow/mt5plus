"use strict";

// ---- element refs ----------------------------------------------------------
const el = (id) => document.getElementById(id);
const ui = {
  symbol: el("symbol"), healthDot: el("healthDot"), healthText: el("healthText"),
  bid: el("bid"), ask: el("ask"), spread: el("spread"),
  volume: el("volume"), sl: el("sl"), tp: el("tp"), mode: el("mode"),
  btnBuy: el("btnBuy"), btnSell: el("btnSell"), btnClose: el("btnClose"),
  posCount: el("posCount"), netLots: el("netLots"), floatPl: el("floatPl"),
  equity: el("equity"), posline: document.querySelector(".posline"),
  log: el("log"),
};

let healthy = false;
let digits = 2;
let busy = false; // prevents double-fire while a request is in flight

// ---- helpers ---------------------------------------------------------------
function fmt(v, d = digits) {
  return (v === undefined || v === null) ? "--" : Number(v).toFixed(d);
}

function now() {
  const t = new Date();
  return t.toLocaleTimeString([], { hour12: false }) +
    "." + String(t.getMilliseconds()).padStart(3, "0");
}

function log(msg, kind = "") {
  const li = document.createElement("li");
  if (kind) li.className = kind;
  li.innerHTML = `<span class="t">${now()}</span>${msg}`;
  ui.log.prepend(li);
  while (ui.log.children.length > 100) ui.log.lastChild.remove();
}

function setFireEnabled(on) {
  ui.btnBuy.disabled = !on;
  ui.btnSell.disabled = !on;
  ui.btnClose.disabled = !on; // close also needs a connection
}

function flash(btn) {
  btn.classList.add("flash");
  setTimeout(() => btn.classList.remove("flash"), 120);
}

// ---- live state via WebSocket ---------------------------------------------
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onmessage = (ev) => {
    const s = JSON.parse(ev.data);
    healthy = !!s.healthy;
    if (typeof s.digits === "number") digits = s.digits;

    ui.symbol.textContent = s.symbol || "XAUUSD";
    ui.bid.textContent = fmt(s.bid);
    ui.ask.textContent = fmt(s.ask);
    ui.spread.textContent = (s.bid != null && s.ask != null)
      ? Math.round((s.ask - s.bid) / (s.point || Math.pow(10, -digits))) + "p" : "--";

    ui.healthDot.className = "dot " + (s.healthy ? "ok" : (s.connected ? "" : "bad"));
    ui.healthText.textContent = s.healthy ? "ready"
      : (s.error || (s.connected ? "trading not allowed" : "terminal offline"));

    const pos = s.positions || [];
    ui.posCount.textContent = pos.length;
    ui.netLots.textContent = (s.net_lots ?? 0);
    ui.floatPl.textContent = (s.floating_pl ?? 0);
    ui.posline.classList.toggle("pl-pos", (s.floating_pl ?? 0) > 0);
    ui.posline.classList.toggle("pl-neg", (s.floating_pl ?? 0) < 0);
    ui.equity.textContent = s.account ? `${s.account.equity} ${s.account.currency}` : "--";

    setFireEnabled(s.healthy && !busy);
  };

  ws.onclose = () => {
    ui.healthDot.className = "dot bad";
    ui.healthText.textContent = "disconnected from server";
    setFireEnabled(false);
    setTimeout(connectWs, 1000); // auto-reconnect
  };
  ws.onerror = () => ws.close();
}

// ---- order actions ---------------------------------------------------------
function payload() {
  return {
    volume: parseFloat(ui.volume.value) || null,
    sl: parseFloat(ui.sl.value) || 0,
    tp: parseFloat(ui.tp.value) || 0,
    sl_tp_mode: ui.mode.value,
  };
}

async function post(path, body) {
  if (busy) return;            // guard against double submit
  if (!healthy && path !== "/close_all") {
    log("blocked: not ready", "err");
    return;
  }
  busy = true;
  setFireEnabled(false);
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const res = await r.json();
    renderResult(path, res);
  } catch (e) {
    log(`${path} error: ${e}`, "err");
  } finally {
    busy = false;
    setFireEnabled(healthy);
  }
}

function renderResult(path, res) {
  if (path === "/close_all") {
    const kind = res.ok ? "ok" : "err";
    log(`CLOSE ALL — closed ${res.closed ?? 0}, remaining ${res.remaining ?? "?"}`, kind);
    return;
  }
  if (res.ok) {
    log(`${res.side} ${res.volume} #${res.ticket} @ ${fmt(res.price)} ` +
        `${res.sl ? "SL " + fmt(res.sl) + " " : ""}${res.tp ? "TP " + fmt(res.tp) : ""}`, "ok");
  } else {
    log(`${path.replace("/", "").toUpperCase()} FAILED — ` +
        `${res.error || ("retcode " + res.retcode + " " + (res.comment || ""))}`, "err");
  }
}

function buy() { flash(ui.btnBuy); post("/buy", payload()); }
function sell() { flash(ui.btnSell); post("/sell", payload()); }
function closeAll() { flash(ui.btnClose); post("/close_all", {}); }

// ---- wiring ----------------------------------------------------------------
ui.btnBuy.addEventListener("click", buy);
ui.btnSell.addEventListener("click", sell);
ui.btnClose.addEventListener("click", closeAll);

// In-page key bindings. Ignore when typing in an input/select.
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "select" || tag === "textarea";
  if (typing && e.key !== "Escape") return;

  switch (e.key) {
    case "Enter":
    case " ":           // Space
      e.preventDefault(); buy(); break;
    case "Backspace":
      e.preventDefault(); sell(); break;
    case "Escape":
      e.preventDefault(); closeAll(); break;
  }
});

document.querySelector(".pad").focus();
connectWs();
