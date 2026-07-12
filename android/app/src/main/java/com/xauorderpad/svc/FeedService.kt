package com.xauorderpad.svc

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.xauorderpad.MainActivity
import com.xauorderpad.R
import com.xauorderpad.data.Feed
import com.xauorderpad.net.ApiResult
import com.xauorderpad.net.Link
import com.xauorderpad.net.Snapshot
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.sample
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.util.Locale
import kotlin.math.abs

/**
 * Keeps the /ws feed alive while the app is backgrounded or the screen is off, and mirrors it
 * into a persistent notification with CLOSE ALL / CLOSE LOSING actions -- so the book can be
 * flattened from the lock screen without unlocking and navigating.
 *
 * ── Why foregroundServiceType="specialUse" and NOT "dataSync" ──
 *
 * Android 15 imposes a CUMULATIVE 6-HOUR/24H CAP on dataSync foreground services; the system
 * stops them when the budget runs out. For a trading feed that is a silent, catastrophic
 * failure: the socket dies mid-session, the notification freezes on a stale P&L, and the user
 * believes they are watching a live book that actually stopped updating hours ago.
 * `specialUse` is not time-capped. Its only real cost is that Google Play demands a written
 * justification -- irrelevant, because this APK is sideloaded and never published.
 */
class FeedService : Service() {

    /**
     * Deliberately NOT Dispatchers.Main.
     *
     * Everything this service does per frame -- rendering the text, building the Notification,
     * posting it -- is off-UI work, and NotificationManager is thread-safe. Running it on Main
     * (as the first version did) put ~20 binder transactions per second on the UI thread with
     * the screen off.
     */
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var watcher: Job? = null

    /**
     * PendingIntents are CONSTANT, but each `getActivity`/`getService` call is a binder
     * round-trip to ActivityManagerService. The first version built all three inside the
     * notification builder -- i.e. 3 IPCs per frame, 5x/sec, forever. Built once, lazily.
     */
    private val tapApp: PendingIntent by lazy {
        PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
    }
    private val actionCloseAll: PendingIntent by lazy { serviceAction(ACTION_CLOSE_ALL) }
    private val actionCloseLosing: PendingIntent by lazy { serviceAction(ACTION_CLOSE_LOSING) }

    /** DecimalFormat is not thread-safe; these belong to this service's thread only. */
    private val money = DecimalFormat("0.00", DecimalFormatSymbols(Locale.US))
    private val priceFmt = HashMap<Int, DecimalFormat>(2)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Feed.init(this)
        createChannel()
    }

    @OptIn(FlowPreview::class)
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CLOSE_ALL -> bulkClose("all")
            ACTION_CLOSE_LOSING -> bulkClose("losing")
            ACTION_STOP -> { stopSelf(); return START_NOT_STICKY }
        }

        // Must post promptly or the system kills the process with
        // "did not call startForeground()". The TYPE must be passed explicitly from API 34 --
        // startForeground(id, notification) alone throws MissingForegroundServiceTypeException.
        startForeground(
            NOTIF_ID,
            build(NotifModel.connecting()),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
        )
        Feed.start()

        if (watcher == null) {
            watcher = scope.launch {
                combine(Feed.snapshot, Feed.link) { s, l -> render(s, l) }
                    // The feed ticks at 5 Hz, but a glanceable lock-screen summary does not
                    // need 5 Hz. sample() first (cheap), then dedupe.
                    .sample(REFRESH_MS)
                    // If the rendered TEXT is identical, do not touch NotificationManager at
                    // all. Without this we re-post an unchanged notification several times a
                    // second; NMS rate-limits and starts DROPPING them
                    // ("Package has posted too many notifications"), so we would be paying the
                    // full binder cost for updates the system throws away.
                    .distinctUntilChanged()
                    .collect { model ->
                        notifier().notify(NOTIF_ID, build(model))
                    }
            }
        }

        // START_STICKY: if Android reclaims us under memory pressure, come back. A trader
        // expects the feed to still be there when they look at the phone.
        return START_STICKY
    }

    /**
     * Fired from a notification action.
     *
     * There is NO confirmation dialog available from a notification, so only the two
     * RISK-REDUCING actions are exposed. CLOSE PROFIT is deliberately absent: accidentally
     * closing winners from a lock screen is a worse outcome than accidentally closing losers.
     *
     * ── The failure path is the whole point here ──
     * `Api.closeWhere` maps the server's {ok:false} -- which arrives as HTTP **200** -- to
     * ApiResult.Failed. The first version reported `closed` unconditionally, so a CLOSE ALL
     * against a disconnected terminal said "Closed 0 position(s)" and looked like success. On
     * a losing book. From the lock screen. That is the exact moment the user most needs the
     * truth.
     */
    private fun bulkClose(filter: String) = scope.launch {
        when (val r = Feed.api.closeWhere(filter)) {
            is ApiResult.Ok ->
                if (r.value.closed == 0) toast("Nothing matched — no positions closed")
                else toast("Closed ${r.value.closed} position(s)")

            is ApiResult.Unauthorized ->
                toast("NOT CLOSED — token rejected. Open the app.")

            // Timed out, NOT failed. The close runs up to five passes over the book with no
            // server-side deadline, so it may still be working -- or have already finished.
            // From a lock screen, "CLOSE FAILED" would send the user to the terminal to close
            // by hand, potentially double-closing. Tell them the truth: we do not know.
            is ApiResult.TimedOut ->
                toast("CLOSE TIMED OUT — may still be running. Open the app; do not assume you are flat.")

            is ApiResult.Failed ->
                toast("CLOSE FAILED — ${r.message}")
        }
    }

    private suspend fun toast(msg: String) = withContext(Dispatchers.Main) {
        android.widget.Toast.makeText(this@FeedService, msg, android.widget.Toast.LENGTH_LONG).show()
    }

    private fun notifier() = getSystemService(NotificationManager::class.java)

    private fun createChannel() {
        val ch = NotificationChannel(
            CHANNEL_ID,
            "Live feed",
            // LOW: persistent and glanceable, never buzzing. IMPORTANCE_DEFAULT would make the
            // phone vibrate every time the P&L moved.
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Live XAUUSD quote, open P&L, and quick close actions"
            setShowBadge(false)
        }
        notifier().createNotificationChannel(ch)
    }

    // ---- rendering -------------------------------------------------------

    /** The notification's entire visible content. Compared by value to suppress no-op posts. */
    private data class NotifModel(
        val title: String,
        val body: String,
        val showActions: Boolean,
    ) {
        companion object {
            fun connecting() = NotifModel("XauOrderPad — connecting…", "Waiting for the feed", false)
        }
    }

    private fun render(s: Snapshot?, link: Link): NotifModel = when {
        link is Link.Unauthorized ->
            NotifModel("XauOrderPad — unauthorized", "Token rejected. Open the app to re-enter it.", false)

        link is Link.Down ->
            NotifModel("XauOrderPad — disconnected", "${link.reason} · retrying in ${link.retryInSec}s", false)

        s == null || link is Link.Connecting ->
            NotifModel.connecting()

        s.isLoggedOut ->
            NotifModel("XauOrderPad — logged out", "MT5 is not logged in. Open the app to log in.", false)

        // A degraded frame carries connected:false / healthy:false and NO `positions`
        // key at all -- the terminal dropped its broker link, or poll_state threw. But
        // `openPositions` is `positions.orEmpty()`, so ABSENT silently becomes EMPTY,
        // and the `else` branch below renders that as "Flat".
        //
        // The failure that produces: the trader is long 8 lots, puts the phone down, and
        // the EC2 box's terminal loses its broker link for 30 s. The socket is still up
        // and frames keep arriving, so the lock screen reads "XAUUSD -- / --  Flat". The
        // trader believes they are out and stops watching. We do not know the book here;
        // say so, and never say "Flat" unless the broker actually told us the book is flat.
        //
        // Keep the actions VISIBLE: the close filter is evaluated server-side against live
        // broker state, so a panic close is still the right thing to be able to fire when
        // the phone's own view is stale -- and a failure is now reported loudly (B1).
        s.connected != true || s.healthy != true ->
            NotifModel(
                title = "XauOrderPad — no data",
                body = s.error ?: "Not connected to the broker. Positions unknown.",
                showActions = true,
            )

        else -> {
            val open = s.openPositions
            val pl = s.floatingPl ?: 0.0
            val real = if (s.account?.isDemo == false) " · REAL" else ""
            val title = "XAUUSD  ${price(s.bid, s.digits)} / ${price(s.ask, s.digits)}$real"
            val body = if (open.isEmpty()) {
                "Flat"
            } else {
                val sign = if (pl < 0) "-" else "+"
                val cur = s.account?.currency.orEmpty()
                "${open.size} open · P&L $sign$cur${money.format(abs(pl))}"
            }
            NotifModel(title, body, showActions = open.isNotEmpty())
        }
    }

    private fun price(v: Double?, digits: Int?): String {
        if (v == null) return "--"
        val d = digits ?: 2
        val f = priceFmt.getOrPut(d) {
            DecimalFormat("0." + "0".repeat(d.coerceIn(0, 8)), DecimalFormatSymbols(Locale.US))
        }
        return f.format(v)
    }

    private fun build(m: NotifModel): Notification {
        val b = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_feed)
            .setContentTitle(m.title)
            .setContentText(m.body)
            .setContentIntent(tapApp)
            .setOngoing(true)
            .setOnlyAlertOnce(true)          // never buzz on a price tick
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)

        // Only offer the close actions when there is actually something to close.
        if (m.showActions) {
            b.addAction(0, "CLOSE ALL", actionCloseAll)
            b.addAction(0, "CLOSE LOSING", actionCloseLosing)
        }
        return b.build()
    }

    private fun serviceAction(a: String): PendingIntent = PendingIntent.getService(
        this,
        a.hashCode(),
        Intent(this, FeedService::class.java).setAction(a),
        PendingIntent.FLAG_IMMUTABLE,
    )

    override fun onDestroy() {
        watcher?.cancel()
        scope.cancel()
        // Deliberately NOT Feed.stop(): the Activity may still be foregrounded and using the
        // socket. Feed is app-scoped and outlives this service.
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ID = "xau_feed"
        private const val NOTIF_ID = 1

        /** A lock-screen glance does not need 5 Hz. */
        private const val REFRESH_MS = 1_000L

        const val ACTION_CLOSE_ALL = "com.xauorderpad.CLOSE_ALL"
        const val ACTION_CLOSE_LOSING = "com.xauorderpad.CLOSE_LOSING"
        const val ACTION_STOP = "com.xauorderpad.STOP"

        fun start(ctx: Context) =
            ctx.startForegroundService(Intent(ctx, FeedService::class.java))

        fun stop(ctx: Context) =
            ctx.startService(Intent(ctx, FeedService::class.java).setAction(ACTION_STOP))
    }
}
