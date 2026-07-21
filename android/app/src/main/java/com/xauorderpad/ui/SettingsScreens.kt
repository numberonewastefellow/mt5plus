package com.xauorderpad.ui

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.net.RiderCard
import com.xauorderpad.net.StrategyStatus
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlin.math.abs

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
    confirmCloses: Boolean,
    layoutMode: LayoutMode,
    health: Health,
    certInfo: com.xauorderpad.data.CertStore.Info?,
    onToggleConfirm: (Boolean) -> Unit,
    onSelectLayout: (LayoutMode) -> Unit,
    onServers: () -> Unit,
    onAccounts: () -> Unit,
    onStrategies: () -> Unit,
    onCerts: () -> Unit,
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

            // The account comes FIRST: which account you are pointed at decides what every other
            // control on this phone will do with real money.
            SectionLabel("ACCOUNT")
            MenuRow(
                title = "MT5 Account",
                // Do not assert REAL/DEMO from a dead feed: `health` is the last frame, kept even
                // after the socket dies, so it may describe an account the terminal has since left.
                subtitle = when {
                    !live -> "no live data — status unknown"
                    health.loggedOut -> "LOGGED OUT — not trading"
                    health.isDemo == false -> "REAL ACCOUNT · ${health.server ?: "?"}"
                    health.isDemo == true -> "DEMO · ${health.server ?: "?"}"
                    else -> "checking…"
                },
                dot = when {
                    !live -> Amber
                    health.loggedOut -> Amber
                    health.isDemo == false -> Red
                    health.isDemo == true -> Green
                    else -> null
                },
                onClick = onAccounts,
            )

            Spacer(Modifier.height(18.dp))
            SectionLabel("TRADING")

            // Moved off the trade screen: it is a set-once preference, not something you work
            // mid-trade, and it was costing the positions grid a row.
            //
            // Turning it OFF makes CLOSE ALL / CLOSE LOSING / CLOSE PROFIT fire on a single tap.
            // That is the point (flattening fast in a spike), and it is also the whole risk, so
            // the OFF state is stated in red rather than left as a quiet toggle position.
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Confirm bulk closes", fontSize = 15.sp, fontWeight = FontWeight.Bold)
                    Text(
                        if (confirmCloses)
                            "CLOSE ALL / LOSING / PROFIT ask first."
                        else
                            "OFF — one tap flattens the book, no questions asked.",
                        fontSize = 11.sp,
                        fontWeight = if (confirmCloses) FontWeight.Normal else FontWeight.Bold,
                        color = if (confirmCloses) MaterialTheme.colorScheme.onSurfaceVariant
                        else Red,
                    )
                }
                Switch(checked = confirmCloses, onCheckedChange = onToggleConfirm)
            }

            Spacer(Modifier.height(18.dp))
            SectionLabel("SCREEN")
            Text(
                "Which trade layout the app opens to. Remembered across restarts.",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                LayoutMode.values().forEach { m ->
                    LayoutChip(
                        label = layoutLabel(m),
                        selected = m == layoutMode,
                        modifier = Modifier.weight(1f),
                    ) { onSelectLayout(m) }
                }
            }

            Spacer(Modifier.height(18.dp))
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
            MenuRow(
                title = "Server",
                subtitle = "switch between saved servers — verifies then connects",
                onClick = onServers,
            )

            Spacer(Modifier.height(10.dp))
            MenuRow(
                title = "Certificates (mTLS)",
                subtitle = certInfo?.let {
                    "loaded: ${it.clientCn} · CA ${it.caCn}"
                } ?: "none — required for the https EC2 box, not for the LAN server",
                dot = if (certInfo != null) Green else null,
                onClick = onCerts,
            )

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
//  Certificates — the EC2 mTLS client identity, uploaded at runtime.
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun CertsScreen(
    certInfo: com.xauorderpad.data.CertStore.Info?,
    error: String?,
    onSave: (ca: ByteArray, p12: ByteArray, password: String) -> Unit,
    onClear: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BackHandler(onBack = onBack)
    val ctx = LocalContext.current

    var caBytes by remember { mutableStateOf<ByteArray?>(null) }
    var p12Bytes by remember { mutableStateOf<ByteArray?>(null) }
    var caName by remember { mutableStateOf("") }
    var p12Name by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var confirmClear by remember { mutableStateOf(false) }

    fun readBytes(uri: android.net.Uri?): ByteArray? =
        uri?.let { ctx.contentResolver.openInputStream(it)?.use { s -> s.readBytes() } }

    // OpenDocument keeps a persistable read grant and returns a stable content Uri; we read the
    // bytes immediately into memory and never keep the Uri, so no persisted permission is needed.
    val pickCa = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        runCatching { readBytes(uri) }.getOrNull()?.let { caBytes = it; caName = uri?.lastPathSegment ?: "ca.crt" }
    }
    val pickP12 = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        runCatching { readBytes(uri) }.getOrNull()?.let { p12Bytes = it; p12Name = uri?.lastPathSegment ?: "client.p12" }
    }

    Column(modifier.fillMaxSize()) {
        TopBar("Certificates", onBack)
        Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {

            Text(
                "For the EC2 box (https, mutual TLS) only. The LAN server (plain http) needs none. " +
                    "Import the two files made by make_certs.py, then connect to https://<ip>:8443.",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "client.p12 is a TRADING CREDENTIAL — move it to the phone over USB, not email.",
                fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Amber,
            )

            Spacer(Modifier.height(16.dp))
            SectionLabel("CURRENTLY LOADED")
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(12.dp)) {
                    if (certInfo == null) {
                        Text("No certificate loaded.", fontSize = 13.sp)
                    } else {
                        Text("client: ${certInfo.clientCn}", fontFamily = FontFamily.Monospace, fontSize = 13.sp)
                        Text("CA: ${certInfo.caCn}", fontFamily = FontFamily.Monospace, fontSize = 12.sp,
                             color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("expires: ${java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date(certInfo.notAfter))}",
                             fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            if (certInfo != null) {
                Spacer(Modifier.height(6.dp))
                TextButton(onClick = { confirmClear = true }) {
                    Text("REMOVE CERTIFICATE", color = Red, fontWeight = FontWeight.Bold)
                }
            }

            Spacer(Modifier.height(18.dp))
            SectionLabel("IMPORT")

            error?.let {
                Card(colors = CardDefaults.cardColors(containerColor = Red.copy(alpha = 0.15f)),
                     modifier = Modifier.fillMaxWidth()) {
                    Text("⚠ $it", Modifier.padding(10.dp), fontSize = 12.sp,
                         fontWeight = FontWeight.Bold, color = Red)
                }
                Spacer(Modifier.height(8.dp))
            }

            OutlinedButton(onClick = { pickCa.launch(arrayOf("*/*")) },
                           modifier = Modifier.fillMaxWidth()) {
                Text(if (caName.isBlank()) "PICK ca.crt" else "ca.crt: $caName")
            }
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = { pickP12.launch(arrayOf("*/*")) },
                           modifier = Modifier.fillMaxWidth()) {
                Text(if (p12Name.isBlank()) "PICK client.p12" else "client.p12: $p12Name")
            }
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = password, onValueChange = { password = it },
                label = { Text("client.p12 password") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            Spacer(Modifier.height(14.dp))
            val ready = caBytes != null && p12Bytes != null && password.isNotEmpty()
            OutlinedButton(
                onClick = { onSave(caBytes!!, p12Bytes!!, password) },
                enabled = ready,
                modifier = Modifier.fillMaxWidth().height(48.dp),
                colors = ButtonDefaults.outlinedButtonColors(),
            ) {
                Text("LOAD CERTIFICATE", fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(24.dp))
        }
    }

    if (confirmClear) {
        AlertDialog(
            onDismissRequest = { confirmClear = false },
            title = { Text("Remove certificate?") },
            text = { Text("The phone will no longer be able to reach the EC2 (https) server until " +
                          "you import it again. The LAN (http) server is unaffected.") },
            confirmButton = {
                TextButton(onClick = { confirmClear = false; onClear() }) {
                    Text("REMOVE", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = { TextButton(onClick = { confirmClear = false }) { Text("Cancel") } },
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

/**
 * What this engine will DO if it signals, at a glance. Red is reserved for the one state that can
 * lose money unattended.
 *
 * The escalation is the whole point, so it is read from the engine's OWN `execution` field before
 * falling back to the ladder's paper flag. Inferring from `paper` alone gets the rider exactly
 * backwards in both directions: it has no `paper`, so an armed suggestion-only rider — which places
 * NOTHING — fell to `else` and showed a red LIVE, and it showed that same red LIVE once `auto_real`
 * was on. The badge that exists to separate "harmless" from "spending real money with nobody
 * watching" could not separate them. Same root cause as the `s.paper != null` gate on the page below.
 */
private fun statusBadge(s: StrategyStatus): Pair<String, Color>? = when {
    s.killed -> "KILLED" to Red
    !s.enabled -> null
    // Engines that describe their own execution mode are believed; nothing is inferred for them.
    s.execution == "AUTO-REAL" -> "REAL $" to Red      // places, with real money
    s.execution == "auto-demo" -> "AUTO" to Amber      // places, demo money
    s.execution == "suggest" -> "SUGGEST" to Green     // armed, but places nothing by itself
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
    /** Which account the server is actually on. This page can arm REAL trading; it must say so. */
    health: Health,
    onSet: (String, Boolean?, JsonObject) -> Unit,
    /**
     * The rider's PLACE tap: (card, pointSize). Fired ONLY from the confirm dialog below.
     * The point size -- not digits -- is what converts the card's prices into the POINT
     * distances /order expects.
     */
    onPlaceCard: (RiderCard, Double?) -> Unit,
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
    var showPlaceConfirm by remember { mutableStateOf(false) }
    // Each execution switch gets its OWN confirm. "I trust it on demo" and "I trust it with
    // my money" are different decisions; one dialog covering both is how the second one
    // happens by accident.
    var confirmAutoDemo by remember { mutableStateOf(false) }
    var confirmAutoReal by remember { mutableStateOf(false) }

    // Branch on the engine's ID, not on the shape of its payload. `s.paper != null` used to stand
    // in for "this is the ladder" -- true only for as long as the ladder was the sole engine with
    // a paper flag. The rider reports `suggestion_only` and no `paper` at all, so that test now
    // silently classifies it as "some other engine" and would keep doing so for whatever ships
    // next. The id is what the server actually keys these by.
    val engineId = s.id

    // The confirm names ONE specific suggestion. If the engine publishes a different trade (or
    // withdraws it) while the dialog is open, drop the dialog: a tap must never confirm numbers
    // the user never read. It also stops a stale `true` from re-opening the dialog by itself
    // when the next card lands.
    //
    // Keyed on the TRADE fields plus `actionable`, not on the whole card: rider.py refreshes
    // `status`/`note` on every hold bar while keeping the same entry card, and keying on those
    // would close the dialog under the user once a bar for no reason. `actionable` IS included --
    // when the suggestion expires the dialog must go with it.
    LaunchedEffect(s.card?.kind, s.card?.side, s.card?.lot, s.card?.entry, s.card?.sl, s.card?.tp,
                   s.actionable) {
        showPlaceConfirm = false
    }

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

            // WHICH ACCOUNT. The web panel has always carried this banner on its strategy
            // page; the phone had no counterpart, so you could arm an engine from a screen
            // that never said whose money was behind it. That is merely untidy for a
            // demo-only engine and unacceptable now that one of them can be switched to
            // real. Unknown is shown as unknown -- never assumed to be demo.
            AccountBanner(health)
            Spacer(Modifier.height(10.dp))

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

            when (engineId) {
                "ladder" -> {
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

                    Spacer(Modifier.height(20.dp))
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    Spacer(Modifier.height(16.dp))

                    // The BOOK, not the tuning. The pyramid can be uncapped, so "how big is
                    // this right now" is the one thing an operator away from the desk actually
                    // needs -- and the stop it will be closed at. Every number here is derived
                    // server-side so the phone cannot disagree with the web panel.
                    SectionLabel("BOOK")
                    LadderBook(s, quote.digits ?: 2)
                }

                // The rider has NO paper/live switch. It has two EXECUTION switches instead,
                // one per account class, and with both off it publishes a card and places
                // nothing -- the PLACE button below is then the only thing on this phone that
                // can turn a suggestion into an order, and it needs a tap plus a confirm.
                "rider" -> {
                    Spacer(Modifier.height(20.dp))
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    Spacer(Modifier.height(16.dp))

                    SectionLabel("EXECUTION")
                    RiderExecution(
                        s = s,
                        live = live,
                        onWantAutoDemo = { want ->
                            // Only the dangerous direction is confirmed. Turning it OFF goes
                            // straight through -- never stand between a user and the brakes.
                            if (want) confirmAutoDemo = true
                            else onSet(s.id, null, buildJsonObject {
                                put("auto_demo", JsonPrimitive(false))
                            })
                        },
                        onWantAutoReal = { want ->
                            if (want) confirmAutoReal = true
                            else onSet(s.id, null, buildJsonObject {
                                put("auto_real", JsonPrimitive(false))
                            })
                        },
                    )

                    Spacer(Modifier.height(20.dp))
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    Spacer(Modifier.height(16.dp))

                    SectionLabel("TRADE NOW")
                    RiderTradeNow(s, quote.digits ?: 2, live) { showPlaceConfirm = true }

                    Spacer(Modifier.height(20.dp))
                    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                    Spacer(Modifier.height(16.dp))

                    SectionLabel("PAPER RECORD")
                    RiderPaperRecord(s)

                    Spacer(Modifier.height(16.dp))
                    Text(
                        "The paper record is what the SIGNAL would have made, tracked whether or " +
                            "not anything was traded — it stays clean of slippage and fill luck, " +
                            "which is what makes it worth reading. Parameters (thrust multiple, " +
                            "stop, trail, hold) are tuned in the web panel.",
                        fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }

                else -> {
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
                        when {
                            // The rider is suggestion-only: arming it cannot send an order, so
                            // saying "REAL orders when it triggers" here would simply be false.
                            engineId == "rider" ->
                                "This engine only SUGGESTS. Armed, it publishes a trade card and " +
                                    "places NOTHING — an order goes out only when you tap PLACE " +
                                    "and confirm it."
                            s.paper == true ->
                                "Paper mode is ON — it will log what it would do and place NO orders."
                            else ->
                                "PAPER MODE IS OFF — this will place REAL orders on the demo account " +
                                    "when it triggers."
                        }
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    onSet(s.id, true, JsonObject(emptyMap())); confirmArm = false
                }) {
                    Text("ARM", fontWeight = FontWeight.Bold,
                         color = if (s.paper == true || engineId == "rider") Green else Red)
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
                        "one spread per trade, and the ladder multiplies that cost. Only " +
                        "your trigger can beat it — and paper mode is how you find out whether it does.\n\n" +
                        "Measured LIVE on 2026-07-21: 68 ladders and 79 trades in 25 minutes, " +
                        "13.9% win rate, and the target was never once reached." +
                        // The one combination with no ceiling at all. Worth its own sentence:
                        // a slow grind adds rungs faster than the target drains them.
                        if ((s.params?.maxPositions ?: 1) == 0 && (s.params?.maxLots ?: 0.0) <= 0.0)
                            "\n\nRungs AND lots are both uncapped. Set a lot cap before going live."
                        else ""
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

    // ---- the rider's PLACE confirmation ------------------------------------
    //
    // Re-read from the LIVE feed rather than captured at tap time, and re-gated on the server's
    // `actionable` : between the tap and the confirm the engine may have withdrawn the card or
    // its bar may have passed, and a dialog is not a licence to send a trade that no longer exists.
    val placeCard = s.card?.takeIf { s.actionable == true }
    if (showPlaceConfirm && placeCard != null) {
        // `digits` FORMATS the numbers shown here. It must never be used to CONVERT them --
        // that is `quote.point`'s job, and conflating the two put a 10x-too-tight stop on a
        // live order once already (see TradingViewModel.placeRiderCard).
        val digits = quote.digits ?: 2
        // DISTANCES, not the prices already shown on the card above: distance is what /order
        // actually receives (POINT distances), and it is what tells you how much this trade can
        // lose. A trader can read "SL 2.10 away" as risk; "SL 4117.90" needs mental arithmetic
        // against a price that is moving while they do it.
        val entry = placeCard.entry
        val slAway = if (entry != null && (placeCard.sl ?: 0.0) != 0.0) abs(entry - placeCard.sl!!) else null
        val tpAway = if (entry != null && (placeCard.tp ?: 0.0) != 0.0) abs(placeCard.tp!! - entry) else null
        val tint = if (placeCard.isBuy) Green else Red

        AlertDialog(
            onDismissRequest = { showPlaceConfirm = false },
            title = {
                Text("Place ${(placeCard.side ?: "").uppercase()} ${Fmt.lot(placeCard.lot)} at market?")
            },
            text = {
                Column {
                    Text(
                        "SL ${slAway?.let { Fmt.price(it, digits) } ?: "none"} away  ·  " +
                            "TP ${tpAway?.let { Fmt.price(it, digits) } ?: "none"} away",
                        fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                    )
                    Spacer(Modifier.height(8.dp))
                    // Name the account CLASS from the feed, not a hardcoded "demo": this screen
                    // can now be looking at a real account, and a dialog that says "demo" while
                    // real money is behind the button would be the worst possible lie to tell.
                    Text(
                        if (health.isDemo == false)
                            "This places a REAL order with REAL MONEY, at MARKET."
                        else
                            "This places a REAL order on the DEMO account, at MARKET — not a " +
                                "paper entry. The rider only suggests; you are the trigger.",
                        fontWeight = FontWeight.Bold,
                        color = if (health.isDemo == false) Red else Amber,
                    )
                    if (slAway == null) {
                        Spacer(Modifier.height(8.dp))
                        Text("⚠ No stop loss on this card — it will go in NAKED.",
                             fontWeight = FontWeight.Bold, color = Red)
                    }
                    if (quote.point == null || quote.point <= 0.0) {
                        Spacer(Modifier.height(8.dp))
                        Text("⚠ The symbol's point size is unknown — the stop distance cannot " +
                             "be computed, so nothing will be sent.",
                             fontWeight = FontWeight.Bold, color = Red)
                    }
                }
            },
            confirmButton = {
                TextButton(
                    // Fail closed rather than guess a point size. Every wrong answer here is
                    // wrong by a factor of ten.
                    enabled = quote.point != null && quote.point > 0.0,
                    onClick = {
                        showPlaceConfirm = false
                        onPlaceCard(placeCard, quote.point)
                    },
                ) { Text("PLACE", color = tint, fontWeight = FontWeight.Bold) }
            },
            dismissButton = {
                TextButton(onClick = { showPlaceConfirm = false }) { Text("Cancel") }
            },
        )
    }

    // ---- the two execution switches ----------------------------------------
    //
    // Separate dialogs, because they are separate decisions. Both name what changes and what
    // the evidence actually is; the dismiss button is the SAFE action by name, not "Cancel",
    // so the harmless choice reads as a choice rather than an escape.
    if (confirmAutoDemo) {
        AlertDialog(
            onDismissRequest = { confirmAutoDemo = false },
            title = { Text("Auto-trade on DEMO accounts?") },
            text = {
                Column {
                    Text("The rider will place orders BY ITSELF whenever it signals — no card, " +
                         "no tap, no per-trade confirmation.")
                    Spacer(Modifier.height(8.dp))
                    Text("A stop is attached at the broker on entry, and the trail runs on the " +
                         "server. Demo money only.",
                         color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                    Spacer(Modifier.height(8.dp))
                    Text("This is how you build the forward-test record before trusting it with " +
                         "anything real.", fontWeight = FontWeight.Bold, color = Amber)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmAutoDemo = false
                    onSet(s.id, null, buildJsonObject { put("auto_demo", JsonPrimitive(true)) })
                }) { Text("AUTO-TRADE DEMO", fontWeight = FontWeight.Bold, color = Amber) }
            },
            dismissButton = {
                TextButton(onClick = { confirmAutoDemo = false }) { Text("Keep suggesting") }
            },
        )
    }

    if (confirmAutoReal) {
        AlertDialog(
            onDismissRequest = { confirmAutoReal = false },
            title = { Text("Auto-trade with REAL MONEY?") },
            text = {
                Column {
                    Text("THIS SPENDS REAL MONEY, unattended, with no confirmation per trade.",
                         fontWeight = FontWeight.Bold, color = Red)
                    Spacer(Modifier.height(8.dp))
                    // The measured truth, not an adjective. The one number that matters about
                    // this strategy is that its edge is not distinguishable from zero.
                    Text("What is actually known: backtested +\$0.585/oz over random, with a " +
                         "confidence interval that INCLUDES ZERO. It is regime-dependent — it " +
                         "needs volatility, and a calm market bleeds. It is not a proven edge.")
                    Spacer(Modifier.height(8.dp))
                    Text("The only cap is the daily-loss kill-switch" +
                         (s.params?.maxDailyLoss?.let { " (\$${Fmt.price(it, 0)})" } ?: "") +
                         ". Position size follows risk × equity, so a bigger balance means a " +
                         "bigger trade.",
                         color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                    Spacer(Modifier.height(8.dp))
                    Text("This switch never resumes by itself after a restart.",
                         fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmAutoReal = false
                    onSet(s.id, null, buildJsonObject { put("auto_real", JsonPrimitive(true)) })
                }) { Text("TRADE REAL MONEY", fontWeight = FontWeight.Bold, color = Red) }
            },
            dismissButton = {
                TextButton(onClick = { confirmAutoReal = false }) { Text("Keep suggesting") }
            },
        )
    }
}

/**
 * Which account the server is attached to, stated plainly at the top of a strategy page.
 *
 * The one thing this must never do is imply "demo" when it does not know. `isDemo` is
 * nullable precisely because the feed may not have said yet, and an engine armed against an
 * assumed-demo account that turns out to be real is the exact accident the whole demo gate
 * exists to prevent.
 */
@Composable
private fun AccountBanner(health: Health) {
    val (text, tint) = when (health.isDemo) {
        true -> "DEMO ACCOUNT — strategies place demo orders here." to Green
        false -> "● REAL ACCOUNT — orders here spend REAL MONEY." to Red
        null -> "Account type unknown — not connected yet." to Amber
    }
    Text(
        text,
        Modifier.fillMaxWidth()
            .background(tint.copy(alpha = 0.12f), RoundedCornerShape(4.dp))
            .padding(horizontal = 8.dp, vertical = 5.dp),
        fontSize = 11.sp, fontWeight = FontWeight.Bold, color = tint,
    )
}

/**
 * The rider's two EXECUTION switches — the only controls in this app that can make an engine
 * trade without a per-trade tap.
 *
 * They are split by ACCOUNT CLASS rather than being one "auto" switch because the server
 * re-reads them against the connected account on every poll: moving MT5 from a demo to a real
 * account then changes what the engine may do immediately, with no restart and no re-arm, and
 * it changes it in the safe direction unless the operator had already said yes to real.
 *
 * Reads `s.params`, i.e. what the server actually SAVED — never local optimistic state — so a
 * write that was refused shows up as a switch that did not move.
 */
@Composable
private fun RiderExecution(
    s: StrategyStatus,
    live: Boolean,
    onWantAutoDemo: (Boolean) -> Unit,
    onWantAutoReal: (Boolean) -> Unit,
) {
    val autoDemo = s.params?.autoDemo == true
    val autoReal = s.params?.autoReal == true

    // What it will actually do next, as decided on the server. Rendered, never recomputed.
    val mode = s.execution ?: "suggest"
    val modeTint = when (mode) {
        "AUTO-REAL" -> Red
        "auto-demo" -> Amber
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text(
        when (mode) {
            "AUTO-REAL" -> "Placing automatically with REAL money."
            "auto-demo" -> "Placing automatically on the demo account."
            "off" -> "Not armed — no signals are being acted on."
            else -> "Suggestion only — it publishes a card and you tap PLACE."
        },
        fontSize = 12.sp, fontWeight = FontWeight.Bold, color = modeTint,
    )
    Spacer(Modifier.height(12.dp))

    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("AUTO-TRADE ON DEMO", fontSize = 15.sp, fontWeight = FontWeight.Bold,
                 color = if (autoDemo) Amber else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                if (autoDemo) "ON — places demo orders by itself, no tap."
                else "OFF — suggests only while on a demo account.",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(checked = autoDemo, enabled = live, onCheckedChange = onWantAutoDemo)
    }

    Spacer(Modifier.height(12.dp))

    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("AUTO-TRADE ON REAL", fontSize = 15.sp, fontWeight = FontWeight.Bold,
                 color = if (autoReal) Red else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                if (autoReal) "ON — places orders with REAL MONEY, unattended."
                else "OFF — suggests only while on a real account.",
                fontSize = 11.sp,
                fontWeight = if (autoReal) FontWeight.Bold else FontWeight.Normal,
                color = if (autoReal) Red else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(checked = autoReal, enabled = live, onCheckedChange = onWantAutoReal)
    }

    if (s.liveTicket != null) {
        Spacer(Modifier.height(12.dp))
        Text(
            "Riding live ticket ${s.liveTicket} — trailing stop at ${s.liveStop ?: "—"}. " +
                "The stop attached at the broker is the backstop if this server dies.",
            fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Amber,
        )
    }
}

/**
 * The rider's live TRADE-NOW card.
 *
 * A SUGGESTION, and nothing more: the button below it is the only thing that can turn it into an
 * order, and it goes through the confirm dialog on the way. Shown only while the SERVER says the
 * suggestion is still actionable — a card whose bar has passed, an engine that is disabled, or one
 * that is already placing for itself must not offer a button.
 */
@Composable
private fun RiderTradeNow(
    s: StrategyStatus,
    digits: Int,
    live: Boolean,
    onPlace: () -> Unit,
) {
    val c = s.card
    // `s.actionable` is the SERVER's answer (rider._actionable): issued on the newest closed
    // bar, engine armed, and not already auto-placing. Do not substitute `c.isActionable` --
    // the card keeps kind:"enter" for the whole trade, so that test would keep this button
    // live on an entry price up to two hours stale, and the web panel would disagree.
    if (c != null && s.actionable == true) {
        val tint = if (c.isBuy) Green else Red
        Card(
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text((c.side ?: "").uppercase(), fontSize = 18.sp,
                         fontWeight = FontWeight.Bold, color = tint)
                    Spacer(Modifier.width(8.dp))
                    Text("${Fmt.lot(c.lot)} lot", fontSize = 15.sp, fontWeight = FontWeight.Bold,
                         fontFamily = FontFamily.Monospace)
                }
                Spacer(Modifier.height(6.dp))
                // "~" on the entry deliberately: the card was computed at the last bar close, and
                // the fill will be at whatever market is when the tap lands. It is a reference
                // level, not a limit price.
                Text("entry ~${Fmt.price(c.entry, digits)}",
                     fontFamily = FontFamily.Monospace, fontSize = 13.sp)
                Text(
                    "SL ${Fmt.price(c.sl, digits)}    TP ${Fmt.price(c.tp, digits)}",
                    fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                c.reason?.takeIf { it.isNotBlank() }?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(it, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Button(
            onClick = onPlace,
            // Same gate as the trade screen's entry buttons: a frozen feed means the price this
            // card was built on may be long gone. The ViewModel checks `live` again anyway.
            enabled = live && s.enabled && c.isActionable,
            colors = ButtonDefaults.buttonColors(containerColor = tint),
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            Text("PLACE THIS TRADE", fontWeight = FontWeight.Bold, color = Color.White)
        }
    } else {
        // The same idle messages, in the same order, as the web panel — so the two clients
        // read identically rather than each inventing its own wording for the same state.
        Text(
            when {
                s.liveTicket != null ->
                    "Riding a LIVE position (ticket ${s.liveTicket}, stop ${s.liveStop ?: "—"}). " +
                        "The engine trails it on every tick and exits at market."
                s.execution == "auto-demo" || s.execution == "AUTO-REAL" ->
                    "Auto-trading is ON (${s.execution}) — the next signal is placed for you, " +
                        "so there is nothing to tap."
                c != null && c.kind == "close" ->
                    "Last exit: ${c.reason ?: "—"} (paper ${Fmt.signedMoney(c.pnlOz)}/oz). " +
                        "Waiting for the next thrust."
                c != null && c.kind == "enter" && c.status != null ->
                    "That suggestion has expired (it was only good for its own bar). " +
                        "Waiting for the next thrust."
                s.inPaperPosition == true ->
                    "In a paper position — a CLOSE card will appear on exit."
                else ->
                    c?.note
                        ?: "No trade signalled right now — waiting for a thrust in a high-vol regime."
            },
            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/**
 * What the rider WOULD have made, tracked by the engine itself. None of it was traded — it is the
 * evidence you use to decide whether to start tapping PLACE, and it must never read as account P&L.
 */
@Composable
private fun RiderPaperRecord(s: StrategyStatus) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp)) {
            KvRow("Paper P&L (USD/oz)", Fmt.signedMoney(s.paperPnlPerOz),
                  (s.paperPnlPerOz ?: 0.0) < 0)
            // Accrued at the lot each trade was SIZED at, not the running $/oz total rescaled by
            // whatever lot the current card carries -- which is what the old label described.
            KvRow("Paper P&L (USD)", Fmt.signedMoney(s.paperPnlUsd),
                  (s.paperPnlUsd ?: 0.0) < 0)
            KvRow("Paper trades", "${s.paperTrades ?: 0}")
            KvRow("In a paper position", if (s.inPaperPosition == true) "yes" else "no")
            s.note?.takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(6.dp))
                Text(it, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/**
 * The ladder's OPEN BOOK, as the server reports it.
 *
 * Nothing here is recomputed on the phone. `effective_stop` in particular is a
 * server-derived number: the stop is measured on the entry-side price but realised on
 * the exit side, so a 0.30 dial costs 0.34 at a 0.04 spread. Showing the raw parameter
 * instead was how a stop looked tighter on screen than it ever was in the account.
 *
 * `max_positions` and `max_lots` use 0 for UNCAPPED, which is why they are rendered
 * through [cap] rather than printed -- "0" would read as "none allowed", the exact
 * opposite of what it means.
 */
@Composable
private fun LadderBook(s: StrategyStatus, digits: Int) {
    val p = s.params
    val capRungs = (p?.maxPositions ?: 0).let { if (it <= 0) "uncapped" else "$it" }
    val capLots = (p?.maxLots ?: 0.0).let { if (it <= 0.0) "uncapped" else Fmt.lot(it) }
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp)) {
            KvRow("Open rungs", "${s.openPositions ?: 0}")
            KvRow("Open lots", Fmt.lot(s.openLots ?: 0.0))
            KvRow("Cap (rungs / lots)", "$capRungs / $capLots")
            if (p?.stopMode == "floor") {
                KvRow("Stop", "floor @ ${Fmt.price(s.floorPrice, digits)}")
            } else {
                KvRow("Stop", "trail ${Fmt.money(p?.retrace)}/oz from the extreme")
            }
            KvRow("Effective stop (incl. spread)", "${Fmt.money(s.effectiveStop)}/oz")
            KvRow("Ladders today", "${s.laddersToday ?: 0}")
            // A flush drains in batches so a big book cannot freeze the worker's poll loop.
            // Counting DOWN is the healthy state; say so, or a non-zero number reads as stuck.
            (s.flushRemaining ?: 0).takeIf { it > 0 }?.let {
                Spacer(Modifier.height(6.dp))
                Text("Closing $it position(s) — draining in batches so the trading loop keeps running.",
                     fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Amber)
            }
        }
    }
}

@Composable
private fun KvRow(label: String, value: String, negative: Boolean = false) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), fontSize = 12.sp,
             color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, fontSize = 13.sp, fontFamily = FontFamily.Monospace,
             fontWeight = FontWeight.Bold,
             color = if (negative) Red else MaterialTheme.colorScheme.onSurface)
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

private fun layoutLabel(m: LayoutMode): String = when (m) {
    LayoutMode.CLASSIC -> "Classic"
    LayoutMode.COMPACT -> "Compact"
    LayoutMode.SCALP -> "Scalp"
    LayoutMode.SPLIT -> "Split"
}

/** A weighted chip for the "Default screen" picker — four across a phone-width row. */
@Composable
private fun LayoutChip(
    label: String,
    selected: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val tint = MaterialTheme.colorScheme.primary
    OutlinedButton(
        onClick = onClick,
        shape = RoundedCornerShape(6.dp),
        contentPadding = PaddingValues(horizontal = 4.dp, vertical = 2.dp),
        colors = ButtonDefaults.outlinedButtonColors(
            containerColor = if (selected) tint.copy(alpha = 0.22f) else Color.Transparent,
            contentColor = if (selected) tint else MaterialTheme.colorScheme.onSurfaceVariant,
        ),
        modifier = modifier.height(38.dp),
    ) {
        Text(label, fontSize = 12.sp, maxLines = 1,
             fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
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
