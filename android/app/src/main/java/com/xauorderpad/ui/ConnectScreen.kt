package com.xauorderpad.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.material3.TextButton
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun ConnectScreen(
    initialUrl: String,
    initialToken: String,
    onSave: (String, String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var url by remember { mutableStateOf(initialUrl) }
    var token by remember { mutableStateOf(initialToken) }
    var showToken by remember { mutableStateOf(false) }

    Column(
        modifier.fillMaxSize().padding(20.dp),
        verticalArrangement = androidx.compose.foundation.layout.Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("XauOrderPad", fontSize = 26.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(4.dp))
        Text(
            "Connect to the MT5 server",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(28.dp))

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Server address") },
            // Two real targets: a LAN dev server (http://192.168.x.x:8765, plain HTTP) or the EC2
            // box (https://<elastic-ip>:8443, mutual TLS). Type the scheme+port for EC2; a bare
            // host defaults to http://host:8765 for the LAN case.
            placeholder = { Text("192.168.0.116:8765  or  https://<ip>:8443") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(10.dp))

        OutlinedTextField(
            value = token,
            onValueChange = { token = it },
            label = { Text("API token") },
            placeholder = { Text("XAUORDERPAD_TOKEN") },
            singleLine = true,
            // Masked by default, but revealable. A mistyped 32-char random token is otherwise
            // impossible to spot, and it fails as an opaque 401 that looks like a server fault.
            visualTransformation =
                if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
            trailingIcon = {
                TextButton(onClick = { showToken = !showToken }) {
                    Text(if (showToken) "HIDE" else "SHOW", fontSize = 11.sp)
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            // Accurate scope: no broker password on THIS screen. It is entered on the Accounts
            // screen when you add an account, sent to the server once, and stored in the server's
            // credential vault -- after that the phone logs in by saved account. (It used to say
            // the password "never comes to the phone", which the account feature made false.)
            "This screen takes only the API token. Your MT5 broker password is entered on the "
                + "Accounts screen when adding an account, sent once, and kept in the server's "
                + "credential vault — not stored on the phone.",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Spacer(Modifier.height(22.dp))
        Button(
            onClick = { onSave(url, token) },
            enabled = url.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(50.dp),
        ) { Text("CONNECT", fontWeight = FontWeight.Bold) }
    }
}
