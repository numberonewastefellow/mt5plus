package com.xauorderpad.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.net.Profile

@Composable
fun LoginScreen(
    profiles: List<Profile>,
    busy: Boolean,
    onPick: (String) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier.fillMaxSize().padding(16.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("MT5 account", fontSize = 20.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f))
            TextButton(onClick = onBack) { Text("Back") }
        }
        Spacer(Modifier.height(4.dp))
        Text(
            // Explains why this screen exists at all: the worker boots with
            // _session_active = False, so after every EC2 restart SOMETHING must log in.
            // The point of the app is that it doesn't have to be the laptop.
            "The server starts logged out. Pick a saved account — its password is read "
                + "from the server's credential vault, never sent from this phone.",
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(12.dp))

        if (busy) { LinearProgressIndicator(Modifier.fillMaxWidth()); Spacer(Modifier.height(8.dp)) }

        if (profiles.isEmpty()) {
            Box(Modifier.fillMaxSize(), Alignment.Center) {
                Text(
                    // Being specific about the fix: the phone cannot create a profile,
                    // because that would require typing the broker password here.
                    "No saved accounts on the server.\n\nAdd one from the desktop web UI "
                        + "(Account panel) — it stores the password in the Windows vault.",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            return@Column
        }

        LazyColumn {
            items(profiles, key = { it.id }) { p ->
                // last_trade_mode: 2 = REAL. Flag it before the tap, not after.
                val isReal = p.lastTradeMode == 2
                Card(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp)
                        .clickable(enabled = !busy) { onPick(p.id) },
                    colors = CardDefaults.cardColors(
                        if (isReal) Red.copy(alpha = 0.12f)
                        else MaterialTheme.colorScheme.surfaceVariant
                    ),
                ) {
                    Column(Modifier.padding(14.dp)) {
                        Text(p.label ?: p.login?.toString() ?: p.id,
                            fontWeight = FontWeight.Bold, fontSize = 15.sp)
                        Text(p.server ?: "", fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (isReal) {
                            Text("REAL ACCOUNT", fontSize = 11.sp,
                                fontWeight = FontWeight.Bold, color = Red)
                        }
                    }
                }
            }
        }
    }
}
