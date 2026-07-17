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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.xauorderpad.data.Feed
import com.xauorderpad.net.Link
import com.xauorderpad.svc.FeedService
import com.xauorderpad.ui.AccountsScreen
import com.xauorderpad.ui.CertsScreen
import com.xauorderpad.ui.ConnectScreen
import com.xauorderpad.ui.HistoryScreen
import com.xauorderpad.ui.LayoutMode
import com.xauorderpad.ui.LoginScreen
import com.xauorderpad.ui.Screen
import com.xauorderpad.ui.ServerScreen
import com.xauorderpad.ui.SettingsScreen
import com.xauorderpad.ui.StrategiesScreen
import com.xauorderpad.ui.StrategyScreen
import com.xauorderpad.ui.TradeScreen
import com.xauorderpad.ui.TradingViewModel
import kotlinx.serialization.json.JsonObject

// Upper bound on the system Font-Size multiplier the app will honour. The trade panel is a dense,
// fixed-height layout; beyond this its two-line buttons overflow. Retune here if 1.15 is too tight.
private const val MAX_FONT_SCALE = 1.15f

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
            // Clamp the system Font-Size so an accessibility "huge fonts" setting cannot inflate
            // the dense trade panel past the heights its controls are laid out at -- unclamped, the
            // two-line BUY/CLOSE text overflowed its button and painted onto the row below. The cap
            // still allows a modest enlargement; it is one number (MAX_FONT_SCALE) to retune.
            val base = LocalDensity.current
            val clamped = remember(base) {
                Density(base.density, base.fontScale.coerceIn(1f, MAX_FONT_SCALE))
            }
            CompositionLocalProvider(LocalDensity provides clamped) {
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
                val inFlight by vm.inFlight.collectAsStateWithLifecycle()
                val closing by vm.closing.collectAsStateWithLifecycle()
                val toast by vm.toast.collectAsStateWithLifecycle()
                val profiles by vm.profiles.collectAsStateWithLifecycle()
                val confirmCloses by vm.confirmCloses.collectAsStateWithLifecycle()
                val live by vm.live.collectAsStateWithLifecycle()
                val layoutMode by vm.layoutMode.collectAsStateWithLifecycle()
                val candleTf by vm.candleTf.collectAsStateWithLifecycle()
                val tickTime by vm.tickTime.collectAsStateWithLifecycle()
                val guard by vm.guard.collectAsStateWithLifecycle()
                val closingTickets by vm.closingTickets.collectAsStateWithLifecycle()
                val serverProfiles by vm.serverProfiles.collectAsStateWithLifecycle()
                val serverBusy by vm.serverBusy.collectAsStateWithLifecycle()
                val serverError by vm.serverError.collectAsStateWithLifecycle()
                val history by vm.history.collectAsStateWithLifecycle()
                val historyLoading by vm.historyLoading.collectAsStateWithLifecycle()
                val strategies by vm.strategies.collectAsStateWithLifecycle()
                val strategy by vm.strategy.collectAsStateWithLifecycle()
                val armed by vm.armed.collectAsStateWithLifecycle()
                val accountError by vm.accountError.collectAsStateWithLifecycle()
                val accountOk by vm.accountOk.collectAsStateWithLifecycle()
                val accountBusy by vm.accountBusy.collectAsStateWithLifecycle()
                val certInfo by vm.certInfo.collectAsStateWithLifecycle()
                val certError by vm.certError.collectAsStateWithLifecycle()

                val snackbar = remember { SnackbarHostState() }

                // A trader watching a quote must not have the screen time out mid-trade -- but
                // there is no reason to hold the screen awake on the Connect or Login screens.
                DisposableEffect(screen) {
                    val keepAwake = screen == Screen.TRADE
                    if (keepAwake) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

                    // FLAG_SECURE on the screens where a secret is typed or shown: the account
                    // screen (broker password, account numbers) and the certificates screen (the
                    // p12 password). It blocks screenshots and screen recording, and excludes the
                    // window from the recent-apps thumbnail the OS may persist.
                    if (screen == Screen.ACCOUNTS || screen == Screen.CERTS)
                        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)

                    onDispose {
                        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                        window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    }
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
                        val onEnterArmed: () -> Unit = { vm.placeArmed() }
                        // SPLIT's direct BUY/SELL buttons place a specific side (no arming).
                        val onPlace: (String) -> Unit = { vm.placeOrder(it) }
                        val onSelectLayout: (LayoutMode) -> Unit = { vm.setLayout(it) }
                        val onCloseArmed: () -> Unit = { vm.closeArmed() }
                        val onArmedSide: (String) -> Unit = { vm.setArmedSide(it) }
                        val onCloseWhere: (String) -> Unit = { vm.closeWhere(it) }
                        val onClosePosition: (Long) -> Unit = { vm.closeOne(it) }
                        val onLogin: () -> Unit = { vm.goto(Screen.LOGIN) }
                        val onBackToTrade: () -> Unit = { vm.goto(Screen.TRADE) }
                        val onPickProfile: (String) -> Unit = { vm.login(it) }
                        val onSaveConnection: (String, String) -> Unit = { u, t -> vm.saveConnection(u, t) }
                        val onDisconnect: () -> Unit = { vm.disconnect() }
                        val onToggleConfirm: (Boolean) -> Unit = { vm.setConfirmCloses(it) }
                        val onSettings: () -> Unit = { vm.goto(Screen.SETTINGS) }
                        val onCycleLayout: () -> Unit = { vm.cycleLayout() }
                        val onSelectTf: (Int) -> Unit = { vm.setCandleTf(it) }
                        val onSetGuard: (Boolean?, Double?, String?) -> Unit =
                            { en, tp, side -> vm.setGuard(en, tp, side) }
                        val onAccounts: () -> Unit = { vm.goto(Screen.ACCOUNTS) }
                        val onServers: () -> Unit = { vm.goto(Screen.SERVERS) }
                        val onSelectServer: (String) -> Unit = { vm.selectServer(it) }
                        val onSaveServer: (String?, String, String, String) -> Unit =
                            { id, label, url, tok -> vm.saveServer(id, label, url, tok) }
                        val onDeleteServer: (String) -> Unit = { vm.deleteServer(it) }
                        val onHistory: () -> Unit = { vm.goto(Screen.HISTORY) }   // goto() triggers the fetch
                        val onRefreshHistory: () -> Unit = { vm.fetchHistory() }
                        val onStrategies: () -> Unit = { vm.goto(Screen.STRATEGIES) }
                        val onCerts: () -> Unit = { vm.goto(Screen.CERTS) }
                        val onSaveCerts: (ByteArray, ByteArray, String) -> Unit =
                            { ca, p12, pw -> vm.saveCerts(ca, p12, pw) }
                        val onClearCerts: () -> Unit = { vm.clearCerts() }
                        val onLoginWith: (String, String, String, Boolean, String) -> Unit =
                            { l, p, s, save, label -> vm.loginWith(l, p, s, save, label) }
                        val onDeleteAccount: (String) -> Unit = { vm.deleteAccount(it) }
                        val onLogoutMt5: () -> Unit = { vm.logoutMt5() }
                        // Stay on the Accounts page after switching: you may want to check the
                        // status card, or fix a second account. Only the LOGIN screen jumps away.
                        val onSwitchProfile: (String) -> Unit = { vm.login(it, goToTrade = false) }
                        val onBackToSettings: () -> Unit = { vm.goto(Screen.SETTINGS) }
                        val onBackToStrategies: () -> Unit = { vm.goto(Screen.STRATEGIES) }
                        val onOpenStrategy: (String) -> Unit = { vm.openStrategy(it) }
                        val onSetStrategy: (String, Boolean?, JsonObject) -> Unit =
                            { id, en, params -> vm.setStrategy(id, en, params) }
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
                            busy = accountBusy,
                            onPick = callbacks.onPickProfile,
                            onAddAccount = callbacks.onAccounts,
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
                            inFlight = inFlight,
                            closing = closing,
                            onLot = callbacks.onLot,
                            onStepLot = callbacks.onStepLot,
                            onSl = callbacks.onSl,
                            onTp = callbacks.onTp,
                            onEnterArmed = callbacks.onEnterArmed,
                            onCloseArmed = callbacks.onCloseArmed,
                            onArmedSide = callbacks.onArmedSide,
                            onPlace = callbacks.onPlace,
                            armed = armed,
                            onCloseWhere = callbacks.onCloseWhere,
                            onClosePosition = callbacks.onClosePosition,
                            closingTickets = closingTickets,
                            onLogin = callbacks.onLogin,
                            onSettings = callbacks.onSettings,
                            onHistory = callbacks.onHistory,
                            confirmCloses = confirmCloses,
                            serverUrl = vm.baseUrl,
                            live = live,
                            strategies = strategies,
                            mode = layoutMode,
                            onCycleLayout = callbacks.onCycleLayout,
                            tickTime = tickTime,
                            candleTf = candleTf,
                            onSelectTf = callbacks.onSelectTf,
                            guard = guard,
                            onSetGuard = callbacks.onSetGuard,
                            modifier = inset,
                        )

                        Screen.SETTINGS -> SettingsScreen(
                            serverUrl = vm.baseUrl,
                            strategies = strategies,
                            live = live,
                            confirmCloses = confirmCloses,
                            layoutMode = layoutMode,
                            health = health,
                            certInfo = certInfo,
                            onToggleConfirm = callbacks.onToggleConfirm,
                            onSelectLayout = callbacks.onSelectLayout,
                            onServers = callbacks.onServers,
                            onAccounts = callbacks.onAccounts,
                            onStrategies = callbacks.onStrategies,
                            onCerts = callbacks.onCerts,
                            onDisconnect = callbacks.onDisconnect,
                            onBack = callbacks.onBackToTrade,
                            modifier = inset,
                        )

                        Screen.CERTS -> CertsScreen(
                            certInfo = certInfo,
                            error = certError,
                            onSave = callbacks.onSaveCerts,
                            onClear = callbacks.onClearCerts,
                            onBack = callbacks.onBackToSettings,
                            modifier = inset,
                        )

                        Screen.ACCOUNTS -> AccountsScreen(
                            profiles = profiles,
                            health = health,
                            live = live,
                            busy = accountBusy,
                            error = accountError,
                            okTick = accountOk,
                            // A pure function of the URL, so Compose recomposes it properly.
                            passwordInClear = TradingViewModel.passwordInClear(vm.baseUrl),
                            onLoginProfile = callbacks.onSwitchProfile,
                            onLoginWith = callbacks.onLoginWith,
                            onDelete = callbacks.onDeleteAccount,
                            onLogout = callbacks.onLogoutMt5,
                            onBack = callbacks.onBackToSettings,
                            modifier = inset,
                        )

                        Screen.STRATEGIES -> StrategiesScreen(
                            strategies = strategies,
                            live = live,
                            onOpen = callbacks.onOpenStrategy,
                            onBack = callbacks.onBackToSettings,
                            modifier = inset,
                        )

                        Screen.STRATEGY -> StrategyScreen(
                            s = strategy,
                            quote = quote,
                            live = live,
                            onSet = callbacks.onSetStrategy,
                            onBack = callbacks.onBackToStrategies,
                            modifier = inset,
                        )

                        Screen.SERVERS -> ServerScreen(
                            profiles = serverProfiles,
                            current = vm.baseUrl,
                            busy = serverBusy,
                            error = serverError,
                            onSelect = callbacks.onSelectServer,
                            onSave = callbacks.onSaveServer,
                            onDelete = callbacks.onDeleteServer,
                            onBack = callbacks.onBackToSettings,
                            modifier = inset,
                        )

                        Screen.HISTORY -> HistoryScreen(
                            history = history,
                            loading = historyLoading,
                            serverUrl = vm.baseUrl,
                            onRefresh = callbacks.onRefreshHistory,
                            onBack = callbacks.onBackToTrade,
                            modifier = inset,
                        )
                    }
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
