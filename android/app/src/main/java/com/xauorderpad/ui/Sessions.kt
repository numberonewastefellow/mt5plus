package com.xauorderpad.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

/**
 * "When does the next market open, and how long is this one still open for?"
 *
 * The trader is in India; the sessions are not. Rather than hard-code IST offsets -- which break
 * twice a year, in the two directions, for three different countries -- each session carries its OWN
 * `ZoneId` and the arithmetic is done on `Instant`s. An instant is an instant everywhere, so the
 * remaining-seconds figure is correct read from Mumbai, London or anywhere else, and DST is handled
 * by the tz database rather than by us.
 *
 * `java.time` needs no desugaring here: it is API 26+, and minSdk is 34.
 */
enum class MarketSession(
    val label: String,
    val zone: ZoneId,
    val open: LocalTime,
    val close: LocalTime,
) {
    ASIA("ASIA", ZoneId.of("Asia/Tokyo"), LocalTime.of(9, 0), LocalTime.of(18, 0)),
    UK("UK", ZoneId.of("Europe/London"), LocalTime.of(8, 0), LocalTime.of(17, 0)),
    US("US", ZoneId.of("America/New_York"), LocalTime.of(8, 0), LocalTime.of(17, 0)),
}

/** @param secondsLeft until close when [isOpen], else until the next open. Never negative. */
data class SessionState(val isOpen: Boolean, val secondsLeft: Long)

private fun DayOfWeek.isWeekday() = this != DayOfWeek.SATURDAY && this != DayOfWeek.SUNDAY

/**
 * Where [s] is right now, relative to the wall clock in ITS OWN city.
 *
 * Weekends are not a session: FX is shut from Friday's close to Monday's open, so a Saturday
 * countdown must point at Monday and not at "tomorrow". That is the `while` loop -- without it the
 * bar would cheerfully count down to a Sunday open that never happens.
 */
fun statusOf(s: MarketSession, nowMillis: Long): SessionState {
    val now: ZonedDateTime = Instant.ofEpochMilli(nowMillis).atZone(s.zone)

    // Inside today's window? Only ever true on a weekday.
    if (now.dayOfWeek.isWeekday()) {
        val openToday = now.with(s.open)
        val closeToday = now.with(s.close)
        if (!now.isBefore(openToday) && now.isBefore(closeToday)) {
            return SessionState(true, ChronoUnit.SECONDS.between(now, closeToday).coerceAtLeast(0))
        }
    }

    // Otherwise count to the next weekday open.
    var next = now.with(s.open)
    if (!next.isAfter(now)) next = next.plusDays(1)          // today's open already went by
    while (!next.dayOfWeek.isWeekday()) next = next.plusDays(1)
    return SessionState(false, ChronoUnit.SECONDS.between(now, next).coerceAtLeast(0))
}

/** `O` = opens in, `C` = closes in. Rendered separately from the digits -- see [SessionBar]. */
fun sessionPrefix(st: SessionState): String = if (st.isOpen) "C" else "O"

/**
 * The duration alone, with an explicit unit:
 *   >= 1h  -> `7h13`  (not "433" minutes, which nobody can parse at a glance)
 *   >= 1m  -> `45m`
 *   < 1m   -> `38s`   (and the caller paints it RED, so the unit change is unmissable)
 *
 * The unit letters are load-bearing. The first cut printed `O:7:13`, which -- with two colons and a
 * capital O sitting in a monospace run -- read as the clock time "0:7:13" rather than "opens in
 * 7h13". Units and a single separator remove both ambiguities.
 */
fun fmtSession(st: SessionState): String {
    val s = st.secondsLeft
    return when {
        s < 60 -> "${s}s"
        s < 3600 -> "${s / 60}m"
        else -> "${s / 3600}h${((s % 3600) / 60).toString().padStart(2, '0')}"
    }
}

/** Red under a minute (it is seconds now), green while open, dim while merely waiting. */
fun sessionColor(st: SessionState, dim: Color): Color = when {
    st.secondsLeft < 60 -> Red
    st.isOpen -> Green
    else -> dim
}

/**
 * Ticks the three sessions.
 *
 * Same shape as `rememberCandleCountdown`, with one deliberate difference: it is anchored on
 * `System.currentTimeMillis()` (wall clock) rather than the broker's tick. Market hours ARE wall
 * clock, and this bar has to stay truthful when the feed is down -- which is exactly when you want
 * to know how long until London opens.
 *
 * Polls at 250 ms so a 1-second display never visibly skips, but only publishes when the value
 * actually changes, so a quiet bar recomposes once a second rather than four times.
 */
@Composable
fun rememberSessionStates(): List<Pair<MarketSession, SessionState>> {
    fun sample() = MarketSession.entries.map { it to statusOf(it, System.currentTimeMillis()) }
    val state by produceState(initialValue = sample()) {
        while (true) {
            val next = sample()
            if (next != value) value = next
            delay(250)
        }
    }
    return state
}

/**
 * The dedicated session strip that sits under the account badge.
 *
 * It has its own row because it does not fit anywhere else: the top bar's three buttons plus the
 * server URL leave ~70dp, and three chips need ~165dp. Parking it on the account banner was also
 * rejected -- that row is the fault surface ("Disconnected… retry 5s", "MT5 is logged out" + LOG IN),
 * so the two would collide precisely when something is wrong.
 */
@Composable
fun SessionBar(modifier: Modifier = Modifier) {
    val sessions = rememberSessionStates()
    val dim = MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        modifier.fillMaxWidth().height(18.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        sessions.forEach { (s, st) ->
            val c = sessionColor(st, dim)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(s.label, fontSize = 9.sp, color = dim, maxLines = 1)
                Spacer(Modifier.width(3.dp))
                // The O/C marker stays in the PROPORTIONAL font on purpose: inside the monospace
                // run a capital O is all but identical to a zero, and "O 7h13" was being read as
                // the time "0:7:13". Out here it is unmistakably a letter.
                Text(sessionPrefix(st), fontSize = 9.sp, fontWeight = FontWeight.Bold,
                    color = c, maxLines = 1)
                Spacer(Modifier.width(2.dp))
                Text(
                    fmtSession(st),
                    // Monospace: the digits tick every second and a proportional font would make
                    // the whole row shuffle sideways on every change.
                    fontFamily = FontFamily.Monospace,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                    color = c,
                    maxLines = 1,
                )
            }
        }
    }
}
