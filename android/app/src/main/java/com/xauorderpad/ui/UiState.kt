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
