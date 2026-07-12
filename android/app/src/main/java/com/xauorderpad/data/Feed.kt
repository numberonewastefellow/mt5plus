package com.xauorderpad.data

import android.content.Context
import com.xauorderpad.net.Api
import com.xauorderpad.net.Link
import com.xauorderpad.net.Snapshot
import com.xauorderpad.net.TradingClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.stateIn
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * App-scoped owner of the live feed. Single source of truth for the socket.
 *
 * ── Why the socket cannot live in the ViewModel ──
 * The ViewModel dies with the Activity, but the whole point of the foreground service is
 * that the feed survives the screen going off. The service's notification (open count,
 * floating P&L, CLOSE ALL / CLOSE LOSING) needs the SAME frames the UI renders -- two
 * sockets would mean double egress and a notification that could silently disagree with the
 * screen about what is open.
 *
 * ── Why the flows are built with flatMapLatest instead of just returning client.snapshot ──
 * This is the fix for a real bug. Reconfiguring (the user re-enters the token) builds a NEW
 * TradingClient with NEW StateFlow instances. Anything that had already subscribed --
 * notably FeedService, which captures the flows once in a `combine` -- would keep collecting
 * the DEAD flow forever. The notification would freeze on a stale quote and a stale P&L
 * while presenting itself as live. That is the single worst failure mode this app has: a
 * frozen number that looks current is far more dangerous than an honest "disconnected".
 *
 * So `snapshot` and `link` are ONE flow each, whose identity NEVER changes for the life of
 * the process. Swapping the client underneath re-plumbs them transparently.
 */
@OptIn(ExperimentalCoroutinesApi::class)
object Feed {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /**
     * ONE OkHttpClient for the whole app, shared by the WebSocket and the REST calls.
     *
     * Previously each TradingClient built its own, and reconfigure() dropped the reference
     * without shutting it down -- leaking the dispatcher's thread pool and its connection
     * pool on every token re-entry.
     */
    val http: OkHttpClient = OkHttpClient.Builder()
        // Ping frames keep the socket alive through carrier NAT. Without them an idle socket
        // on a mobile network can be silently reaped, leaving us "connected" and showing
        // stale prices -- worse than showing offline.
        .pingInterval(20, TimeUnit.SECONDS)
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)   // 0 = none; the socket is long-lived by design
        .callTimeout(15, TimeUnit.SECONDS)       // applies to the REST calls, not the socket
        .build()

    /** Set once, under the lock, before anything reads it. */
    lateinit var secrets: Secrets
        private set

    /** The current client. Its IDENTITY changes on reconfigure; the flows above do not. */
    private val client = MutableStateFlow<TradingClient?>(null)

    /**
     * Bumped on every reconfigure. A 401 from a request issued BEFORE the user fixed their
     * token must not be allowed to wipe the new one -- callers compare this against the value
     * they captured when they made the call. See TradingViewModel.
     */
    private val _epoch = AtomicInteger(0)
    val epoch: Int get() = _epoch.get()

    /** Stable for the life of the process. Never re-created. */
    val snapshot: StateFlow<Snapshot?> =
        client.flatMapLatest { it?.snapshot ?: flowOf(null) }
            .stateIn(scope, SharingStarted.Eagerly, null)

    val link: StateFlow<Link> =
        client.flatMapLatest { it?.link ?: flowOf(Link.Connecting) }
            .stateIn(scope, SharingStarted.Eagerly, Link.Connecting)

    /**
     * The REST surface. Reads baseUrl/token through lambdas rather than capturing them, so a
     * reconfigure is picked up without rebuilding this object.
     */
    val api: Api by lazy {
        Api(http, baseUrl = { secrets.baseUrl }, token = { secrets.token })
    }

    @Synchronized
    fun init(context: Context) {
        if (!::secrets.isInitialized) secrets = Secrets.create(context)
    }

    /**
     * Idempotent. `@Synchronized` because this was a check-then-act race: `@Volatile` makes a
     * read visible but does NOT make check-and-set atomic, so the main thread (Compose reading
     * the flows) and the service thread (start()) could both pass the null check and build two
     * clients -- leaving the UI collecting one while start() ran on the other. Symptom: a
     * permanently blank quote with the banner stuck on "Connecting…", recoverable only by
     * force-stopping the app.
     */
    @Synchronized
    fun start() {
        if (!::secrets.isInitialized || !secrets.isConfigured) return
        val existing = client.value
        if (existing != null) {
            existing.start()
            return
        }
        val c = TradingClient(scope, http, { secrets.baseUrl }, { secrets.token })
        client.value = c
        c.start()
    }

    @Synchronized
    fun stop() {
        client.value?.stop()
    }

    /** After the user edits the URL/token: tear the old socket down and build a fresh client. */
    @Synchronized
    fun reconfigure() {
        _epoch.incrementAndGet()          // invalidate any 401 already in flight
        client.value?.stop()
        client.value = null
        start()
    }

    // Explicit Unit bodies, not expression bodies: a safe-call returns Unit?, which then
    // infers as the function's return type and breaks callers that expect Unit (e.g. an
    // override of ConnectivityManager.NetworkCallback.onAvailable).
    fun retryNow() {
        client.value?.retryNow()
    }

    fun onNetworkAvailable() {
        client.value?.onNetworkAvailable()
    }
}
