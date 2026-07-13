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
    private fun buildClient(): OkHttpClient {
        val b = OkHttpClient.Builder()
            // Ping frames keep the socket alive through carrier NAT. Without them an idle socket
            // on a mobile network can be silently reaped, leaving us "connected" and showing
            // stale prices -- worse than showing offline.
            //
            // 5 s, not 20: this interval IS the detection latency for a silently-reaped socket --
            // OkHttp only discovers the death when a ping goes unanswered. At 20 s the app could
            // sit for twenty seconds showing a frozen quote while `live` still read true, with
            // BUY/SELL and the strategy switches armed against it. A keepalive frame every 5 s is
            // nothing next to the 5 Hz snapshot stream it is protecting.
            .pingInterval(5, TimeUnit.SECONDS)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)   // 0 = none; the socket is long-lived by design
            .callTimeout(15, TimeUnit.SECONDS)       // applies to the REST calls, not the socket
        // mTLS material, when the user has uploaded a cert (the EC2 path). Applied unconditionally
        // once loaded -- but OkHttp only INVOKES the SSLSocketFactory for https:// URLs, so this
        // does NOT affect the plain-HTTP LAN server. One client, both destinations.
        CertStore.tls()?.let { b.sslSocketFactory(it.factory, it.trustManager) }
        return b.build()
    }

    /**
     * ONE OkHttpClient for the whole app, shared by the WebSocket and the REST calls.
     *
     * A `var`, not a `val`: uploading or clearing a certificate rebuilds it (see [reloadTls]).
     * Consumers must read `Feed.http` fresh rather than capture it -- Api takes a `() -> http`
     * supplier, and start() passes the current value into each new TradingClient.
     */
    @Volatile
    var http: OkHttpClient = buildClient()
        private set

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
        // A supplier, not the instance: http is rebuilt when a certificate is uploaded/cleared, and
        // Api must pick up the new client. It already reads baseUrl/token through lambdas for the
        // same reason.
        Api(http = { http }, baseUrl = { secrets.baseUrl }, token = { secrets.token })
    }

    @Synchronized
    fun init(context: Context) {
        CertStore.init(context)
        if (!::secrets.isInitialized) secrets = Secrets.create(context)
        // "Launch and trade" demo build: import the client cert bundled in the APK (if any) so the
        // EC2 mTLS path works with no manual upload. `http` was built at object-init WITHOUT a cert
        // (CertStore needs a context, which we only have now), so rebuild it if one just loaded.
        // Nothing has connected yet at init time, so this is a plain rebuild, not a live swap.
        if (CertStore.seedFromAssetsIfEmpty(context, com.xauorderpad.BuildConfig.DEFAULT_P12_PASSWORD)) {
            http = buildClient()
        }
    }

    /**
     * Rebuild the OkHttp client after a certificate change, then reconnect. The old client's
     * dispatcher thread pool and connection pool are shut down explicitly -- without this we
     * reintroduce the exact leak the shared-client design (above) fixed.
     */
    @Synchronized
    fun reloadTls() {
        val old = http
        http = buildClient()
        old.dispatcher.executorService.shutdown()
        old.connectionPool.evictAll()
        reconfigure()
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

    /**
     * Drop the held frame after an account switch, so the UI stops showing the previous account's
     * book. Safe if there is no client yet (nothing to clear). The socket stays up; the next frame
     * refills `snapshot`.
     */
    fun invalidateSnapshot() {
        client.value?.clearSnapshot()
    }
}
