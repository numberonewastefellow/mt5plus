package com.xauorderpad.data

import android.content.Context
import android.content.SharedPreferences

/**
 * Persisted connection settings: the server's base URL and the API token.
 *
 * ── Why plain SharedPreferences and NOT EncryptedSharedPreferences ──
 *
 * `androidx.security:security-crypto` was deprecated in April 2025, never left alpha, and
 * has documented keyset-corruption crashes on some OEM devices -- i.e. a crash-on-launch
 * whose only recovery is "clear app data". It also cost 50-200 ms of Keystore + Tink work
 * on the main thread at every launch, which is the entire launch budget.
 *
 * Weigh that against what is actually stored HERE:
 *
 *   - a base URL (not a secret), and
 *   - the API token: the bearer secret for the trading API.
 *
 * The MT5 broker password is NOT stored here. Note the precise claim: it is not *persisted* on
 * the phone. It IS typed on the Accounts screen and sent to the server once (to log in / save),
 * after which every later login is by `profile_id` and the server reads the password from its own
 * credential vault. So a captured phone at rest yields the API token, not the broker password --
 * and an attacker holding the UNLOCKED phone can just use the app regardless. The device lock
 * screen is the real control. An at-rest layer that can brick the app (see above) is a bad trade
 * for a threat it does not actually stop.
 *
 * The token is not "only reachable inside a tailnet" any more: depending on the server it guards
 * either a LAN endpoint (plain HTTP) or the EC2 mTLS front door. On the mTLS path the client
 * certificate is the outer gate and this token is the inner one; on the LAN path the token is the
 * only gate, which is why the Connect screen warns when the transport is cleartext.
 *
 * Values are cached in memory after the first read: `baseUrl`/`token` are read on the hot
 * path (every reconnect, every request header) and must not hit disk each time.
 */
class Secrets private constructor(private val prefs: SharedPreferences) {

    // Seed from the build-time defaults ONLY when nothing is stored.
    //
    // BuildConfig.DEFAULT_* come from android/local.properties (gitignored) and are baked into
    // the DEBUG variant only -- the release variant compiles them as "". This is a dev
    // convenience so the URL and token are not retyped on every reinstall.
    //
    // "Only when nothing is stored" is the load-bearing part: a value the user typed on the
    // Connect screen must always win, and must NOT be silently reverted to a stale baked-in
    // default by the next install. That would be maddening to debug -- the app would keep
    // pointing at the wrong server no matter what you typed.
    //
    // Note the token is deliberately NOT persisted here. It stays in memory for this launch;
    // it lands in prefs only if the user actually saves on the Connect screen. So a rebuilt
    // APK with a new token picks it up cleanly instead of being shadowed by an old stored one.
    @Volatile private var cachedBaseUrl: String =
        prefs.getString(KEY_BASE_URL, "").orEmpty()
            .ifBlank { normalizeBaseUrl(com.xauorderpad.BuildConfig.DEFAULT_BASE_URL) }

    @Volatile private var cachedToken: String =
        prefs.getString(KEY_TOKEN, "").orEmpty()
            .ifBlank { com.xauorderpad.BuildConfig.DEFAULT_TOKEN.trim() }

    // Has the user explicitly pressed CONNECT? This is deliberately SEPARATE from "we have a
    // URL". The baked-in DEFAULT_BASE_URL makes a URL present on a fresh install, and gating
    // on that alone meant the app silently skipped the Connect screen and dialled straight out
    // -- so you could never see, let alone change, which server it was talking to. Prefilling
    // is a convenience; connecting is a decision, and only the user makes it.
    @Volatile private var cachedConnected: Boolean = prefs.getBoolean(KEY_CONNECTED, false)

    // Confirm before a bulk close? Defaults to TRUE -- a mis-tap on CLOSE ALL is irreversible.
    // But a confirm dialog costs a second and a second tap, and flattening is the panic path, so
    // the trader is allowed to turn it off. Persisted, because re-arming it on every launch would
    // just train the user to tap through it.
    @Volatile private var cachedConfirmCloses: Boolean = prefs.getBoolean(KEY_CONFIRM_CLOSES, true)

    var confirmCloses: Boolean
        get() = cachedConfirmCloses
        set(v) {
            cachedConfirmCloses = v
            prefs.edit().putBoolean(KEY_CONFIRM_CLOSES, v).apply()
        }

    // Which trade-screen layout is active, persisted as an ordinal (0=Classic, 1=Compact, 2=Scalp);
    // see ui.LayoutMode. The top-bar chip cycles it. Seeded from the OLD boolean `compact_layout`
    // pref if this device still has it (true -> Compact=1), so an in-place upgrade does not reset.
    @Volatile private var cachedLayoutMode: Int =
        prefs.getInt(KEY_LAYOUT_MODE, if (prefs.getBoolean(KEY_COMPACT_LAYOUT, false)) 1 else 0)

    var layoutMode: Int
        get() = cachedLayoutMode
        set(v) {
            cachedLayoutMode = v
            prefs.edit().putInt(KEY_LAYOUT_MODE, v).apply()
        }

    // Selected candle timeframe for the Scalp countdown, in MINUTES (1/2/5/15/30/60/240).
    // Default 15 (M15). Persisted so the choice survives a relaunch.
    @Volatile private var cachedCandleTf: Int = prefs.getInt(KEY_CANDLE_TF, 15)

    var candleTf: Int
        get() = cachedCandleTf
        set(v) {
            cachedCandleTf = v
            prefs.edit().putInt(KEY_CANDLE_TF, v).apply()
        }

    // Which side the ENTRY button trades: "buy" or "sell". Persisted, like the web UI, so the
    // trader is not re-arming it every launch.
    //
    // A persisted MODE that changes what a big button does is exactly the thing that gets people
    // into the wrong trade -- so the tab bar that shows it is deliberately loud, and the buttons
    // never move between modes. You should never have to TAP to discover which side you are on.
    @Volatile private var cachedArmedSide: String =
        prefs.getString(KEY_ARMED_SIDE, null)?.takeIf { it == "buy" || it == "sell" } ?: "buy"

    var armedSide: String
        get() = cachedArmedSide
        set(v) {
            val s = if (v == "sell") "sell" else "buy"   // never store anything else
            cachedArmedSide = s
            prefs.edit().putString(KEY_ARMED_SIDE, s).apply()
        }

    /** e.g. "http://100.101.102.103:8765" -- the box's Tailscale address. */
    var baseUrl: String
        get() = cachedBaseUrl
        set(v) {
            val n = normalizeBaseUrl(v)
            cachedBaseUrl = n
            prefs.edit().putString(KEY_BASE_URL, n).apply()   // apply() = async, off the caller's thread
        }

    /** Sent as the `x-token` header, and as `?token=` on the /ws URL. */
    var token: String
        get() = cachedToken
        set(v) {
            val t = v.trim()
            cachedToken = t
            prefs.edit().putString(KEY_TOKEN, t).apply()
        }

    /**
     * Gates [com.xauorderpad.data.Feed.start] -- i.e. whether the socket may open at all.
     * Requires BOTH a URL and an explicit CONNECT, so a prefilled default never auto-dials.
     */
    val isConfigured: Boolean get() = cachedBaseUrl.isNotBlank() && cachedConnected

    /** The user pressed CONNECT. Called by Secrets' owner after the URL/token are set. */
    fun markConnected() {
        cachedConnected = true
        prefs.edit().putBoolean(KEY_CONNECTED, true).apply()
    }

    /**
     * Log out: forget the token and the connected state, so the next launch lands back on the
     * Connect screen. The base URL is KEPT on purpose -- it is not a secret, and retyping the
     * server address every time you log out would be pointless friction. It comes back prefilled.
     */
    fun disconnect() {
        // Genuinely forget the token in-session. This used to re-seed cachedToken from
        // BuildConfig.DEFAULT_TOKEN, so on a debug build "LOG OUT" left the token sitting in
        // memory and pre-filled again on the very next screen -- i.e. it did not actually log you
        // out. Clear it. (The fresh-install convenience still works: KEY_TOKEN is removed from
        // disk, so a NEW process re-seeds from the baked-in default via the init block; a release
        // build's default is "" and forgets for real either way.)
        cachedToken = ""
        cachedConnected = false
        prefs.edit().remove(KEY_TOKEN).putBoolean(KEY_CONNECTED, false).apply()
    }

    fun clearToken() {
        cachedToken = ""
        prefs.edit().remove(KEY_TOKEN).apply()
    }

    companion object {
        private const val PREFS = "xau_settings"
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_CONNECTED = "connected"
        private const val KEY_CONFIRM_CLOSES = "confirm_closes"
        private const val KEY_COMPACT_LAYOUT = "compact_layout"   // legacy bool; migrated into KEY_LAYOUT_MODE
        private const val KEY_LAYOUT_MODE = "layout_mode"
        private const val KEY_CANDLE_TF = "candle_tf"
        private const val KEY_ARMED_SIDE = "armed_side"

        /**
         * Construct off the main thread where possible. Plain SharedPreferences still does a
         * synchronous disk read on first access, which is cheap (~1-5 ms) but not free.
         */
        fun create(context: Context): Secrets =
            Secrets(context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE))

        /**
         * Tolerate what a human actually types on a phone keyboard: a bare IP, a trailing
         * slash, a missing scheme. Getting this wrong yields an opaque "connection failed"
         * that looks like a server fault.
         *
         * The port is only appended when the AUTHORITY has no port -- checking the whole
         * string would turn "http://host/api" into "http://host/api:8765".
         */
        fun normalizeBaseUrl(raw: String): String {
            var s = raw.trim().trimEnd('/')
            if (s.isEmpty()) return ""
            if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://$s"

            val schemeEnd = s.indexOf("://") + 3
            val pathStart = s.indexOf('/', schemeEnd).let { if (it == -1) s.length else it }
            val authority = s.substring(schemeEnd, pathStart)

            if (!authority.contains(':')) {
                s = s.substring(0, pathStart) + ":$DEFAULT_PORT" + s.substring(pathStart)
            }
            return s
        }

        private const val DEFAULT_PORT = 8765
    }
}
