package com.xauorderpad.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.InterruptedIOException
import java.util.concurrent.TimeUnit

private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

/**
 * Every call returns this instead of throwing, so the UI always has something to render and
 * a failure can never be swallowed by a missing catch.
 */
sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>

    /** `retcode`/`comment` are populated only for /order, which alone returns them. */
    data class Failed(
        val message: String,
        val retcode: Int? = null,
        val comment: String? = null,
    ) : ApiResult<Nothing>

    /** HTTP 401 -- the token is wrong. The UI must send the user back to Connect. */
    data object Unauthorized : ApiResult<Nothing>

    /**
     * The call timed out client-side. THIS IS NOT A FAILURE -- it is an UNKNOWN.
     *
     * The server has no timeout of its own: `_do()` awaits the worker future indefinitely,
     * and the request is serialised behind a 15 Hz poll loop. So a request that times out
     * here may well still be executing -- and, for /order, may well FILL.
     *
     * Collapsing that into Failed is how you get a double position: the app says "order
     * failed", the user taps BUY again, and the first order fills too. On XAUUSD at 1.0 lot
     * that is a $100/point error nobody chose to take. It is a separate type precisely so
     * the compiler forces every call site to decide what to do about it.
     */
    data class TimedOut(val what: String) : ApiResult<Nothing>
}

/**
 * The handful of endpoints this app uses.
 *
 * No Retrofit: six endpoints do not justify a second HTTP library and a codegen step on top
 * of the OkHttp already present for the WebSocket. The client is SHARED with TradingClient
 * (one connection pool, one dispatcher thread pool) -- see Feed.
 */
class Api(
    private val http: OkHttpClient,
    private val baseUrl: () -> String,
    private val token: () -> String,
) {

    /**
     * A longer budget for the three calls that make the broker do work.
     *
     * The shared client's 15 s callTimeout is right for /api/config and /api/accounts, but it
     * is too tight here: `_close_where` runs up to FIVE passes over N positions, each an
     * `mt5.order_send` round-trip with a 50 ms sleep between passes, all serialised behind the
     * worker's poll loop. A requote storm on a six-position book -- which is exactly the
     * situation in which someone taps CLOSE ALL -- can outlast 15 s while the server is still
     * making progress.
     *
     * newBuilder() shares the connection pool and dispatcher, so this costs nothing.
     */
    private val tradeHttp: OkHttpClient = http.newBuilder()
        .callTimeout(60, TimeUnit.SECONDS)
        .build()

    // ---- orders ----------------------------------------------------------

    /**
     * Market order.
     *
     * [slPoints] / [tpPoints] are POINT DISTANCES, not prices. The server's `_place_order`
     * hardcodes `sl_tp_mode="points"` and converts them itself. 0 means "no stop". Passing an
     * absolute price here would silently place a stop miles from the market -- it would be
     * accepted, not rejected, which is what makes it dangerous.
     *
     * Two fields are deliberately NEVER sent:
     *   - `auto_test` -- the demo-only burst-tester flag. The server returns 403 for it on a
     *     live account, so sending it would break real trading.
     *   - `symbol`    -- the server is single-symbol; `PlaceReq.symbol` is accepted and then
     *     ignored. Sending it would imply a choice the user does not have.
     */
    suspend fun order(
        side: String,                 // "buy" | "sell"
        volume: Double,
        slPoints: Double,
        tpPoints: Double,
    ): ApiResult<OrderResult> = post(
        path = "/order",
        body = buildJsonObject {
            put("side", JsonPrimitive(side))
            put("type", JsonPrimitive("market"))
            put("volume", JsonPrimitive(volume))
            put("sl", JsonPrimitive(slPoints))
            put("tp", JsonPrimitive(tpPoints))
        },
        client = tradeHttp,
    ) { json.decodeFromString<OrderResult>(it) }

    suspend fun close(ticket: Long): ApiResult<Unit> = post(
        path = "/close",
        body = buildJsonObject { put("ticket", JsonPrimitive(ticket)) },
        client = tradeHttp,
    ) { }

    /**
     * Enable/disable or retune one server-side strategy engine.
     *
     * Returns the engine's REAL status, which is not the same as "the request
     * succeeded". The server can answer 200 with `enabled:false` and a reason -- a
     * target inside the spread, a non-demo account, the kill-switch latched. The
     * caller must render what comes back, never what it asked for.
     *
     * `params` is deliberately a free-form JsonObject: the engines take different
     * tunables, and the server ignores what an engine does not recognise. Modelling
     * the union here would mean the phone had to be updated to add a param to an
     * engine, which is the wrong place for that coupling.
     */
    suspend fun setStrategy(
        id: String,
        enabled: Boolean?,
        params: JsonObject = JsonObject(emptyMap()),
    ): ApiResult<StrategyStatus> = post(
        path = "/api/strategy/$id",
        body = buildJsonObject {
            params.forEach { (k, v) -> put(k, v) }
            if (enabled != null) put("enabled", JsonPrimitive(enabled))
        },
        client = tradeHttp,
    ) { json.decodeFromString<StrategyStatus>(it) }

    /**
     * Bulk close. [filter] is "all" | "losing" | "profit".
     *
     * ─────────────────────────────────────────────────────────────────────────────
     *  A FAILED BULK CLOSE COMES BACK AS HTTP 200. THE BODY IS THE ONLY TRUTH.
     * ─────────────────────────────────────────────────────────────────────────────
     * `Mt5Worker._close_where` gives up after 5 retry passes and returns
     *     {"ok": false, "closed": 0, "remaining": 8}
     * and a disconnected terminal returns
     *     {"ok": false, "error": "terminal not connected"}          (no counts at all)
     * Both are 200, because both are well-formed answers rather than protocol errors.
     *
     * Trusting the HTTP status here means an emergency CLOSE ALL that shut NOTHING reports
     * "Closed 0 position(s)" as a success -- in green, from the lock screen, on a losing
     * book. The user puts the phone down believing they are flat. They are not.
     *
     * So: `ok == false` is mapped to ApiResult.Failed, and the caller cannot ignore it.
     *
     * The P&L sign is evaluated SERVER-side against live broker state. We deliberately do
     * not filter positions[] locally and fan out N /close calls: a position can cross zero
     * between the frame we rendered and the close landing, and that would cost N round-trips
     * over a ~150 ms link.
     */
    suspend fun closeWhere(filter: String): ApiResult<CloseResult> {
        val r = post(
            path = "/close_where",
            body = buildJsonObject { put("filter", JsonPrimitive(filter)) },
            client = tradeHttp,
        ) { json.decodeFromString<CloseResult>(it) }

        if (r is ApiResult.Ok && !r.value.ok) {
            val v = r.value
            return ApiResult.Failed(
                v.error
                    ?: "${v.remaining} position(s) STILL OPEN after closing ${v.closed}"
            )
        }
        return r
    }

    // ---- session ---------------------------------------------------------

    /** Unauthenticated probe: reports THAT a token is required, never what it is. */
    suspend fun config(): ApiResult<ServerConfig> =
        get("/api/config") { json.decodeFromString<ServerConfig>(it) }

    suspend fun accounts(): ApiResult<AccountsResponse> =
        get("/api/accounts") { json.decodeFromString<AccountsResponse>(it) }

    /**
     * Log the terminal into a SAVED profile. The broker password is never sent from the
     * phone -- the server reads it from the Windows Credential Vault (accounts.py).
     */
    suspend fun login(profileId: String): ApiResult<LoginResult> = post(
        path = "/api/login",
        body = buildJsonObject { put("profile_id", JsonPrimitive(profileId)) },
    ) { json.decodeFromString<LoginResult>(it) }

    // ---- plumbing --------------------------------------------------------

    private suspend fun <T> get(path: String, parse: (String) -> T): ApiResult<T> =
        execute(Request.Builder().url(baseUrl() + path).get(), http, path, parse)

    private suspend fun <T> post(
        path: String,
        body: JsonObject,
        client: OkHttpClient = http,
        parse: (String) -> T,
    ): ApiResult<T> = execute(
        Request.Builder().url(baseUrl() + path)
            .post(body.toString().toRequestBody(JSON_MEDIA)),
        client,
        path,
        parse,
    )

    private suspend fun <T> execute(
        builder: Request.Builder,
        client: OkHttpClient,
        path: String,
        parse: (String) -> T,
    ): ApiResult<T> = withContext(Dispatchers.IO) {
        // Read the token at SEND time, not at construction: it can change under us when the
        // user re-enters it, and a retry must use the new one.
        val tok = token()
        if (tok.isNotBlank()) builder.header("x-token", tok)

        try {
            client.newCall(builder.build()).execute().use { res ->
                val text = res.body?.string().orEmpty()
                when {
                    res.code == 401 -> ApiResult.Unauthorized
                    res.isSuccessful -> ApiResult.Ok(parse(text))
                    else -> parseError(res.code, text)
                }
            }
        } catch (e: InterruptedIOException) {
            // OkHttp signals BOTH callTimeout and socket read timeouts with an
            // InterruptedIOException (SocketTimeoutException extends it). We gave up waiting;
            // the SERVER did not give up working. Never report this as a failure -- see
            // ApiResult.TimedOut.
            ApiResult.TimedOut(path)
        } catch (e: Exception) {
            ApiResult.Failed(e.message ?: "network error")
        }
    }

    /**
     * The server's error shapes are INCONSISTENT, and all of them land here:
     *
     *   /order        -> 400, `detail` is an OBJECT: {message, retcode, comment}
     *   /close        -> 400, `detail` is a STRING
     *   /close_where  -> 400, `detail` is a STRING
     *   403           -> plain string (the auto_test live-account guard; we never send that
     *                    flag, but a stale build might)
     *
     * Assuming either shape alone throws on the other -- which would turn a plain broker
     * rejection ("Market closed") into an opaque crash-shaped toast.
     */
    private fun parseError(code: Int, text: String): ApiResult.Failed {
        val fallback = "request failed (HTTP $code)"
        return try {
            val detail = json.parseToJsonElement(text).jsonObject["detail"]
                ?: return ApiResult.Failed(fallback)

            if (detail is JsonPrimitive) {
                ApiResult.Failed(detail.contentOrNull ?: fallback)
            } else {
                val o = detail.jsonObject
                ApiResult.Failed(
                    message = o["message"]?.jsonPrimitive?.contentOrNull ?: fallback,
                    retcode = o["retcode"]?.jsonPrimitive?.intOrNull,
                    comment = o["comment"]?.jsonPrimitive?.contentOrNull,
                )
            }
        } catch (_: Exception) {
            ApiResult.Failed(fallback)
        }
    }
}
