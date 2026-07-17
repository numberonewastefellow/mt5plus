package com.xauorderpad.ui

import android.app.Application
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.xauorderpad.data.Feed
import com.xauorderpad.data.ServerProfile
import com.xauorderpad.net.Api
import com.xauorderpad.net.ApiResult
import com.xauorderpad.net.HistoryResponse
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
enum class Screen { CONNECT, LOGIN, TRADE, SETTINGS, STRATEGIES, STRATEGY, ACCOUNTS, CERTS, HISTORY, SERVERS }

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

    /**
     * MT5 BROKER time of the latest tick (epoch seconds), for the Scalp candle countdown. Already
     * arrives on the snapshot (`tick_time`); it was parsed and discarded until now. Its own slice so
     * the countdown re-anchors on each tick without dragging other consumers into a per-tick recompose.
     */
    val tickTime: StateFlow<Long?> = feed
        .map { it?.tickTime }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /**
     * The account P&L guard as the SERVER reports it. The switch/amount on screen reflect what is
     * actually armed on the box, so a guard armed on one phone shows armed on another, and an
     * account switch (which disarms it server-side) flips the switch off here automatically.
     */
    val guard: StateFlow<GuardUi> = feed
        .map {
            val g = it?.guard
            GuardUi(
                enabled = g?.enabled == true,
                target = g?.targetPl ?: 0.0,
                side = g?.side ?: "profit",
                fired = g?.fired == true,
            )
        }
        .distinctUntilChanged()
        .stateIn(viewModelScope, SharingStarted.Eagerly, GuardUi())

    val health: StateFlow<Health> = feed
        .map {
            Health(
                healthy = it?.healthy == true,
                connected = it?.connected == true,
                loggedOut = it?.isLoggedOut == true,
                isDemo = it?.account?.isDemo,
                login = it?.account?.login,
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

    /**
     * How many order/close requests are on the wire right now. Increments as each BUY/SELL/CLOSE
     * launches and decrements when it returns. It is NOT a gate -- the buttons stay live -- it only
     * feeds a small "N sending" indicator so a fast burst is visible while the grid catches up.
     */
    private val _inFlight = MutableStateFlow(0)
    val inFlight: StateFlow<Int> = _inFlight.asStateFlow()

    /**
     * Tickets with a close already on the wire. Rapid CLOSE taps read the same (snapshot-lagged)
     * newest ticket, so without this taps 2..N all target it and only the first wins. The armed
     * target skips tickets in this set, so N taps close the N newest DISTINCT positions. Cleared
     * per ticket when its close returns -- a failed close frees it to be retried; a successful one
     * is already gone from the next snapshot. Mutated only on the Main dispatcher (viewModelScope).
     */
    private val _pendingCloses = MutableStateFlow<Set<Long>>(emptySet())

    /**
     * Tickets the user has asked to close, for the SPLIT grid's row-close ANIMATION. Distinct from
     * [_pendingCloses] (the dedup guard, which clears the instant the HTTP call returns): a ticket
     * stays here from the tap until the position actually LEAVES the next server snapshot, so the row
     * animates and holds its "closing" state until the close is CONFIRMED -- not merely acknowledged.
     * A failed/timed-out close removes it here immediately so the row snaps back. Pruned against the
     * live snapshot in `init` below. Only the SPLIT positions grid reads this; other layouts ignore it.
     */
    private val _closingTickets = MutableStateFlow<Set<Long>>(emptySet())
    val closingTickets: StateFlow<Set<Long>> = _closingTickets.asStateFlow()

    // Housekeeping: once a closed ticket actually leaves the snapshot (server dropped it), remove it
    // from _closingTickets so the set stays bounded. Placed AFTER both `positions` and `_closingTickets`
    // are initialised -- an init block earlier in the class ran while `_closingTickets` was still null
    // (the Eagerly-started `positions` emits synchronously on first collect), which crashed at launch.
    init {
        viewModelScope.launch {
            positions.collect { snap ->
                if (_closingTickets.value.isNotEmpty()) {
                    val live = snap.items.mapTo(HashSet()) { it.ticket }
                    val kept = _closingTickets.value.filterTo(HashSet()) { it in live }
                    if (kept.size != _closingTickets.value.size) _closingTickets.value = kept
                }
            }
        }
    }

    /**
     * Separate from [busy] ON PURPOSE. The bulk-close bar used to be gated on the order path -- so a
     * BUY hanging on a flaky cellular link greyed out CLOSE ALL / CLOSE LOSING / CLOSE PROFIT for
     * the whole call. That is the panic path being disabled by the order path, at the exact moment
     * gold is gapping against you. The flatten buttons must never be blocked by an unrelated request.
     */
    private val _closing = MutableStateFlow(false)
    val closing: StateFlow<Boolean> = _closing.asStateFlow()

    /**
     * Account operations (login/switch/delete/logout) have their own busy flag, for the same
     * reason [_closing] does. A profile login can take up to 60 s (it launches terminal64.exe and
     * waits on a broker handshake); if that shared [_busy] with the trading path, a slow login
     * would grey out BUY / SELL / single-CLOSE on the Trade screen for the whole minute. Only the
     * account screens read this.
     */
    private val _accountBusy = MutableStateFlow(false)
    val accountBusy: StateFlow<Boolean> = _accountBusy.asStateFlow()

    // ---- client certificate (the EC2 mTLS path) ----------------------------
    private val _certInfo = MutableStateFlow(com.xauorderpad.data.CertStore.info())
    val certInfo: StateFlow<com.xauorderpad.data.CertStore.Info?> = _certInfo.asStateFlow()
    private val _certError = MutableStateFlow<String?>(null)
    val certError: StateFlow<String?> = _certError.asStateFlow()

    /**
     * Persist an uploaded CA + client PKCS12, rebuild the HTTP client so it takes effect WITHOUT a
     * restart, and refresh the displayed info. CertStore.save validates first (a wrong p12 password
     * or a non-PEM CA throws), so a bad upload is reported here rather than as an opaque handshake
     * failure at connect time.
     */
    fun saveCerts(ca: ByteArray, p12: ByteArray, p12Password: String) {
        try {
            com.xauorderpad.data.CertStore.save(ca, p12, p12Password)
            Feed.reloadTls()
            _certInfo.value = com.xauorderpad.data.CertStore.info()
            _certError.value = null
            say("Certificate loaded", error = false)
        } catch (e: Exception) {
            _certError.value =
                "Could not load: ${e.message ?: "invalid certificate or wrong p12 password"}"
        }
    }

    fun clearCerts() {
        com.xauorderpad.data.CertStore.clear()
        Feed.reloadTls()
        _certInfo.value = null
        _certError.value = null
        say("Certificate removed", error = false)
    }

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

    /** Active trade-screen layout. Persisted; see Secrets.layoutMode. The top-bar chip cycles it. */
    private val _layoutMode = MutableStateFlow(
        LayoutMode.values().getOrElse(Feed.secrets.layoutMode) { LayoutMode.CLASSIC }
    )
    val layoutMode: StateFlow<LayoutMode> = _layoutMode.asStateFlow()

    fun cycleLayout() {
        val modes = LayoutMode.values()
        val next = modes[(_layoutMode.value.ordinal + 1) % modes.size]
        secrets.layoutMode = next.ordinal
        _layoutMode.value = next
    }

    /** Set a specific layout directly (the Settings "Default screen" picker). Persisted, so it is
     *  both "switch now" and "the layout the app reopens on". */
    fun setLayout(mode: LayoutMode) {
        secrets.layoutMode = mode.ordinal
        _layoutMode.value = mode
    }

    /** Scalp candle timeframe in MINUTES (1/2/5/15/30/60/240). Persisted; see Secrets.candleTf. */
    private val _candleTf = MutableStateFlow(Feed.secrets.candleTf)
    val candleTf: StateFlow<Int> = _candleTf.asStateFlow()

    fun setCandleTf(minutes: Int) {
        secrets.candleTf = minutes
        _candleTf.value = minutes
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
    val armed: StateFlow<ArmedUi> = combine(feed, _armedSide, _pendingCloses) { snap, side, pending ->
        val want = if (side == "sell") "SELL" else "BUY"
        val mine = snap?.openPositions.orEmpty().filter { it.side == want }
        // Target the newest position that does NOT already have a close on the wire, so rapid CLOSE
        // taps walk down distinct tickets instead of all fighting over the same newest one.
        val newest = mine.filter { it.ticket !in pending }.maxWithOrNull(
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

    // ---- saved server profiles (the "Switch server" list) ----------------

    private val _serverProfiles = MutableStateFlow(Feed.secrets.serverProfiles)
    val serverProfiles: StateFlow<List<ServerProfile>> = _serverProfiles.asStateFlow()
    private val _serverBusy = MutableStateFlow(false)
    val serverBusy: StateFlow<Boolean> = _serverBusy.asStateFlow()
    private val _serverError = MutableStateFlow<String?>(null)
    val serverError: StateFlow<String?> = _serverError.asStateFlow()

    /**
     * Verify a saved server, then switch to it ONLY if it answers. The probe is a throwaway [Api]
     * pointed at the candidate's URL+token (reusing the shared TLS-configured client), hitting the
     * authenticated /api/accounts: Ok = reachable + scheme/TLS ok + token valid; Unauthorized = bad
     * token; TimedOut/Failed = unreachable. On success we run the same commit path as saveConnection.
     */
    fun selectServer(id: String) = viewModelScope.launch {
        val p = _serverProfiles.value.find { it.id == id } ?: return@launch
        _serverBusy.value = true
        _serverError.value = null
        try {
            val probe = Api(http = { Feed.http }, baseUrl = { p.url }, token = { p.token })
            when (val r = probe.accounts()) {
                is ApiResult.Ok -> {
                    secrets.baseUrl = p.url
                    secrets.token = p.token
                    secrets.markConnected()
                    Feed.reconfigure()
                    _screen.value = Screen.TRADE
                }
                is ApiResult.Unauthorized ->
                    _serverError.value = "${p.label}: token rejected — check the token in Edit."
                is ApiResult.TimedOut ->
                    _serverError.value = "${p.label}: timed out — unreachable from this network?"
                is ApiResult.Failed ->
                    _serverError.value = "${p.label}: ${r.message}"
            }
        } finally {
            _serverBusy.value = false
        }
    }

    /** Add (id == null) or update a server profile, then persist. URL/token are normalized in Secrets. */
    fun saveServer(id: String?, label: String, url: String, token: String) {
        val list = _serverProfiles.value.toMutableList()
        val lbl = label.ifBlank { "Server" }
        if (id == null) {
            list += ServerProfile(java.util.UUID.randomUUID().toString(), lbl, url, token)
        } else {
            val i = list.indexOfFirst { it.id == id }
            if (i >= 0) list[i] = list[i].copy(label = lbl, url = url, token = token)
            else list += ServerProfile(id, lbl, url, token)
        }
        secrets.serverProfiles = list
        _serverProfiles.value = secrets.serverProfiles   // read back the normalized copy
    }

    fun deleteServer(id: String) {
        secrets.serverProfiles = _serverProfiles.value.filterNot { it.id == id }
        _serverProfiles.value = secrets.serverProfiles
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
        // Fetch today's activity on entry (on-demand; the screen also has a Refresh button).
        if (s == Screen.HISTORY) fetchHistory()
        if (s == Screen.SERVERS) _serverError.value = null   // drop a stale connect-failure banner
        if (s == Screen.LOGIN || s == Screen.ACCOUNTS) {
            // Drop any stale banner on the way IN. The banner is deliberately sticky WITHIN a
            // visit -- that is the whole point of it -- but it must not survive leaving and
            // coming back, or a week-old "TIMED OUT" sits in red above a status card that reads
            // DEMO on a perfectly healthy session.
            _accountError.value = null
            loadProfiles()
        }
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

    /**
     * Bumped on every SUCCESSFUL account action. The Accounts screen watches it to clear the
     * password field -- and only then.
     *
     * Clearing on send (what this used to do) meant that a typo in the SERVER name -- Exness has
     * a dozen of them, -MT5Trial6, -MT5Trial7, -MT5Trial16 -- cost you the whole password too.
     * Worse, a non-numeric login is rejected CLIENT-side without a request ever leaving the
     * phone, and the password was wiped anyway.
     */
    // Bumped ONLY when a typed-credential login (loginWith) succeeds. It is the single trigger for
    // clearing the new-account form, so nothing else may bump it: a delete or a profile-switch that
    // touched this counter would wipe a password the user is still typing for a different account.
    private val _accountOk = MutableStateFlow(0)
    val accountOk: StateFlow<Int> = _accountOk.asStateFlow()

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

    // ---- today's activity (history) --------------------------------------
    // On-demand only (fetched when the screen opens + the Refresh button). Never auto-polled: the
    // whole point of this screen is a stable snapshot the user can read, not a live-updating book.
    private val _history = MutableStateFlow<HistoryResponse?>(null)
    val history: StateFlow<HistoryResponse?> = _history.asStateFlow()
    private val _historyLoading = MutableStateFlow(false)
    val historyLoading: StateFlow<Boolean> = _historyLoading.asStateFlow()

    fun fetchHistory() = viewModelScope.launch {
        _historyLoading.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.history()) {
                is ApiResult.Ok -> _history.value = r.value
                is ApiResult.Unauthorized -> onUnauthorized(e)
                is ApiResult.TimedOut -> say("Timed out loading today's activity — tap ⟳ to retry", error = true)
                is ApiResult.Failed -> say("History: ${r.message}", error = true)
            }
        } finally {
            _historyLoading.value = false
        }
    }

    /** Log into a SAVED profile. No password leaves the phone -- the server has it. */
    fun login(profileId: String, goToTrade: Boolean = true) = viewModelScope.launch {
        _accountBusy.value = true
        val e = Feed.epoch
        try {
            handleLogin(Feed.api.login(profileId), e, goToTrade)
        } finally {
            _accountBusy.value = false
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
        save: Boolean,
        label: String,
    ) = viewModelScope.launch {
        // The form is validated before this is reachable (AccountFormErrors), so this is the
        // belt to that braces: never send a request we know the server will reject.
        val id = login.trim().toLongOrNull()
        if (id == null || password.isEmpty() || server.isBlank()) {
            _accountError.value = "login, password and server are required"
            return@launch
        }
        // BLOCK, not just warn: a REAL broker password must never leave the phone in cleartext.
        // The transport layer permits cleartext for the LAN dev case (the platform cannot scope
        // it to private ranges), but that is for the API token the user accepted, not a broker
        // credential. Use https (the EC2 mTLS box) or loopback to add an account.
        if (passwordInClear(Feed.secrets.baseUrl)) {
            _accountError.value =
                "Refused: this server is plain HTTP, so a broker password would be sent " +
                    "UNENCRYPTED. Add the account over https (the EC2 box) or on the desktop; " +
                    "the phone can still trade a saved account here."
            return@launch
        }
        _accountBusy.value = true
        val e = Feed.epoch
        try {
            val ok = handleLogin(
                // The password is NOT trimmed. An MT5 password may legitimately contain spaces,
                // and silently trimming one is how you lock somebody out of their own account.
                // The UI warns about stray whitespace instead; it does not "fix" it.
                Feed.api.loginWith(id, password, server.trim(), save, label.trim()),
                e,
                goToTrade = true,
            )
            // Clear the typed form ONLY here, and ONLY on success. _accountOk used to be bumped by
            // delete and by profile-switch as well, and the screen wipes the whole form -- password
            // included -- on any bump. So forgetting an unrelated row, or switching accounts, would
            // erase the new-account credentials you were half-way through typing. The form belongs
            // to this path alone, so this path alone clears it.
            if (ok) _accountOk.value += 1
            // Whether or not it worked, the saved list may have changed (save happens only on a
            // SUCCESSFUL login server-side, so this is how the new row appears).
            loadProfiles()
        } finally {
            _accountBusy.value = false
        }
    }

    /** @return true iff the login/switch succeeded. */
    private fun handleLogin(r: ApiResult<com.xauorderpad.net.LoginResult>, epoch: Int, goToTrade: Boolean): Boolean {
        when (r) {
            is ApiResult.Ok -> {
                val prev = r.value.prevOpen ?: 0
                // A save that the user ASKED for but that failed must not be reported as a plain
                // success. The account cannot be auto-restored without a stored password, so this
                // is a sticky warning, not a 4-second toast -- and it takes priority over the
                // normal "logged in" line so it cannot be missed.
                if (r.value.saved == false) {
                    _accountError.value =
                        "Logged in, but the password was NOT saved on the server" +
                            (r.value.saveError?.let { " ($it)" } ?: "") +
                            ". This account cannot be auto-restored — keep the password to re-add it."
                    say("Logged in — but NOT saved on the server", error = true)
                } else {
                    _accountError.value = null      // a clean success is the only thing that clears it
                }
                when {
                    // Switching account leaves the PREVIOUS account's positions OPEN. Staying
                    // silent here would let the user believe they were flat when they are not.
                    prev > 0 ->
                        say("Logged in — $prev position(s) STILL OPEN on the previous account", error = true)
                    r.value.isDemo == false ->
                        say("Logged in — REAL ACCOUNT", error = true)
                    r.value.saved == false -> Unit   // already surfaced above; don't overwrite it
                    else ->
                        say("Logged in (demo)", error = false)
                }
                // The account just changed, which changes the whole book. Feed.snapshot still
                // holds the PREVIOUS account's positions/equity/quote, and it is not cleared when
                // frames stop -- so without this the Trade screen renders the old account's book,
                // with CLOSE ALL / CLOSE LOSING enabled against tickets from a different account,
                // until the next frame lands (which is unbounded if the socket is down while HTTP
                // still works). The old "a stale ticket can only be one that no longer exists"
                // safety argument was reasoned within ONE account; a switch changes the ticket
                // namespace, so it no longer holds. Drop the stale frame and let the feed refill.
                Feed.invalidateSnapshot()
                if (goToTrade) _screen.value = Screen.TRADE
                return true
            }
            is ApiResult.Unauthorized -> { onUnauthorized(epoch); return false }

            // MT5 login is slow (terminal handshake + broker auth, and a cold start LAUNCHES
            // terminal64.exe). A timeout here does not mean it failed -- it may already be logged
            // in, and retrying a login that is still in flight is how you end up switching
            // accounts under yourself. The feed is the authority: if it went through, the next
            // frame stops saying logged_out.
            is ApiResult.TimedOut -> {
                // "the login may still have SUCCEEDED" -- so the terminal may now be on a DIFFERENT
                // account while Feed.snapshot still holds the previous one's positions and P&L, with
                // live per-ticket CLOSE buttons aimed at a foreign book. The Ok and logout branches
                // invalidate for exactly this reason; the timeout is the case where it is MOST likely
                // to be wrong, so it must invalidate too.
                Feed.invalidateSnapshot()
                _accountError.value =
                    "TIMED OUT — the login may still have SUCCEEDED. Watch the status above before retrying."
                say("Timed out — check the status before retrying", error = true)
                return false
            }

            is ApiResult.Failed -> {
                _accountError.value = r.message
                say(r.message, error = true)
                return false
            }
        }
        return false
    }

    fun deleteAccount(profileId: String) = viewModelScope.launch {
        _accountBusy.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.deleteAccount(profileId)) {
                is ApiResult.Ok -> {
                    if (r.value.deleted) {
                        if (r.value.active) {
                            // Forgetting a profile does NOT log you out. The terminal keeps
                            // trading it -- but the password is gone, so if a later login fails,
                            // this session can no longer be restored. A row vanishing while the
                            // account stays live is exactly the kind of quiet inconsistency that
                            // gets someone trading an account they believe they removed.
                            _accountError.value =
                                "Forgotten — but you are STILL LOGGED IN to it. It keeps trading, " +
                                    "and its session can no longer be restored if a login fails."
                        } else {
                            _accountError.value = null
                            say("Account forgotten", error = false)
                        }
                        // NOTE: deliberately does NOT bump _accountOk. That signal clears the typed
                        // new-account form, and forgetting one row must not wipe the credentials you
                        // are half-way through entering for a different one.
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
            _accountBusy.value = false
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
        _accountBusy.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.logout()) {
                is ApiResult.Ok -> {
                    // The account is gone; its positions/equity must not linger on screen.
                    Feed.invalidateSnapshot()
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
            _accountBusy.value = false
        }
    }

    /**
     * True when a typed password would cross the network in the CLEAR.
     *
     * The web UI types the broker password on loopback, where plaintext HTTP is harmless. The
     * phone types it over the network -- a different risk entirely for a REAL broker credential.
     * Tailscale (100.64.0.0/10) is WireGuard, so it is encrypted; https is encrypted; a plain
     * http:// LAN address is not.
     *
     * A WARNING, not a block. It is the user's own network and their call.
     *
     * A pure function of the URL, and the URL is passed to the screen as a parameter -- so
     * Compose recomposes it like any other input. It used to be a `val get()` reading a plain
     * field, which Compose has NO invalidation source for: the one control telling you not to
     * type a real broker password over open Wi-Fi was being re-evaluated by luck.
     */
    companion object {
        fun passwordInClear(baseUrl: String): Boolean {
            val url = baseUrl.trim().lowercase()
            if (url.startsWith("https://")) return false

            var host = url.removePrefix("http://").substringBefore('/')
            // IPv6 literals are bracketed: http://[::1]:8765. Strip the brackets BEFORE the port
            // split -- splitting on ':' first turns "[::1]" into "[", which is why the old
            // `host == "::1"` check could never fire.
            host = if (host.startsWith("[")) {
                host.substringAfter('[').substringBefore(']')
            } else {
                host.substringBefore(':')
            }

            if (host == "127.0.0.1" || host == "localhost" || host == "::1") return false

            // Tailscale hands out 100.64.x.x - 100.127.x.x. The second octet matters:
            // 100.0.0.0/8 at large is ordinary public space, not the CGNAT range.
            val octets = host.split('.')
            if (octets.size == 4 && octets[0] == "100") {
                val second = octets[1].toIntOrNull()
                if (second != null && second in 64..127) return false
            }
            return true
        }
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
            val e = Feed.epoch
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

        // NOTE: no busy gate. Each tap is its own coroutine, so a burst fires concurrently; OkHttp
        // (5 in-flight per host) and the server's single-threaded command queue serialise the actual
        // fills. The grid (WebSocket snapshot) is the confirmation, so success is SILENT -- a toast
        // per fill would bury the screen during a 10/sec scalp. Only failures speak up.
        _inFlight.value += 1
        val e = Feed.epoch
        try {
            when (val r = Feed.api.order(side, lot, sl, tp)) {
                is ApiResult.Ok -> Unit   // filled -> it shows up in the grid; no toast
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
            // finally, not a trailing assignment: a throw in between must not leak the counter.
            _inFlight.value -= 1
        }
    }

    fun closeOne(ticket: Long) = viewModelScope.launch {
        // Mark this ticket as closing so the armed target skips it (rapid taps -> distinct tickets),
        // and so the grid's own row-close cannot double-submit the same ticket. Freed in `finally`:
        // a failed close can be retried; a successful one is gone from the next snapshot anyway.
        _pendingCloses.value = _pendingCloses.value + ticket
        _closingTickets.value = _closingTickets.value + ticket   // SPLIT row-close animation
        _inFlight.value += 1
        val e = Feed.epoch
        try {
            when (val r = Feed.api.close(ticket)) {
                // Keep it in _closingTickets on success: the row holds its "closing" animation until
                // the position actually leaves the next snapshot (pruned in init). Any failure path
                // frees it so the row snaps back.
                is ApiResult.Ok -> Unit   // closed -> the grid drops the row; no toast
                is ApiResult.Unauthorized -> { _closingTickets.value = _closingTickets.value - ticket; onUnauthorized(e) }
                is ApiResult.TimedOut -> {
                    _closingTickets.value = _closingTickets.value - ticket
                    say("TIMED OUT — #$ticket may still have closed. Check the grid.", error = true)
                }
                is ApiResult.Failed -> {
                    _closingTickets.value = _closingTickets.value - ticket
                    say(r.message, error = true)
                }
            }
        } finally {
            _inFlight.value -= 1
            _pendingCloses.value = _pendingCloses.value - ticket
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

    /**
     * Arm/disarm/retune the server-enforced P&L guard. NOT gated on `live`: like the bulk-close
     * bar, this is a protective control and the WebSocket can be stale while HTTP still works. The
     * server validates and is authoritative; the switch reflects the next snapshot.
     */
    fun setGuard(enabled: Boolean?, targetPl: Double?, side: String?) = viewModelScope.launch {
        val e = Feed.epoch
        when (val r = Feed.api.setGuard(enabled, targetPl, side)) {
            is ApiResult.Ok -> {
                val g = r.value
                if (g.enabled) {
                    val sign = if (g.side == "loss") "-" else "+"
                    say("Auto-close armed: ${g.side} at $sign${Fmt.money(g.targetPl)}", error = false)
                } else {
                    say("Auto-close off", error = false)
                }
            }
            is ApiResult.Unauthorized -> onUnauthorized(e)
            is ApiResult.TimedOut ->
                say("Auto-close change TIMED OUT — check the switch.", error = true)
            is ApiResult.Failed -> say("Auto-close: ${r.message}", error = true)
        }
    }

    // ---- toasts ----------------------------------------------------------

    private var toastSeq = 0L
    private fun say(text: String, error: Boolean) { _toast.value = Toast(text, error, ++toastSeq) }
    fun toastShown() { _toast.value = null }
}
