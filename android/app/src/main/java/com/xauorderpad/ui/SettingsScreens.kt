package com.xauorderpad.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.net.StrategyStatus
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/**
 * Settings, and the strategy pages beneath it.
 *
 * ── Why this replaced a single dialog ──
 *
 * The old panel listed BOTH engines' enable switches and the ladder's PAPER switch in one
 * scrolling column. Nothing tied a switch to its engine, so the control that decides whether
 * REAL ORDERS go out sat one row away from an unrelated engine's toggle. Reading it wrong is
 * not a cosmetic mistake — it is arming the wrong trader, or going live believing you did not.
 *
 * The fix is structural, not decorative: an engine can be armed ONLY from its own page, where
 * there is exactly one engine to arm and PAPER unambiguously belongs to it. The list that gets
 * you there deliberately carries NO switches — it only reports, and navigates.
 */

// ─────────────────────────────────────────────────────────────────────────────
//  Settings root
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun SettingsScreen(
    serverUrl: String,
    strategies: StrategiesUi,
    live: Boolean,
    onStrategies: () -> Unit,
    onDisconnect: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BackHandler(onBack = onBack)
    var confirmDisconnect by remember { mutableStateOf(false) }

    val armed = strategies.items.count { it.enabled }
    val killed = strategies.items.any { it.killed }

    Column(modifier.fillMaxSize()) {
        TopBar("Settings", onBack)

        Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {

            SectionLabel("ENGINES")
            MenuRow(
                title = "Strategies",
                subtitle = when {
                    strategies.items.isEmpty() -> "none reported by the server"
                    armed > 0 -> "$armed of ${strategies.items.size} armed — trading may happen without you"
                    else -> "${strategies.items.size} engines · all off"
                },
                dot = when {
                    killed -> Red
                    armed > 0 -> Green
                    else -> null
                },
                onClick = onStrategies,
            )

            Spacer(Modifier.height(18.dp))
            SectionLabel("CONNECTION")

            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(12.dp)) {
                    Text("SERVER", fontSize = 10.sp, fontWeight = FontWeight.Bold,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(
                        serverUrl.removePrefix("http://").removePrefix("https://")
                            .ifBlank { "not set" },
                        fontFamily = FontFamily.Monospace, fontSize = 14.sp,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        if (live) "feed live" else "feed STALE — the screen is frozen",
                        fontSize = 11.sp,
                        fontWeight = if (live) FontWeight.Normal else FontWeight.Bold,
                        color = if (live) Green else Amber,
                    )
                }
            }

            Spacer(Modifier.height(10.dp))
            TextButton(onClick = { confirmDisconnect = true }) {
                Text("LOG OUT / CHANGE SERVER", color = Red, fontWeight = FontWeight.Bold)
            }
        }
    }

    if (confirmDisconnect) {
        AlertDialog(
            onDismissRequest = { confirmDisconnect = false },
            title = { Text("Disconnect from this server?") },
            text = {
                Text(
                    "You will go back to the Connect screen and the API token will be forgotten. " +
                        "The server address stays filled in.\n\n" +
                        "Any OPEN POSITIONS are NOT closed — they stay open on the server. " +
                        "Any ARMED STRATEGY also keeps running on the server: it lives there, " +
                        "not on this phone."
                )
            },
            confirmButton = {
                TextButton(onClick = { confirmDisconnect = false; onDisconnect() }) {
                    Text("DISCONNECT", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmDisconnect = false }) { Text("Cancel") }
            },
        )
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  The engine list — reports and navigates. It does NOT arm anything.
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun StrategiesScreen(
    strategies: StrategiesUi,
    live: Boolean,
    onOpen: (String) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BackHandler(onBack = onBack)

    Column(modifier.fillMaxSize()) {
        TopBar("Strategies", onBack)

        Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {
            Text(
                "These run on the SERVER, not on this phone — they keep running with the app " +
                    "closed, and with the phone off.",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (!live) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "⚠ Feed is stale — these are last-known states, and nothing can be armed.",
                    fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Amber,
                )
            }
            Spacer(Modifier.height(14.dp))

            if (strategies.items.isEmpty()) {
                Text("No strategies reported by the server.",
                     fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            for (s in strategies.items) {
                // Deliberately no Switch here. Arming happens on the engine's OWN page, where
                // there is exactly one engine it could possibly mean.
                MenuRow(
                    title = s.name.ifBlank { s.id },
                    subtitle = s.state,
                    subtitleMono = true,
                    badge = statusBadge(s),
                    dot = when {
                        s.killed -> Red
                        s.enabled -> Green
                        else -> null
                    },
                    onClick = { onOpen(s.id) },
                )
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}

/** LIVE is the one that can lose money unattended, so it is the one that is red. */
private fun statusBadge(s: StrategyStatus): Pair<String, Color>? = when {
    s.killed -> "KILLED" to Red
    !s.enabled -> null
    s.paper == true -> "PAPER" to Amber
    else -> "LIVE" to Red
}

// ─────────────────────────────────────────────────────────────────────────────
//  One engine, one page.
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun StrategyScreen(
    s: StrategyStatus?,
    quote: Quote,
    live: Boolean,
    onSet: (String, Boolean?, JsonObject) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BackHandler(onBack = onBack)

    if (s == null) {
        // The engine vanished from the feed (server restarted, or it was never there). Say so
        // rather than rendering an empty page with a live-looking switch on it.
        Column(modifier.fillMaxSize()) {
            TopBar("Strategy", onBack)
            Text("This engine is no longer reported by the server.",
                 Modifier.padding(16.dp), fontSize = 13.sp, color = Amber)
        }
        return
    }

    var confirmArm by remember { mutableStateOf(false) }
    var confirmPaperOff by remember { mutableStateOf(false) }

    val isLadder = s.paper != null

    Column(modifier.fillMaxSize()) {
        TopBar(s.name.ifBlank { s.id }, onBack)

        Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {

            if (!live) {
                Text(
                    "⚠ Feed is stale — controls disabled. You are looking at a frozen screen.",
                    fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Amber,
                )
                Spacer(Modifier.height(10.dp))
            }

            // ---- status ------------------------------------------------------
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(12.dp)) {
                    Text("STATUS", fontSize = 10.sp, fontWeight = FontWeight.Bold,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(s.state, fontFamily = FontFamily.Monospace, fontSize = 13.sp)

                    // Server-computed, from real measurements. Never swallow these.
                    s.warning?.let {
                        Spacer(Modifier.height(6.dp))
                        Text("⚠ $it", fontSize = 11.sp, color = Amber)
                    }
                    s.error?.let {
                        Spacer(Modifier.height(6.dp))
                        Text("⚠ $it", fontSize = 11.sp, color = Red)
                    }
                }
            }
            Spacer(Modifier.height(16.dp))

            // ---- the ONE switch that arms THIS engine -------------------------
            SectionLabel("ENGINE")
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(if (s.enabled) "ARMED" else "OFF",
                         fontSize = 15.sp, fontWeight = FontWeight.Bold,
                         color = if (s.enabled) Green else MaterialTheme.colorScheme.onSurface)
                    Text(
                        if (s.enabled) "The server is running this engine right now."
                        else "The server will not open anything for this engine.",
                        fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Switch(
                    checked = s.enabled,
                    enabled = live,
                    onCheckedChange = { want ->
                        if (want) confirmArm = true
                        else onSet(s.id, false, JsonObject(emptyMap()))
                    },
                )
            }

            if (isLadder) {
                Spacer(Modifier.height(20.dp))
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Spacer(Modifier.height(16.dp))

                SectionLabel("TRIGGER")
                LadderTrigger(s, quote, live, onSet)

                Spacer(Modifier.height(20.dp))
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Spacer(Modifier.height(16.dp))

                // PAPER is on the ENGINE'S OWN PAGE, so there is no question which engine it
                // belongs to. That ambiguity is exactly what the old shared dialog created.
                //
                // The TITLE names what the SWITCH does, and never the current state. Titling it
                // "PAPER" while the switch sat OFF read as "paper is turned off" -- i.e. exactly
                // backwards, on the one control that decides whether real money moves. The state
                // goes in the subtitle, where it cannot be confused for the switch's meaning.
                //
                // On = dangerous, the same direction as the ENGINE switch above.
                SectionLabel("ORDERS")
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            "LIVE ORDERS",
                            fontSize = 15.sp, fontWeight = FontWeight.Bold,
                            color = if (s.paper == true)
                                MaterialTheme.colorScheme.onSurfaceVariant else Red,
                        )
                        Text(
                            if (s.paper == true)
                                "OFF — paper mode. Logs what it would do, places NO orders."
                            else
                                "ON — placing REAL orders on the demo account.",
                            fontSize = 11.sp,
                            fontWeight = if (s.paper == true) FontWeight.Normal else FontWeight.Bold,
                            color = if (s.paper == true) Amber else Red,
                        )
                    }
                    Switch(
                        checked = s.paper != true,     // the switch means "live", not "paper"
                        enabled = live,
                        onCheckedChange = { wantLive ->
                            if (wantLive) confirmPaperOff = true
                            else onSet(s.id, null, buildJsonObject {
                                put("paper", JsonPrimitive(true))
                            })
                        },
                    )
                }
            } else {
                Spacer(Modifier.height(20.dp))
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                Spacer(Modifier.height(16.dp))
                SectionLabel("TUNING")
                Text(
                    "This engine has no phone-side settings. Its parameters (volume threshold, " +
                        "ATR stop, target R, hold time) are tuned in the web panel.",
                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Spacer(Modifier.height(24.dp))
        }
    }

    if (confirmArm) {
        // The APPLIED trigger, not whatever is in the text field — arming acts on what the
        // SERVER holds. If that level is already behind the market this engine does not wait
        // for anything: it enters on the next tick. This is the last point it can be stopped.
        val t = s.params?.trigger?.takeIf { it > 0.0 }
        val bid = quote.bid
        val armsNow = t != null && bid != null &&
            if ((s.params?.side ?: "sell") == "sell") bid < t else bid > t

        AlertDialog(
            onDismissRequest = { confirmArm = false },
            title = { Text("Arm ${s.name.ifBlank { s.id }}?") },
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
                        if (s.paper == true)
                            "Paper mode is ON — it will log what it would do and place NO orders."
                        else
                            "PAPER MODE IS OFF — this will place REAL orders on the demo account " +
                                "when it triggers."
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    onSet(s.id, true, JsonObject(emptyMap())); confirmArm = false
                }) {
                    Text("ARM", fontWeight = FontWeight.Bold,
                         color = if (s.paper == true) Green else Red)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmArm = false }) { Text("Cancel") }
            },
        )
    }

    if (confirmPaperOff) {
        AlertDialog(
            onDismissRequest = { confirmPaperOff = false },
            title = { Text("Place REAL orders?") },
            text = {
                Text(
                    "${s.name.ifBlank { s.id }} will place REAL orders on the demo account from " +
                        "the next trigger.\n\n" +
                        "Measured on 37,500 real ticks: with no directional edge this loses about " +
                        "one spread (0.24/oz) per trade, and the ladder multiplies that cost. Only " +
                        "your trigger can beat it — and paper mode is how you find out whether it does."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    onSet(s.id, null, buildJsonObject { put("paper", JsonPrimitive(false)) })
                    confirmPaperOff = false
                }) { Text("GO LIVE", color = Red, fontWeight = FontWeight.Bold) }
            },
            dismissButton = {
                TextButton(onClick = { confirmPaperOff = false }) { Text("Keep paper") }
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
 * number — which is exactly why a phone keyboard makes it dangerous.
 *
 * It is a WARNING, not a block. Entering immediately is sometimes precisely what is wanted.
 * But it must never be a surprise, and the trigger is never silently rewritten: a UI that
 * quietly "corrects" a price the user typed is worse than one that tells them what it will do.
 *
 * APPLY sends `enabled = null`, so setting a level can never ARM the engine as a side effect.
 * Arming stays one deliberate act: the switch above.
 */
@Composable
private fun LadderTrigger(
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

    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        SideChip("SELL", side == "sell", Red, live) { side = "sell" }
        Spacer(Modifier.width(6.dp))
        SideChip("BUY", side == "buy", Green, live) { side = "buy" }

        Spacer(Modifier.weight(1f))

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

    // Full width, and a big monospace face. At ~170dp this field CLIPPED: "4118.50" rendered
    // as "18.5" with the leading digits scrolled out of view. The price you are about to arm
    // is the one thing that must always be legible.
    OutlinedTextField(
        value = trig,
        onValueChange = { trig = it },
        enabled = live,
        singleLine = true,
        label = { Text("trigger price", fontSize = 10.sp) },
        textStyle = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        isError = crossed,
        modifier = Modifier.fillMaxWidth().padding(bottom = 6.dp),
    )

    if (crossed) {
        Text(
            "⚠ Trigger is ALREADY crossed (bid ${Fmt.price(bid, quote.digits ?: 2)}) — " +
                "arming will enter IMMEDIATELY, not wait.",
            fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Red,
        )
        Spacer(Modifier.height(6.dp))
    }

    s.spread?.let {
        Text("spread ${Fmt.price(it, 2)}/oz — every trade pays this",
             fontSize = 11.sp, fontFamily = FontFamily.Monospace,
             color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
//  shared bits
// ─────────────────────────────────────────────────────────────────────────────

@Composable
private fun TopBar(title: String, onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 8.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onBack) { Text("‹  BACK", fontSize = 12.sp) }
        Spacer(Modifier.width(4.dp))
        Text(title, fontSize = 16.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(text, fontSize = 10.sp, fontWeight = FontWeight.Bold,
         color = MaterialTheme.colorScheme.onSurfaceVariant,
         modifier = Modifier.padding(bottom = 6.dp))
}

@Composable
private fun MenuRow(
    title: String,
    subtitle: String,
    onClick: () -> Unit,
    subtitleMono: Boolean = false,
    badge: Pair<String, Color>? = null,
    dot: Color? = null,
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant),
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(title, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                    dot?.let {
                        Spacer(Modifier.width(6.dp))
                        Text("●", fontSize = 12.sp, color = it)
                    }
                }
                Text(
                    subtitle,
                    fontSize = 11.sp,
                    fontFamily = if (subtitleMono) FontFamily.Monospace else FontFamily.Default,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            badge?.let { (text, color) ->
                Text(text, fontSize = 10.sp, fontWeight = FontWeight.Bold, color = color,
                     modifier = Modifier.padding(end = 8.dp))
            }
            Text("›", fontSize = 20.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
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
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 2.dp),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = if (selected) tint.copy(alpha = 0.22f) else Color.Transparent,
            contentColor = if (selected) tint else MaterialTheme.colorScheme.onSurfaceVariant,
        ),
        modifier = Modifier.height(38.dp),
    ) {
        Text(label, fontSize = 12.sp,
             fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
    }
}
