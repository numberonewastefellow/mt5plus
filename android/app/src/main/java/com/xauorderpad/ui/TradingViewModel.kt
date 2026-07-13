package com.xauorderpad.ui

import android.app.Application
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.xauorderpad.data.Feed
import com.xauorderpad.net.ApiResult
import com.xauorderpad.net.Link
import com.xauorderpad.net.Profile
import com.xauorderpad.net.Snapshot
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.math.max
import kotlin.math.round

/**
 * No frame for this long => the feed is dead, whatever the socket claims.
 *
 * The server force-resends at least every 1.0 s even when the state has not changed
 * (`HEARTBEAT_S` in server.py), so this is ten missed heartbeats.
 */
private const val STALE_AFTER_MS = 10_000L

/**
 * SETTINGS -> STRATEGIES -> STRATEGY is a real navigation stack, not a stack of dialogs.
 *
 * The dialog it replaces put both engines' enable switches AND the ladder's PAPER switch in
 * one scrolling column. Nothing said which switch owned which engine, so the control that
 * decides whether REAL orders go out sat next to an unrelated engine's toggle. On this screen
 * an engine can only be armed from its own page, where there is exactly one thing to arm.
 */
enum class Screen { CONNECT, LOGIN, TRADE, SETTINGS, STRATEGIES, STRATEGY, ACCOUNTS }

/** One-shot message for the snackbar. `id` makes repeats of the same text fire again. */
data class Toast(val text: String, val isError: Boolean, val id: Long)

data class OrderForm(
    val lot: String = "0.01",
    val slPoints: String = "",     // blank => 0 => no stop
    val tpPoints: String = "",
)

/**
 * Owns screen state and the order form.
 *
 * It does NOT own the socket -- see [Feed], which is app-scoped so the foreground service and
 * the UI share exactly one stream.
 *
 * The UI is fed NARROW, DEDUPLICATED SLICES rather than the raw Snapshot. See UiState.kt for
 * why: handing the whole snapshot to Compose makes every bid tick recompose the order form.
 */
class TradingViewModel(app: Application) : AndroidViewModel(app) {

    init { Feed.init(app) }

    private val secrets get() = Feed.secrets
    private val feed: StateFlow<Snapshot?> get() = Feed.snapshot

    val link: StateFlow<Link> get() = Feed.link

    /**
     * Is the feed actually LIVE right now?
     *
     * This exists because `snapshot` is NOT cleared when the socket dies -- the last frame just
     * sits there. Everything derived from it (health, quote, P&L) therefore keeps reading as
     * current: `health.healthy` stays true, so BUY/SELL would stay enabled and the bid/ask would
     * keep displaying a frozen price as if it were live.
     *
     * That is the dangerous case, and it is not hypothetical: the WebSocket can die while plain
     * HTTP still works (proxy or idle timeout). A tap on BUY then places a REAL order against a
     * price that has since moved, and it SUCCEEDS.
     *
     * So: one source of truth for staleness -- and it takes BOTH halves.
     *
     *  - The SOCKET must be up. `Link.Up` is the only state in which frames can arrive.
     *  - FRAMES must actually be arriving. An open socket is not a moving feed: the server
     *    can hold the connection while its MT5 worker is wedged, and OkHttp only notices a
     *    silently-reaped socket when a ping goes unanswered (5 s, see Feed). Between those
     *    pings `Link.Up` is a claim, not a fact.
     *
     * The age is measured from the LOCAL arrival time of the newest frame, never from
     * `Snapshot.ts`. That field is the SERVER's clock, and the phone's clock can be minutes
     * off it -- comparing the two would compute an age made of clock skew and could read
     * "stale" on a perfectly live feed, or (worse) "live" on a dead one.
     *
     * 10 s is the threshold because the server force-resends at least every 1.0 s even when
     * nothing has changed (`HEARTBEAT_S`, server.py). Ten missed heartbeats is not a quiet
     * market; it is a dead feed.
     *
     * Fail-closed: before the first frame, `lastFrameAt` is 0 and this is false.
     */
    private val lastFrameAt: StateFlow<Long> = feed
        .filterNotNull()
        .map { SystemClock.elapsedRealtime() }   // monotonic: immune to clock changes and skew
        .stateIn(viewModelScope, SharingStarted.Eagerly, 0L)

    val live: StateFlow<Boolean> = combine(
        Feed.link,
        lastFrameAt,
        // The socket going quiet produces NO emission, so nothing would re-evaluate the age
        // and `live` would stay stuck at its last value forever. This tick is what makes
        // silence itself an event.
        flow { while (true) { emit(Unit); delay(1_000) } },
    ) { link, at, _ ->
        link is Link.Up && at != 0L && SystemClock.elapsedRealtime() - at < STALE_AFTER_MS
    }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    // ---- derived UI slices (C2) -----------------------------------------
    // Each one is distinctUntilChanged, so a field that did not move does not invalidate the
    // composable that reads it. A price tick touches `quote` and `positions` (profit moves)
    // and nothing else.

    val quote: StateFlow<Quote> = feed
        .map { Quote(it?.bid, it?.ask, it?.spreadPoints, it?.digits) }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, Quote())

    val health: StateFlow<Health> = feed
        .map {
            Health(
                healthy = it?.healthy == true,
                connected = it?.connected == true,
                loggedOut = it?.isLoggedOut == true,
                isDemo = it?.account?.isDemo,
                server = it?.account?.server,
                error = it?.error,
            )
        }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, Health())

    val account: StateFlow<AccountUi> = feed
        .map {
            AccountUi(
                equity = it?.account?.equity,
                floatingPl = it?.floatingPl,
                openCount = it?.openPositions?.size ?: 0,
                currency = it?.account?.currency,
            )
        }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, AccountUi())

    /** Broker constraints. Change ~never, so this almost never re-emits. */
    val limits: StateFlow<Limits> = feed
        .map {
            Limits(
                volumeMin = it?.volumeMin?.takeIf { v -> v > 0 } ?: 0.01,
                volumeStep = it?.volumeStep?.takeIf { v -> v > 0 } ?: 0.01,
                digits = it?.digits ?: 2,
            )
        }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, Limits())

    val positions: StateFlow<PositionsUi> = feed
        .map { PositionsUi(it?.openPositions.orEmpty().toImmutableList(), it?.digits ?: 2) }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, PositionsUi(persistentListOf()))

    /** Server-side strategy engines, straight off the feed. Ordered for a stable UI. */
    val strategies: StateFlow<StrategiesUi> = feed
        .map { s ->
            StrategiesUi(
                s?.strategies.orEmpty().values.sortedBy { it.id }.toImmutableList())
        }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, StrategiesUi(persistentListOf()))

    // ---- screen / form state --------------------------------------------

    // isConfigured now means "has a URL AND the user pressed CONNECT" -- so a fresh install with
    // baked-in debug defaults lands on CONNECT with the fields prefilled, rather than silently
    // dialling a server the user never got to see.
    private val _screen = MutableStateFlow(
        if (Feed.secrets.isConfigured) Screen.TRADE else Screen.CONNECT
    )
    val screen: StateFlow<Screen> = _screen.asStateFlow()

    private val _form = MutableStateFlow(OrderForm())
    val form: StateFlow<OrderForm> = _form.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    /**
     * Separate from [busy] ON PURPOSE. `busy` is set by placeOrder/closeOne/login, and the
     * bulk-close bar used to be gated on it -- so a BUY hanging on a flaky cellular link
     * greyed out CLOSE ALL / CLOSE LOSING / CLOSE PROFIT for the whole call. That is the
     * panic path being disabled by the order path, at the exact moment gold is gapping
     * against you. The flatten buttons must never be blocked by an unrelated request.
     */
    private val _closing = MutableStateFlow(false)
    val closing: StateFlow<Boolean> = _closing.asStateFlow()

    private val _toast = MutableStateFlow<Toast?>(null)
    val toast: StateFlow<Toast?> = _toast.asStateFlow()

    private val _profiles = MutableStateFlow<List<Profile>>(emptyList())
    val profiles: StateFlow<List<Profile>> = _profiles.asStateFlow()

    /** Whether a bulk close asks first. User-settable; see Secrets.confirmCloses. */
    private val _confirmCloses = MutableStateFlow(Feed.secrets.confirmCloses)
    val confirmCloses: StateFlow<Boolean> = _confirmCloses.asStateFlow()

    fun setConfirmCloses(v: Boolean) {
        secrets.confirmCloses = v
        _confirmCloses.value = v
    }

    // ---- armed side ------------------------------------------------------

    private val _armedSide = MutableStateFlow(Feed.secrets.armedSide)

    /**
     * The armed side, plus the ticket CLOSE would actually close.
     *
     * On a hedging account the opposite side does NOT close a position -- it opens a new one --
     * so the exit has to name a ticket. We pick the NEWEST on the armed side (LIFO): the natural
     * unwind of a pyramid. `time` is broker seconds and can tie when several fills land in the
     * same second, so ticket breaks the tie -- MT5 tickets increase with time, which makes the
     * choice deterministic instead of dependent on list order.
     */
    val armed: StateFlow<ArmedUi> = combine(feed, _armedSide) { snap, side ->
        val want = if (side == "sell") "SELL" else "BUY"
        val mine = snap?.openPositions.orEmpty().filter { it.side == want }
        val newest = mine.maxWithOrNull(
            compareBy<com.xauorderpad.net.Position> { it.time ?: 0L }.thenBy { it.ticket }
        )
        ArmedUi(
            side = side,
            targetTicket = newest?.ticket,
            targetEntry = newest?.priceOpen,
            count = mine.size,
            digits = snap?.digits ?: 2,
        )
    }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, ArmedUi(side = Feed.secrets.armedSide))

    fun setArmedSide(side: String) {
        val s = if (side == "sell") "sell" else "buy"
        secrets.armedSide = s
        _armedSide.value = s
    }

    /** ENTRY: trade the armed side. The button never has to know which way it points. */
    fun placeArmed() = placeOrder(_armedSide.value)

    /**
     * EXIT: close the newest position on the armed side.
     *
     * Deliberately NOT gated on `live`, unlike entry -- same reasoning as the bulk-close bar. A
     * dead socket is not a dead server (the WebSocket can drop while HTTP still works), and this
     * is the way OUT. Refusing to even ask, while a position runs against you, is the worse
     * failure.
     *
     * The ticket comes from the last snapshot, which may be stale -- but a stale ticket can only
     * be a ticket that no longer exists, and the server rejects that loudly. It can never close
     * the WRONG position, because tickets are unique and never reused.
     */
    fun closeArmed() {
        val t = armed.value.targetTicket
        if (t == null) {
            say("No ${_armedSide.value.uppercase()} position to close", error = true)
            return
        }
        closeOne(t)
    }

    val baseUrl: String get() = secrets.baseUrl
    val token: String get() = secrets.token

    // ---- strategy navigation ---------------------------------------------

    private val _openId = MutableStateFlow<String?>(null)

    /**
     * The engine whose page is open, re-read from the LIVE feed every frame rather than
     * captured when the row was tapped. A snapshot taken at tap time would freeze: the page
     * would keep showing "disabled" while the engine armed, or -- far worse -- keep showing
     * "armed" after the server had killed it. The page must always show the engine as it IS.
     */
    val strategy: StateFlow<com.xauorderpad.net.StrategyStatus?> =
        combine(strategies, _openId) { list, id ->
            if (id == null) null else list.items.firstOrNull { it.id == id }
        }
            .distinctUntilChanged()
            .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    fun openStrategy(id: String) {
        _openId.value = id
        _screen.value = Screen.STRATEGY
    }

    // ---- connect ---------------------------------------------------------

    fun saveConnection(url: String, tok: String) {
        secrets.baseUrl = url          // normalizes scheme + port
        secrets.token = tok
        secrets.markConnected()        // must precede reconfigure(): Feed.start() gates on this
        Feed.reconfigure()             // also bumps the epoch, invalidating in-flight 401s
        _screen.value = Screen.TRADE
    }

    /**
     * Log out: drop the socket, forget the token, and go back to the Connect screen so the user
     * can see (and change) exactly which server they are about to talk to.
     *
     * The base URL survives, so the form comes back prefilled rather than empty.
     *
     * The epoch bump inside reconfigure() is what makes this safe: any request still in flight
     * against the OLD token cannot come back and 401 us into a confusing state afterwards.
     */
    fun disconnect() {
        secrets.disconnect()
        Feed.reconfigure()             // isConfigured is now false -> start() returns without a socket
        _screen.value = Screen.CONNECT
    }

    /**
     * The socket closed 4401, or an API call returned 401.
     *
     * Two guards, both needed (B8):
     *
     *  - `epoch`: a 401 from a request issued BEFORE the user re-entered their token must not
     *    wipe the NEW token. Without this, a slow /order that 401s and lands after a
     *    successful reconnect would clear a perfectly good token and bounce the user out.
     *
     *  - `screen`: this is called from BOTH the socket observer and every API call site, so a
     *    single bad token fires it several times. Being already on CONNECT means we have
     *    handled it.
     */
    fun onUnauthorized(callEpoch: Int = Feed.epoch) {
        if (callEpoch != Feed.epoch) return           // stale: the user already fixed it
        if (_screen.value == Screen.CONNECT) return   // already handled
        secrets.clearToken()
        _screen.value = Screen.CONNECT
        say("Token rejected by the server — re-enter it", error = true)
    }

    fun goto(s: Screen) {
        _screen.value = s
        if (s == Screen.LOGIN || s == Screen.ACCOUNTS) loadProfiles()
    }

    // ---- MT5 session -----------------------------------------------------

    /**
     * The last account error, held until the next SUCCESS clears it.
     *
     * A toast is not enough here, and the web UI learned that the hard way: a rejected login that
     * only flashes for four seconds reads as "the button did nothing", and the user taps it again
     * and again. This banner stays on the Accounts screen until something actually works.
     */
    private val _accountError = MutableStateFlow<String?>(null)
    val accountError: StateFlow<String?> = _accountError.asStateFlow()

    fun clearAccountError() { _accountError.value = null }

    /** Public so the Accounts screen can refresh after add/switch/delete. */
    fun loadProfiles() = viewModelScope.launch {
        val e = Feed.epoch
        when (val r = Feed.api.accounts()) {
            is ApiResult.Ok -> _profiles.value = r.value.accounts
            is ApiResult.Unauthorized -> onUnauthorized(e)
            is ApiResult.TimedOut -> say("Timed out loading profiles — pull back and retry", error = true)
            is ApiResult.Failed -> say(r.message, error = true)
        }
    }

    /** Log into a SAVED profile. No password leaves the phone -- the server has it. */
    fun login(profileId: String, goToTrade: Boolean = true) = viewModelScope.launch {
        _busy.value = true
        val e = Feed.epoch
        try {
            handleLogin(Feed.api.login(profileId), e, goToTrade)
        } finally {
            _busy.value = false
        }
    }

    /**
     * Log in with TYPED credentials, optionally saving them to the server's vault.
     *
     * The password is a plain parameter and is deliberately not held anywhere: it goes straight
     * into the request and out of scope. It is never put in a StateFlow, never in the form state,
     * and never logged.
     */
    fun loginWith(
        login: String,
        password: String,
        server: String,
        path: String,
        save: Boolean,
        label: String,
    ) = viewModelScope.launch {
        val id = login.trim().toLongOrNull()
        if (id == null || password.isBlank() || server.isBlank()) {
            _accountError.value = "login, password and server are required"
            return@launch
        }
        _busy.value = true
        val e = Feed.epoch
        try {
            handleLogin(
                Feed.api.loginWith(id, password, server.trim(), path.trim(), save, label.trim()),
                e,
                goToTrade = true,
            )
            // Whether or not it worked, the saved list may have changed (save happens only on a
            // SUCCESSFUL login server-side, so this is how the new row appears).
            loadProfiles()
        } finally {
            _busy.value = false
        }
    }

    private fun handleLogin(r: ApiResult<com.xauorderpad.net.LoginResult>, epoch: Int, goToTrade: Boolean) {
        when (r) {
            is ApiResult.Ok -> {
                _accountError.value = null          // a success is the only thing that clears it
                val prev = r.value.prevOpen ?: 0
                when {
                    // Switching account leaves the PREVIOUS account's positions OPEN. Staying
                    // silent here would let the user believe they were flat when they are not.
                    prev > 0 ->
                        say("Logged in — $prev position(s) STILL OPEN on the previous account", error = true)
                    r.value.isDemo == false ->
                        say("Logged in — REAL ACCOUNT", error = true)
                    else ->
                        say("Logged in (demo)", error = false)
                }
                if (goToTrade) _screen.value = Screen.TRADE
            }
            is ApiResult.Unauthorized -> onUnauthorized(epoch)

            // MT5 login is slow (terminal handshake + broker auth, and a cold start LAUNCHES
            // terminal64.exe). A timeout here does not mean it failed -- it may already be logged
            // in, and retrying a login that is still in flight is how you end up switching
            // accounts under yourself. The feed is the authority: if it went through, the next
            // frame stops saying logged_out.
            is ApiResult.TimedOut -> {
                _accountError.value =
                    "TIMED OUT — the login may still have SUCCEEDED. Watch the status above before retrying."
                say("Timed out — check the status before retrying", error = true)
            }

            is ApiResult.Failed -> {
                _accountError.value = r.message
                say(r.message, error = true)
            }
        }
    }

    fun deleteAccount(profileId: String) = viewModelScope.launch {
        _busy.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.deleteAccount(profileId)) {
                is ApiResult.Ok -> {
                    if (r.value.deleted) {
                        _accountError.value = null
                        say("Account forgotten", error = false)
                    } else {
                        // The server answers 200 {deleted:false} for an unknown id. Reporting that
                        // as success would leave a row on screen that the server says is gone.
                        _accountError.value = "Nothing was deleted — the server did not know that id"
                    }
                    loadProfiles()
                }
                is ApiResult.Unauthorized -> onUnauthorized(e)
                is ApiResult.TimedOut -> _accountError.value =
                    "TIMED OUT — the account may still have been deleted."
                is ApiResult.Failed -> _accountError.value = r.message
            }
        } finally {
            _busy.value = false
        }
    }

    /**
     * Stop driving the terminal.
     *
     * This is NOT a broker logout and it does NOT close anything: MT5 has no real logout, so the
     * positions stay open on the account with nothing watching them. `prev_open` is the count, and
     * it is reported loudly rather than swallowed.
     */
    fun logoutMt5() = viewModelScope.launch {
        _busy.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.logout()) {
                is ApiResult.Ok -> {
                    val prev = r.value.prevOpen ?: 0
                    _accountError.value = null
                    if (prev > 0) {
                        say("Logged out — $prev position(s) STILL OPEN on the account", error = true)
                    } else {
                        say("Logged out of MT5", error = false)
                    }
                }
                is ApiResult.Unauthorized -> onUnauthorized(e)
                is ApiResult.TimedOut -> _accountError.value =
                    "TIMED OUT — the logout may still have gone through."
                is ApiResult.Failed -> _accountError.value = r.message
            }
        } finally {
            _busy.value = false
        }
    }

    /**
     * True when a typed password would cross the network in the CLEAR.
     *
     * The web UI types the broker password on loopback, where plaintext HTTP is harmless. The
     * phone types it over the network -- and that is a different risk entirely for a REAL broker
     * credential. Tailscale (100.64.0.0/10) is WireGuard, so it is encrypted; https is encrypted;
     * a plain http:// LAN address is not.
     *
     * This drives a WARNING, not a block. It is the user's own network and their call.
     */
    val passwordInClear: Boolean
        get() {
            val url = secrets.baseUrl.lowercase()
            if (url.startsWith("https://")) return false
            val host = url.removePrefix("http://").substringBefore(':').substringBefore('/')
            if (host == "127.0.0.1" || host == "localhost" || host == "::1") return false
            // Tailscale hands out 100.64.x.x - 100.127.x.x. Checking the second octet matters:
            // 100.0.0.0/8 at large is ordinary public space, not the CGNAT range.
            val octets = host.split('.')
            if (octets.size == 4 && octets[0] == "100") {
                val second = octets[1].toIntOrNull()
                if (second != null && second in 64..127) return false
            }
            return true
        }

    // ---- strategies (server-side engines; this is a remote control) --------

    /**
     * Enable/disable or retune one engine.
     *
     * Two things this deliberately does NOT do:
     *
     *  - It does not decide anything. Every guard (demo-only, hedging, target vs
     *    spread, kill-switch) lives on the server, where it cannot be bypassed by a
     *    stale phone or a forged request. The phone asks; the server rules.
     *
     *  - It does not assume success. The server answers 200 with the engine's REAL
     *    status, which can be `enabled:false` plus a reason -- and reporting "armed"
     *    on the strength of an HTTP code would be a lie in the dangerous direction.
     *    So we read `enabled` back and say what actually happened.
     */
    fun setStrategy(id: String, enabled: Boolean?, params: JsonObject = JsonObject(emptyMap())) =
        viewModelScope.launch {
            if (!live.value) {
                say("Feed is stale — cannot arm a strategy from a frozen screen", error = true)
                return@launch
            }
            _busy.value = true
            val e = Feed.epoch
            try {
                when (val r = Feed.api.setStrategy(id, enabled, params)) {
                    is ApiResult.Ok -> {
                        val s = r.value
                        when {
                            enabled == true && !s.enabled ->
                                say(s.error ?: "$id refused to arm", error = true)
                            enabled == true ->
                                say("${s.name} armed" + if (s.paper == true) " (PAPER — no orders)" else " — LIVE ORDERS", error = false)
                            enabled == false -> say("${s.name} disabled", error = false)
                            else -> say("${s.name} updated", error = false)
                        }
                    }
                    is ApiResult.Unauthorized -> onUnauthorized(e)
                    is ApiResult.TimedOut -> say(
                        "TIMED OUT — the strategy may still have changed. Check the panel.",
                        error = true,
                    )
                    is ApiResult.Failed -> say(r.message, error = true)
                }
            } finally {
                _busy.value = false
            }
        }

    // ---- order form ------------------------------------------------------

    fun setLot(v: String) { _form.value = _form.value.copy(lot = v) }
    fun setSl(v: String) { _form.value = _form.value.copy(slPoints = v) }
    fun setTp(v: String) { _form.value = _form.value.copy(tpPoints = v) }

    fun stepLot(dir: Int) {
        val l = limits.value
        val cur = Fmt.parseDecimal(_form.value.lot) ?: l.volumeMin
        val next = max(l.volumeMin, snapToStep(cur + dir * l.volumeStep, l))
        // Fmt.lot is Locale.US, so this string parses back cleanly on ANY device locale.
        _form.value = _form.value.copy(lot = Fmt.lot(next))
    }

    /**
     * The broker rejects a volume that is not an exact multiple of `volume_step`, with an
     * opaque retcode. Snapping here turns a typed "0.017" into "0.02" instead of a rejected
     * order the user has to decode.
     */
    private fun snapToStep(v: Double, l: Limits): Double {
        if (l.volumeStep <= 0) return v
        val snapped = l.volumeMin + round((v - l.volumeMin) / l.volumeStep) * l.volumeStep

        // Quantise away the binary-float residue. The arithmetic above is exact in DECIMAL
        // but not in BINARY: with volumeMin = volumeStep = 0.01 it maps a clean 0.10 to
        // 0.09999999999999999, and 0.06 to 0.060000000000000005. kotlinx.serialization then
        // writes those digits verbatim onto the wire, and the server does NOT round --
        // `mt5_worker` hands `volume` straight to `mt5.order_send`, where a value that is not
        // an exact multiple of volume_step is rejected as INVALID_VOLUME (10014).
        //
        // The user sees an opaque broker rejection on a lot size they typed correctly: "the
        // app won't let me trade 0.10". So the fix for a rejected volume must not itself
        // produce a rejected volume.
        return BigDecimal.valueOf(snapped)
            .setScale(stepDecimals(l.volumeStep), RoundingMode.HALF_UP)
            .toDouble()
    }

    /** Decimal places implied by the broker's volume_step (0.01 -> 2, 0.001 -> 3). */
    private fun stepDecimals(step: Double): Int =
        BigDecimal.valueOf(step).stripTrailingZeros().scale().coerceAtLeast(0)

    /** Null when the form is unusable; the reason has already been shown to the user. */
    private fun validatedLot(): Double? {
        val l = limits.value
        val v = Fmt.parseDecimal(_form.value.lot)
        if (v == null || v <= 0) { say("Enter a valid lot size", error = true); return null }
        if (v < l.volumeMin) { say("Minimum lot is ${Fmt.lot(l.volumeMin)}", error = true); return null }
        return snapToStep(v, l)
    }

    // ---- trading ---------------------------------------------------------

    fun placeOrder(side: String) = viewModelScope.launch {
        // Checked BEFORE health, because `health` is derived from a snapshot that may be stale --
        // it would happily report "healthy" from a frame received before the socket died. The
        // UI already greys out BUY/SELL when the feed is not live; this is the guard in depth,
        // so a stale entry cannot slip through even if that gate is bypassed. ENTRY only --
        // closing is never blocked (see closeWhere).
        if (!live.value) {
            say("Feed is stale — price may have moved. Reconnecting…", error = true)
            return@launch
        }
        val h = health.value
        if (!h.healthy) {
            // h.error carries the SPECIFIC reason -- "market closed / symbol not tradable",
            // "AutoTrading is OFF in MT5", "terminal not connected to broker". Far more use
            // than a generic failure message.
            say(h.error ?: "Not connected to the broker", error = true)
            return@launch
        }
        val lot = validatedLot() ?: return@launch

        // POINT DISTANCES, not prices -- the server hardcodes sl_tp_mode="points".
        // Blank => 0 => no stop.
        val sl = Fmt.parseDecimal(_form.value.slPoints) ?: 0.0
        val tp = Fmt.parseDecimal(_form.value.tpPoints) ?: 0.0

        _busy.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.order(side, lot, sl, tp)) {
                is ApiResult.Ok -> say(
                    "${side.uppercase()} ${Fmt.lot(lot)} @ ${Fmt.price(r.value.price, limits.value.digits)}" +
                        "  #${r.value.ticket}",
                    error = false,
                )
                is ApiResult.Unauthorized -> onUnauthorized(e)

                // A timeout is NOT a rejection. The server has no timeout of its own, so the
                // order may well have filled while we stopped listening. Saying "failed" here
                // invites the user to tap BUY again -- and then BOTH fill. Say "unknown", and
                // point them at the grid, which is the only authority on what actually exists.
                is ApiResult.TimedOut -> say(
                    "TIMED OUT — the ${side.uppercase()} may still have FILLED. " +
                        "Check the positions grid before retrying.",
                    error = true,
                )

                is ApiResult.Failed -> {
                    // Surface the BROKER's own words ("Market closed", "Not enough money") rather
                    // than a bare "order failed" that leaves the user guessing.
                    //
                    // The comment is appended only when it ADDS something: MT5 often sets it to
                    // the same string as the message, which rendered as "Market closed — Market
                    // closed". A toast that stutters reads like a bug in the app, and that is the
                    // last thing you want to be wondering about while an order is not going in.
                    val extra = r.comment
                        ?.takeIf { it.isNotBlank() && !it.equals(r.message, ignoreCase = true) }
                        ?.let { " — $it" }
                        .orEmpty()
                    say("${r.message}$extra", error = true)
                }
            }
        } finally {
            // finally, not a trailing assignment: any throw between here and there would leave
            // the order form disabled for the life of the ViewModel.
            _busy.value = false
        }
    }

    fun closeOne(ticket: Long) = viewModelScope.launch {
        _busy.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.close(ticket)) {
                is ApiResult.Ok -> say("Closed #$ticket", error = false)
                is ApiResult.Unauthorized -> onUnauthorized(e)
                is ApiResult.TimedOut -> say(
                    "TIMED OUT — #$ticket may still have closed. Check the grid.",
                    error = true,
                )
                is ApiResult.Failed -> say(r.message, error = true)
            }
        } finally {
            _busy.value = false
        }
    }

    /**
     * Bulk close. filter: "all" | "losing" | "profit".
     *
     * `Api.closeWhere` already maps the server's {ok:false} (which arrives as HTTP 200!) to
     * ApiResult.Failed, so a close that did NOT flatten cannot reach the success branch here.
     * That mapping is the difference between "you are flat" and "you are still holding a
     * losing book and the app told you otherwise".
     */
    fun closeWhere(filter: String) = viewModelScope.launch {
        _closing.value = true
        val e = Feed.epoch
        val label = when (filter) {
            "losing" -> "losing"
            "profit" -> "profitable"
            else -> "open"
        }
        try {
            when (val r = Feed.api.closeWhere(filter)) {
                is ApiResult.Ok -> {
                    // ok == true. `closed == 0` here means nothing MATCHED at live broker prices --
                    // not a failure, but it must not read as a win either.
                    if (r.value.closed == 0) say("No $label positions matched", error = false)
                    else say("Closed ${r.value.closed} $label position(s)", error = false)
                }
                is ApiResult.Unauthorized -> onUnauthorized(e)

                // The close may still be running server-side (up to 5 passes over the book).
                // "FAILED" would be a lie in the dangerous direction -- it reads as "you are
                // still holding everything" when you may in fact be flat, or half-flat.
                is ApiResult.TimedOut -> say(
                    "CLOSE ${label.uppercase()} TIMED OUT — it may still be running. " +
                        "Check the grid; do not assume you are flat.",
                    error = true,
                )

                is ApiResult.Failed -> say("CLOSE ${label.uppercase()} FAILED — ${r.message}", error = true)
            }
        } finally {
            _closing.value = false
        }
    }

    // ---- toasts ----------------------------------------------------------

    private var toastSeq = 0L
    private fun say(text: String, error: Boolean) { _toast.value = Toast(text, error, ++toastSeq) }
    fun toastShown() { _toast.value = null }
}
