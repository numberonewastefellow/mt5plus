package com.xauorderpad.net

import androidx.compose.runtime.Immutable
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Wire models for the XauOrderPad server.
 *
 * ── EVERY FIELD IS NULLABLE. THIS IS REQUIRED, NOT DEFENSIVE STYLE. ──
 *
 * The server does not send a fixed schema. `Mt5Worker._poll_state` builds the dict
 * incrementally and returns EARLY when the terminal is not connected, so a degraded frame
 * contains only:
 *
 *     {"ts":…, "symbol":"XAUUSD", "connected":false, "healthy":false,
 *      "logged_out":true, "error":"logged out", "strategy":{…}}
 *
 * `account`, `positions`, `bid`, `ask`, `digits`, `point`, `volume_min` … are ABSENT --
 * not null, absent. (Verified against the running server, not inferred.) A non-null field
 * would throw MissingFieldException the first time the EC2 box restarts, someone logs out
 * on the desktop, or the broker connection blips -- i.e. constantly.
 *
 * `ignoreUnknownKeys` covers the other direction: a field added to the server later must
 * not crash a phone that cannot be updated on the spot.
 */
val json = Json {
    ignoreUnknownKeys = true
    isLenient = true
    coerceInputValues = true
}

/**
 * @Immutable is a PROMISE to the Compose compiler: these fields never change after
 * construction (they don't -- the client always builds a fresh Snapshot per frame). Without
 * it, `List<Position>` makes Snapshot *unstable*, and under Kotlin 2.x strong-skipping an
 * unstable param is compared by IDENTITY. A new instance arrives every frame, so every
 * composable reading it would recompose 5x/sec -- including the LOT/SL/TP text fields
 * *while the user is typing in them*.
 */
@Immutable
@Serializable
data class Snapshot(
    val ts: Double? = null,
    val symbol: String? = null,

    val connected: Boolean? = null,
    val healthy: Boolean? = null,
    @SerialName("trade_allowed") val tradeAllowed: Boolean? = null,
    @SerialName("symbol_ok") val symbolOk: Boolean? = null,
    // Present ONLY when true. Its absence does not mean "logged in".
    @SerialName("logged_out") val loggedOut: Boolean? = null,
    val error: String? = null,

    val digits: Int? = null,
    val point: Double? = null,
    @SerialName("volume_min") val volumeMin: Double? = null,
    @SerialName("volume_step") val volumeStep: Double? = null,

    val bid: Double? = null,
    val ask: Double? = null,
    /**
     * CAUTION: a PRICE DIFFERENCE (ask - bid), not points. Divide by [point] to show it the
     * way a trader reads it -- rendering it raw prints "0.22" where the user expects "22".
     * Use [spreadPoints].
     */
    val spread: Double? = null,
    @SerialName("tick_time") val tickTime: Long? = null,

    val account: Account? = null,
    val positions: List<Position>? = null,
    val orders: List<PendingOrder>? = null,

    @SerialName("net_lots") val netLots: Double? = null,
    /** Server-side sum of positions[].profit. Broker-computed. Never recompute. */
    @SerialName("floating_pl") val floatingPl: Double? = null,

    /**
     * Status of every server-side strategy engine, keyed by id.
     *
     * It rides the /ws snapshot rather than a REST poll, because the worker already
     * puts it there -- polling would be duplicate machinery for data the socket is
     * carrying anyway. The strategies RUN on the server; the phone only watches and
     * toggles them.
     */
    val strategies: Map<String, StrategyStatus>? = null,

    /** The account P&L guard the SERVER is enforcing (auto-close-all at a target). See [Guard]. */
    val guard: Guard? = null,
) {
    /** Spread in points -- the unit the UI shows. Null when either half is missing. */
    val spreadPoints: Double?
        get() {
            val s = spread ?: return null
            val p = point ?: return null
            return if (p <= 0.0) null else s / p
        }

    val openPositions: List<Position> get() = positions.orEmpty()

    /** True only when the server explicitly said so. Absence != logged in. */
    val isLoggedOut: Boolean get() = loggedOut == true
}

/**
 * One server-side strategy engine, as the server reports it.
 *
 * Only the fields the phone RENDERS. The phone is a remote control, not a config
 * editor: full tuning lives in the web panel, and every guard lives on the server.
 *
 * `error` and `warning` are computed server-side from real measurements (a target
 * inside the spread cannot win; extra ladder positions multiply cost, not edge), so
 * they must be shown, never swallowed. `enabled` is the SERVER's answer -- a POST can
 * return 200 and `enabled:false` because the engine refused. Trust this, not the
 * request that was sent.
 */
@Immutable
@Serializable
data class StrategyStatus(
    val id: String = "",
    val name: String = "",
    val enabled: Boolean = false,
    val state: String = "",
    val error: String? = null,
    val killed: Boolean = false,
    // ladder-only; null on engines that do not have them
    val paper: Boolean? = null,
    val spread: Double? = null,
    val warning: String? = null,
    /**
     * The ladder's spread GUARD, decided SERVER-SIDE (`TrendLadder._guard_check`) and re-checked
     * every poll. [guardOk] is false when the target or the trail sits inside the live spread --
     * a trade that cannot win, which the engine refuses to arm. [guardReason] is the exact
     * sentence the server would raise, so the client can grey out ARM with it BEFORE posting
     * rather than posting and reading the refusal back as an error. [minStop] is the floor both
     * the target and the trail must clear (the live spread). Null on non-ladder engines.
     */
    @SerialName("guard_ok") val guardOk: Boolean? = null,
    @SerialName("guard_reason") val guardReason: String? = null,
    @SerialName("min_stop") val minStop: Double? = null,
    /**
     * Why NEW entries are blocked, when they are: `"params_changed"` (a mid-ladder edit -- the open
     * book finishes on its old rules, then the queued level goes live) or `"adopted"` (positions
     * rebuilt from the broker after a restart). Null when not managing. Lets the panel say "new
     * level queued behind N open trades" instead of leaving the operator guessing why ARM did
     * nothing.
     */
    @SerialName("managing_reason") val managingReason: String? = null,
    val params: StrategyParams? = null,
    /** Rungs currently open, and what they add up to in lots. */
    @SerialName("open_positions") val openPositions: Int? = null,
    @SerialName("open_lots") val openLots: Double? = null,
    @SerialName("ladders_done") val laddersDone: Int? = null,
    @SerialName("ladders_today") val laddersToday: Int? = null,
    /**
     * What the dialled stop ACTUALLY costs: the stop is measured on the entry-side price
     * but realised on the exit side, so the operator gives up `stop + spread`. Derived
     * SERVER-SIDE (see `TrendLadder._extra_status`) precisely so this screen and the web
     * panel cannot quote different numbers for the same engine.
     */
    @SerialName("effective_stop") val effectiveStop: Double? = null,
    /** Where a floor-mode stop sits, in price. Null in retrace mode. */
    @SerialName("floor_price") val floorPrice: Double? = null,
    /**
     * Positions still queued for closing. A flush is drained in batches so a large book
     * cannot freeze the worker's poll loop, so this counts DOWN over several polls -- a
     * non-zero value means "closing in progress", not "stuck".
     */
    @SerialName("flush_remaining") val flushRemaining: Int? = null,
    /**
     * Manual-reload alert. The ladder rides ONE leg then PARKS -- it never re-enters itself
     * (that would be unattended new risk). When it flushes the server latches this, and
     * [attentionReason] says why, so the app can chime + prompt the operator to SET A NEW LEVEL
     * rather than let a trend walk away un-traded. Cleared when they re-level, toggle, or it
     * re-arms. Derived SERVER-SIDE so both clients agree; gated on `enabled` server-side.
     */
    @SerialName("needs_attention") val needsAttention: Boolean? = null,
    @SerialName("attention_reason") val attentionReason: String? = null,
    /**
     * True when the CURRENT trigger is already on the crossed side (a SELL level above the bid,
     * a BUY below the ask) so a fresh ladder would arm INSTANTLY rather than wait for the move.
     * Server-derived from the live quote; warn on it before the operator commits a new level.
     */
    @SerialName("would_fire_now") val wouldFireNow: Boolean? = null,
    /**
     * Whether the retrace trail is currently ACTIVE. With `trail_activate > 0` the trail is inert
     * until the run is up that much (only the hard_sl protects); false here + a positive
     * `trail_activate` means "waiting to arm". Null on non-ladder engines / no open run.
     */
    @SerialName("trail_armed") val trailArmed: Boolean? = null,

    // ---- straddle-ladder (sladder) only; null on every other engine ---------
    /**
     * Which phase the walking straddle grid is in, decided SERVER-SIDE (`StraddleGridState.phase`):
     * `"armed"` (waiting for price to reach the stepped level) -> `"straddle"` (a straddle is open,
     * resolving) -> back to `"armed"` at the stepped level -> ... -> `"parked"` (max_legs hit).
     * Render it; do not recompute it.
     */
    val phase: String? = null,
    /** The LAST resolved direction (for display): `"buy"` | `"sell"`. Null before the first resolve. */
    val direction: String? = null,
    /** Price the NEXT straddle arms at (the grid's current level), or the level while still armed. */
    @SerialName("next_entry") val nextEntry: Double? = null,
    /** Straddles placed this run -- counted against `max_legs` (a "leg" here is one straddle). */
    @SerialName("legs_taken") val legsTaken: Int? = null,
    /** Legs currently open (0 between straddles, 2 while a straddle resolves, 1 while a winner rides). */
    @SerialName("open_legs") val openLegs: Int? = null,

    // ---- rider-only; null on every other engine ----------------------------
    /** The current SUGGESTION. Only `kind == "enter"` is actionable. See [RiderCard]. */
    val card: RiderCard? = null,
    /** True while the rider only SUGGESTS. False once an auto-trade switch covers this account. */
    @SerialName("suggestion_only") val suggestionOnly: Boolean? = null,
    /**
     * What the engine will do with its next signal, decided SERVER-SIDE:
     * `"off"` | `"suggest"` | `"auto-demo"` | `"AUTO-REAL"`. Render it; do not recompute it
     * from the two switches, or this screen and the web panel can describe the same engine
     * differently.
     */
    val execution: String? = null,
    /**
     * May the operator usefully tap PLACE right now? Server-derived (rider `_actionable`):
     * the card was issued on the newest closed bar, the engine is armed, and it is not
     * already placing by itself.
     *
     * Do NOT substitute `card.kind == "enter"`. The card keeps that kind for the whole
     * trade (up to ~2 h), so it would offer a long-expired entry price.
     */
    val actionable: Boolean? = null,
    /** Ticket of the REAL position the rider is currently riding, if any. */
    @SerialName("live_ticket") val liveTicket: Long? = null,
    /** Where the engine-side trailing stop currently sits, in price. */
    @SerialName("live_stop") val liveStop: Double? = null,
    /**
     * CAUTION: the LADDER sends `paper_pl_per_oz` (pl) and the RIDER sends `paper_pnl_per_oz`
     * (pnl). Different fields, different engines -- merging them would silently show one
     * engine's record on the other's page.
     */
    @SerialName("paper_pnl_per_oz") val paperPnlPerOz: Double? = null,
    /** Accrued at the lot each trade was SIZED at -- a real running total, not a rescale. */
    @SerialName("paper_pnl_usd") val paperPnlUsd: Double? = null,
    @SerialName("paper_trades") val paperTrades: Int? = null,
    @SerialName("in_paper_position") val inPaperPosition: Boolean? = null,
    val note: String? = null,
)

/**
 * One rider SUGGESTION.
 *
 * Named RiderCard, not Card: `androidx.compose.material3.Card` is imported on every screen
 * that would render this, and a clashing name there is a compile error at best and the wrong
 * symbol at worst.
 *
 * ── The unit trap ──
 * [entry], [sl] and [tp] are ABSOLUTE PRICES. The /order endpoint takes POINT DISTANCES.
 * Anything that turns this card into an order MUST convert (see TradingViewModel.placeRiderCard);
 * posting the price straight through is ACCEPTED by the broker, not rejected -- it just places a
 * stop miles away. The web UI shipped that bug once already.
 */
@Immutable
@Serializable
data class RiderCard(
    /** "enter" | "close" | "flat" | "hold". Only "enter" carries a tradable side/lot/prices. */
    val kind: String? = null,
    val side: String? = null,
    val lot: Double? = null,
    val entry: Double? = null,
    val sl: Double? = null,
    val tp: Double? = null,
    val reason: String? = null,
    @SerialName("bar_ts") val barTs: Long? = null,
    @SerialName("pnl_oz") val pnlOz: Double? = null,
    val status: String? = null,
    val note: String? = null,
) {
    /** Fail closed: no side or no lot means there is nothing a human could be asked to confirm. */
    val isActionable: Boolean get() = kind == "enter" && side != null && lot != null
    val isBuy: Boolean get() = side.equals("buy", ignoreCase = true)
}

@Immutable
@Serializable
data class StrategyParams(
    val side: String? = null,
    val trigger: Double? = null,
    val target: Double? = null,
    /** "retrace" (trail the extreme) | "floor" (fixed level at the trigger). */
    @SerialName("stop_mode") val stopMode: String? = null,
    val retrace: Double? = null,
    @SerialName("floor_offset") val floorOffset: Double? = null,
    /**
     * How far in PROFIT the run must get before the retrace trail arms. 0 = trail from entry
     * (can close below entry on a dip). >= retrace => arms at breakeven, so the trail never
     * books a loss (the hard_sl covers the downside until then). retrace mode only.
     */
    @SerialName("trail_activate") val trailActivate: Double? = null,
    /**
     * Ladder only: the BROKER-side stop, in $/oz, attached to every rung at fill time
     * (`strategy_place(..., sl_dist=hard_sl, ...)`). It is a backstop for the process
     * DYING, not the working stop -- [retrace] is what actually exits the ladder, and it
     * is an order of magnitude tighter. The webapp has exposed this since day one
     * (`ldHardSl`); Android could not read it at all, which is why a 3.00 default sat
     * unnoticed behind a 0.30 trail.
     */
    @SerialName("hard_sl") val hardSl: Double? = null,
    val volume: Double? = null,
    /** 0 means UNCAPPED for both of these -- they are ceilings, not counts. */
    @SerialName("max_positions") val maxPositions: Int? = null,
    @SerialName("max_lots") val maxLots: Double? = null,
    val paper: Boolean? = null,
    /**
     * Ladder re-arm policy. false (default) = ONE-SHOT: after a run's stop, park and wait for
     * the operator to SET LEVEL again. true = AUTO-CONTINUE: keep taking runs while price stays
     * past the level, stop only when it returns. Opt-in (in a chop it churns). See the engine's
     * `_rearm_gate`.
     */
    @SerialName("auto_continue") val autoContinue: Boolean? = null,
    /**
     * Rider execution switches, one per account class. Read here (not off the top-level
     * status) because the SWITCH must show what was actually SAVED, so a rejected or
     * ignored write is visible rather than reflected back optimistically.
     */
    @SerialName("auto_demo") val autoDemo: Boolean? = null,
    @SerialName("auto_real") val autoReal: Boolean? = null,
    @SerialName("max_daily_loss") val maxDailyLoss: Double? = null,
    /**
     * Rider only: how [sl] and [trail] are READ — `"fixed"` = $/oz, `"atr"` = multiples of
     * ATR(14). NOT the ladder's `stop_mode` (retrace|floor); they are deliberately different
     * wire fields because one name meaning two things is how a 6 becomes $6 or $27.
     */
    @SerialName("stop_units") val stopUnits: String? = null,
    val sl: Double? = null,
    val trail: Double? = null,
    // ---- straddle-ladder (sladder) only ----
    /** The arm price the straddle is placed at. `sl`/`volume`/`maxLots`/`maxDailyLoss` above are reused. */
    val level: Double? = null,
    /** $/oz take-profit per leg (sladder). NOT the ladder's `target`; a separate wire field. */
    val tp: Double? = null,
    /** $/oz past each take-profit before the next continuation entry arms. */
    val gap: Double? = null,
    /** Continuation entries per run before the engine parks. 0 = uncapped. The primary safety brake. */
    @SerialName("max_legs") val maxLegs: Int? = null,
    @SerialName("cooldown_s") val cooldownS: Double? = null,
    /**
     * Continuation mode. false (DEFAULT) = single leg on the trend side after the first straddle
     * (rides the trend). true = every entry is a straddle (walking grid; re-detects direction each
     * step, nets ~0). Server-owned; the panel toggle just reflects and sets it.
     */
    @SerialName("always_straddle") val alwaysStraddle: Boolean? = null,
)

/**
 * The account-level P&L guard, as the SERVER reports it (it rides the /ws snapshot). The worker
 * enforces it — auto-closes the whole book when FLOATING P&L reaches [targetPl] — so this is the
 * authoritative state; the phone reflects it rather than tracking its own. `fired` latches for one
 * breach and clears once the book goes flat.
 */
@Immutable
@Serializable
data class Guard(
    val enabled: Boolean = false,
    @SerialName("target_pl") val targetPl: Double = 0.0,
    /** "profit" (close at >= +target) | "loss" (close at <= -target). */
    val side: String = "profit",
    val fired: Boolean = false,
)

/** The /api/guard POST response: `{"ok":true,"guard":{…}}`. */
@Serializable
data class GuardResp(
    val ok: Boolean = false,
    val guard: Guard? = null,
)

@Immutable
@Serializable
data class Account(
    val balance: Double? = null,
    val equity: Double? = null,
    val currency: String? = null,
    val login: Long? = null,
    val server: String? = null,
    /** MT5 enum: 0=demo 1=contest 2=real */
    @SerialName("trade_mode") val tradeMode: Int? = null,
    @SerialName("is_demo") val isDemo: Boolean? = null,
    /** 0=netting 2=hedging */
    @SerialName("margin_mode") val marginMode: Int? = null,
    @SerialName("daily_realized") val dailyRealized: Double? = null,
    val wins: Int? = null,
    val losses: Int? = null,
)
// NOTE: margin / margin_free / margin_level are NOT exposed by the server (_poll_state
// reads account_info() but copies only the fields above). Adding them here yields nulls.

@Immutable
@Serializable
data class Position(
    val ticket: Long,                       // the one field the server always sends
    val side: String? = null,               // "BUY" | "SELL" (uppercase)
    val volume: Double? = null,
    @SerialName("price_open") val priceOpen: Double? = null,
    /**
     * ABSOLUTE PRICE here (0.0 = none) -- the OPPOSITE of the order REQUEST, where sl/tp
     * are point distances. Same field names, different units, in the same app.
     */
    val sl: Double? = null,
    val tp: Double? = null,
    /** Broker-computed floating P&L in account currency. NEVER recompute this. */
    val profit: Double? = null,
    val time: Long? = null,
    /**
     * Long, NOT Int. The MT5 magic is a **uint32**, and `mt5_worker.py` sends it raw. Any
     * EA or copier using a magic above 2^31 (timestamps and hashes routinely are) would
     * overflow an Int and make the WHOLE FRAME fail to parse. TradingClient swallows a bad
     * frame and keeps the last good snapshot on screen -- so the symptom is not a crash,
     * it is a frozen book presented as live. That is the worst possible failure here.
     */
    val magic: Long? = null,
    /** Present on the account-wide history view (many symbols); absent/null on the live poll. */
    val symbol: String? = null,
    /**
     * Who asked for this position: "R" rider-suggested, "L" ladder, "S" straddle, "" manual.
     *
     * DERIVED SERVER-SIDE, from magic/comment, so the phone and the web UI can never disagree
     * about what a row came from. Do NOT re-derive it here -- read it.
     */
    val origin: String? = null,
) {
    val isBuy: Boolean get() = side.equals("BUY", ignoreCase = true)
    val hasSl: Boolean get() = (sl ?: 0.0) > 0.0
    val hasTp: Boolean get() = (tp ?: 0.0) > 0.0
}

@Immutable
@Serializable
data class PendingOrder(
    val ticket: Long,
    val side: String? = null,
    val volume: Double? = null,
    @SerialName("price_open") val priceOpen: Double? = null,
    val sl: Double? = null,
    val tp: Double? = null,
    val type: String? = null,
    val time: Long? = null,
    val symbol: String? = null,
) {
    val isBuy: Boolean get() = side.equals("BUY", ignoreCase = true)
}

/** GET /api/config -- unauthenticated probe. Says THAT a token is needed, never what. */
@Serializable
data class ServerConfig(
    @SerialName("auth_required") val authRequired: Boolean = false,
    @SerialName("poll_hz") val pollHz: Int = 15,
)

@Serializable
data class AccountsResponse(val accounts: List<Profile> = emptyList())

/**
 * GET /api/history -- today's trading activity for the logged-in account (broker-side, on demand).
 * All fields nullable-with-default: a partial/failed body must still parse (see the parser config
 * at the top of this file) rather than throw and blank the screen.
 */
@Immutable
@Serializable
data class HistoryStats(
    @SerialName("closed_count") val closedCount: Int = 0,
    val wins: Int = 0,
    val losses: Int = 0,
    val flat: Int = 0,
    @SerialName("gross_profit") val grossProfit: Double = 0.0,
    @SerialName("gross_loss") val grossLoss: Double = 0.0,
    val net: Double = 0.0,
    @SerialName("biggest_win") val biggestWin: Double = 0.0,
    @SerialName("biggest_loss") val biggestLoss: Double = 0.0,
    @SerialName("avg_win") val avgWin: Double = 0.0,
    @SerialName("avg_loss") val avgLoss: Double = 0.0,
    /** wins / closed_count, 0..1 (matches the broker's "81%"). */
    @SerialName("win_rate") val winRate: Double = 0.0,
)

/** One closed trade: a closing deal paired with its opening deal (entry -> exit). */
@Immutable
@Serializable
data class ClosedTrade(
    @SerialName("position_id") val positionId: Long = 0,
    val ticket: Long = 0,
    val side: String? = null,               // the POSITION's direction, "BUY" | "SELL"
    val symbol: String? = null,
    val volume: Double? = null,
    /** null when the position was opened BEFORE today (no IN leg in range) -> render "-> exit". */
    @SerialName("entry_price") val entryPrice: Double? = null,
    @SerialName("exit_price") val exitPrice: Double? = null,
    @SerialName("entry_time") val entryTime: Long? = null,
    @SerialName("exit_time") val exitTime: Long? = null,
    /** profit + swap + commission of the closing leg, account currency. */
    val pnl: Double = 0.0,
) {
    val isBuy: Boolean get() = side.equals("BUY", ignoreCase = true)
}

@Serializable
data class HistoryResponse(
    val ok: Boolean = false,
    @SerialName("as_of") val asOf: Long = 0,
    @SerialName("day_start") val dayStart: Long = 0,
    val stats: HistoryStats = HistoryStats(),
    val closed: List<ClosedTrade> = emptyList(),
    val open: List<Position> = emptyList(),
    val pending: List<PendingOrder> = emptyList(),
    val error: String? = null,
)

@Immutable
@Serializable
data class Profile(
    val id: String,                         // "<login>@<server>"
    val label: String? = null,
    val login: Long? = null,
    val server: String? = null,
    @SerialName("last_trade_mode") val lastTradeMode: Int? = null,
)

@Serializable
data class LoginResult(
    val ok: Boolean = false,
    val login: Long? = null,
    val server: String? = null,
    /**
     * 0 = demo, 1 = contest, 2 = REAL. The server derives `is_demo` from this, but we keep the
     * raw value: "not demo" and "real money" want to be distinguishable in a log after the fact.
     */
    @SerialName("trade_mode") val tradeMode: Int? = null,
    @SerialName("is_demo") val isDemo: Boolean? = null,
    /** Positions left open on the PREVIOUS account when switching. Must be surfaced. */
    @SerialName("prev_open") val prevOpen: Int? = null,
    /**
     * Set ONLY when the client asked to save (ad-hoc login with save=true); null otherwise.
     * `false` means the login worked but the password was NOT stored on the server -- so this
     * account cannot be auto-restored, and the user must be told plainly rather than shown a
     * bare success. `saveError` carries the reason. See server.py's login handler.
     */
    val saved: Boolean? = null,
    @SerialName("save_error") val saveError: String? = null,
)

/**
 * POST /api/logout.
 *
 * MT5 has no true "log out" -- the terminal stays logged in; the server simply refuses to drive
 * it (mt5_worker `_logout`). So `prev_open` is not cosmetic: those positions are STILL OPEN on
 * the account, now with nothing watching them. The UI must say so.
 */
@Serializable
data class LogoutResult(
    val ok: Boolean = false,
    @SerialName("prev_open") val prevOpen: Int? = null,
)

/**
 * DELETE /api/accounts/{id}. `deleted=false` means the id was not there -- the server still
 * answers 200, so the status code alone would read as success.
 *
 * `active=true` means we just forgot the password for the account the terminal is CURRENTLY
 * logged into. It keeps trading -- deleting a profile does not log you out -- but the session
 * can no longer be restored if a later login fails. The UI must say so.
 */
@Serializable
data class DeleteResult(
    val deleted: Boolean = false,
    val active: Boolean = false,
)

@Serializable
data class OrderResult(
    val ticket: Long? = null,
    val price: Double? = null,
    val state: String? = null,              // "open" | "pending"
)

/**
 * POST /close_where and POST /close_all.
 *
 * `ok` and `error` are load-bearing. The server returns **HTTP 200** even when the close
 * FAILED: `_close_where` gives up after 5 retry passes and returns
 * {ok:false, closed:N, remaining:M}, and a disconnected terminal returns
 * {ok:false, error:"terminal not connected"} with no counts at all. Both are 200, because
 * both are well-formed answers rather than protocol errors.
 *
 * Treating HTTP 200 as success here means an emergency CLOSE ALL that shut NOTHING reports
 * "Closed 0 position(s)" as a green success -- from the lock screen, on a losing book. See Api.closeWhere.
 */
@Serializable
data class CloseResult(
    val ok: Boolean = false,
    val filter: String? = null,
    val closed: Int = 0,
    /** Positions still matching the FILTER -- not the whole book. After filter="losing", a
     *  surviving winner is not a failure and is not counted here. */
    val remaining: Int = 0,
    val error: String? = null,
)
