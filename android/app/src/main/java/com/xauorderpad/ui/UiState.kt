package com.xauorderpad.ui

import androidx.compose.runtime.Immutable
import kotlinx.collections.immutable.ImmutableList

/**
 * Narrow, deduplicated views of the server snapshot.
 *
 * ── Why these exist (C2) ──
 *
 * The wire model `Snapshot` is one big object and the client builds a NEW instance on every
 * frame (5/sec). If the UI takes `Snapshot` directly, then under Compose's strong skipping
 * every composable that reads it sees a "changed" parameter every single frame -- so a bid
 * tick recomposes the entire screen, including the LOT / SL / TP text fields and the whole
 * positions list. That is pure waste: the lot field has nothing to do with the bid.
 *
 * Splitting the snapshot into small slices, each `distinctUntilChanged()`, means a price tick
 * invalidates ONLY the quote cells. The order form and the buttons recompose zero times.
 *
 * Each slice is @Immutable and uses only stable types (ImmutableList, not List), so Compose
 * can compare them by value and skip properly.
 */

@Immutable
data class Quote(
    val bid: Double? = null,
    val ask: Double? = null,
    /** Already converted to POINTS. The raw `spread` field is a price difference. */
    val spreadPoints: Double? = null,
    val digits: Int? = null,
)

@Immutable
data class Health(
    val healthy: Boolean = false,
    val connected: Boolean = false,
    val loggedOut: Boolean = false,
    /** null = unknown. `false` means a REAL account -- must be shown loudly. */
    val isDemo: Boolean? = null,
    /**
     * The MT5 account NUMBER. Without it, "which account am I on?" is unanswerable: the server
     * name alone does not identify an account, and a demo and a real account on the SAME broker
     * server is the normal setup. The Accounts screen matches on login AND server; matching on
     * server alone lit up BOTH rows as active.
     */
    val login: Long? = null,
    val server: String? = null,
    /** The server's own specific reason: "market closed", "AutoTrading is OFF", etc. */
    val error: String? = null,
)

@Immutable
data class AccountUi(
    val equity: Double? = null,
    val floatingPl: Double? = null,
    val openCount: Int = 0,
    val currency: String? = null,
)

/** Broker constraints for the order form. Changes essentially never -- hence its own slice. */
@Immutable
data class Limits(
    val volumeMin: Double = 0.01,
    val volumeStep: Double = 0.01,
    val digits: Int = 2,
)

@Immutable
data class PositionsUi(
    val items: ImmutableList<com.xauorderpad.net.Position>,
    val digits: Int = 2,
)

/**
 * The server-side strategy engines. Arrives on the /ws snapshot, so it costs nothing
 * extra -- the strategies RUN on the server; this is only a view of them.
 *
 * Its own slice for the usual reason (see the header): the strategy panel must not
 * recompose on every bid tick.
 */
@Immutable
data class StrategiesUi(
    val items: ImmutableList<com.xauorderpad.net.StrategyStatus>,
)

/**
 * The ARMED SIDE, and what CLOSE would actually close.
 *
 * ── Why the target ticket is computed here and not in the button ──
 *
 * On a HEDGING account (this one: margin_mode=2) SELL does not close a BUY -- it opens a new
 * short. So "exit" cannot be "press the other side"; it has to name a ticket. This slice picks
 * that ticket: the NEWEST position on the armed side (LIFO), which is the natural way to unwind
 * a pyramid you just scaled into.
 *
 * [targetEntry] is rendered ON the CLOSE button, so you can see WHICH position is about to go
 * rather than trusting that "close one" meant the one you had in mind.
 *
 * [count] == 0 means there is nothing to close on this side, and the button is disabled -- it
 * must never fall through to closing a position on the OTHER side.
 */
@Immutable
data class ArmedUi(
    /** "buy" | "sell" */
    val side: String = "buy",
    val targetTicket: Long? = null,
    val targetEntry: Double? = null,
    /** Open positions on the armed side. */
    val count: Int = 0,
    val digits: Int = 2,
) {
    val isBuy: Boolean get() = side != "sell"
}
