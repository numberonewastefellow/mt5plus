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

enum class Screen { CONNECT, LOGIN, TRADE }

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

    val baseUrl: String get() = secrets.baseUrl
    val token: String get() = secrets.token

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
        if (s == Screen.LOGIN) loadProfiles()
    }

    // ---- MT5 session -----------------------------------------------------

    private fun loadProfiles() = viewModelScope.launch {
        val e = Feed.epoch
        when (val r = Feed.api.accounts()) {
            is ApiResult.Ok -> _profiles.value = r.value.accounts
            is ApiResult.Unauthorized -> onUnauthorized(e)
            is ApiResult.TimedOut -> say("Timed out loading profiles — pull back and retry", error = true)
            is ApiResult.Failed -> say(r.message, error = true)
        }
    }

    fun login(profileId: String) = viewModelScope.launch {
        _busy.value = true
        val e = Feed.epoch
        try {
            when (val r = Feed.api.login(profileId)) {
                is ApiResult.Ok -> {
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
                    _screen.value = Screen.TRADE
                }
                is ApiResult.Unauthorized -> onUnauthorized(e)

                // MT5 login is slow (terminal handshake + broker auth). A timeout here does not
                // mean it failed -- it may already be logged in, and retrying a login that is
                // still in flight is how you end up switching accounts under yourself. The feed
                // is the authority: if it went through, the next frame stops saying logged_out.
                is ApiResult.TimedOut -> say(
                    "TIMED OUT — the login may still have succeeded. Watch the banner before retrying.",
                    error = true,
                )

                is ApiResult.Failed -> say(r.message, error = true)
            }
        } finally {
            _busy.value = false
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
                    val extra = r.comment?.takeIf { it.isNotBlank() }?.let { " — $it" }.orEmpty()
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
