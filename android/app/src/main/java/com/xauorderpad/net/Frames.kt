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
)

/** GET /api/config -- unauthenticated probe. Says THAT a token is needed, never what. */
@Serializable
data class ServerConfig(
    @SerialName("auth_required") val authRequired: Boolean = false,
    @SerialName("poll_hz") val pollHz: Int = 15,
)

@Serializable
data class AccountsResponse(val accounts: List<Profile> = emptyList())

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
    @SerialName("is_demo") val isDemo: Boolean? = null,
    /** Positions left open on the PREVIOUS account when switching. Must be surfaced. */
    @SerialName("prev_open") val prevOpen: Int? = null,
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
