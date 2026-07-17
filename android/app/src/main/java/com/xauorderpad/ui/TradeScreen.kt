package com.xauorderpad.ui

import android.os.SystemClock
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.border
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.net.Link
import kotlinx.coroutines.delay
import kotlin.math.ceil

/**
 * The trading screen.
 *
 * Every parameter is a NARROW SLICE, not the whole Snapshot. That is what stops a bid tick
 * (5/sec) from recomposing the LOT / SL / TP text fields and the positions list along with the
 * quote. See UiState.kt.
 */
@Composable
fun TradeScreen(
    quote: Quote,
    health: Health,
    account: AccountUi,
    positions: PositionsUi,
    link: Link,
    form: OrderForm,
    /** Order/close requests currently on the wire -- feeds the "N sending" chip, never gates a button. */
    inFlight: Int,
    onLot: (String) -> Unit,
    onStepLot: (Int) -> Unit,
    onSl: (String) -> Unit,
    onTp: (String) -> Unit,
    onEnterArmed: () -> Unit,
    onCloseArmed: () -> Unit,
    onArmedSide: (String) -> Unit,
    /** Place a specific side directly ("buy"|"sell"). SPLIT-only: its two direct BUY/SELL buttons. */
    onPlace: (String) -> Unit = {},
    armed: ArmedUi,
    onCloseWhere: (String) -> Unit,
    onClosePosition: (Long) -> Unit,
    /** Tickets currently being closed — SPLIT grid uses this to animate the row. */
    closingTickets: Set<Long> = emptySet(),
    onLogin: () -> Unit,
    onSettings: () -> Unit,
    /** Open today's-activity screen. Shown in the top bar only when the broker is connected. */
    onHistory: () -> Unit = {},
    /** Read-only here: the SWITCH lives in Settings now. This only decides whether to ask. */
    confirmCloses: Boolean,
    serverUrl: String,
    /** Socket is Up. False => everything on this screen is a frozen last-known frame. */
    live: Boolean,
    strategies: StrategiesUi,
    /** Active layout. Classic vs Compact vs Scalp; the top-bar chip cycles it. */
    mode: LayoutMode = LayoutMode.CLASSIC,
    onCycleLayout: () -> Unit = {},
    /** MT5 broker tick time (epoch s) for the Scalp candle countdown; null until first tick. */
    tickTime: Long? = null,
    /** Selected candle timeframe in minutes (Scalp). */
    candleTf: Int = 15,
    onSelectTf: (Int) -> Unit = {},
    /** Server-enforced account P&L guard (auto-close-all at a target). */
    guard: GuardUi = GuardUi(),
    onSetGuard: (Boolean?, Double?, String?) -> Unit = { _, _, _ -> },
    modifier: Modifier = Modifier,
    closing: Boolean = false,
) {
    // Compact order form + quotes for both Compact AND Scalp; only Classic keeps the roomy layout.
    val compact = mode != LayoutMode.CLASSIC
    // Every bulk close is confirmed: they are irreversible and a single tap can flatten the
    // whole book. `confirm` holds the pending filter, or null.
    var confirm by remember { mutableStateOf<String?>(null) }
    // Shared by every layout's bulk-close controls: confirm first (unless the user turned confirm
    // off), else fire straight through. Same AlertDialog handles all of them.
    val onBulkClose: (String) -> Unit = { filter ->
        if (confirmCloses) confirm = filter else onCloseWhere(filter)
    }

    // Every control height is a FRACTION of the real screen height, not a fixed dp.
    //
    // The controls used to eat ~70% of the screen and the positions grid got whatever was left
    // -- about three rows. The grid is the thing you actually watch while a trade is on, so the
    // chrome above it has to yield. Fractions (not hardcoded dp) mean this holds on a small
    // phone and a tall one alike; the coerceIn bounds stop it from collapsing into an untappable
    // control on a tiny screen or ballooning on a tablet.
    BoxWithConstraints(modifier.fillMaxSize()) {
        val hPx = maxHeight
        // Grow the height floors/ceilings with the (already-clamped) font scale, so when the user's
        // Font-Size enlarges the text the controls enlarge WITH it instead of being overrun. fs is in
        // [1, MAX_FONT_SCALE] because the root clamps LocalDensity.
        val fs = LocalDensity.current.fontScale
        fun frac(f: Float, min: Dp, max: Dp): Dp = (hPx * f).coerceIn(min * fs, max * fs)
        val d = Dims(
            gap = frac(0.007f, 4.dp, 10.dp),
            quotePad = frac(0.006f, 4.dp, 10.dp),
            priceSp = (hPx.value * 0.026f).coerceIn(17f, 22f).sp,
            tabH = frac(0.036f, 28.dp, 40.dp),
            fieldH = frac(0.052f, 40.dp, 54.dp),
            actionH = frac(0.068f, 52.dp, 64.dp),
            bulkH = frac(0.044f, 34.dp, 46.dp),
        )

    if (mode == LayoutMode.SPLIT) {
        SplitBody(
            serverUrl = serverUrl, strategies = strategies, mode = mode,
            onCycleLayout = onCycleLayout, onSettings = onSettings, onHistory = onHistory,
            health = health, link = link, onLogin = onLogin, live = live,
            quote = quote,
            form = form, canTrade = health.healthy && live, digits = positions.digits,
            onLot = onLot, onStepLot = onStepLot, onSl = onSl, onTp = onTp, onPlace = onPlace,
            onBulkClose = onBulkClose, closing = closing,
            guard = guard, account = account, onSetGuard = onSetGuard,
            positions = positions, onClosePosition = onClosePosition, closingTickets = closingTickets,
            d = d,
        )
    } else {
    Column(Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 8.dp)) {
        ServerBar(
            serverUrl = serverUrl,
            strategyDot = strategies.items.any { it.enabled },
            strategyKilled = strategies.items.any { it.killed },
            mode = mode,
            showHistory = health.connected,
            onHistory = onHistory,
            onCycleLayout = onCycleLayout,
            onSettings = onSettings,
        )
        Spacer(Modifier.height(d.gap))

        // Scalp swaps the full status banner for a trimmed identity + candle countdown. Every other
        // mode keeps the full banner. Critical states (disconnect / logged out / REAL) survive both.
        if (mode == LayoutMode.SCALP) {
            ScalpHeader(health, link, live, tickTime, candleTf, onSelectTf, onLogin)
        } else {
            StatusBanner(health, link, onLogin)
        }
        Spacer(Modifier.height(d.gap))

        QuoteBlock(quote, live, d, compact)
        Spacer(Modifier.height(d.gap))

        ArmedTabs(armed.side, d, onArmedSide)
        Spacer(Modifier.height(d.gap))

        // `live` gates ENTRY only. `health.healthy` alone is not enough: it is derived from the
        // last snapshot, which survives the socket's death -- so it still reports "healthy" from
        // a frame that may be minutes old, and BUY/SELL would stay armed against a frozen price.
        // No `busy` term any more: the entry/close buttons fire-and-forget so a burst is not gated.
        OrderFormBlock(form, canTrade = health.healthy && live, digits = positions.digits,
            armed = armed, quote = quote, inFlight = inFlight, d = d, compact = compact,
            onLot = onLot, onStepLot = onStepLot, onSl = onSl, onTp = onTp,
            onEnterArmed = onEnterArmed, onCloseArmed = onCloseArmed)
        Spacer(Modifier.height(d.gap))

        // Gated ONLY on a bulk close already being in flight.
        //
        // Not on `busy`: that is set by BUY/SELL, and gating on it meant an order hanging on a
        // flaky link disabled the entire panic path for the duration.
        //
        // Not on `positions.items.isNotEmpty()` either: `positions` is empty both when the book
        // is genuinely flat AND when the frame is degraded (the server omits the `positions`
        // key entirely when the terminal drops its broker link -- absent, not empty). That veto
        // therefore greyed out CLOSE ALL at precisely the moment the app had no idea what the
        // book was. The same stale-snapshot veto was already removed from the web UI; the
        // server re-evaluates the filter against live broker state and reports honestly if
        // nothing matched, so the client has no business refusing to ask.
        // With CONFIRM off, the tap goes straight to the server -- that is the whole point of the
        // switch (flattening fast in a spike). The switch itself is the deliberate act; making
        // the user re-confirm after they explicitly turned confirmation off would be absurd.
        //
        // NOT gated on `live`, deliberately -- unlike BUY/SELL. A dead socket does not mean a dead
        // server: the WebSocket can drop while HTTP still works, and this is the panic path. The
        // server re-evaluates the filter against live broker prices, and if the network really is
        // down the call fails loudly with a toast. Refusing to even ask, at the moment gold is
        // gapping against you, is the worse failure.
        BulkCloseBar(enabled = !closing, d = d, onPick = onBulkClose)
        Spacer(Modifier.height(d.gap))

        // Server-enforced auto-close-all at a floating-P&L target. Rendered once here, so it shows
        // in all three layouts. See GuardBar / the /api/guard path.
        GuardBar(guard = guard, floatingPl = account.floatingPl, d = d, onSetGuard = onSetGuard)
        Spacer(Modifier.height(d.gap))

        AccountStrip(account)
        Spacer(Modifier.height(d.gap))

        PositionsGrid(
            state = positions,
            onClose = onClosePosition,
            connected = health.connected,
            live = live,
            // weight(1f), not fillMaxSize(): as the last child of a Column, fillMaxSize is a
            // fragile idiom that can fight the siblings for space. Everything above shrank so
            // that THIS gets the remainder.
            modifier = Modifier.weight(1f),
        )
    }
    }
    }

    confirm?.let { filter ->
        val label = when (filter) {
            "losing" -> "all LOSING"
            "profit" -> "all PROFITABLE"
            else -> "ALL"
        }
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text("Close $label positions?") },
            text = {
                Text(
                    "This cannot be undone.\n\nThe server picks which positions match using " +
                        "live broker prices at the moment of the close, so the exact count may " +
                        "differ from what is on screen."
                )
            },
            confirmButton = {
                TextButton(onClick = { onCloseWhere(filter); confirm = null }) {
                    Text("CLOSE", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Cancel") } },
        )
    }
}

/**
 * Shows WHICH server this app is talking to, and the way into everything else.
 *
 * The address is on screen because without it there is no way to tell a phone pointed at the
 * right box from one pointed at a stale baked-in default.
 *
 * CONFIRM moved to Settings. It is a set-once preference, not a control you work during a trade,
 * and the row it occupied here was costing the positions grid space it needs more.
 *
 * The dot on the gear is the one thing that must survive that move: a strategy running on the
 * SERVER is invisible from the phone unless we say so, and it now lives two screens away.
 * Green = something is armed and may be trading while you are not looking.
 */
@Composable
private fun ServerBar(
    serverUrl: String,
    strategyDot: Boolean,
    strategyKilled: Boolean,
    mode: LayoutMode,
    showHistory: Boolean,
    onHistory: () -> Unit,
    onCycleLayout: () -> Unit,
    onSettings: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().height(28.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            // Strip the scheme: "http://" is noise on a phone-width line, and the host:port is
            // the part that actually identifies the box.
            serverUrl.removePrefix("http://").removePrefix("https://").ifBlank { "not set" },
            fontFamily = FontFamily.Monospace,
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )

        // Today's-activity screen. Only offered when the terminal is connected to the broker --
        // there is no history to fetch otherwise, and the endpoint would just error.
        if (showHistory) {
            TextButton(
                onClick = onHistory,
                contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
            ) {
                Text("📊", fontSize = 15.sp)
            }
        }

        // Layout cycle chip. Taps through Classic -> Compact -> Scalp, persisted, so the designs can
        // be compared on the same phone with the same live feed. Temporary comparison scaffold.
        TextButton(
            onClick = onCycleLayout,
            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
        ) {
            Text(
                mode.name,
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
        }

        TextButton(
            onClick = onSettings,
            contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
        ) {
            Text("⚙", fontSize = 18.sp)
            if (strategyDot || strategyKilled) {
                Spacer(Modifier.width(3.dp))
                Text("●", fontSize = 11.sp, color = if (strategyKilled) Red else Green)
            }
        }
    }
}

/**
 * The banner's (text, colour, action) decision, shared by the full [StatusBanner] and the Scalp
 * [ScalpHeader] so the two can NEVER disagree about state. `trimmed` only shortens the healthy
 * demo/real identity line -- every fault state (disconnect / logged out / not-healthy / token) is
 * identical in both, and a REAL account stays a loud red badge either way.
 *
 * Order matters: a connection problem MASKS a broker problem, so report the outermost one. Showing
 * "market closed" while the socket is actually dead would send the user hunting the wrong fault.
 */
private fun bannerState(h: Health, link: Link, trimmed: Boolean): Triple<String, Color, String?> = when {
    link is Link.Unauthorized -> Triple("Token rejected — reconnect", Red, null)
    link is Link.Down -> Triple("Disconnected: ${link.reason} · retry ${link.retryInSec}s", Red, null)
    link is Link.Connecting -> Triple("Connecting…", Amber, null)
    h.loggedOut -> Triple("MT5 is logged out", Red, "LOG IN")
    !h.healthy -> Triple(h.error ?: "Trading not allowed", Red, null)

    // isDemo == false means a REAL account. This screen has three one-tap buttons that flatten a
    // book -- make it impossible to miss which account you are on. Trimmed keeps it LOUD (red, ●).
    h.isDemo == false -> Triple(
        if (trimmed) "● R-${brokerShort(h.server)}${loginSuffix(h.login)}"
        else "● REAL ACCOUNT — ${h.server.orEmpty()}",
        Red, null,
    )

    else -> Triple(
        if (trimmed) "D-${brokerShort(h.server)}${loginSuffix(h.login)}"
        else "● DEMO · ${h.server ?: "connected"}",
        Green, null,
    )
}

/** First 2 letters of the broker (before the "-" in the server name), e.g. Exness-MT5Trial16 -> EX. */
private fun brokerShort(server: String?): String =
    server?.substringBefore('-')?.trim()?.take(2)?.uppercase().orEmpty().ifEmpty { "?" }

private fun loginSuffix(login: Long?): String = login?.let { " ($it)" } ?: ""

@Composable
private fun StatusBanner(h: Health, link: Link, onLogin: () -> Unit) {
    val (text, color, action) = bannerState(h, link, trimmed = false)
    Row(
        Modifier
            .fillMaxWidth()
            .background(color.copy(alpha = 0.14f), RoundedCornerShape(6.dp))
            .padding(horizontal = 10.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, color = color, fontSize = 12.sp, fontWeight = FontWeight.Bold,
            modifier = Modifier.weight(1f))
        if (action != null) TextButton(onClick = onLogin) { Text(action, fontSize = 12.sp) }
    }
}

/**
 * Scalp mode's header: a TRIMMED identity badge + an MT5 candle-close countdown. On any fault
 * state (disconnect / logged out / token / not-healthy) it falls back to the same loud message the
 * full banner would show and hides the countdown -- a running timer next to a dead feed would lie.
 */
@Composable
private fun ScalpHeader(
    h: Health,
    link: Link,
    live: Boolean,
    tickTime: Long?,
    candleTf: Int,
    onSelectTf: (Int) -> Unit,
    onLogin: () -> Unit,
) {
    val (text, color, action) = bannerState(h, link, trimmed = true)
    // Only show the countdown when there is no fault to report AND the feed is live.
    val showCandle = action == null && live
    // produceState runs regardless (cheap); we just gate its RENDERING.
    val (leftSec, fill) = rememberCandleCountdown(tickTime, candleTf)

    Column(
        Modifier
            .fillMaxWidth()
            .background(color.copy(alpha = 0.14f), RoundedCornerShape(6.dp))
            .padding(horizontal = 10.dp, vertical = 3.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(text, color = color, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (action != null) {
                TextButton(onClick = onLogin) { Text(action, fontSize = 12.sp) }
            } else {
                TfDropdown(candleTf, enabled = live, onSelect = onSelectTf)
                Spacer(Modifier.width(6.dp))
                Text(
                    fmtLeft(leftSec),
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    color = if (live) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (showCandle) {
            Spacer(Modifier.height(2.dp))
            CandleBar(fill)
        }
    }
}

/**
 * Seconds-left-to-candle-close and the elapsed fraction, anchored to MT5 BROKER time.
 *
 * `tickTime` is the broker epoch-seconds of the latest tick; it only advances on a new tick, so
 * between ticks we interpolate with the phone's MONOTONIC clock (elapsedRealtime) and re-anchor
 * whenever a new tick arrives (produceState is keyed on tickTime). When no tick has arrived yet
 * (market closed at connect), fall back to the device epoch clock -- candle boundaries are
 * epoch-aligned, so only the phone's absolute offset differs. Returns (secondsLeft, elapsedFrac).
 */
@Composable
private fun rememberCandleCountdown(tickTime: Long?, tfMinutes: Int): Pair<Int, Float> {
    val period = tfMinutes.coerceAtLeast(1) * 60
    val state by produceState(initialValue = period to 0f, tickTime, tfMinutes) {
        val anchorServer = tickTime
        val anchorLocal = SystemClock.elapsedRealtime()
        while (true) {
            val serverNow = if (anchorServer != null)
                anchorServer + (SystemClock.elapsedRealtime() - anchorLocal) / 1000.0
            else
                System.currentTimeMillis() / 1000.0
            val elapsed = ((serverNow % period) + period) % period   // guard any negative
            val left = ceil(period - elapsed).toInt().coerceIn(0, period)
            value = left to (elapsed / period).toFloat()
            delay(250)
        }
    }
    return state
}

/** M1/M2/M5/M15/M30/H1/H4 picker. One tap opens the list. */
@Composable
private fun TfDropdown(tfMinutes: Int, enabled: Boolean, onSelect: (Int) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        // A compact clickable label, NOT a TextButton: a Material button forces a ~40 dp min touch
        // height, which is what made this header row tall. The row height now tracks the text.
        Text(
            tfLabel(tfMinutes) + " ▾",
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            color = if (enabled) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier
                .clip(RoundedCornerShape(4.dp))
                .clickable(enabled = enabled) { open = true }
                .padding(horizontal = 6.dp, vertical = 2.dp),
        )
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            for (m in intArrayOf(1, 2, 5, 15, 30, 60, 240)) {
                DropdownMenuItem(
                    text = { Text(tfLabel(m), fontWeight = if (m == tfMinutes) FontWeight.Bold else FontWeight.Normal) },
                    onClick = { onSelect(m); open = false },
                )
            }
        }
    }
}

/**
 * A thin candle-progress bar: fills toward the right as the candle nears its close, AND shifts
 * colour green -> amber -> red so the LEFT-to-close is legible from colour alone, not just width.
 */
@Composable
private fun CandleBar(fill: Float) {
    val f = fill.coerceIn(0f, 1f)
    Box(
        Modifier
            .fillMaxWidth()
            .height(4.dp)
            .clip(RoundedCornerShape(2.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Box(
            Modifier
                .fillMaxWidth(f)
                .height(4.dp)
                .background(candleColor(f)),
        )
    }
}

/**
 * Candle-progress colour by elapsed fraction (0 = fresh, 1 = about to close). Green for most of the
 * candle, warming through amber, and RED in the final ~10% so "closing now" reads at a glance. The
 * amber midpoint keeps a direct green->red lerp out of muddy brown. One lerp per frame -- negligible.
 */
private fun candleColor(fill: Float): Color = when {
    fill < 0.75f -> Green
    fill < 0.90f -> lerp(Green, Amber, (fill - 0.75f) / 0.15f)
    else -> lerp(Amber, Red, ((fill - 0.90f) / 0.10f).coerceIn(0f, 1f))
}

private fun tfLabel(m: Int): String = when (m) {
    60 -> "H1"
    240 -> "H4"
    else -> "M$m"
}

/** Seconds -> "M:SS", or "H:MM:SS" for >= 1h so H1/H4 read sanely. */
private fun fmtLeft(sec: Int): String {
    val s = sec.coerceAtLeast(0)
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60)
    else "%d:%02d".format(s / 60, s % 60)
}

/**
 * The quote.
 *
 * When the feed is not live these numbers are FROZEN -- the last frame before the socket died --
 * but nothing about a price on a dark screen says so. The status banner alone is not enough: the
 * eye is on the digits, not the banner. So the cells dim and a STALE stamp sits across them. A
 * frozen price that still looks live is the actual hazard here, not the disconnection itself.
 */
@Composable
private fun QuoteBlock(q: Quote, live: Boolean, d: Dims, compact: Boolean) {
    Box(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().alpha(if (live) 1f else 0.35f),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            QuoteCell("BID", Fmt.price(q.bid, q.digits), Red, d, compact, Modifier.weight(1f))

            Card(
                Modifier.width(if (compact) 64.dp else 78.dp),
                colors = CardDefaults.cardColors(MaterialTheme.colorScheme.surfaceVariant),
            ) {
                // spreadPoints, NOT the raw `spread` field: the server sends a PRICE difference.
                // Rendering it raw prints "0.22" where a trader expects "22".
                val spreadColor = MaterialTheme.colorScheme.onSurfaceVariant
                if (compact) {
                    // Number-only + a faint "SPD" watermark in the corner, to save the label row.
                    Box(Modifier.fillMaxWidth().padding(vertical = d.quotePad)) {
                        Text("SPD", fontSize = 8.sp, fontWeight = FontWeight.Bold,
                            color = spreadColor.copy(alpha = 0.5f),
                            modifier = Modifier.align(Alignment.TopStart).padding(start = 5.dp))
                        Text(Fmt.points(q.spreadPoints), fontFamily = FontFamily.Monospace,
                            fontSize = d.priceSp * 0.82f, fontWeight = FontWeight.Bold, maxLines = 1,
                            modifier = Modifier.align(Alignment.Center))
                    }
                } else {
                    Column(
                        Modifier.fillMaxWidth().padding(vertical = d.quotePad),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("SPREAD", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = spreadColor)
                        Text(Fmt.points(q.spreadPoints), fontFamily = FontFamily.Monospace,
                            fontSize = d.priceSp * 0.82f, fontWeight = FontWeight.Bold)
                    }
                }
            }

            QuoteCell("ASK", Fmt.price(q.ask, q.digits), Green, d, compact, Modifier.weight(1f))
        }

        if (!live) {
            Box(
                Modifier
                    .align(Alignment.Center)
                    .background(Amber, RoundedCornerShape(4.dp))
                    .padding(horizontal = 10.dp, vertical = 3.dp),
            ) {
                Text(
                    "STALE",
                    color = Color.Black,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
    }
}

@Composable
private fun QuoteCell(label: String, value: String, color: Color, d: Dims, compact: Boolean, modifier: Modifier) {
    Card(modifier, colors = CardDefaults.cardColors(color.copy(alpha = 0.12f))) {
        if (compact) {
            // Number only, with the BID/ASK label as a faint corner watermark -- saves the label
            // row while keeping the hint. Colour + position still carry the meaning (BID=red/left,
            // ASK=green/right).
            Box(Modifier.fillMaxWidth().padding(vertical = d.quotePad)) {
                Text(label, fontSize = 8.sp, fontWeight = FontWeight.Bold, color = color.copy(alpha = 0.45f),
                    modifier = Modifier.align(Alignment.TopStart).padding(start = 6.dp))
                Text(value, fontFamily = FontFamily.Monospace, fontSize = d.priceSp,
                    fontWeight = FontWeight.Bold, color = color, maxLines = 1,
                    modifier = Modifier.align(Alignment.Center))
            }
        } else {
            Column(
                Modifier.fillMaxWidth().padding(vertical = d.quotePad),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(label, fontSize = 9.sp, fontWeight = FontWeight.Bold, color = color)
                Text(value, fontFamily = FontFamily.Monospace, fontSize = d.priceSp,
                    fontWeight = FontWeight.Bold, color = color, maxLines = 1)
            }
        }
    }
}

/**
 * Every control height on this screen, derived from the REAL screen height rather than baked in.
 *
 * The trader watches the positions grid; the chrome above it is there to be used and then
 * ignored. Fixed dp meant the chrome took whatever it liked and the grid took the remainder --
 * about three rows. These fractions invert that, and the coerceIn bounds keep a control from
 * shrinking below a tappable size on a small phone.
 */
@Immutable
private data class Dims(
    val gap: Dp,
    val quotePad: Dp,
    val priceSp: TextUnit,
    val tabH: Dp,
    val fieldH: Dp,
    val actionH: Dp,
    val bulkH: Dp,
)

/**
 * A text field we can actually SIZE.
 *
 * Material3's OutlinedTextField enforces a 56.dp minimum height and reserves another line for a
 * floating label -- roughly 70.dp per field. Three of them (LOT, SL, TP) ate a fifth of the
 * screen before a single price was on it, and no Modifier.height() will talk it down. So the
 * field is a BasicTextField in a bordered Box, with the label as a caption above it: same
 * behaviour, height we control.
 */
@Composable
private fun CompactField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    height: Dp,
    keyboardType: KeyboardType,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    fontSize: TextUnit = 17.sp,
) {
    Column(modifier) {
        Text(label, fontSize = 9.sp, fontWeight = FontWeight.Bold,
             color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(2.dp))
        BasicTextField(
            value = value,
            onValueChange = onValueChange,
            singleLine = true,
            textStyle = LocalTextStyle.current.copy(
                color = MaterialTheme.colorScheme.onSurface,
                fontSize = fontSize,
                fontWeight = FontWeight.Bold,
            ),
            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
            keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
            modifier = Modifier
                .fillMaxWidth()
                .height(height)
                .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp))
                .padding(horizontal = 10.dp),
            decorationBox = { inner ->
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.CenterStart) {
                    if (value.isEmpty() && placeholder.isNotEmpty()) {
                        Text(placeholder, fontSize = 12.sp,
                             color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    inner()
                }
            },
        )
    }
}

/**
 * Translates the POINT distance the user typed into the dollar distance it actually means.
 *
 * On XAUUSD `point` = 0.001, so 1000 points = $1.00 of gold -- a factor of a THOUSAND between
 * what you type and what you mean. Someone intending a $1 stop and typing "1" gets a stop
 * 0.1 cents away, which is inside the spread and stops out instantly. The field cannot be
 * relabelled to dollars without changing the wire contract, so it shows the arithmetic instead.
 */
@Composable
private fun PointsHint(raw: String, digits: Int) {
    val pts = raw.trim().toDoubleOrNull()
    if (pts == null || pts <= 0.0) return
    val dollars = pts * Math.pow(10.0, -digits.toDouble())
    Text(
        "= $" + Fmt.price(dollars, 2),
        fontSize = 10.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** Inline field label for the compact layout (sits to the LEFT of the box, not above it). */
@Composable
private fun FieldLabel(text: String) = Text(
    text,
    fontSize = 11.sp,
    fontWeight = FontWeight.Bold,
    color = MaterialTheme.colorScheme.onSurfaceVariant,
)

/**
 * LOT on one line: inline label + short box + −/+ steppers. Shared by Compact/Scalp (fixed-width box)
 * and Split (fills the narrow column when [fillField] is true).
 */
@Composable
private fun CompactLotRow(
    form: OrderForm,
    d: Dims,
    onLot: (String) -> Unit,
    onStepLot: (Int) -> Unit,
    fillField: Boolean = false,
) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FieldLabel("LOT")
        OutlinedButton(
            onClick = { onStepLot(-1) },
            contentPadding = PaddingValues(0.dp),
            modifier = Modifier.width(42.dp).height(d.fieldH),
        ) { Text("−", fontSize = 20.sp, fontWeight = FontWeight.Bold) }
        BareField(
            value = form.lot,
            onValueChange = onLot,
            height = d.fieldH,
            keyboardType = KeyboardType.Decimal,
            modifier = if (fillField) Modifier.weight(1f) else Modifier.width(104.dp),
        )
        OutlinedButton(
            onClick = { onStepLot(1) },
            contentPadding = PaddingValues(0.dp),
            modifier = Modifier.width(42.dp).height(d.fieldH),
        ) { Text("+", fontSize = 20.sp, fontWeight = FontWeight.Bold) }
    }
}

/**
 * SL / TP on one row: inline labels + short boxes + the $-equivalent [PointsHint]. Shared by
 * Compact/Scalp and Split. The hint is a safety readout (these are POINT distances, not prices).
 */
@Composable
private fun CompactSlTpRow(
    form: OrderForm,
    digits: Int,
    d: Dims,
    onSl: (String) -> Unit,
    onTp: (String) -> Unit,
) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Column(Modifier.weight(1f)) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                FieldLabel("SL")
                BareField(form.slPoints, onSl, d.fieldH, KeyboardType.Number,
                    Modifier.weight(1f), placeholder = "0")
            }
            PointsHint(form.slPoints, digits)
        }
        Column(Modifier.weight(1f)) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                FieldLabel("TP")
                BareField(form.tpPoints, onTp, d.fieldH, KeyboardType.Number,
                    Modifier.weight(1f), placeholder = "0")
            }
            PointsHint(form.tpPoints, digits)
        }
    }
}

/**
 * The bordered input box WITHOUT the caption line -- the compact layout puts the label inline
 * (see [FieldLabel]) instead of above, so it must not carry its own. Same behaviour and height
 * control as [CompactField]; only the caption is gone.
 */
@Composable
private fun BareField(
    value: String,
    onValueChange: (String) -> Unit,
    height: Dp,
    keyboardType: KeyboardType,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    fontSize: TextUnit = 17.sp,
    enabled: Boolean = true,
) {
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        enabled = enabled,
        singleLine = true,
        textStyle = LocalTextStyle.current.copy(
            color = MaterialTheme.colorScheme.onSurface,
            fontSize = fontSize,
            fontWeight = FontWeight.Bold,
        ),
        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
        modifier = modifier
            .height(height)
            .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp),
        decorationBox = { inner ->
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.CenterStart) {
                if (value.isEmpty() && placeholder.isNotEmpty()) {
                    Text(placeholder, fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                inner()
            }
        },
    )
}

@Composable
private fun OrderFormBlock(
    form: OrderForm,
    canTrade: Boolean,
    digits: Int,
    armed: ArmedUi,
    quote: Quote,
    inFlight: Int,
    onLot: (String) -> Unit,
    onStepLot: (Int) -> Unit,
    onSl: (String) -> Unit,
    onTp: (String) -> Unit,
    onEnterArmed: () -> Unit,
    onCloseArmed: () -> Unit,
    d: Dims,
    compact: Boolean,
) {
    Column {
        if (compact) {
            CompactLotRow(form, d, onLot, onStepLot)
            Spacer(Modifier.height(d.gap))
            CompactSlTpRow(form, digits, d, onSl, onTp)
            Spacer(Modifier.height(d.gap))
        } else {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            // contentPadding = 0 is load-bearing. Material's default button padding is 24.dp on
            // EACH side; inside a 48.dp-wide button that leaves ZERO width for the label, so the
            // glyph was clipped to nothing -- two blank circles that still worked when tapped.
            OutlinedButton(
                onClick = { onStepLot(-1) },
                contentPadding = PaddingValues(0.dp),
                modifier = Modifier.width(46.dp).height(d.fieldH),
            ) { Text("−", fontSize = 20.sp, fontWeight = FontWeight.Bold) }

            CompactField(
                value = form.lot,
                onValueChange = onLot,
                label = "LOT",
                height = d.fieldH,
                keyboardType = KeyboardType.Decimal,
                modifier = Modifier.weight(1f),
            )

            OutlinedButton(
                onClick = { onStepLot(1) },
                contentPadding = PaddingValues(0.dp),
                modifier = Modifier.width(46.dp).height(d.fieldH),
            ) { Text("+", fontSize = 20.sp, fontWeight = FontWeight.Bold) }
        }

        Spacer(Modifier.height(d.gap))

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            // The labels say "points" on purpose. These are POINT DISTANCES, not prices -- the
            // server hardcodes sl_tp_mode="points". A field labelled just "SL" invites a price,
            // and a price entered here would be silently ACCEPTED as a distance, placing a stop
            // thousands of points away. Being accepted rather than rejected is what makes it
            // dangerous.
            Column(Modifier.weight(1f)) {
                CompactField(
                    value = form.slPoints,
                    onValueChange = onSl,
                    label = "SL (points)",
                    height = d.fieldH,
                    keyboardType = KeyboardType.Number,
                    placeholder = "0 = none",
                )
                PointsHint(form.slPoints, digits)
            }
            Column(Modifier.weight(1f)) {
                CompactField(
                    value = form.tpPoints,
                    onValueChange = onTp,
                    label = "TP (points)",
                    height = d.fieldH,
                    keyboardType = KeyboardType.Number,
                    placeholder = "0 = none",
                )
                PointsHint(form.tpPoints, digits)
            }
        }

        Spacer(Modifier.height(d.gap))
        }

        // ── CLOSE is ALWAYS the left slot. ENTRY is ALWAYS the right slot. ──
        //
        // The web UI swaps them between modes ([CLOSE][BUY] vs [SELL][CLOSE]) because on a
        // keyboard the mode cannot bite you: space and backspace are different physical keys
        // whichever side is armed. On a PHONE you tap by POSITION. Swap the buttons and the spot
        // that was CLOSE becomes SELL after a flip -- you meant to get out and you opened a short.
        //
        // So position carries the FUNCTION and never changes; colour and label carry the
        // direction. This is the whole reason the armed mode is safe to have on a touchscreen.
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {

            val hasTarget = armed.targetTicket != null

            // NOT gated on `canTrade`/`live`, deliberately -- like the bulk-close bar below. A
            // dead socket is not a dead server, and this is the way OUT. The only thing that
            // disables it is having nothing on this side to close.
            OutlinedButton(
                onClick = onCloseArmed,
                // Only "nothing left to close" disables it. No busy gate: rapid CLOSE taps must go
                // through, each one walking down to the next-newest ticket (target skips in-flight).
                enabled = hasTarget,
                colors = ButtonDefaults.outlinedButtonColors(
                    contentColor = if (hasTarget) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                ),
                // Horizontal padding 0 (the default 24.dp would clip the label); a little vertical
                // padding + heightIn(min) lets the button GROW to fit both lines under a large font
                // instead of the second line (ticket / price) overflowing onto the row below.
                contentPadding = PaddingValues(horizontal = 0.dp, vertical = 3.dp),
                modifier = Modifier.weight(1f).heightIn(min = d.actionH),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("CLOSE", fontWeight = FontWeight.Bold, fontSize = 14.sp)
                    // WHICH position is about to go. "Close one" without saying which one is an
                    // invitation to close the wrong rung of a pyramid.
                    Text(
                        if (hasTarget)
                            "${Fmt.price(armed.targetEntry, armed.digits)}  (${armed.count})"
                        else if (armed.isBuy) "no long" else "no short",
                        fontSize = 10.sp,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Button(
                onClick = onEnterArmed,
                enabled = canTrade,
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (armed.isBuy) Green else Red),
                contentPadding = PaddingValues(horizontal = 0.dp, vertical = 3.dp),
                modifier = Modifier.weight(1f).heightIn(min = d.actionH),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(if (armed.isBuy) "BUY" else "SELL",
                         fontWeight = FontWeight.Bold, fontSize = 16.sp)
                    // The price it will actually fill at: BUY lifts the ASK, SELL hits the BID.
                    Text(
                        Fmt.price(if (armed.isBuy) quote.ask else quote.bid, armed.digits),
                        fontSize = 10.sp, fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }

        // Burst indicator. Fixed height so it appearing/vanishing mid-scalp does not jitter the
        // layout and shove the grid; empty when nothing is on the wire.
        Row(
            Modifier.fillMaxWidth().height(14.dp),
            horizontalArrangement = Arrangement.End,
        ) {
            if (inFlight > 0) Text(
                "$inFlight sending…",
                fontSize = 10.sp,
                fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

/**
 * BUY ARMED / SELL ARMED.
 *
 * This is a MODE, and a mode that changes what a big button does is exactly how people end up in
 * the wrong trade. Two things keep it honest, and both are deliberate:
 *
 *  - It is LOUD. Full width, filled with the side's colour. You never have to tap to find out
 *    which way you are armed -- which matters because the setting PERSISTS across launches.
 *  - The buttons below it NEVER MOVE. Only their colour and label change. See OrderFormBlock.
 */
@Composable
private fun ArmedTabs(side: String, d: Dims, onPick: (String) -> Unit) {
    val isBuy = side != "sell"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        ArmedTab("BUY ARMED", selected = isBuy, tint = Green, h = d.tabH,
                 modifier = Modifier.weight(1f)) { onPick("buy") }
        ArmedTab("SELL ARMED", selected = !isBuy, tint = Red, h = d.tabH,
                 modifier = Modifier.weight(1f)) { onPick("sell") }
    }
}

@Composable
private fun ArmedTab(
    label: String,
    selected: Boolean,
    tint: Color,
    h: Dp,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Button(
        onClick = onClick,
        shape = RoundedCornerShape(6.dp),
        contentPadding = PaddingValues(0.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = if (selected) tint else MaterialTheme.colorScheme.surfaceVariant,
            contentColor = if (selected) Color.Black
            else MaterialTheme.colorScheme.onSurfaceVariant,
        ),
        modifier = modifier.heightIn(min = h),
    ) {
        Text(label, fontSize = 12.sp, maxLines = 1,
             fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
    }
}

@Composable
private fun BulkCloseBar(enabled: Boolean, d: Dims, onPick: (String) -> Unit) {
    // maxLines = 1 and contentPadding = 0: at the default padding "CLOSE LOSING" wrapped to two
    // lines, which silently made this bar half as tall again as it needed to be. Three buttons
    // wrapping is a surprising amount of the screen to lose to word-wrap.
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        // The word CLOSE stays on every one of them. These are destructive, and "LOSING" on its
        // own reads like a filter, not like a button that flattens your losers.
        BulkBtn("CLOSE ALL", MaterialTheme.colorScheme.primary, enabled, d, Modifier.weight(1f)) {
            onPick("all")
        }
        BulkBtn("CLOSE LOSING", Red, enabled, d, Modifier.weight(1f)) { onPick("losing") }
        BulkBtn("CLOSE PROFIT", Green, enabled, d, Modifier.weight(1f)) { onPick("profit") }
    }
}

@Composable
private fun BulkBtn(
    label: String,
    tint: Color,
    enabled: Boolean,
    d: Dims,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        contentPadding = PaddingValues(0.dp),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = tint),
        modifier = modifier.heightIn(min = d.bulkH),
    ) {
        Text(label, fontSize = 11.sp, fontWeight = FontWeight.Bold, maxLines = 1)
    }
}

/**
 * Server-enforced auto-close-all at a floating-P&L target.
 *
 * The SERVER owns the guard (it polls MT5 and closes the book when FLOATING crosses the target),
 * so the switch reflects `guard.enabled` from the snapshot, not local state. You set an amount and a
 * direction, then flip the switch to arm; while armed the fields lock (toggle off to change). It
 * STAYS armed after firing and re-arms once flat, so it keeps protecting until switched off. Because
 * an account switch disarms it server-side, the switch here flips off automatically on a switch.
 */
@Composable
private fun GuardBar(
    guard: GuardUi,
    floatingPl: Double?,
    d: Dims,
    onSetGuard: (Boolean?, Double?, String?) -> Unit,
) {
    val armed = guard.enabled
    var amount by rememberSaveable { mutableStateOf("") }
    var side by remember { mutableStateOf("profit") }
    // Sync the draft to SERVER truth whenever it is armed (another client, or the account-switch
    // reset). While disarmed the user's draft is left untouched.
    LaunchedEffect(armed, guard.side, guard.target) {
        if (armed) {
            side = guard.side
            amount = trimAmount(guard.target)
        }
    }

    val amt = amount.trim().toDoubleOrNull()
    val validAmt = amt != null && amt > 0
    val wouldFireNow = !armed && amt != null && amt > 0 && floatingPl != null &&
        (if (side == "loss") floatingPl <= -amt else floatingPl >= amt)

    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        // Row 1 — arm switch + side selection. Kept on its own line so the amount field below
        // gets the full column width; cramming all four into one row on the narrow SPLIT column
        // squeezed the field to an unusable sliver.
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Switch(
                checked = armed,
                onCheckedChange = { on ->
                    // Arming needs a valid amount; without one the toggle is ignored (server stays
                    // off, so the switch springs back) and the caption tells the user why.
                    if (on) { if (validAmt) onSetGuard(true, amt, side) }
                    else onSetGuard(false, null, null)
                },
            )
            GuardSideChip("PROFIT", side == "profit", Green, enabled = !armed) { side = "profit" }
            GuardSideChip("LOSS", side == "loss", Red, enabled = !armed) { side = "loss" }
        }
        // Row 2 — the target amount, now full width so it is actually tappable/readable.
        BareField(
            value = amount,
            onValueChange = { amount = it },
            height = d.fieldH,
            keyboardType = KeyboardType.Number,
            modifier = Modifier.fillMaxWidth(),
            placeholder = "close-all at…",
            enabled = !armed,
        )
        val (msg, msgColor) = when {
            armed -> ("Auto-close ALL at ${if (guard.side == "loss") "-" else "+"}" +
                "${trimAmount(guard.target)}  ·  FLOATING ${Fmt.signedMoney(floatingPl)}") to Green
            wouldFireNow ->
                "⚠ FLOATING already past this — arming will close ALL immediately" to Amber
            !validAmt ->
                "Close ALL when FLOATING reaches your target (server-enforced)" to
                    MaterialTheme.colorScheme.onSurfaceVariant
            else ->
                "Arm to close ALL when FLOATING hits ${if (side == "loss") "-" else "+"}${amount.trim()}" to
                    MaterialTheme.colorScheme.onSurfaceVariant
        }
        Text(msg, fontSize = 10.sp, color = msgColor, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun GuardSideChip(
    label: String,
    selected: Boolean,
    tint: Color,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Text(
        label,
        fontSize = 10.sp,
        fontWeight = FontWeight.Bold,
        color = if (selected) Color.Black else MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(if (selected) tint else MaterialTheme.colorScheme.surfaceVariant)
            .clickable(enabled = enabled) { onClick() }
            .padding(horizontal = 8.dp, vertical = 6.dp),
    )
}

/** 500.0 -> "500", 500.5 -> "500.5" -- no trailing ".0" on whole amounts. */
private fun trimAmount(v: Double): String =
    if (v == v.toLong().toDouble()) v.toLong().toString() else v.toString()

/**
 * SPLIT layout: controls in a left column, a stripped positions list (entry + P&L, swipe-to-close)
 * in a right column. Identity banner on top, totals footer at the bottom, both spanning. Pure
 * rearrangement — every control reuses the same callbacks the stacked layouts use.
 */
@Composable
private fun SplitBody(
    serverUrl: String,
    strategies: StrategiesUi,
    mode: LayoutMode,
    onCycleLayout: () -> Unit,
    onSettings: () -> Unit,
    onHistory: () -> Unit,
    health: Health,
    link: Link,
    onLogin: () -> Unit,
    live: Boolean,
    quote: Quote,
    form: OrderForm,
    canTrade: Boolean,
    digits: Int,
    onLot: (String) -> Unit,
    onStepLot: (Int) -> Unit,
    onSl: (String) -> Unit,
    onTp: (String) -> Unit,
    onPlace: (String) -> Unit,
    onBulkClose: (String) -> Unit,
    closing: Boolean,
    guard: GuardUi,
    account: AccountUi,
    onSetGuard: (Boolean?, Double?, String?) -> Unit,
    positions: PositionsUi,
    onClosePosition: (Long) -> Unit,
    closingTickets: Set<Long>,
    d: Dims,
) {
    Column(Modifier.fillMaxSize().padding(horizontal = 8.dp, vertical = 8.dp)) {
        ServerBar(
            serverUrl = serverUrl,
            strategyDot = strategies.items.any { it.enabled },
            strategyKilled = strategies.items.any { it.killed },
            mode = mode,
            showHistory = health.connected,
            onHistory = onHistory,
            onCycleLayout = onCycleLayout,
            onSettings = onSettings,
        )
        Spacer(Modifier.height(d.gap))
        StatusBanner(health, link, onLogin)
        Spacer(Modifier.height(d.gap))

        Row(Modifier.fillMaxWidth().weight(1f), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // LEFT — controls. Scrolls if a large font/zoom pushes them past the available height,
            // so they never overlap (the whole reason the stacked layouts got the font-scale work).
            Column(
                Modifier.weight(0.55f).fillMaxHeight().verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(d.gap),
            ) {
                QuoteBlock(quote, live, d, compact = true)
                CompactLotRow(form, d, onLot, onStepLot, fillField = true)
                CompactSlTpRow(form, digits, d, onSl, onTp)
                SplitEntryButtons(quote, digits, canTrade, d, onBuy = { onPlace("buy") }, onSell = { onPlace("sell") })
                SplitBulkCloses(enabled = !closing, d = d, onPick = onBulkClose)
                GuardBar(guard = guard, floatingPl = account.floatingPl, d = d, onSetGuard = onSetGuard)
            }
            // RIGHT — positions: entry price + P&L only, ✕ or swipe a row to close it.
            SplitPositions(
                state = positions,
                onClose = onClosePosition,
                connected = health.connected,
                live = live,
                closing = closingTickets,
                modifier = Modifier.weight(0.45f).fillMaxHeight(),
            )
        }
        Spacer(Modifier.height(d.gap))
        AccountStrip(account)
    }
}

/**
 * SPLIT's two DIRECT entry buttons: BUY buys (long), SELL sells (short) — no arming step. Each shows
 * its fill price (BUY→ask, SELL→bid). Ordering is async/non-blocking (vm.placeOrder). SPLIT-only.
 */
@Composable
private fun SplitEntryButtons(
    quote: Quote,
    digits: Int,
    canTrade: Boolean,
    d: Dims,
    onBuy: () -> Unit,
    onSell: () -> Unit,
) {
    val dg = quote.digits ?: digits
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        EntrySideButton("BUY", quote.ask, dg, Green, canTrade, d, Modifier.weight(1f), onBuy)
        EntrySideButton("SELL", quote.bid, dg, Red, canTrade, d, Modifier.weight(1f), onSell)
    }
}

@Composable
private fun EntrySideButton(
    label: String,
    price: Double?,
    digits: Int,
    tint: Color,
    canTrade: Boolean,
    d: Dims,
    modifier: Modifier,
    onClick: () -> Unit,
) {
    Button(
        onClick = onClick,
        enabled = canTrade,
        colors = ButtonDefaults.buttonColors(containerColor = tint),
        contentPadding = PaddingValues(horizontal = 0.dp, vertical = 3.dp),
        modifier = modifier.heightIn(min = d.actionH),
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(label, fontWeight = FontWeight.Bold, fontSize = 16.sp)
            Text(Fmt.price(price, digits), fontSize = 10.sp, fontFamily = FontFamily.Monospace)
        }
    }
}

/** Bulk closes, responsive: a row when the column is wide enough, stacked when it is narrow. */
@Composable
private fun SplitBulkCloses(enabled: Boolean, d: Dims, onPick: (String) -> Unit) {
    BoxWithConstraints {
        if (maxWidth >= 260.dp) {
            BulkCloseBar(enabled = enabled, d = d, onPick = onPick)
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(d.gap)) {
                BulkBtn("CLOSE ALL", MaterialTheme.colorScheme.primary, enabled, d, Modifier.fillMaxWidth()) { onPick("all") }
                BulkBtn("CLOSE LOSING", Red, enabled, d, Modifier.fillMaxWidth()) { onPick("losing") }
                BulkBtn("CLOSE PROFIT", Green, enabled, d, Modifier.fillMaxWidth()) { onPick("profit") }
            }
        }
    }
}

@Composable
private fun AccountStrip(a: AccountUi) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Stat(
            "FLOATING",
            Fmt.signedMoney(a.floatingPl),
            if ((a.floatingPl ?: 0.0) < 0) Red else Green,
            Modifier.weight(1f),
        )
        Stat("EQUITY", Fmt.money(a.equity), MaterialTheme.colorScheme.onSurface, Modifier.weight(1f))
        Stat("OPEN", a.openCount.toString(), MaterialTheme.colorScheme.onSurface, Modifier.weight(1f))
    }
}

@Composable
private fun Stat(label: String, value: String, color: Color, modifier: Modifier) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Text(label, fontSize = 9.sp, fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, fontFamily = FontFamily.Monospace, fontSize = 15.sp,
            fontWeight = FontWeight.Bold, color = color, textAlign = TextAlign.Center,
            maxLines = 1, softWrap = false)
    }
}
