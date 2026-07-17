package com.xauorderpad.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.xauorderpad.net.ClosedTrade
import com.xauorderpad.net.HistoryResponse
import com.xauorderpad.net.HistoryStats
import com.xauorderpad.net.PendingOrder
import com.xauorderpad.net.Position
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.roundToInt

/**
 * "Today's Activity" — a broker-style read of the logged-in account: today's CLOSED trades
 * (entry → exit), current OPEN positions, and PENDING orders, with realized net/gross P&L and the
 * win-rate-vs-asymmetry picture up top.
 *
 * ── On-demand, deliberately ──
 *
 * This screen NEVER auto-refreshes. `history_deals_get` is heavy, and the whole value here is a
 * STABLE snapshot you can read and reason about — not a book that reshuffles under your thumb. It
 * fetches once when opened (see TradingViewModel.goto) and again only when you tap ⟳. So a P&L shown
 * on an OPEN row is "as of" the fetch time, labelled as such — not live.
 *
 * All heavy lifting is server-side: the phone only renders what /api/history returns. The colour
 * convention matches the SPLIT positions list — a left accent bar carries LONG/SHORT (green/red),
 * and green/red on a money column means only profit/loss.
 */
private enum class Tab { OPEN, CLOSED, PENDING }

@Composable
fun HistoryScreen(
    history: HistoryResponse?,
    loading: Boolean,
    serverUrl: String,
    onRefresh: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BackHandler(onBack = onBack)

    Column(modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 8.dp)) {
        // ── Top bar: back · title · refresh ──
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("‹ BACK", fontSize = 13.sp) }
            Text(
                "TODAY'S ACTIVITY",
                Modifier.weight(1f),
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface,
            )
            if (loading) {
                CircularProgressIndicator(
                    Modifier.width(18.dp).height(18.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.width(8.dp))
            }
            TextButton(onClick = onRefresh, enabled = !loading) { Text("⟳", fontSize = 20.sp) }
        }
        // Identity + freshness. The "as of" is the point of the on-demand model: it tells you
        // exactly how stale the numbers are.
        Text(
            buildString {
                append(serverUrl.removePrefix("http://").removePrefix("https://").ifBlank { "—" })
                val asOf = history?.asOf ?: 0L
                if (asOf > 0) append("  ·  as of ${clock(asOf)}")
            },
            fontFamily = FontFamily.Monospace,
            fontSize = 10.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))

        when {
            history == null && loading ->
                Box(Modifier.fillMaxSize(), Alignment.Center) {
                    CircularProgressIndicator()
                }
            history == null ->
                Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Text("Tap ⟳ to load today's activity",
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            !history.ok ->
                Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Text(history.error ?: "Could not load history", color = Red)
                }
            else -> HistoryBody(history)
        }
    }
}

@Composable
private fun HistoryBody(h: HistoryResponse) {
    val s = h.stats
    HeroStats(s)
    Spacer(Modifier.height(8.dp))
    Text(
        insight(s),
        fontSize = 11.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(10.dp))

    var tab by rememberSaveable { mutableStateOf(Tab.CLOSED) }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        TabChip("OPEN", h.open.size, tab == Tab.OPEN, Modifier.weight(1f)) { tab = Tab.OPEN }
        TabChip("CLOSED", h.closed.size, tab == Tab.CLOSED, Modifier.weight(1f)) { tab = Tab.CLOSED }
        TabChip("PENDING", h.pending.size, tab == Tab.PENDING, Modifier.weight(1f)) { tab = Tab.PENDING }
    }
    Spacer(Modifier.height(8.dp))

    when (tab) {
        Tab.OPEN -> ListOrEmpty(h.open.isEmpty(), "No open positions") {
            ColumnHeader("ENTRY", "P&L (as of fetch)")
            LazyColumn {
                items(h.open, key = { it.ticket }) { p ->
                    OpenRow(p)
                    HistoryDivider()
                }
            }
        }
        Tab.CLOSED -> ListOrEmpty(h.closed.isEmpty(), "No closed trades today") {
            ColumnHeader("ENTRY → EXIT", "P&L")
            LazyColumn {
                items(h.closed, key = { it.ticket }) { c ->
                    ClosedRow(c)
                    HistoryDivider()
                }
            }
        }
        Tab.PENDING -> ListOrEmpty(h.pending.isEmpty(), "No pending orders") {
            ColumnHeader("ORDER", "PRICE")
            LazyColumn {
                items(h.pending, key = { it.ticket }) { o ->
                    PendingRow(o)
                    HistoryDivider()
                }
            }
        }
    }
}

// ── Hero band: the day in four numbers ──
@Composable
private fun HeroStats(s: HistoryStats) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(10.dp))
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Text("NET REALISED", fontSize = 10.sp, fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            Fmt.signedMoney(s.net),
            fontSize = 30.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
            color = if (s.net < 0) Red else Green,
        )
        Spacer(Modifier.height(10.dp))
        Row(Modifier.fillMaxWidth()) {
            StatCell("WINS", "${s.wins}", Fmt.signedMoney(s.grossProfit), Green, Modifier.weight(1f))
            StatCell("LOSSES", "${s.losses}", Fmt.signedMoney(s.grossLoss), Red, Modifier.weight(1f))
            StatCell("WIN %", "${(s.winRate * 100).roundToInt()}%", "${s.closedCount} closed",
                MaterialTheme.colorScheme.onSurface, Modifier.weight(1f))
            StatCell("BIGGEST", Fmt.signedMoney(s.biggestWin), Fmt.signedMoney(s.biggestLoss),
                MaterialTheme.colorScheme.onSurface, Modifier.weight(1f))
        }
    }
}

@Composable
private fun StatCell(label: String, big: String, sub: String, bigColor: Color, modifier: Modifier) {
    Column(modifier) {
        Text(label, fontSize = 9.sp, fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(big, fontSize = 16.sp, fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace, color = bigColor)
        Text(sub, fontSize = 9.sp, fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/**
 * A one-line read of the day, computed purely from the stats. The point is to name the pattern the
 * raw numbers hide — most often a high win rate that is still net-negative because the losers are
 * far bigger than the winners.
 */
private fun insight(s: HistoryStats): String {
    if (s.closedCount == 0) return "No closed trades today yet."
    val wr = (s.winRate * 100).roundToInt()
    val aw = Fmt.signedMoney(s.avgWin)
    val al = Fmt.signedMoney(s.avgLoss)
    return when {
        s.net < 0 && s.winRate >= 0.6 ->
            "$wr% win rate but NET NEGATIVE — avg win $aw vs avg loss $al; a few large losses erased many small wins."
        s.net > 0 && s.winRate < 0.4 ->
            "Only $wr% wins yet NET POSITIVE — winners (avg $aw) outrun losers (avg $al)."
        s.net >= 0 ->
            "Net positive — $wr% wins, avg win $aw vs avg loss $al."
        else ->
            "Net negative — $wr% wins, avg win $aw vs avg loss $al."
    }
}

@Composable
private fun TabChip(label: String, count: Int, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Column(
        modifier
            .background(
                if (selected) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.surfaceVariant,
                RoundedCornerShape(8.dp),
            )
            .clickable { onClick() }
            .padding(vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(label, fontSize = 10.sp, fontWeight = FontWeight.Bold,
            color = if (selected) Color.White else MaterialTheme.colorScheme.onSurfaceVariant)
        Text("$count", fontSize = 13.sp, fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
            color = if (selected) Color.White else MaterialTheme.colorScheme.onSurface)
    }
}

@Composable
private fun ColumnHeader(left: String, right: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(start = 12.dp, end = 8.dp, top = 5.dp, bottom = 5.dp),
    ) {
        Text(left, Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(right, Modifier.weight(1f), fontSize = 10.sp, fontWeight = FontWeight.Bold,
            textAlign = androidx.compose.ui.text.style.TextAlign.End,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    HorizontalDivider()
}

@Composable
private fun ListOrEmpty(empty: Boolean, emptyMsg: String, content: @Composable () -> Unit) {
    if (empty) {
        Box(Modifier.fillMaxSize(), Alignment.Center) {
            Text(emptyMsg, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
        }
    } else {
        content()
    }
}

@Composable
private fun HistoryDivider() =
    HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

// ── Rows. Each carries the left accent bar (green=long / red=short), same as the SPLIT list. ──

@Composable
private fun AccentRow(isBuy: Boolean, content: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
            .background(MaterialTheme.colorScheme.surface),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(4.dp).fillMaxHeight().background(if (isBuy) Green else Red))
        Row(
            Modifier.weight(1f).padding(horizontal = 8.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically,
            content = content,
        )
    }
}

@Composable
private fun OpenRow(p: Position) {
    val pl = p.profit ?: 0.0
    AccentRow(p.isBuy) {
        Column(Modifier.weight(1f)) {
            Text(Fmt.price(p.priceOpen, digitsFor(p.symbol)),
                fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onSurface)
            Text("${sym(p.symbol)} · ${Fmt.lot(p.volume)}", fontSize = 9.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(Fmt.signedMoney(pl), Modifier.weight(1f),
            textAlign = androidx.compose.ui.text.style.TextAlign.End,
            fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 13.sp,
            color = if (pl < 0) Red else Green)
    }
}

@Composable
private fun ClosedRow(c: ClosedTrade) {
    val d = digitsFor(c.symbol)
    val entry = if (c.entryPrice != null) Fmt.price(c.entryPrice, d) else "—"
    AccentRow(c.isBuy) {
        Column(Modifier.weight(1f)) {
            Text("$entry → ${Fmt.price(c.exitPrice, d)}",
                fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onSurface)
            Text("${sym(c.symbol)} · ${Fmt.lot(c.volume)} · ${clock(c.exitTime)}", fontSize = 9.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(Fmt.signedMoney(c.pnl), Modifier.weight(1f),
            textAlign = androidx.compose.ui.text.style.TextAlign.End,
            fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 13.sp,
            color = if (c.pnl < 0) Red else Green)
    }
}

@Composable
private fun PendingRow(o: PendingOrder) {
    AccentRow(o.isBuy) {
        Column(Modifier.weight(1f)) {
            Text("${o.side ?: "?"} ${o.type ?: "limit"}",
                fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onSurface)
            Text("${sym(o.symbol)} · ${Fmt.lot(o.volume)}", fontSize = 9.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Text(Fmt.price(o.priceOpen, digitsFor(o.symbol)), Modifier.weight(1f),
            textAlign = androidx.compose.ui.text.style.TextAlign.End,
            fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onSurface)
    }
}

// ── small helpers ──

/** Price precision is per-symbol; metals/indices are ~2dp, most FX 5dp. The server does not send
 *  `digits` per closed deal, so infer: XAU/XAG/JPY-quoted stay coarse, the rest default to 5dp. */
private fun digitsFor(symbol: String?): Int {
    val u = symbol?.uppercase() ?: return 2
    return if (u.startsWith("XAU") || u.startsWith("XAG") || u.endsWith("JPY")) 2 else 5
}

private fun sym(symbol: String?): String = symbol ?: "—"

private val CLOCK = SimpleDateFormat("HH:mm:ss", Locale.US)

/** epoch seconds -> HH:mm:ss in the device's local zone. */
private fun clock(epochSecs: Long?): String =
    if (epochSecs == null || epochSecs <= 0) "--:--:--" else CLOCK.format(Date(epochSecs * 1000))
