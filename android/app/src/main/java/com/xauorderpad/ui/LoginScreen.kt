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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
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
    onAddAccount: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    // A tap here logs straight in, so REAL and UNVERIFIED accounts must confirm first -- same rule
    // as the Accounts screen. Holds the pending pick until confirmed.
    var confirmSwitch by remember { mutableStateOf<Pair<AcctMode, () -> Unit>?>(null) }

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
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        // This used to be a dead end -- "go to the desktop web UI". On an EC2 box
                        // with no saved profile that left the phone unable to trade at all, which
                        // is the one situation the phone exists for.
                        "No saved accounts on the server.",
                        fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(10.dp))
                    TextButton(onClick = onAddAccount) {
                        Text("ADD AN ACCOUNT", fontWeight = FontWeight.Bold)
                    }
                }
            }
            return@Column
        }

        LazyColumn {
            items(profiles, key = { it.id }) { p ->
                // Tri-state, not `== 2`. A null last_trade_mode means we have never actually seen
                // this account, so it is UNVERIFIED (amber) -- not silently DEMO.
                val mode = acctMode(p.lastTradeMode)
                val tint = when (mode) {
                    AcctMode.REAL -> Red.copy(alpha = 0.12f)
                    AcctMode.UNKNOWN -> Amber.copy(alpha = 0.12f)
                    AcctMode.DEMO -> MaterialTheme.colorScheme.surfaceVariant
                }
                Card(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp)
                        .clickable(enabled = !busy) {
                            val go = { onPick(p.id) }
                            if (mode.confirmBeforeSwitch) confirmSwitch = mode to go else go()
                        },
                    colors = CardDefaults.cardColors(tint),
                ) {
                    Column(Modifier.padding(14.dp)) {
                        Text(p.label ?: p.login?.toString() ?: p.id,
                            fontWeight = FontWeight.Bold, fontSize = 15.sp)
                        Text(p.server ?: "", fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (mode != AcctMode.DEMO) {
                            Text(
                                if (mode == AcctMode.REAL) "REAL ACCOUNT" else "UNVERIFIED — treated as REAL",
                                fontSize = 11.sp, fontWeight = FontWeight.Bold, color = mode.color,
                            )
                        }
                    }
                }
            }
        }
    }

    confirmSwitch?.let { (mode, go) ->
        val unverified = mode == AcctMode.UNKNOWN
        AlertDialog(
            onDismissRequest = { confirmSwitch = null },
            title = { Text(if (unverified) "Log in to an UNVERIFIED account?" else "Log in to a REAL account?") },
            text = {
                Text(
                    if (unverified)
                        "This account has never been logged in through this server, so whether it " +
                            "is DEMO or REAL is UNKNOWN. It is treated as REAL until proven otherwise."
                    else
                        "This account trades REAL MONEY. Manual BUY / SELL / CLOSE will place real orders."
                )
            },
            confirmButton = {
                TextButton(onClick = { confirmSwitch = null; go() }) {
                    Text(if (unverified) "LOG IN ANYWAY" else "LOG IN TO REAL",
                         color = Red, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmSwitch = null }) { Text("Cancel") }
            },
        )
    }
}
