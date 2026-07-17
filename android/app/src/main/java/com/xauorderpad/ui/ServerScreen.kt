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
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.data.ServerProfile

/**
 * Switch which backend the app talks to. A list of saved server profiles (each with its OWN
 * URL + token — the LAN dev server and the EC2 box use different tokens), the current one marked
 * ● active. "Select & connect" VERIFIES the candidate (server-side reachability + TLS/scheme +
 * token) before switching, so a wrong URL/token shows a banner instead of bouncing you to Connect.
 *
 * Modelled on AccountsScreen: same BackHandler, TopBar, error-banner Card, busy gating idioms.
 */
@Composable
fun ServerScreen(
    profiles: List<ServerProfile>,
    /** The active server's normalized URL (== Secrets.baseUrl). Marks the selected row. */
    current: String,
    busy: Boolean,
    error: String?,
    onSelect: (String) -> Unit,
    onSave: (id: String?, label: String, url: String, token: String) -> Unit,
    onDelete: (String) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BackHandler(onBack = onBack)

    // editing: null = form closed; "" = adding a new one; else = editing that profile id.
    var editing by rememberSaveable { mutableStateOf<String?>(null) }
    var fLabel by rememberSaveable { mutableStateOf("") }
    var fUrl by rememberSaveable { mutableStateOf("") }
    var fToken by rememberSaveable { mutableStateOf("") }
    var confirmDelete by remember { mutableStateOf<ServerProfile?>(null) }

    fun openForm(p: ServerProfile?) {
        editing = p?.id ?: ""
        fLabel = p?.label ?: ""
        fUrl = p?.url ?: ""
        fToken = p?.token ?: ""
    }

    Column(modifier.fillMaxSize()) {
        SrvTopBar(onBack)

        Column(Modifier.verticalScroll(rememberScrollState()).padding(12.dp)) {
            Text(
                "Pick which backend the app talks to. “Select & connect” verifies the connection first "
                    + "and switches only if it works.",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

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

            Spacer(Modifier.height(14.dp))
            SrvSectionLabel("SERVERS")
            if (profiles.isEmpty()) {
                Text("None saved. Add one below.",
                    fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            for (p in profiles) {
                ServerRow(
                    p = p,
                    active = p.url == current,
                    busy = busy,
                    onSelect = { onSelect(p.id) },
                    onEdit = { openForm(p) },
                    onDelete = { confirmDelete = p },
                )
                Spacer(Modifier.height(6.dp))
            }

            Spacer(Modifier.height(12.dp))
            if (editing == null) {
                OutlinedButton(onClick = { openForm(null) }, enabled = !busy) {
                    Text("+ Add server", fontWeight = FontWeight.Bold)
                }
            } else {
                SrvSectionLabel(if (editing.isNullOrEmpty()) "ADD SERVER" else "EDIT SERVER")
                SrvField(fLabel, { fLabel = it }, "Label", "e.g. Local (LAN)", !busy)
                SrvField(fUrl, { fUrl = it }, "Server URL / IP",
                    "192.168.0.116:8765  or  https://<ip>:8443", !busy, KeyboardType.Uri)
                SrvField(fToken, { fToken = it }, "API token", "the token for THIS server", !busy)
                Spacer(Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = {
                            onSave(editing?.ifEmpty { null }, fLabel, fUrl, fToken)
                            editing = null
                        },
                        enabled = !busy && fUrl.isNotBlank(),
                    ) { Text("SAVE", fontWeight = FontWeight.Bold) }
                    TextButton(onClick = { editing = null }, enabled = !busy) { Text("Cancel") }
                }
            }
        }
    }

    confirmDelete?.let { p ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text("Delete server?") },
            text = { Text("Remove “${p.label}” (${hostOf(p.url)}) from the list?") },
            confirmButton = {
                TextButton(onClick = { onDelete(p.id); confirmDelete = null }) {
                    Text("DELETE", color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = null }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun ServerRow(
    p: ServerProfile,
    active: Boolean,
    busy: Boolean,
    onSelect: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    val tint = MaterialTheme.colorScheme.primary
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (active) tint.copy(alpha = 0.14f)
            else MaterialTheme.colorScheme.surfaceVariant,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.fillMaxWidth().padding(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(p.label, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                if (active) {
                    Spacer(Modifier.width(6.dp))
                    Text("● active", fontSize = 9.sp, color = Green)
                }
                if (p.token.isBlank()) {
                    Spacer(Modifier.width(6.dp))
                    Text("no token", fontSize = 9.sp, color = Amber, fontWeight = FontWeight.Bold)
                }
            }
            Text(hostOf(p.url), fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = onSelect,
                    enabled = !busy && !active,
                    modifier = Modifier.height(38.dp),
                ) {
                    Text(
                        if (busy) "WORKING…" else if (active) "CONNECTED" else "SELECT & CONNECT",
                        fontSize = 12.sp, fontWeight = FontWeight.Bold,
                    )
                }
                Spacer(Modifier.width(4.dp))
                TextButton(onClick = onEdit, enabled = !busy) { Text("Edit", fontSize = 12.sp) }
                Spacer(Modifier.weight(1f))
                TextButton(onClick = onDelete, enabled = !busy) {
                    Text("✕", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** Labeled text field for the add/edit form. */
@Composable
private fun SrvField(
    value: String,
    onChange: (String) -> Unit,
    label: String,
    placeholder: String,
    enabled: Boolean,
    keyboard: KeyboardType = KeyboardType.Text,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        enabled = enabled,
        singleLine = true,
        label = { Text(label, fontSize = 11.sp) },
        placeholder = { Text(placeholder, fontSize = 12.sp) },
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        modifier = Modifier.fillMaxWidth().padding(bottom = 6.dp),
    )
}

@Composable
private fun SrvTopBar(onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 4.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onBack) { Text("‹  BACK", fontSize = 13.sp) }
        Text("Servers", fontSize = 16.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun SrvSectionLabel(text: String) {
    Text(text, fontSize = 10.sp, fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(bottom = 6.dp))
}

/** Strip the scheme for a compact host:port display, like ServerBar/Settings do. */
private fun hostOf(url: String): String =
    url.removePrefix("http://").removePrefix("https://").ifBlank { "not set" }
