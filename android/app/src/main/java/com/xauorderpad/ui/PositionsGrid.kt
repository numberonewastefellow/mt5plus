package com.xauorderpad.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.net.Position

val Green = Color(0xFF1FA97A)
val Red = Color(0xFFE0544E)
val Amber = Color(0xFFD9A441)

/**
 * What kind of money is behind an account.
 *
 * ── Why this is not a Boolean ──
 * It used to be `val isReal = p.lastTradeMode == 2`, rendered as `if (isReal) "REAL" else "DEMO"`.
 * That makes **null** -- "we have never actually seen this account" -- render as a green DEMO
 * badge, and skip the "switch to a REAL account?" confirmation entirely.
 *
 * `last_trade_mode` is null for any profile that has not yet been logged into *through this
 * server* (accounts.py defaults it to None): a real account added from the web UI's modal, or any
 * profile from a profiles.json written before the field existed. So the most dangerous account in
 * the list was the one shown in green.
 *
 * On the one screen whose job is to tell you which account you are about to trade, UNKNOWN must
 * fail toward "dangerous", never toward "safe". Hence three states, and [confirmBeforeSwitch].
 *
 * MT5's ACCOUNT_TRADE_MODE: 0 = DEMO, 1 = CONTEST, 2 = REAL. Contest is play money, so it groups
 * with DEMO -- but note that only 2 is REAL, and anything we do not recognise is UNKNOWN, not DEMO.
 */
enum class AcctMode { DEMO, REAL, UNKNOWN }

fun acctMode(lastTradeMode: Int?): AcctMode = when (lastTradeMode) {
    2 -> AcctMode.REAL
    0, 1 -> AcctMode.DEMO       // demo / contest -- neither risks real money
    else -> AcctMode.UNKNOWN    // null, or a value MetaQuotes added after this was written
}

/** REAL *and* UNKNOWN both get the are-you-sure dialog. Only a PROVEN demo skips it. */
val AcctMode.confirmBeforeSwitch: Boolean get() = this != AcctMode.DEMO

val AcctMode.badge: String get() = when (this) {
    AcctMode.REAL -> "REAL"
    AcctMode.DEMO -> "DEMO"
    AcctMode.UNKNOWN -> "UNVERIFIED"
}

val AcctMode.color: Color get() = when (this) {
    AcctMode.REAL -> Red
    AcctMode.DEMO -> Green
    AcctMode.UNKNOWN -> Amber
}

/**
 * WHERE a position came from: "R" rider-suggested, "L" ladder, "S" straddle, "" manual.
 *
 * The letter is computed on the SERVER (Position.origin) so this phone and the web panel can
 * never label the same ticket differently. Nothing is rendered for a manual position: the
 * common case must stay quiet, or the badge stops meaning anything.
 *
 * Deliberately tinted with the theme's PRIMARY, never Green/Red/Amber. Those three already
 * carry meaning in this file -- side, P&L, and "no stop attached" -- and a fourth use of colour
 * would make a grid that is read at a glance ambiguous.
 *
 * Same visual idiom as the account badge above (9sp bold, tinted ground at .18, 3dp corners).
 */
@Composable
private fun OriginBadge(origin: String?, modifier: Modifier = Modifier) {
    val tag = origin?.trim().orEmpty()
    if (tag.isEmpty()) return
    val tint = MaterialTheme.colorScheme.primary
    Text(
        tag,
        modifier
            .background(tint.copy(alpha = 0.18f), RoundedCornerShape(3.dp))
            .padding(horizontal = 4.dp, vertical = 1.dp),
        fontSize = 9.sp,
        fontWeight = FontWeight.Bold,
        color = tint,
        maxLines = 1,
        softWrap = false,
    )
}

/**
 * The open book.
 *
 * Takes [PositionsUi] (an ImmutableList) rather than the raw Snapshot, so Compose can compare
 * it by value and skip when nothing changed. See UiState.kt.
 */
@Composable
fun PositionsGrid(
    state: PositionsUi,
    onClose: (Long) -> Unit,
    modifier: Modifier = Modifier,
    connected: Boolean = true,
    /** Socket is Up. False => these rows are a frozen last-known frame, not live P&L. */
    live: Boolean = true,
) {
    Column(modifier) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceVariant)
                .padding(horizontal = 8.dp, vertical = 6.dp)
        ) {
            Head("SIDE", 0.85f)
            Head("LOT", 0.7f, TextAlign.End)
            Head("ENTRY", 1.15f, TextAlign.End)
            Head("SL", 1.1f, TextAlign.End)
            Head("TP", 1.1f, TextAlign.End)
            Head("P&L", 1.05f, TextAlign.End)
            Head("", 0.65f)
        }
        HorizontalDivider()

        if (state.items.isEmpty()) {
            Box(Modifier.fillMaxWidth().padding(24.dp), Alignment.Center) {
                Text(
                    // "Flat" and "we have no data" look identical on an empty table but mean
                    // very different things. Never let the user read one as the other.
                    if (connected) "No open positions" else "No data — not connected",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 13.sp,
                )
            }
            return@Column
        }

        // The book is NOT hidden when the feed dies -- these rows are still the best information
        // available, and blanking the grid during a disconnect would read as "you are flat",
        // which is the most dangerous lie this screen could tell. They just stop claiming to be
        // current: the P&L below is frozen at the last frame received.
        if (!live) {
            Box(
                Modifier
                    .fillMaxWidth()
                    .background(Amber.copy(alpha = 0.18f))
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            ) {
                Text(
                    "NOT LIVE — P&L frozen at last update",
                    color = Amber,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }

        // key = ticket: Compose DIFFS the rows instead of recreating them on every frame --
        // the equivalent of RecyclerView's DiffUtil, in one argument.
        LazyColumn {
            items(state.items, key = { it.ticket }) { p ->
                PositionRow(p, state.digits, onClose)
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            }
        }
    }
}

@Composable
private fun PositionRow(p: Position, digits: Int, onClose: (Long) -> Unit) {
    // `profit` is BROKER-COMPUTED (positions[].profit). Never recalculate it from the tick:
    // the number on screen must be the number the broker will actually settle.
    val pl = p.profit ?: 0.0

    Row(
        Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // SIDE + origin share ONE cell. The 0.85f weight is matched by the header row above;
        // adding a column (or changing this weight) shifts every price column out from under
        // its heading, so the badge goes inside the cell instead.
        Row(Modifier.weight(0.85f), verticalAlignment = Alignment.CenterVertically) {
            Text(
                p.side ?: "?",
                color = if (p.isBuy) Green else Red,
                fontWeight = FontWeight.Bold,
                fontSize = 13.sp,
                maxLines = 1,
                softWrap = false,
            )
            OriginBadge(p.origin, Modifier.padding(start = 4.dp))
        }
        Mono(Fmt.lot(p.volume), 0.7f)
        Mono(Fmt.price(p.priceOpen, digits), 1.15f)

        // SL / TP.
        //
        // These are ABSOLUTE PRICES on a position (0.0 = not set) -- the opposite of the order
        // REQUEST, where sl/tp are point distances. Same names, different units.
        //
        // Showing them is not decoration. This app exists to manage risk, and a naked position
        // -- one with no stop attached at all -- is the single most important thing a trader
        // needs to see at a glance. An amber "NONE" is deliberately louder than a price.
        if (p.hasSl) Mono(Fmt.price(p.sl, digits), 1.1f)
        else Warn("NONE", 1.1f)

        if (p.hasTp) Mono(Fmt.price(p.tp, digits), 1.1f)
        else Mono("—", 1.1f)          // no TP is normal and not a risk; SL missing is not

        Text(
            Fmt.signedMoney(pl),
            Modifier.weight(1.05f),
            color = if (pl < 0) Red else Green,
            textAlign = TextAlign.End,
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = 13.sp,
        )
        Box(Modifier.weight(0.65f), Alignment.CenterEnd) {
            TextButton(onClick = { onClose(p.ticket) }) { Text("✕", color = Red) }
        }
    }
}

@Composable
private fun RowScope.Head(text: String, weight: Float, align: TextAlign = TextAlign.Start) = Text(
    text,
    Modifier.weight(weight),
    fontSize = 10.sp,
    fontWeight = FontWeight.Bold,
    textAlign = align,
    color = MaterialTheme.colorScheme.onSurfaceVariant,
    maxLines = 1,
    softWrap = false,
)

@Composable
private fun RowScope.Mono(text: String, weight: Float) = Text(
    text,
    Modifier.weight(weight),
    textAlign = TextAlign.End,
    fontFamily = FontFamily.Monospace,
    fontSize = 12.sp,
    maxLines = 1,
    softWrap = false,
)

/** A position with no stop loss. Amber, not red: it is a warning, not an error. */
@Composable
private fun RowScope.Warn(text: String, weight: Float) = Text(
    text,
    Modifier.weight(weight),
    textAlign = TextAlign.End,
    fontFamily = FontFamily.Monospace,
    fontWeight = FontWeight.Bold,
    fontSize = 11.sp,
    color = Amber,
)

/**
 * The SPLIT-layout right column: positions stripped to ENTRY price + P&L, swipe a row LEFT to close
 * it. Same data as [PositionsGrid] (an ImmutableList, keyed by ticket) so a tick only touches P&L.
 */
@Composable
fun SplitPositions(
    state: PositionsUi,
    onClose: (Long) -> Unit,
    connected: Boolean,
    live: Boolean,
    closing: Set<Long> = emptySet(),
    modifier: Modifier = Modifier,
) {
    Column(modifier) {
        Row(
            Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceVariant)
                // start = 12 (4dp accent strip + 8dp row inset) keeps ENTRY over the price column.
                .padding(start = 12.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
        ) {
            Text("ENTRY", Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("P&L", Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.Bold,
                textAlign = TextAlign.End, color = MaterialTheme.colorScheme.onSurfaceVariant)
            // Trailing column reserved for the ✕ close button on each row (keeps ENTRY/P&L aligned).
            Spacer(Modifier.width(CLOSE_COL_WIDTH))
        }
        HorizontalDivider()

        if (state.items.isEmpty()) {
            Box(Modifier.fillMaxWidth().padding(16.dp), Alignment.Center) {
                Text(
                    if (connected) "No open positions" else "No data — not connected",
                    color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp,
                )
            }
            return@Column
        }

        // Frozen, not hidden: the same rationale as PositionsGrid -- a blank list during a
        // disconnect reads as "flat", the most dangerous lie this screen could tell.
        if (!live) {
            Box(
                Modifier.fillMaxWidth().background(Amber.copy(alpha = 0.18f))
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            ) {
                Text("NOT LIVE — P&L frozen", color = Amber, fontSize = 10.sp,
                    fontWeight = FontWeight.Bold)
            }
        }

        LazyColumn {
            items(state.items, key = { it.ticket }) { p ->
                // animateItem(): when a confirmed close drops the ticket from the snapshot, the row
                // slides/fades out instead of vanishing abruptly.
                Column(Modifier.animateItem()) {
                    SplitPositionRow(p, state.digits, onClose, closing = p.ticket in closing)
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                }
            }
        }
    }
}

/** Fixed width reserved for the per-row ✕ close button (and the matching header spacer). Kept tight
 *  so the ENTRY / P&L columns still fit their prices on ONE line in the narrow SPLIT panel. */
private val CLOSE_COL_WIDTH = 26.dp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SplitPositionRow(p: Position, digits: Int, onClose: (Long) -> Unit, closing: Boolean) {
    // Optimistic feedback: on close request the row dims to 0.4 and holds there until the server
    // confirms (the ticket leaves the snapshot -> the whole item animates out via animateItem()).
    val rowAlpha by animateFloatAsState(if (closing) 0.4f else 1f, label = "rowClosingAlpha")
    val dismiss = rememberSwipeToDismissBoxState(
        confirmValueChange = { v ->
            // While already closing, ignore further swipes (can't re-fire the same ticket).
            if (!closing && v == SwipeToDismissBoxValue.EndToStart) onClose(p.ticket)
            // Never dismiss the row ourselves: fire the close and snap back. The closed position
            // disappears from the next snapshot (the list is keyed by ticket), which removes the row
            // cleanly -- and if the close FAILS the row simply stays, no dangling empty slot.
            false
        },
    )
    SwipeToDismissBox(
        state = dismiss,
        // Swipe LEFT only; a right-swipe would fight the vertical scroll and there is no second action.
        enableDismissFromStartToEnd = false,
        gesturesEnabled = !closing,
        backgroundContent = {
            Box(
                Modifier.fillMaxSize().background(Red.copy(alpha = 0.85f)).padding(horizontal = 12.dp),
                contentAlignment = Alignment.CenterEnd,
            ) {
                Text("CLOSE", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
            }
        },
    ) {
        val pl = p.profit ?: 0.0
        Row(
            Modifier
                .fillMaxWidth()
                .height(IntrinsicSize.Min)
                .background(MaterialTheme.colorScheme.surface)
                .alpha(rowAlpha),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Left accent strip: green = LONG, red = SHORT. This is the ONLY long/short signal now,
            // so the P&L colour is free to mean only profit/loss (a losing long no longer looks like
            // a short). fillMaxHeight against the row's IntrinsicSize.Min makes it span the full row.
            Box(
                Modifier
                    .width(4.dp)
                    .fillMaxHeight()
                    .background(if (p.isBuy) Green else Red),
            )
            Row(
                Modifier
                    .weight(1f)
                    .padding(start = 6.dp, top = 10.dp, bottom = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Origin rides INLINE with the entry price, inside the existing ENTRY cell. This
                // panel is narrow and CLOSE_COL_WIDTH is fixed, so there is no room for a column
                // of its own -- and a manual position renders nothing at all.
                Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        Fmt.price(p.priceOpen, digits),
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1,
                        softWrap = false,
                        fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 12.sp,
                    )
                    OriginBadge(p.origin, Modifier.padding(start = 4.dp))
                }
                Text(
                    Fmt.signedMoney(pl),
                    Modifier.weight(1f),
                    color = if (pl < 0) Red else Green,
                    textAlign = TextAlign.End,
                    maxLines = 1,
                    fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 12.sp,
                )
            }
            // Trailing ✕: the explicit per-row close (swipe still works too). Glyph, not an icon
            // asset — matches the app convention and needs no material-icons dependency.
            Box(
                Modifier
                    .width(CLOSE_COL_WIDTH)
                    .fillMaxHeight()
                    .clickable(enabled = !closing) { onClose(p.ticket) },
                contentAlignment = Alignment.Center,
            ) {
                Text("✕", color = Red, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            }
        }
    }
}
