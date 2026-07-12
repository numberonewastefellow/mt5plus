package com.xauorderpad.ui

import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.util.Locale

/**
 * Number formatting for the whole app. One place, on purpose.
 *
 * ── Why this exists (B2): `String.format("%.2f", v)` uses the DEFAULT LOCALE ──
 *
 * On a comma-decimal locale (de, fr, es, pt-BR, ru, id …) that produces "0,02". The lot
 * field is round-tripped through this formatting -- so `"0,02".toDoubleOrNull()` returns
 * NULL, `validatedLot()` rejects it, and `placeOrder` silently sends nothing. The app
 * simply cannot trade, and it looks like the user typed something wrong.
 *
 * Machine-facing numbers (anything that will be parsed back, or sent over the wire) MUST
 * be Locale.US. That is not a cosmetic choice: it is the difference between an order being
 * placed and not.
 *
 * ── Why DecimalFormat and not String.format (C4) ──
 *
 * `String.format("%.${digits}f", …)` builds a NEW format string AND a new `Formatter` on
 * every call. At 5 Hz across a grid that is ~75 throwaway Formatters per second, all of it
 * garbage. DecimalFormat instances are cached here and reused.
 *
 * DecimalFormat is NOT thread-safe, so every instance is confined to the UI thread. The
 * notification service formats on its own thread and therefore uses its own instances --
 * see FeedService.
 */
object Fmt {

    private val US: DecimalFormatSymbols = DecimalFormatSymbols(Locale.US)

    /** Cache keyed by decimal places. `digits` comes from the broker and changes ~never. */
    private val byDigits = HashMap<Int, DecimalFormat>(4)

    private fun fmt(digits: Int): DecimalFormat = byDigits.getOrPut(digits) {
        DecimalFormat("0." + "0".repeat(digits.coerceIn(0, 8)), US).apply {
            if (digits <= 0) applyPattern("0")
        }
    }

    /** A price, at the broker's own precision. `null` renders as "--", never as "0.00":
     *  absent and zero mean very different things on a trading screen. */
    fun price(v: Double?, digits: Int?): String =
        v?.let { fmt(digits ?: 2).format(it) } ?: "--"

    /** A lot size. Always 2dp, always Locale.US -- this string is parsed back to a Double. */
    fun lot(v: Double?): String = v?.let { fmt(2).format(it) } ?: "--"

    /** Money, signed. The leading "+" is deliberate: on a P&L column the sign IS the
     *  information, and a bare "12.40" next to a red "-12.40" is easy to misread. */
    fun signedMoney(v: Double?): String {
        if (v == null) return "--"
        val s = fmt(2).format(kotlin.math.abs(v))
        return if (v < 0) "-$s" else "+$s"
    }

    fun money(v: Double?): String = v?.let { fmt(2).format(it) } ?: "--"

    /** Spread, already converted to POINTS by Snapshot.spreadPoints. Whole numbers. */
    fun points(v: Double?): String = v?.let { fmt(0).format(it) } ?: "--"

    /** Parse a user-typed decimal. Accepts a comma as well as a dot, because a phone
     *  keyboard on a comma-locale offers a comma and the user WILL type it. */
    fun parseDecimal(s: String): Double? =
        s.trim().replace(',', '.').toDoubleOrNull()
}
