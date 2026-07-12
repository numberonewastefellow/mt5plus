package com.xauorderpad.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
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
    onBuy: () -> Unit,
    onSell: () -> Unit,
    onCloseWhere: (String) -> Unit,
    onClosePosition: (Long) -> Unit,
    onLogin: () -> Unit,
    onDisconnect: () -> Unit,
    onToggleConfirm: (Boolean) -> Unit,
    confirmCloses: Boolean,
    serverUrl: String,
    /** Socket is Up. False => everything on this screen is a frozen last-known frame. */
    live: Boolean,
    modifier: Modifier = Modifier,
    closing: Boolean = false,
) {
    // Every bulk close is confirmed: they are irreversible and a single tap can flatten the
    // whole book. `confirm` holds the pending filter, or null.
    var confirm by remember { mutableStateOf<String?>(null) }

    // Disconnecting is confirmed too. It is not destructive to the BOOK -- open positions stay
    // open on the server, which the dialog says plainly, because a "logout" button on a trading
    // screen absolutely reads like it might flatten you.
    var confirmDisconnect by remember { mutableStateOf(false) }

    Column(modifier.fillMaxSize().padding(12.dp)) {
        ServerBar(
            serverUrl = serverUrl,
            confirmCloses = confirmCloses,
            onToggleConfirm = onToggleConfirm,
            onDisconnect = { confirmDisconnect = true },
        )
        Spacer(Modifier.height(6.dp))

        StatusBanner(health, link, onLogin)
        Spacer(Modifier.height(8.dp))

        QuoteBlock(quote, live)
        Spacer(Modifier.height(10.dp))

        // `live` gates ENTRY only. `health.healthy` alone is not enough: it is derived from the
        // last snapshot, which survives the socket's death -- so it still reports "healthy" from
        // a frame that may be minutes old, and BUY/SELL would stay armed against a frozen price.
        OrderFormBlock(form, canTrade = health.healthy && !busy && live, digits = positions.digits,
            onLot = onLot, onStepLot = onStepLot, onSl = onSl, onTp = onTp,
            onBuy = onBuy, onSell = onSell)
        Spacer(Modifier.height(10.dp))

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
        BulkCloseBar(enabled = !closing) { filter ->
            if (confirmCloses) confirm = filter else onCloseWhere(filter)
        }
        Spacer(Modifier.height(10.dp))

        AccountStrip(account)
        Spacer(Modifier.height(6.dp))

        PositionsGrid(
            state = positions,
            onClose = onClosePosition,
            connected = health.connected,
            live = live,
            // weight(1f), not fillMaxSize(): as the last child of a Column, fillMaxSize is a
            // fragile idiom that can fight the siblings for space.
            modifier = Modifier.weight(1f),
        )
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

    if (confirmDisconnect) {
        AlertDialog(
            onDismissRequest = { confirmDisconnect = false },
            title = { Text("Disconnect from this server?") },
            text = {
                Text(
                    "You will go back to the Connect screen and the API token will be forgotten. " +
                        "The server address stays filled in.\n\n" +
                        "Any OPEN POSITIONS are NOT closed — they stay open on the server."
                )
            },
            confirmButton = {
                TextButton(onClick = { confirmDisconnect = false; onDisconnect() }) {
                    Text("DISCONNECT", fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmDisconnect = false }) { Text("Cancel") }
            },
        )
    }
}

/**
 * Shows WHICH server this app is talking to, and the way out.
 *
 * Both halves are the point: without the address on screen there is no way to tell a phone
 * pointed at the right box from one pointed at a stale baked-in default, and without a logout
 * there is no way to change it.
 */
@Composable
private fun ServerBar(
    serverUrl: String,
    confirmCloses: Boolean,
    onToggleConfirm: (Boolean) -> Unit,
    onDisconnect: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth(),
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

        // Turning this OFF makes CLOSE ALL / CLOSE LOSING / CLOSE PROFIT fire on a single tap.
        // Coloured red when off, because "one tap flattens the book" is a state worth seeing.
        Text(
            "CONFIRM",
            fontSize = 10.sp,
            fontWeight = FontWeight.Bold,
            color = if (confirmCloses) MaterialTheme.colorScheme.onSurfaceVariant else Red,
        )
        Switch(
            checked = confirmCloses,
            onCheckedChange = onToggleConfirm,
            modifier = Modifier.scale(0.7f),
        )

        TextButton(onClick = onDisconnect) { Text("LOGOUT", fontSize = 11.sp) }
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
private fun QuoteBlock(q: Quote, live: Boolean) {
    Box(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().alpha(if (live) 1f else 0.35f),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            QuoteCell("BID", Fmt.price(q.bid, q.digits), Red, Modifier.weight(1f))

            Card(
                Modifier.width(88.dp),
                colors = CardDefaults.cardColors(MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(
                    Modifier.fillMaxWidth().padding(vertical = 10.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("SPREAD", fontSize = 9.sp, fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    // spreadPoints, NOT the raw `spread` field: the server sends a PRICE
                    // difference. Rendering it raw prints "0.22" where a trader expects "22".
                    Text(
                        Fmt.points(q.spreadPoints),
                        fontFamily = FontFamily.Monospace, fontSize = 18.sp, fontWeight = FontWeight.Bold,
                    )
                }
            }

            QuoteCell("ASK", Fmt.price(q.ask, q.digits), Green, Modifier.weight(1f))
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
private fun QuoteCell(label: String, value: String, color: Color, modifier: Modifier) {
    Card(modifier, colors = CardDefaults.cardColors(color.copy(alpha = 0.12f))) {
        Column(
            Modifier.fillMaxWidth().padding(vertical = 10.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(label, fontSize = 9.sp, fontWeight = FontWeight.Bold, color = color)
            Text(value, fontFamily = FontFamily.Monospace, fontSize = 22.sp,
                fontWeight = FontWeight.Bold, color = color)
        }
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
    onLot: (String) -> Unit,
    onStepLot: (Int) -> Unit,
    onSl: (String) -> Unit,
    onTp: (String) -> Unit,
    onBuy: () -> Unit,
    onSell: () -> Unit,
) {
    Column {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // contentPadding = 0 is load-bearing. Material's default button padding is 24.dp on
            // EACH side; inside a 48.dp-wide button that leaves ZERO width for the label, so the
            // glyph was clipped to nothing -- two blank circles that still worked when tapped.
            OutlinedButton(
                onClick = { onStepLot(-1) },
                contentPadding = PaddingValues(0.dp),
                modifier = Modifier.width(48.dp),
            ) { Text("−", fontSize = 20.sp, fontWeight = FontWeight.Bold) }

            OutlinedTextField(
                value = form.lot,
                onValueChange = onLot,
                label = { Text("LOT", fontSize = 10.sp) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                modifier = Modifier.weight(1f),
            )

            OutlinedButton(
                onClick = { onStepLot(1) },
                contentPadding = PaddingValues(0.dp),
                modifier = Modifier.width(48.dp),
            ) { Text("+", fontSize = 20.sp, fontWeight = FontWeight.Bold) }
        }

        Spacer(Modifier.height(6.dp))

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            // The labels say "points" on purpose. These are POINT DISTANCES, not prices -- the
            // server hardcodes sl_tp_mode="points". A field labelled just "SL" invites a price,
            // and a price entered here would be silently ACCEPTED as a distance, placing a stop
            // thousands of points away. Being accepted rather than rejected is what makes it
            // dangerous.
            OutlinedTextField(
                value = form.slPoints,
                onValueChange = onSl,
                label = { Text("SL (points)", fontSize = 10.sp) },
                placeholder = { Text("0 = none", fontSize = 11.sp) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                supportingText = { PointsHint(form.slPoints, digits) },
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = form.tpPoints,
                onValueChange = onTp,
                label = { Text("TP (points)", fontSize = 10.sp) },
                placeholder = { Text("0 = none", fontSize = 11.sp) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                supportingText = { PointsHint(form.tpPoints, digits) },
                modifier = Modifier.weight(1f),
            )
        }

        Spacer(Modifier.height(8.dp))

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = onSell,
                enabled = canTrade,
                colors = ButtonDefaults.buttonColors(containerColor = Red),
                modifier = Modifier.weight(1f).height(56.dp),
            ) { Text("SELL", fontWeight = FontWeight.Bold, fontSize = 17.sp) }

            Button(
                onClick = onBuy,
                enabled = canTrade,
                colors = ButtonDefaults.buttonColors(containerColor = Green),
                modifier = Modifier.weight(1f).height(56.dp),
            ) { Text("BUY", fontWeight = FontWeight.Bold, fontSize = 17.sp) }
        }
    }
}

@Composable
private fun BulkCloseBar(enabled: Boolean, onPick: (String) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        OutlinedButton(onClick = { onPick("all") }, enabled = enabled, modifier = Modifier.weight(1f)) {
            Text("CLOSE ALL", fontSize = 10.sp, fontWeight = FontWeight.Bold)
        }
        OutlinedButton(onClick = { onPick("losing") }, enabled = enabled, modifier = Modifier.weight(1f)) {
            Text("CLOSE LOSING", fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Red)
        }
        OutlinedButton(onClick = { onPick("profit") }, enabled = enabled, modifier = Modifier.weight(1f)) {
            Text("CLOSE PROFIT", fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Green)
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
            fontWeight = FontWeight.Bold, color = color, textAlign = TextAlign.Center)
    }
}
