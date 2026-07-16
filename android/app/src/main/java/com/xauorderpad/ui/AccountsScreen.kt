package com.xauorderpad.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.BuildConfig
import com.xauorderpad.data.ServerCatalog
import com.xauorderpad.net.Profile

/**
 * MT5 account management — the phone's version of the web's ACCOUNT modal.
 *
 * ── Why this exists ──
 *
 * The phone was a PICK-ONLY client: it could log into a profile the desktop had already saved,
 * and nothing else. Since the only saved profile was the demo one, the phone was effectively
 * pinned to demo — not by any check, but by an absent feature. This is that feature.
 *
 * ── The one password in the whole app ──
 *
 * Everywhere else, the broker password stays on the server: the phone posts a profile id and the
 * server reads the secret from the Windows Credential Manager. This screen is the single place a
 * password is typed, and it is sent EXACTLY ONCE. With "Remember" on, the server stores it in the
 * vault and every later login is by profile id again.
 *
 * The transport is usually plain HTTP. On Tailscale that is WireGuard-encrypted; on open Wi-Fi it
 * is not, and a REAL broker password would be on the wire in the clear. We say so, in red, before
 * the field is touched. We warn; we do not block — it is the user's own network.
 */
@Composable
fun AccountsScreen(
    profiles: List<Profile>,
    health: Health,
    /** Whether the feed is actually live. A dead socket must not be read as "this account is active". */
    live: Boolean,
    busy: Boolean,
    error: String?,
    /** Bumped ONLY when a typed-credential login succeeds. The sole trigger for clearing the form. */
    okTick: Int,
    /** True when a typed password would cross the network unencrypted. */
    passwordInClear: Boolean,
    onLoginProfile: (String) -> Unit,
    onLoginWith: (login: String, password: String, server: String, save: Boolean, label: String) -> Unit,
    onDelete: (String) -> Unit,
    onLogout: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BackHandler(onBack = onBack)

    // rememberSaveable for everything EXCEPT the password. Rotating the phone, or -- the flow
    // that will actually happen -- switching to a password manager to copy the broker password
    // and coming back to a process Android killed, must not wipe the four fields you already
    // typed.
    //
    // The password is a plain `remember`, deliberately. rememberSaveable writes into the saved
    // instance state Bundle, which Android may persist to disk. A broker password does not go
    // to disk on the phone. Losing it on a process death is the correct trade.
    // Pre-fill the form from the shipped default account (servers.json) plus the baked password, so a
    // fresh install can log in with one tap. rememberSaveable keeps any edits across recreation; the
    // okTick effect below blanks the form once a login actually succeeds. When no default is shipped
    // the fields start empty, exactly as before.
    val context = LocalContext.current
    val defaultAcct = remember { ServerCatalog.load(context).defaultAccount }
    var label by rememberSaveable { mutableStateOf(defaultAcct?.label ?: "") }
    var login by rememberSaveable { mutableStateOf(defaultAcct?.login?.toString() ?: "") }
    var server by rememberSaveable { mutableStateOf(defaultAcct?.server ?: "") }
    var save by rememberSaveable { mutableStateOf(true) }
    var password by remember { mutableStateOf(BuildConfig.DEFAULT_ACCOUNT_PASSWORD) }

    // Cleared on a successful typed login ONLY. Clearing it on send meant a one-character typo in
    // the server name cost you the whole password as well.
    //
    // We track the last tick we ACTED on, in saved state, rather than firing on `okTick > 0`. The
    // ViewModel outlives an Activity recreation, so after any earlier success `okTick` is already
    // non-zero; keying on `> 0` re-ran this on every rotation and wiped the four rememberSaveable
    // fields it was supposed to protect. Comparing against the last-handled value fires exactly
    // once per real success and never on a bare recomposition.
    var handledTick by rememberSaveable { mutableStateOf(0) }
    LaunchedEffect(okTick) {
        if (okTick != handledTick) {
            handledTick = okTick
            if (okTick > 0) {
                password = ""
                label = ""; login = ""; server = ""
            }
        }
    }

    val v = validateAccountForm(login, password, server)

    var confirmDelete by remember { mutableStateOf<Profile?>(null) }
    var confirmLogout by remember { mutableStateOf(false) }
    // Switching to a REAL -- or an UNVERIFIED -- account is a decision, not a tap. Holds the pending
    // switch (its mode + the action) until confirmed. Demo switches never land here.
    var confirmSwitch by remember { mutableStateOf<Pair<AcctMode, () -> Unit>?>(null) }

    Column(modifier.fillMaxSize()) {
        AccTopBar("MT5 Account", onBack)

        Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {

            // ---- what we are connected to RIGHT NOW ---------------------------
            AccountStatus(health, live)

            // The banner the web had to learn to add: a login failure that only toasts for four
            // seconds reads as "the button did nothing", and the user taps it again and again.
            // This stays until something actually succeeds.
            error?.let {
                Spacer(Modifier.height(8.dp))
                Card(
                    colors = CardDefaults.cardColors(containerColor = Red.copy(alpha = 0.15f)),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("⚠ $it", Modifier.padding(10.dp),
                         fontSize = 12.sp, fontWeight = FontWeight.Bold, color = Red)
                }
            }

            Spacer(Modifier.height(18.dp))

            // ---- saved accounts ----------------------------------------------
            AccSectionLabel("SAVED ACCOUNTS")
            if (profiles.isEmpty()) {
                Text("None saved yet. Add one below.",
                     fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            for (p in profiles) {
                val mode = acctMode(p.lastTradeMode)
                // Match on LOGIN **and** SERVER, from the live feed.
                //
                // This used to compare the server alone -- and a demo and a real account on the
                // same broker server is the normal Exness setup, so BOTH rows lit up as active.
                // On the one screen whose job is to tell you which account you are about to
                // trade, that is the single thing it must not get wrong.
                //
                // `live` gates it now: Feed.snapshot is NOT cleared when the socket dies, so the
                // last frame just sits there. Without this the screen would keep asserting "●
                // active" and a green DEMO badge from a frame that may be minutes old and about
                // an account the terminal has since left.
                val active = live && !health.loggedOut &&
                    health.login != null && p.login != null &&
                    health.login == p.login && health.server == p.server
                SavedRow(
                    p = p,
                    mode = mode,
                    active = active,
                    enabled = !busy,
                    onSwitch = {
                        val go = { onLoginProfile(p.id) }
                        // REAL *and* UNVERIFIED both confirm. An account we have never actually
                        // logged into might be a real one; treating it as demo is the one mistake
                        // this dialog exists to prevent.
                        if (mode.confirmBeforeSwitch) confirmSwitch = mode to go else go()
                    },
                    onDelete = { confirmDelete = p },
                )
                Spacer(Modifier.height(6.dp))
            }

            Spacer(Modifier.height(18.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Spacer(Modifier.height(16.dp))

            // ---- add / log in ------------------------------------------------
            AccSectionLabel("LOG IN TO AN ACCOUNT")

            if (passwordInClear) {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Amber.copy(alpha = 0.15f)),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        "⚠ This server is plain HTTP over the network, so the password below is " +
                            "sent UNENCRYPTED. Fine on a trusted LAN or Tailscale; do not do it " +
                            "with a real broker password on Wi-Fi you do not control.",
                        Modifier.padding(10.dp),
                        fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Amber,
                    )
                }
                Spacer(Modifier.height(10.dp))
            }

            AccField(label, { label = it }, "Label (optional)", "e.g. Exness Demo", enabled = !busy)

            AccField(login, { login = it }, "Login (account number)", "12345678",
                     keyboard = KeyboardType.Number, enabled = !busy, error = v.login)

            // The password is NEVER trimmed -- an MT5 password may legitimately contain spaces,
            // and silently "fixing" one locks you out of your own account. But a pasted password
            // with a trailing space is rejected by MT5 with the SAME opaque -6 as a wrong
            // password, so without this warning you would retype a password that was right all
            // along.
            AccField(password, { password = it }, "Password", "",
                     keyboard = KeyboardType.Password, isPassword = true, enabled = !busy,
                     error = v.password, warn = v.passwordWarning)

            ServerDropdown(server, { server = it }, enabled = !busy, error = v.server)

            // No "Terminal path" field: the server no longer accepts a client-supplied path
            // (it launched that executable -- a remote-code-execution hole). The terminal the
            // server drives is fixed in its own config.

            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Remember this account", fontSize = 13.sp, fontWeight = FontWeight.Bold)
                    Text(
                        if (save)
                            "The password is stored in the Windows Credential Manager ON THE " +
                                "SERVER, not on this phone. After this, logging in never sends " +
                                "it again."
                        else
                            "OFF — nothing is stored. Note: if a LATER login fails, MT5 drops " +
                                "the session, and an unsaved account cannot be restored — the " +
                                "server holds no password for it.",
                        fontSize = 10.sp,
                        fontWeight = if (save) FontWeight.Normal else FontWeight.Bold,
                        color = if (save) MaterialTheme.colorScheme.onSurfaceVariant else Amber,
                    )
                }
                Switch(checked = save, onCheckedChange = { save = it }, enabled = !busy)
            }

            Spacer(Modifier.height(12.dp))
            // No REAL-confirm on this button, deliberately: the account's trade_mode is not known
            // until the server has logged in, so there is nothing to confirm against yet. The
            // status card turns red immediately afterwards. The confirm guards the case we CAN
            // know in advance: switching to a saved profile already marked REAL.
            Button(
                // The password is NOT cleared here. It is cleared on the success tick, and only
                // there -- see the LaunchedEffect above.
                onClick = {
                    // Remember a hand-typed server so it appears in the dropdown next time (no-op for
                    // a built-in or an already-known name).
                    ServerCatalog.addCustomServer(context, server)
                    onLoginWith(login, password, server, save, label)
                },
                enabled = !busy && v.valid,
                modifier = Modifier.fillMaxWidth().height(48.dp),
            ) {
                Text(if (busy) "WORKING…" else "LOG IN", fontWeight = FontWeight.Bold)
            }

            Spacer(Modifier.height(20.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Spacer(Modifier.height(10.dp))

            TextButton(onClick = { confirmLogout = true }, enabled = !busy) {
                Text("LOG OUT OF MT5", color = Red, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(24.dp))
        }
    }

    confirmDelete?.let { p ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text("Forget ${p.label ?: p.id}?") },
            text = {
                Text(
                    "The saved password is deleted from the Windows Credential Manager on the " +
                        "server. The MT5 account itself is untouched — you can add it again by " +
                        "typing the password."
                )
            },
            confirmButton = {
                TextButton(onClick = { onDelete(p.id); confirmDelete = null }) {
                    Text("FORGET", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmDelete = null }) { Text("Cancel") }
            },
        )
    }

    if (confirmLogout) {
        AlertDialog(
            onDismissRequest = { confirmLogout = false },
            title = { Text("Log out of MT5?") },
            text = {
                Text(
                    "The order pad stops trading until you log in again.\n\n" +
                        "This does NOT close anything. MT5 has no real logout — any OPEN " +
                        "POSITIONS stay open on the account, with nothing watching them."
                )
            },
            confirmButton = {
                TextButton(onClick = { confirmLogout = false; onLogout() }) {
                    Text("LOG OUT", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmLogout = false }) { Text("Cancel") }
            },
        )
    }

    confirmSwitch?.let { (mode, go) ->
        val unverified = mode == AcctMode.UNKNOWN
        AlertDialog(
            onDismissRequest = { confirmSwitch = null },
            title = { Text(if (unverified) "Switch to an UNVERIFIED account?" else "Switch to a REAL account?") },
            text = {
                Text(
                    if (unverified)
                        "This account has never been logged in through this server, so whether it " +
                            "is DEMO or REAL is UNKNOWN. It is treated as REAL until proven " +
                            "otherwise.\n\n" +
                            "If it turns out to be real, manual BUY / SELL / CLOSE will place real " +
                            "orders. The status card will show the true type once you are in."
                    else
                        "This account trades REAL MONEY. Manual BUY / SELL / CLOSE will place real " +
                            "orders.\n\n" +
                            "The automated strategy engines will REFUSE to run on it — they are " +
                            "demo-only, and the server re-checks that on every tick."
                )
            },
            confirmButton = {
                TextButton(onClick = { confirmSwitch = null; go() }) {
                    Text(if (unverified) "SWITCH ANYWAY" else "SWITCH TO REAL",
                         color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmSwitch = null }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun AccountStatus(h: Health, live: Boolean) {
    // With a dead feed the snapshot is the LAST frame, which may be minutes old and about an
    // account the terminal has since left. Refuse to assert REAL/DEMO from it -- a stale green
    // "DEMO" on a screen whose job is to say what you are trading is exactly the wrong failure.
    val (text, color) = when {
        !live -> "NO LIVE DATA — status unknown (feed is down)" to Amber
        h.loggedOut -> "LOGGED OUT — no account connected" to Amber
        h.isDemo == false -> "REAL ACCOUNT" to Red
        h.isDemo == true -> "DEMO" to Green
        else -> "checking…" to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Card(
        colors = CardDefaults.cardColors(containerColor = color.copy(alpha = 0.12f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp)) {
            Text(text, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = color)
            h.server?.let {
                Text(it, fontSize = 12.sp, fontFamily = FontFamily.Monospace,
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun SavedRow(
    p: Profile,
    mode: AcctMode,
    active: Boolean,
    enabled: Boolean,
    onSwitch: () -> Unit,
    onDelete: () -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(
                Modifier.weight(1f).clickable(enabled = enabled, onClick = onSwitch)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(p.label ?: p.id, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.width(6.dp))
                    Text(
                        mode.badge,
                        fontSize = 9.sp, fontWeight = FontWeight.Bold,
                        color = mode.color,
                        modifier = Modifier
                            .background(mode.color.copy(alpha = 0.18f), RoundedCornerShape(3.dp))
                            .padding(horizontal = 4.dp, vertical = 1.dp),
                    )
                    if (active) {
                        Spacer(Modifier.width(6.dp))
                        Text("● active", fontSize = 9.sp, color = Green)
                    }
                }
                Text("${p.login ?: "?"} @ ${p.server ?: "?"}",
                     fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            TextButton(onClick = onSwitch, enabled = enabled) {
                Text("SWITCH", fontSize = 11.sp, fontWeight = FontWeight.Bold)
            }
            TextButton(onClick = onDelete, enabled = enabled) {
                Text("✕", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/**
 * What is wrong with the form, field by field.
 *
 * Per-field, not one shared banner: "login, password and server are required" tells you nothing
 * about WHICH one, and the failure that actually happens -- a wrong Exness server suffix, or a
 * pasted password with a trailing space -- both come back from MT5 as the same opaque
 * `-6: Authorization failed`. Indistinguishable from a wrong password. So the app has to catch
 * what it can before the request leaves.
 */
@Immutable
data class AccountFormErrors(
    val login: String? = null,
    val password: String? = null,
    val server: String? = null,
    /** Not an error -- the form still submits. A likely paste artefact worth seeing. */
    val passwordWarning: String? = null,
) {
    val valid: Boolean get() = login == null && password == null && server == null
}

fun validateAccountForm(login: String, password: String, server: String): AccountFormErrors {
    val l = login.trim()
    val loginErr = when {
        l.isEmpty() -> "required"
        // KeyboardType.Number is only a keyboard HINT -- paste and hardware keyboards put
        // letters in here happily.
        !l.all { it.isDigit() } -> "digits only"
        l.length > 12 -> "too long for an MT5 login"
        l.toLongOrNull()?.let { it <= 0L } != false -> "must be a positive account number"
        else -> null
    }

    // NOT trimmed: an MT5 password may legitimately contain spaces.
    val pwErr = if (password.isEmpty()) "required" else null
    val pwWarn = if (password.isNotEmpty() && password != password.trim())
        "starts or ends with a space — usually a paste artefact. MT5 rejects it with the same " +
            "error as a wrong password."
    else null

    val s = server.trim()
    val srvErr = when {
        s.isEmpty() -> "required"
        // Internal spaces are legitimate: many brokers name servers "VTMarkets-Live 2".
        // The API collapses whitespace runs (server.py _validated_server), so only genuine
        // paste garbage -- tabs / newlines -- is worth blocking here. Leading/trailing spaces
        // are already gone via server.trim() above.
        s.any { it.isWhitespace() && it != ' ' } -> "no tabs or line breaks in a server name"
        else -> null
    }
    return AccountFormErrors(loginErr, pwErr, srvErr, pwWarn)
}

/**
 * The Server field as a dropdown of known MT5 servers (servers.json + any the user has typed before),
 * but still fully editable so a server not on the list can just be typed. A hand-typed name is
 * remembered on LOG IN and shows in the list next time.
 *
 * Editable, not pick-only: MT5 server names must match the broker's EXACTLY (a "Trail"/"Trial" swap or
 * a missing space is a silent -6), and no curated list is ever complete — so the field accepts free
 * text; the dropdown just removes the typing for the common ones.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ServerDropdown(
    value: String,
    onChange: (String) -> Unit,
    enabled: Boolean,
    error: String? = null,
) {
    val context = LocalContext.current
    val brokerOf = remember { ServerCatalog.load(context).servers.associate { it.name to it.broker } }
    var expanded by remember { mutableStateOf(false) }
    // Recomputed each time the menu opens so a name just added via free-text (and persisted) appears.
    val names = remember(expanded) { ServerCatalog.serverNames(context) }

    val show = if (value.isNotEmpty()) error else null
    val q = value.trim()
    // Show the whole list when the field is empty or already holds a known server -- otherwise a
    // pre-filled value (e.g. the default account's server) would filter the dropdown down to itself and
    // hide every other choice. Filter only while the user is typing a genuine partial/custom name.
    val exact = names.any { it.equals(q, ignoreCase = true) }
    val matches = if (q.isEmpty() || exact) names else names.filter { it.contains(q, ignoreCase = true) }

    Column(Modifier.fillMaxWidth().padding(bottom = 6.dp)) {
        ExposedDropdownMenuBox(
            expanded = expanded && enabled,
            onExpandedChange = { if (enabled) expanded = it },
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = { onChange(it); expanded = true },
                enabled = enabled,
                singleLine = true,
                isError = show != null,
                label = { Text("Server", fontSize = 11.sp) },
                placeholder = { Text("Exness-MT5Trial16", fontSize = 12.sp) },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
                modifier = Modifier
                    .menuAnchor(MenuAnchorType.PrimaryEditable, enabled)
                    .fillMaxWidth(),
            )
            ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                for (name in matches) {
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(name, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                                brokerOf[name]?.takeIf { it.isNotBlank() }?.let {
                                    Text(it, fontSize = 10.sp,
                                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                        },
                        onClick = { onChange(name); expanded = false },
                    )
                }
                // Offer the free-typed value explicitly when it is not a known server.
                if (q.isNotEmpty() && !exact) {
                    DropdownMenuItem(
                        text = { Text("Use \"$q\" — not listed", fontSize = 12.sp, color = Amber) },
                        onClick = { onChange(q); expanded = false },
                    )
                }
            }
        }
        show?.let {
            Text("⚠ $it", fontSize = 10.sp, color = Red,
                 modifier = Modifier.padding(start = 4.dp, top = 2.dp))
        }
    }
}

@Composable
private fun AccField(
    value: String,
    onChange: (String) -> Unit,
    label: String,
    placeholder: String,
    enabled: Boolean,
    keyboard: KeyboardType = KeyboardType.Text,
    isPassword: Boolean = false,
    error: String? = null,
    warn: String? = null,
) {
    // Only complain about a field the user has actually touched: a form that lights up red the
    // instant you open it teaches you to ignore red.
    val touched = value.isNotEmpty()
    val show = if (touched) error else null

    Column(Modifier.fillMaxWidth().padding(bottom = 6.dp)) {
        OutlinedTextField(
            value = value,
            onValueChange = onChange,
            enabled = enabled,
            singleLine = true,
            isError = show != null,
            label = { Text(label, fontSize = 11.sp) },
            placeholder = { Text(placeholder, fontSize = 12.sp) },
            keyboardOptions = KeyboardOptions(keyboardType = keyboard),
            visualTransformation =
                if (isPassword) PasswordVisualTransformation() else VisualTransformation.None,
            modifier = Modifier.fillMaxWidth(),
        )
        show?.let {
            Text("⚠ $it", fontSize = 10.sp, color = Red,
                 modifier = Modifier.padding(start = 4.dp, top = 2.dp))
        }
        if (show == null) warn?.let {
            Text("⚠ $it", fontSize = 10.sp, color = Amber,
                 modifier = Modifier.padding(start = 4.dp, top = 2.dp))
        }
    }
}

@Composable
private fun AccTopBar(title: String, onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 8.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Start,
    ) {
        TextButton(onClick = onBack) { Text("‹  BACK", fontSize = 12.sp) }
        Spacer(Modifier.width(4.dp))
        Text(title, fontSize = 16.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun AccSectionLabel(text: String) {
    Text(text, fontSize = 10.sp, fontWeight = FontWeight.Bold,
         color = MaterialTheme.colorScheme.onSurfaceVariant,
         modifier = Modifier.padding(bottom = 6.dp))
}
