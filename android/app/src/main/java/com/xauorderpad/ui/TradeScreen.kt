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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
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
import com.xauorderpad.net.StrategyStatus
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

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
    strategies: StrategiesUi,
    onSetStrategy: (String, Boolean?, JsonObject) -> Unit,
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
    var showStrategies by remember { mutableStateOf(false) }

    Column(modifier.fillMaxSize().padding(12.dp)) {
        ServerBar(
            serverUrl = serverUrl,
            confirmCloses = confirmCloses,
            onToggleConfirm = onToggleConfirm,
            onDisconnect = { confirmDisconnect = true },
            strategyDot = strategies.items.any { it.enabled },
            strategyKilled = strategies.items.any { it.killed },
            onStrategies = { showStrategies = true },
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

    if (showStrategies) {
        StrategiesDialog(
            strategies = strategies,
            quote = quote,
            live = live,
            onSet = onSetStrategy,
            onDismiss = { showStrategies = false },
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
 * The server-side strategy engines.
 *
 * The phone is a REMOTE CONTROL: it renders what the server says and asks it to
 * change. Every guard -- demo-only, hedging, target-vs-spread, the kill-switch --
 * lives on the server, where a stale phone or a forged request cannot get round it.
 *
 * Three rules this UI does enforce, because they are about what the user is told:
 *
 *  1. `live` gates arming. Arming a real trader from a screen whose prices are frozen
 *     is the exact scenario the stale-feed guard exists for.
 *  2. Turning PAPER off gets its OWN confirm, separate from enabling. It is the moment
 *     real orders begin -- a different decision, and folding the two into one dialog
 *     would let someone arm a live trader without ever being asked about it.
 *  3. `error` and `warning` come from real measurements on the server. Render them.
 */
@Composable
private fun StrategiesDialog(
    strategies: StrategiesUi,
    quote: Quote,
    live: Boolean,
    onSet: (String, Boolean?, JsonObject) -> Unit,
    onDismiss: () -> Unit,
) {
    var confirmArm by remember { mutableStateOf<StrategyStatus?>(null) }
    var confirmPaperOff by remember { mutableStateOf<StrategyStatus?>(null) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Strategies") },
        text = {
            // Scrollable: two engines plus the ladder's side/trigger row overflow a phone
            // dialog, and an AlertDialog CLIPS its body rather than scrolling it -- the PAPER
            // switch would simply be unreachable on a short screen.
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(
                    "These run on the SERVER, not on this phone — they keep running with the " +
                        "app closed.",
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (!live) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "⚠ Feed is stale — controls disabled. You are looking at a frozen screen.",
                        fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Amber,
                    )
                }
                Spacer(Modifier.height(10.dp))

                if (strategies.items.isEmpty()) {
                    Text("No strategies reported by the server.",
                         fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                for (s in strategies.items) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(s.name.ifBlank { s.id },
                                 fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Text(s.state, fontSize = 10.sp, fontFamily = FontFamily.Monospace,
                                 color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Switch(
                            checked = s.enabled,
                            enabled = live,
                            onCheckedChange = { want ->
                                if (want) confirmArm = s else onSet(s.id, false, JsonObject(emptyMap()))
                            },
                        )
                    }

                    // Ladder only. `paper != null` is the marker: engines without a paper mode
                    // do not have a trigger either.
                    if (s.paper != null) {
                        LadderControls(s, quote, live, onSet)

                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                if (s.paper) "PAPER — logs only, places NO orders"
                                else "LIVE — placing REAL orders",
                                Modifier.weight(1f),
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = if (s.paper) Amber else Red,
                            )
                            Switch(
                                checked = s.paper,
                                enabled = live,
                                onCheckedChange = { wantPaper ->
                                    if (!wantPaper) confirmPaperOff = s
                                    else onSet(s.id, null, buildJsonObject {
                                        put("paper", JsonPrimitive(true))
                                    })
                                },
                            )
                        }
                    }

                    // Server-computed, from real measurements. Never swallow these.
                    s.warning?.let {
                        Text("⚠ $it", fontSize = 10.sp, color = Amber)
                    }
                    s.error?.let {
                        Text("⚠ $it", fontSize = 10.sp, color = Red)
                    }
                    Spacer(Modifier.height(10.dp))
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    Spacer(Modifier.height(6.dp))
                }
                Text(
                    "Full tuning is in the web panel. This is a remote control.",
                    fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("CLOSE") } },
    )

    confirmArm?.let { s ->
        val livePaper = s.paper == false

        // The APPLIED trigger, not the one in the text field -- arming acts on what the server
        // holds. If that level is already behind the market, this engine does not wait for
        // anything: it enters on the next tick. Say so at the moment of arming, which is the
        // last point at which it can still be stopped.
        val t = s.params?.trigger?.takeIf { it > 0.0 }
        val bid = quote.bid
        val armsNow = t != null && bid != null &&
            if ((s.params?.side ?: "sell") == "sell") bid < t else bid > t

        AlertDialog(
            onDismissRequest = { confirmArm = null },
            title = { Text("Enable ${s.name.ifBlank { s.id }}?") },
            text = {
                Column {
                    if (armsNow) {
                        Text(
                            "⚠ The trigger is ALREADY crossed — this will ENTER IMMEDIATELY, " +
                                "not wait for a level.",
                            fontWeight = FontWeight.Bold, color = Red,
                        )
                        Spacer(Modifier.height(8.dp))
                    }
                    Text(
                        if (livePaper)
                            "PAPER MODE IS OFF — this will place REAL orders on the demo account " +
                                "when it triggers."
                        else if (s.paper == true)
                            "Paper mode is ON — it will log what it would do and place NO orders."
                        else
                            "It will place REAL orders on the demo account when it signals."
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    onSet(s.id, true, JsonObject(emptyMap())); confirmArm = null
                }) { Text("ENABLE", fontWeight = FontWeight.Bold,
                          color = if (livePaper) Red else Green) }
            },
            dismissButton = { TextButton(onClick = { confirmArm = null }) { Text("Cancel") } },
        )
    }

    confirmPaperOff?.let { s ->
        AlertDialog(
            onDismissRequest = { confirmPaperOff = null },
            title = { Text("Turn PAPER MODE off?") },
            text = {
                Text(
                    "It will place REAL orders on the demo account from the next trigger.\n\n" +
                        "Measured on 37,500 real ticks: with no directional edge this loses about " +
                        "one spread (0.24/oz) per trade, and the ladder multiplies that cost. Only " +
                        "your trigger can beat it — and paper mode is how you find out whether it does."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    onSet(s.id, null, buildJsonObject { put("paper", JsonPrimitive(false)) })
                    confirmPaperOff = null
                }) { Text("GO LIVE", color = Red, fontWeight = FontWeight.Bold) }
            },
            dismissButton = {
                TextButton(onClick = { confirmPaperOff = null }) { Text("Keep paper") }
            },
        )
    }
}

/**
 * Side + trigger for the ladder — the "SELL if it goes below 4119" part.
 *
 * ── The warning is the reason this exists ──
 *
 * `LadderState.on_tick` arms on `mark < trigger` for a sell. So a sell trigger ABOVE the
 * current bid is ALREADY crossed: it does not wait, it fires on the very next tick. Typing
 * 4200 while the bid is 4119 means "enter now", and it looks like a perfectly ordinary
 * number -- which is exactly why a phone keyboard makes it dangerous.
 *
 * It is a WARNING, not a block. Entering immediately is sometimes precisely what is wanted.
 * But it must never be a surprise, and the trigger is never silently rewritten: a UI that
 * quietly "corrects" a price the user typed is worse than one that tells them what it will do.
 *
 * APPLY sends `enabled = null`, so setting a level can never ARM the engine as a side effect.
 * Arming stays one deliberate act: the switch.
 */
@Composable
private fun LadderControls(
    s: StrategyStatus,
    quote: Quote,
    live: Boolean,
    onSet: (String, Boolean?, JsonObject) -> Unit,
) {
    // Keyed on the SERVER's values: a 5 Hz snapshot carrying the same trigger keeps the same
    // key, so it cannot wipe what is being typed. When the value genuinely changes -- an APPLY
    // landed, or the web panel moved it -- the key changes and the field re-seeds to the truth.
    var side by remember(s.params?.side) { mutableStateOf(s.params?.side ?: "sell") }
    var trig by remember(s.params?.trigger) {
        mutableStateOf(s.params?.trigger?.takeIf { it > 0.0 }?.let { Fmt.price(it, 2) } ?: "")
    }

    val typed = trig.trim().toDoubleOrNull()
    val valid = typed != null && typed > 0.0
    val bid = quote.bid

    // The bid is the mark for BOTH sides -- the chart is the bid, and the engine compares
    // against it. (XAUUSDm is a CFD: tick.last is 0.0 on every tick, there is no LTP.)
    val crossed = valid && bid != null &&
        if (side == "sell") bid < typed!! else bid > typed!!

    Row(Modifier.fillMaxWidth().padding(bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically) {

        SideChip("SELL", side == "sell", Red, live) { side = "sell" }
        Spacer(Modifier.width(6.dp))
        SideChip("BUY", side == "buy", Green, live) { side = "buy" }
        Spacer(Modifier.width(8.dp))

        OutlinedTextField(
            value = trig,
            onValueChange = { trig = it },
            enabled = live,
            singleLine = true,
            label = { Text("trigger", fontSize = 10.sp) },
            textStyle = MaterialTheme.typography.bodyMedium.copy(
                fontFamily = FontFamily.Monospace),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(6.dp))

        // Disabled rather than toasting: a blank or zero trigger is not an error to report,
        // it is simply nothing to send. The server would reject it anyway ("waiting: no
        // trigger price set"), and a round-trip to be told so is noise.
        TextButton(
            onClick = {
                onSet(s.id, null, buildJsonObject {
                    put("side", JsonPrimitive(side))
                    put("trigger", JsonPrimitive(typed))
                })
            },
            enabled = live && valid,
        ) { Text("APPLY", fontWeight = FontWeight.Bold) }
    }

    if (crossed) {
        Text(
            "⚠ Trigger is ALREADY crossed (bid ${Fmt.price(bid, quote.digits ?: 2)}) — " +
                "arming will enter IMMEDIATELY, not wait.",
            fontSize = 10.sp, fontWeight = FontWeight.Bold, color = Red,
            modifier = Modifier.padding(bottom = 6.dp),
        )
    }

    s.spread?.let {
        Text("spread ${Fmt.price(it, 2)}/oz — every trade pays this",
             fontSize = 10.sp, fontFamily = FontFamily.Monospace,
             color = MaterialTheme.colorScheme.onSurfaceVariant,
             modifier = Modifier.padding(bottom = 6.dp))
    }
}

@Composable
private fun SideChip(
    label: String,
    selected: Boolean,
    tint: Color,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        shape = RoundedCornerShape(6.dp),
        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 2.dp),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = if (selected) tint.copy(alpha = 0.22f) else Color.Transparent,
            contentColor = if (selected) tint else MaterialTheme.colorScheme.onSurfaceVariant,
        ),
        modifier = Modifier.height(36.dp),
    ) {
        Text(label, fontSize = 11.sp,
             fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
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
    strategyDot: Boolean,
    strategyKilled: Boolean,
    onStrategies: () -> Unit,
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

        // A strategy running on the SERVER is invisible from the phone unless we say
        // so. Green = something is armed and may be trading without you watching.
        TextButton(onClick = onStrategies) {
            Text("STRAT", fontSize = 11.sp)
            if (strategyDot || strategyKilled) {
                Spacer(Modifier.width(4.dp))
                Text("●", fontSize = 11.sp,
                     color = if (strategyKilled) Red else Green)
            }
        }

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
