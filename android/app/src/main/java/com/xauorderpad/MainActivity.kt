package com.xauorderpad

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.Network
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.xauorderpad.data.Feed
import com.xauorderpad.net.Link
import com.xauorderpad.svc.FeedService
import com.xauorderpad.ui.ConnectScreen
import com.xauorderpad.ui.LoginScreen
import com.xauorderpad.ui.Screen
import com.xauorderpad.ui.TradeScreen
import com.xauorderpad.ui.TradingViewModel

class MainActivity : ComponentActivity() {

    private val askNotifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* best effort */ }

    private var netCallback: ConnectivityManager.NetworkCallback? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // targetSdk 36 ENFORCES edge-to-edge. Without this (and without handing the Scaffold's
        // insets to every screen below) content renders under the status and navigation bars.
        enableEdgeToEdge()

        // Feed.init() builds Secrets, which is now plain SharedPreferences rather than
        // EncryptedSharedPreferences -- a few ms of disk instead of 50-200 ms of Keystore +
        // Tink. Cheap enough to do inline; the values are then cached in memory so the hot
        // paths (every reconnect, every request header) never touch disk again.
        Feed.init(this)

        // POST_NOTIFICATIONS is a runtime permission from API 33. Without it the foreground
        // service still runs, but its notification -- and therefore the lock-screen CLOSE ALL /
        // CLOSE LOSING actions -- is invisible. Since minSdk is 34, no version guard is needed.
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        registerNetworkCallback()

        setContent {
            // Dark, always. This is a trading screen: it is used in the dark, and the red/green
            // P&L semantics are far easier to read against a dark ground.
            MaterialTheme(colorScheme = dynamicDarkColorScheme(this)) {
                val vm: TradingViewModel = viewModel()

                val screen by vm.screen.collectAsStateWithLifecycle()
                val link by vm.link.collectAsStateWithLifecycle()
                val quote by vm.quote.collectAsStateWithLifecycle()
                val health by vm.health.collectAsStateWithLifecycle()
                val account by vm.account.collectAsStateWithLifecycle()
                val positions by vm.positions.collectAsStateWithLifecycle()
                val form by vm.form.collectAsStateWithLifecycle()
                val busy by vm.busy.collectAsStateWithLifecycle()
                val closing by vm.closing.collectAsStateWithLifecycle()
                val toast by vm.toast.collectAsStateWithLifecycle()
                val profiles by vm.profiles.collectAsStateWithLifecycle()
                val confirmCloses by vm.confirmCloses.collectAsStateWithLifecycle()

                val snackbar = remember { SnackbarHostState() }

                // A trader watching a quote must not have the screen time out mid-trade -- but
                // there is no reason to hold the screen awake on the Connect or Login screens.
                DisposableEffect(screen) {
                    val keepAwake = screen == Screen.TRADE
                    if (keepAwake) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    onDispose { window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
                }

                // A 4401 arrives on the SOCKET, not from an API call, so the ViewModel cannot
                // see it from its own request results. Bridge it here, or a rejected token
                // leaves the user staring at a silently dead screen.
                // (onUnauthorized is idempotent -- it no-ops if we are already on CONNECT --
                // so this firing alongside an API 401 is harmless.)
                LaunchedEffect(link) {
                    if (link is Link.Unauthorized) vm.onUnauthorized()
                }

                LaunchedEffect(toast?.id) {
                    toast?.let {
                        snackbar.showSnackbar(it.text)
                        vm.toastShown()
                    }
                }

                // Only run the foreground service once there is somewhere to connect to.
                // Starting it on CONNECT would post a permanent "connecting…" notification for
                // a server that was never configured.
                //
                // The stop() half matters just as much: after a logout the socket is gone, so an
                // ongoing notification left behind would sit in the shade claiming a live feed --
                // with working CLOSE ALL / CLOSE LOSING actions on a connection that no longer
                // exists.
                LaunchedEffect(screen) {
                    if (screen != Screen.CONNECT) FeedService.start(this@MainActivity)
                    else FeedService.stop(this@MainActivity)
                }

                // Compose's strong skipping memoizes lambda LITERALS but NOT method references:
                // `vm::setLot` allocates a fresh KFunction object on every recomposition, so the
                // parameter always looks "changed" and the child can never skip. Wrapping them in
                // remember(vm) restores skipping.
                val callbacks = remember(vm) {
                    object {
                        val onLot: (String) -> Unit = { vm.setLot(it) }
                        val onStepLot: (Int) -> Unit = { vm.stepLot(it) }
                        val onSl: (String) -> Unit = { vm.setSl(it) }
                        val onTp: (String) -> Unit = { vm.setTp(it) }
                        val onBuy: () -> Unit = { vm.placeOrder("buy") }
                        val onSell: () -> Unit = { vm.placeOrder("sell") }
                        val onCloseWhere: (String) -> Unit = { vm.closeWhere(it) }
                        val onClosePosition: (Long) -> Unit = { vm.closeOne(it) }
                        val onLogin: () -> Unit = { vm.goto(Screen.LOGIN) }
                        val onBackToTrade: () -> Unit = { vm.goto(Screen.TRADE) }
                        val onPickProfile: (String) -> Unit = { vm.login(it) }
                        val onSaveConnection: (String, String) -> Unit = { u, t -> vm.saveConnection(u, t) }
                        val onDisconnect: () -> Unit = { vm.disconnect() }
                        val onToggleConfirm: (Boolean) -> Unit = { vm.setConfirmCloses(it) }
                    }
                }

                Scaffold(snackbarHost = { SnackbarHost(snackbar) }) { pad ->
                    // EVERY screen gets the insets, not just Trade. Under enforced edge-to-edge
                    // the others would otherwise render beneath the status bar.
                    val inset = Modifier.padding(pad)

                    when (screen) {
                        Screen.CONNECT -> ConnectScreen(
                            initialUrl = vm.baseUrl,
                            initialToken = vm.token,
                            onSave = callbacks.onSaveConnection,
                            modifier = inset,
                        )

                        Screen.LOGIN -> LoginScreen(
                            profiles = profiles,
                            busy = busy,
                            onPick = callbacks.onPickProfile,
                            onBack = callbacks.onBackToTrade,
                            modifier = inset,
                        )

                        Screen.TRADE -> TradeScreen(
                            quote = quote,
                            health = health,
                            account = account,
                            positions = positions,
                            link = link,
                            form = form,
                            busy = busy,
                            closing = closing,
                            onLot = callbacks.onLot,
                            onStepLot = callbacks.onStepLot,
                            onSl = callbacks.onSl,
                            onTp = callbacks.onTp,
                            onBuy = callbacks.onBuy,
                            onSell = callbacks.onSell,
                            onCloseWhere = callbacks.onCloseWhere,
                            onClosePosition = callbacks.onClosePosition,
                            onLogin = callbacks.onLogin,
                            onDisconnect = callbacks.onDisconnect,
                            onToggleConfirm = callbacks.onToggleConfirm,
                            confirmCloses = confirmCloses,
                            serverUrl = vm.baseUrl,
                            modifier = inset,
                        )
                    }
                }
            }
        }
    }

    /**
     * The phone WILL roam between Wi-Fi and cellular. Without this, a reconnect has to wait out
     * whatever backoff sleep it happened to be in when signal returned -- up to a minute of dead
     * screen after the network is already back.
     */
    private fun registerNetworkCallback() {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = Feed.onNetworkAvailable()
        }
        cm.registerDefaultNetworkCallback(cb)
        netCallback = cb
    }

    override fun onDestroy() {
        netCallback?.let {
            (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager)
                .unregisterNetworkCallback(it)
        }
        super.onDestroy()
    }
}
