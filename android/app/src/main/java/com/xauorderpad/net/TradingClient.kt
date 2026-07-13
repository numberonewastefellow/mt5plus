package com.xauorderpad.net

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import kotlin.coroutines.resume
import kotlin.math.min
import kotlin.random.Random

const val TAG = "XauOrderPad"

/** How the app is currently getting on with the server. Drives the status banner. */
sealed interface Link {
    data object Connecting : Link

    /** Socket open AND at least one frame received. See the note in onOpen. */
    data object Up : Link

    /** Transient: server down, EC2 box auto-stopped, phone roaming. We keep retrying. */
    data class Down(val reason: String, val retryInSec: Int) : Link

    /**
     * The server rejected our token (/ws close 4401, or an HTTP 401).
     * TERMINAL until the user supplies a new one -- retrying is an infinite loop against a
     * server that will never let us in.
     */
    data object Unauthorized : Link
}

/**
 * The live /ws feed.
 *
 * Frames are deserialized on OkHttp's own dispatcher thread inside `onMessage`, so JSON
 * parsing never touches the UI thread.
 *
 * The feed is exposed as a StateFlow, which CONFLATES: if frames arrive faster than a
 * collector drains them, the intermediates are dropped and the latest always wins. That is
 * exactly the coalescing a quote feed wants, and it costs nothing -- no throttle code, no
 * ring buffer. (The desktop web UI gets this wrong and rebuilds its entire positions table
 * 15x/sec.)
 *
 * @param http shared with the REST Api -- one connection pool, one dispatcher (see Feed).
 */
class TradingClient(
    private val scope: CoroutineScope,
    private val http: OkHttpClient,
    private val baseUrl: () -> String,
    private val token: () -> String,
    private val hz: Int = 5,          // the phone asks for 5; the server's own poll is 15
) {

    private val _snapshot = MutableStateFlow<Snapshot?>(null)
    val snapshot: StateFlow<Snapshot?> = _snapshot.asStateFlow()

    /**
     * Drop the last frame WITHOUT tearing the socket down. Used after an account switch: the held
     * frame describes the previous account's book, and the screen would otherwise render it (with
     * the close buttons live against foreign tickets) until the next frame arrives. Back to null =
     * "no data yet", which the UI already renders safely; the very next /ws frame refills it.
     */
    fun clearSnapshot() {
        _snapshot.value = null
    }

    private val _link = MutableStateFlow<Link>(Link.Connecting)
    val link: StateFlow<Link> = _link.asStateFlow()

    private var socket: WebSocket? = null
    private var loop: Job? = null

    /** Consecutive failed attempts. Drives the backoff; see the reset rule in runLoop. */
    private var attempt = 0

    @Volatile private var lastReason: String = "connecting"

    fun start() {
        if (loop?.isActive == true) return
        loop = scope.launch(Dispatchers.IO) { runLoop() }
    }

    fun stop() {
        loop?.cancel()
        loop = null
        socket?.close(NORMAL_CLOSURE, "client stopping")
        socket = null
    }

    /**
     * Break out of the terminal Unauthorized state after the user supplies a new token.
     *
     * Clearing `_link` is the whole point and was missing: `runLoop` bails on its first line
     * if the state is still Unauthorized, so without this the "retry" was a guaranteed no-op
     * -- a landmine for whoever eventually wires up a Retry button.
     */
    fun retryNow() {
        attempt = 0
        _link.value = Link.Connecting
        stop()
        start()
    }

    /**
     * Called from the ConnectivityManager callback when a network appears. Collapses the
     * backoff so a phone that just regained signal reconnects immediately instead of sitting
     * out the remainder of a 60 s sleep staring at a dead screen.
     */
    fun onNetworkAvailable() {
        if (_link.value is Link.Down) retryNow()
    }

    private suspend fun runLoop() {
        while (scope.isActive) {
            // Unauthorized is TERMINAL. Retrying a rejected token would hammer the box and
            // drain the battery while the UI just said "disconnected". Wait for the user.
            if (_link.value is Link.Unauthorized) return

            if (_link.value !is Link.Down) _link.value = Link.Connecting

            val code = connectAndWait()
            if (code == UNAUTHORIZED) {
                _link.value = Link.Unauthorized
                return
            }
            if (!scope.isActive) return

            attempt++

            // Exponential backoff with jitter, capped at 60 s.
            //
            // "Server gone" is a NORMAL state here, not an exception: the EC2 box AUTO-STOPS
            // 360 minutes after boot. A phone that sleeps for hours must not machine-gun a
            // host that is deliberately switched off. Jitter stops the phone and the desktop
            // pad from retrying in lockstep.
            val ceiling = min(MAX_BACKOFF_MS, BASE_BACKOFF_MS shl min(attempt, MAX_SHIFT))
            val wait = ceiling / 2 + Random.nextLong(ceiling / 2 + 1)

            _link.value = Link.Down(lastReason, (wait / 1000).toInt())
            Log.i(TAG, "ws down ($lastReason); retry in ${wait}ms (attempt $attempt)")
            delay(wait)
        }
    }

    /** Opens the socket and suspends until it closes. Returns the close code. */
    private suspend fun connectAndWait(): Int = suspendCancellableCoroutine { cont ->
        // Token goes in the x-token HEADER, not the query string. OkHttp can set handshake
        // headers (browsers cannot -- the web UI still uses ?token=), and a header never lands in
        // an access log the way a URL query does. The server prefers the header over the param.
        val url = buildString {
            append(baseUrl().replaceFirst("http", "ws"))
            append("/ws?hz=").append(hz)
        }

        var resumed = false
        fun finish(code: Int, reason: String) {
            if (resumed) return
            resumed = true
            lastReason = reason
            socket = null
            if (cont.isActive) cont.resume(code)
        }

        val listener = object : WebSocketListener() {

            override fun onOpen(webSocket: WebSocket, response: Response) {
                // Deliberately NOT setting Link.Up here, and NOT resetting `attempt`.
                //
                // Two separate reasons, both real:
                //
                // 1. (UX) A socket that is open but has not yet delivered a frame has nothing
                //    to show. Saying "connected" over a blank screen is a lie.
                //
                // 2. (Backoff) The server accepts and THEN closes on several paths -- a bad
                //    token closes with 4401 after accept(), and any exception inside its /ws
                //    loop closes with 1000 after accept(). If onOpen reset `attempt`, such a
                //    server would be retried at 1-2 s FOREVER, never escalating to the 60 s
                //    cap: a hot loop against a broken box, on battery.
                //
                // Both are fixed by treating "received a frame" as the definition of success.
                Log.d(TAG, "ws open")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    // Parse HERE: this is OkHttp's dispatcher thread, not Main.
                    _snapshot.value = json.decodeFromString<Snapshot>(text)

                    // A frame arrived -- THIS is what proves the connection is genuinely good.
                    attempt = 0
                    if (_link.value !is Link.Up) _link.value = Link.Up
                } catch (e: Exception) {
                    // A malformed frame must never kill the socket -- but it must not be
                    // silent either. We keep the last good snapshot on screen, and a frozen
                    // number that looks live is dangerous, so this is logged at WARN and the
                    // frame is counted. (The classic cause was Position.magic overflowing an
                    // Int; it is a Long now.)
                    Log.w(TAG, "dropped a malformed frame: ${e.message}")
                }
            }

            // A close CODE arrives in onClosing/onClosed -- NOT in onFailure.
            //
            // The server's 4401 ("bad token") is delivered this way, which is exactly why the
            // server closes AFTER accept(): a close BEFORE accept() is an HTTP 403 handshake
            // rejection, and OkHttp surfaces that as onFailure with no code at all --
            // indistinguishable from the box being switched off.
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(NORMAL_CLOSURE, null)
                finishClose(code, reason, ::finish)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                finishClose(code, reason, ::finish)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                // Belt and braces: if a proxy ever turned the 4401 into an HTTP status.
                val code = if (response?.code == 401 || response?.code == 403) UNAUTHORIZED else FAILED
                finish(code, t.message ?: "connection failed")
            }
        }

        val reqBuilder = Request.Builder().url(url)
        token().let { if (it.isNotBlank()) reqBuilder.header("x-token", it) }
        val ws = http.newWebSocket(reqBuilder.build(), listener)
        socket = ws
        cont.invokeOnCancellation { ws.close(NORMAL_CLOSURE, "cancelled") }
    }

    private inline fun finishClose(code: Int, reason: String, finish: (Int, String) -> Unit) {
        if (code == UNAUTHORIZED) finish(UNAUTHORIZED, "token rejected")
        else finish(code, reason.ifBlank { "server closed the connection ($code)" })
    }

    companion object {
        /** App-level close code the server sends for a bad/missing token. */
        const val UNAUTHORIZED = 4401
        private const val NORMAL_CLOSURE = 1000
        private const val FAILED = -1

        private const val BASE_BACKOFF_MS = 1_000L
        private const val MAX_BACKOFF_MS = 60_000L
        private const val MAX_SHIFT = 6            // 1s -> 64s, capped at MAX_BACKOFF_MS
    }
}
