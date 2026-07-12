package com.xauorderpad.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
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
    busy: Boolean,
    onLot: (String) -> Unit,
    onStepLot: (Int) -> Unit,
    onSl: (String) -> Unit,
    onTp: (String) -> Unit,
    onEnterArmed: () -> Unit,
    onCloseArmed: () -> Unit,
    onArmedSide: (String) -> Unit,
    armed: ArmedUi,
    onCloseWhere: (String) -> Unit,
    onClosePosition: (Long) -> Unit,
    onLogin: () -> Unit,
    onSettings: () -> Unit,
    /** Read-only here: the SWITCH lives in Settings now. This only decides whether to ask. */
    confirmCloses: Boolean,
    serverUrl: String,
    /** Socket is Up. False => everything on this screen is a frozen last-known frame. */
    live: Boolean,
    strategies: StrategiesUi,
    modifier: Modifier = Modifier,
    closing: Boolean = false,
) {
    // Every bulk close is confirmed: they are irreversible and a single tap can flatten the
    // whole book. `confirm` holds the pending filter, or null.
    var confirm by remember { mutableStateOf<String?>(null) }

    // Every control height is a FRACTION of the real screen height, not a fixed dp.
    //
    // The controls used to eat ~70% of the screen and the positions grid got whatever was left
    // -- about three rows. The grid is the thing you actually watch while a trade is on, so the
    // chrome above it has to yield. Fractions (not hardcoded dp) mean this holds on a small
    // phone and a tall one alike; the coerceIn bounds stop it from collapsing into an untappable
    // control on a tiny screen or ballooning on a tablet.
    BoxWithConstraints(modifier.fillMaxSize()) {
        val hPx = maxHeight
        fun frac(f: Float, min: Dp, max: Dp): Dp = (hPx * f).coerceIn(min, max)
        val d = Dims(
            gap = frac(0.007f, 4.dp, 10.dp),
            quotePad = frac(0.006f, 4.dp, 10.dp),
            priceSp = (hPx.value * 0.026f).coerceIn(17f, 22f).sp,
            tabH = frac(0.036f, 28.dp, 40.dp),
            fieldH = frac(0.052f, 40.dp, 54.dp),
            actionH = frac(0.068f, 52.dp, 64.dp),
            bulkH = frac(0.044f, 34.dp, 46.dp),
        )

    Column(Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 8.dp)) {
        ServerBar(
            serverUrl = serverUrl,
            strategyDot = strategies.items.any { it.enabled },
            strategyKilled = strategies.items.any { it.killed },
            onSettings = onSettings,
        )
        Spacer(Modifier.height(d.gap))

        StatusBanner(health, link, onLogin)
        Spacer(Modifier.height(d.gap))

        QuoteBlock(quote, live, d)
        Spacer(Modifier.height(d.gap))

        ArmedTabs(armed.side, d, onArmedSide)
        Spacer(Modifier.height(d.gap))

        // `live` gates ENTRY only. `health.healthy` alone is not enough: it is derived from the
        // last snapshot, which survives the socket's death -- so it still reports "healthy" from
        // a frame that may be minutes old, and BUY/SELL would stay armed against a frozen price.
        OrderFormBlock(form, canTrade = health.healthy && !busy && live, digits = positions.digits,
            armed = armed, quote = quote, busy = busy, d = d,
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
        BulkCloseBar(enabled = !closing, d = d) { filter ->
            if (confirmCloses) confirm = filter else onCloseWhere(filter)
        }
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

@Composable
private fun StatusBanner(h: Health, link: Link, onLogin: () -> Unit) {
    // Order matters: a connection problem MASKS a broker problem, so report the outermost one.
    // Showing "market closed" while the socket is actually dead would send the user hunting the
    // wrong fault.
    val (text, color, action) = when {
        link is Link.Unauthorized ->
            Triple("Token rejected — reconnect", Red, null)

        link is Link.Down ->
            Triple("Disconnected: ${link.reason} · retry ${link.retryInSec}s", Red, null)

        link is Link.Connecting ->
            Triple("Connecting…", Amber, null)

        h.loggedOut ->
            Triple("MT5 is logged out", Red, "LOG IN")

        !h.healthy ->
            Triple(h.error ?: "Trading not allowed", Red, null)

        // isDemo == false means a REAL account. This screen has three one-tap buttons that
        // flatten a book -- make it impossible to miss which account you are on.
        h.isDemo == false ->
            Triple("● REAL ACCOUNT — ${h.server.orEmpty()}", Red, null)

        else ->
            Triple("● DEMO · ${h.server ?: "connected"}", Green, null)
    }

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
 * The quote.
 *
 * When the feed is not live these numbers are FROZEN -- the last frame before the socket died --
 * but nothing about a price on a dark screen says so. The status banner alone is not enough: the
 * eye is on the digits, not the banner. So the cells dim and a STALE stamp sits across them. A
 * frozen price that still looks live is the actual hazard here, not the disconnection itself.
 */
@Composable
private fun QuoteBlock(q: Quote, live: Boolean, d: Dims) {
    Box(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().alpha(if (live) 1f else 0.35f),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            QuoteCell("BID", Fmt.price(q.bid, q.digits), Red, d, Modifier.weight(1f))

            Card(
                Modifier.width(78.dp),
                colors = CardDefaults.cardColors(MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(
                    Modifier.fillMaxWidth().padding(vertical = d.quotePad),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("SPREAD", fontSize = 9.sp, fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    // spreadPoints, NOT the raw `spread` field: the server sends a PRICE
                    // difference. Rendering it raw prints "0.22" where a trader expects "22".
                    Text(
                        Fmt.points(q.spreadPoints),
                        fontFamily = FontFamily.Monospace,
                        fontSize = d.priceSp * 0.82f,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }

            QuoteCell("ASK", Fmt.price(q.ask, q.digits), Green, d, Modifier.weight(1f))
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
private fun QuoteCell(label: String, value: String, color: Color, d: Dims, modifier: Modifier) {
    Card(modifier, colors = CardDefaults.cardColors(color.copy(alpha = 0.12f))) {
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

@Composable
private fun OrderFormBlock(
    form: OrderForm,
    canTrade: Boolean,
    digits: Int,
    armed: ArmedUi,
    quote: Quote,
    busy: Boolean,
    onLot: (String) -> Unit,
    onStepLot: (Int) -> Unit,
    onSl: (String) -> Unit,
    onTp: (String) -> Unit,
    onEnterArmed: () -> Unit,
    onCloseArmed: () -> Unit,
    d: Dims,
) {
    Column {
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
                enabled = hasTarget && !busy,
                colors = ButtonDefaults.outlinedButtonColors(
                    contentColor = if (hasTarget) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                ),
                // Zero padding: the default 8.dp top+bottom clipped the second line clean off,
                // so CLOSE showed no ticket and BUY showed no price -- the two facts those
                // buttons exist to tell you.
                contentPadding = PaddingValues(0.dp),
                modifier = Modifier.weight(1f).height(d.actionH),
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
                contentPadding = PaddingValues(0.dp),
                modifier = Modifier.weight(1f).height(d.actionH),
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
        modifier = modifier.height(h),
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
        modifier = modifier.height(d.bulkH),
    ) {
        Text(label, fontSize = 11.sp, fontWeight = FontWeight.Bold, maxLines = 1)
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
            fontWeight = FontWeight.Bold, color = color, textAlign = TextAlign.Center)
    }
}
