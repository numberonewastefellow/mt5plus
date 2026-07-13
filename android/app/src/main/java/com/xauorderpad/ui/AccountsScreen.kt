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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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
    busy: Boolean,
    error: String?,
    /** Bumped on every successful account action. Clears the password -- and ONLY on success. */
    okTick: Int,
    /** True when a typed password would cross the network unencrypted. */
    passwordInClear: Boolean,
    onLoginProfile: (String) -> Unit,
    onLoginWith: (login: String, password: String, server: String, path: String, save: Boolean, label: String) -> Unit,
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
    var label by rememberSaveable { mutableStateOf("") }
    var login by rememberSaveable { mutableStateOf("") }
    var server by rememberSaveable { mutableStateOf("") }
    var path by rememberSaveable { mutableStateOf("") }
    var save by rememberSaveable { mutableStateOf(true) }
    var password by remember { mutableStateOf("") }

    // Cleared on SUCCESS only. Clearing it on send meant a one-character typo in the server name
    // cost you the whole password as well.
    LaunchedEffect(okTick) {
        if (okTick > 0) {
            password = ""
            label = ""; login = ""; server = ""; path = ""
        }
    }

    val v = validateAccountForm(login, password, server)

    var confirmDelete by remember { mutableStateOf<Profile?>(null) }
    var confirmLogout by remember { mutableStateOf(false) }
    // A REAL account is a decision, not a tap. Holds the pending action until confirmed.
    var confirmReal by remember { mutableStateOf<(() -> Unit)?>(null) }

    Column(modifier.fillMaxSize()) {
        AccTopBar("MT5 Account", onBack)

        Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {

            // ---- what we are connected to RIGHT NOW ---------------------------
            AccountStatus(health)

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
                val isReal = p.lastTradeMode == 2
                // Match on LOGIN **and** SERVER, from the live feed.
                //
                // This used to compare the server alone -- and a demo and a real account on the
                // same broker server is the normal Exness setup, so BOTH rows lit up as active.
                // On the one screen whose job is to tell you which account you are about to
                // trade, that is the single thing it must not get wrong.
                val active = !health.loggedOut &&
                    health.login != null && p.login != null &&
                    health.login == p.login && health.server == p.server
                SavedRow(
                    p = p,
                    isReal = isReal,
                    active = active,
                    enabled = !busy,
                    onSwitch = {
                        val go = { onLoginProfile(p.id) }
                        if (isReal) confirmReal = go else go()
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

            AccField(server, { server = it }, "Server", "Exness-MT5Trial16",
                     enabled = !busy, error = v.server)

            AccField(path, { path = it }, "Terminal path (optional)",
                     "blank = use the running terminal", enabled = !busy)

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
                onClick = { onLoginWith(login, password, server, path, save, label) },
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

    confirmReal?.let { go ->
        AlertDialog(
            onDismissRequest = { confirmReal = null },
            title = { Text("Switch to a REAL account?") },
            text = {
                Text(
                    "This account trades REAL MONEY. Manual BUY / SELL / CLOSE will place real " +
                        "orders.\n\n" +
                        "The automated strategy engines will REFUSE to run on it — they are " +
                        "demo-only, and the server re-checks that on every tick."
                )
            },
            confirmButton = {
                TextButton(onClick = { confirmReal = null; go() }) {
                    Text("SWITCH TO REAL", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmReal = null }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun AccountStatus(h: Health) {
    val (text, color) = when {
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
    isReal: Boolean,
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
                        if (isReal) "REAL" else "DEMO",
                        fontSize = 9.sp, fontWeight = FontWeight.Bold,
                        color = if (isReal) Red else Green,
                        modifier = Modifier
                            .background(
                                (if (isReal) Red else Green).copy(alpha = 0.18f),
                                RoundedCornerShape(3.dp),
                            )
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
        s.any { it.isWhitespace() } -> "an MT5 server name has no spaces"
        else -> null
    }
    return AccountFormErrors(loginErr, pwErr, srvErr, pwWarn)
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
