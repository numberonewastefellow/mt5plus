package com.xauorderpad.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlin.math.PI
import kotlin.math.sin

// The launch palette — warm gold on the app's own near-black ground (#101214, see themes.xml),
// so there is no colour jump when the welcome fades into the real screens.
private val Ink = Color(0xFF0A0B0D)
private val InkWarm = Color(0xFF17140E)
private val GoldHi = Color(0xFFFFE9A8)
private val Gold = Color(0xFFE7B94D)
private val GoldDeep = Color(0xFFB07E1E)

/**
 * The launch/welcome overlay. Fully self-contained and presentational: it draws its own gold "Om"
 * emblem with Canvas (so it looks intentional even where the ॐ glyph is unavailable), plays a
 * one-shot synthesized chime, and calls [onFinished] once — either when its short timeline ends or
 * when the user taps to skip. It touches no trading state; MainActivity fades it out on finish.
 */
@Composable
fun WelcomeScreen(onFinished: () -> Unit, modifier: Modifier = Modifier) {

    // Fire-once guard: the auto-timeout and a skip-tap both race to end the screen.
    val finish = remember(onFinished) {
        var fired = false
        { if (!fired) { fired = true; onFinished() } }
    }

    // Sound: play once on first composition.
    LaunchedEffect(Unit) { Chime.playOnce() }

    // `enter` sweeps 0→1 to stage every element in (same speed as before), then the fully-formed
    // emblem holds on screen before we hand off. The hold is the knob to make the screen linger.
    val enter = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        enter.animateTo(1f, tween(950, easing = FastOutSlowInEasing))
        delay(2400)
        finish()
    }

    // The trending mini-chart "prints" its candles left-to-right (a live chart forming). Its own
    // animatable so it keeps building through the hold, independent of the entrance stagger.
    val chart = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        delay(450)   // let the emblem land first
        chart.animateTo(1f, tween(1700, easing = FastOutSlowInEasing))
    }

    // Continuous life: a slow aura pulse and a sweeping tick arc around the emblem.
    val inf = rememberInfiniteTransition(label = "welcome")
    val pulse by inf.animateFloat(
        0f, 1f, infiniteRepeatable(tween(2400, easing = LinearEasing), RepeatMode.Restart),
        label = "pulse",
    )
    val sweep by inf.animateFloat(
        0f, 360f, infiniteRepeatable(tween(3600, easing = LinearEasing), RepeatMode.Restart),
        label = "sweep",
    )

    val e = enter.value
    val emblem = seg(e, 0f, 0.7f)     // emblem scales/fades in first
    val word = seg(e, 0.35f, 1f)      // wordmark follows
    val tag = seg(e, 0.6f, 1f)        // tagline last
    val bars = seg(e, 0.5f, 1f)       // candlesticks rise with the wordmark

    Box(
        modifier
            .fillMaxSize()
            .background(Brush.verticalGradient(listOf(Ink, Color(0xFF101214), InkWarm)))
            .pointerInput(Unit) { detectTapGestures { finish() } },
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {

            // ── Emblem: glow + aura rings + a sweeping tick arc, with the ॐ glyph on top ──
            Box(contentAlignment = Alignment.Center) {
                Canvas(
                    Modifier
                        .size(220.dp)
                        .graphicsLayer {
                            val s = 0.7f + 0.3f * emblem
                            scaleX = s; scaleY = s; alpha = emblem
                        },
                ) { drawEmblem(pulse, sweep) }

                androidx.compose.material3.Text(
                    "ॐ",
                    fontSize = 96.sp,
                    style = TextStyle(
                        brush = Brush.verticalGradient(listOf(GoldHi, Gold, GoldDeep)),
                        shadow = Shadow(color = Gold.copy(alpha = 0.85f), blurRadius = 36f),
                    ),
                    modifier = Modifier.graphicsLayer {
                        val s = 0.7f + 0.3f * emblem
                        scaleX = s; scaleY = s; alpha = emblem
                    },
                )
            }

            Spacer(Modifier.height(28.dp))

            // ── Wordmark ──
            androidx.compose.material3.Text(
                "Om!",
                fontSize = 60.sp,
                fontWeight = FontWeight.Black,
                style = TextStyle(brush = Brush.verticalGradient(listOf(GoldHi, Gold, GoldDeep))),
                modifier = Modifier.graphicsLayer {
                    alpha = word
                    translationY = (1f - word) * 34f
                },
            )

            Spacer(Modifier.height(10.dp))

            // ── Tagline ──
            androidx.compose.material3.Text(
                "G O L D   ·   P R E C I S I O N   T R A D I N G",
                fontSize = 11.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = 2.sp,
                color = Gold.copy(alpha = 0.75f * tag),
                modifier = Modifier.graphicsLayer { alpha = tag },
            )

            Spacer(Modifier.height(30.dp))

            // ── The trading signature: a small row of candlesticks that rise into place ──
            Canvas(Modifier.size(width = 168.dp, height = 40.dp)) { drawCandles(bars) }

            Spacer(Modifier.height(22.dp))

            // ── Real chart: candles print left-to-right in a trending sequence (green, red, red,
            //    then green up, up, up), with a gentle live "dance" and a gold uptrend line ──
            Canvas(
                Modifier
                    .size(width = 240.dp, height = 108.dp)
                    .graphicsLayer { alpha = seg(e, 0.5f, 1f) },
            ) { drawTrendChart(chart.value, pulse) }
        }
    }
}

/** Linear ramp of [p] mapped from [start,end] into 0..1, clamped. Stages the entrance. */
private fun seg(p: Float, start: Float, end: Float): Float =
    ((p - start) / (end - start)).coerceIn(0f, 1f)

private fun DrawScope.drawEmblem(pulse: Float, sweep: Float) {
    val c = Offset(size.width / 2f, size.height / 2f)
    val baseR = size.minDimension / 2f * 0.62f

    // Soft radial glow that breathes with the pulse.
    val glow = 0.16f + 0.10f * (0.5f + 0.5f * sin(pulse * 2f * PI).toFloat())
    drawCircle(
        brush = Brush.radialGradient(
            colors = listOf(Gold.copy(alpha = glow), Color.Transparent),
            center = c,
            radius = baseR * 2.1f,
        ),
        radius = baseR * 2.1f,
        center = c,
    )

    // Aura rings expanding outward and fading — three, evenly out of phase.
    for (k in 0 until 3) {
        val phase = (pulse + k / 3f) % 1f
        val r = baseR * (0.85f + phase * 0.95f)
        val a = (1f - phase) * 0.30f
        drawCircle(
            color = Gold.copy(alpha = a),
            radius = r,
            center = c,
            style = Stroke(width = (1f - phase) * 5f + 1f),
        )
    }

    // A faint full ring, then a bright sweeping arc riding on it (the "tick" motion).
    drawCircle(color = Gold.copy(alpha = 0.14f), radius = baseR, center = c, style = Stroke(1.5f))
    val d = baseR * 2f
    drawArc(
        brush = Brush.sweepGradient(listOf(Color.Transparent, Gold, GoldHi), center = c),
        startAngle = sweep,
        sweepAngle = 80f,
        useCenter = false,
        topLeft = Offset(c.x - baseR, c.y - baseR),
        size = androidx.compose.ui.geometry.Size(d, d),
        style = Stroke(width = 3f, cap = StrokeCap.Round),
    )
}

// Fixed heights + directions so the motif is stable frame to frame; `p` (0..1) raises them in.
private val CANDLE_H = floatArrayOf(0.45f, 0.72f, 0.55f, 0.88f, 0.6f, 0.95f, 0.5f)
private val CANDLE_UP = booleanArrayOf(true, true, false, true, true, true, false)

private fun DrawScope.drawCandles(p: Float) {
    if (p <= 0f) return
    val n = CANDLE_H.size
    val slot = size.width / n
    val bodyW = slot * 0.34f
    val up = Color(0xFF1FA97A)
    val down = Color(0xFFE0544E)
    for (i in 0 until n) {
        // Stagger each candle slightly so they don't pop up all at once.
        val local = ((p - i * 0.05f) / 0.6f).coerceIn(0f, 1f)
        if (local <= 0f) continue
        val cx = slot * (i + 0.5f)
        val full = size.height * CANDLE_H[i]
        val h = full * local
        val top = size.height - h
        val col = (if (CANDLE_UP[i]) up else down).copy(alpha = 0.9f * local)
        // Wick.
        drawLine(
            color = col,
            start = Offset(cx, top - size.height * 0.12f * local),
            end = Offset(cx, size.height),
            strokeWidth = 2f,
            cap = StrokeCap.Round,
        )
        // Body.
        drawRect(
            color = col,
            topLeft = Offset(cx - bodyW / 2f, top),
            size = androidx.compose.ui.geometry.Size(bodyW, h),
        )
    }
}

// The trending sequence the user asked for: green, red, red, then green-up, up, up, up. Each candle
// is (open, close) as a fraction of the chart height (0 = bottom); close>open is a green (bull)
// candle, close<open a red (bear) one. The path dips early then rallies into a clean uptrend.
private val TREND_OPEN = floatArrayOf(0.24f, 0.34f, 0.28f, 0.22f, 0.40f, 0.56f, 0.72f)
private val TREND_CLOSE = floatArrayOf(0.34f, 0.28f, 0.22f, 0.40f, 0.56f, 0.72f, 0.88f)

/**
 * A live-feeling candlestick chart. [build] (0..1) prints the candles left-to-right, each one
 * "forming" out of its open price; [live] (0..1, continuous) adds a gentle dance and a price
 * flicker on the leading candle. A gold line joins the closes to spell out the uptrend.
 */
private fun DrawScope.drawTrendChart(build: Float, live: Float) {
    val n = TREND_OPEN.size
    val slot = size.width / n
    val bodyW = slot * 0.46f
    val up = Color(0xFF1FA97A)
    val down = Color(0xFFE0544E)
    val padY = size.height * 0.12f
    val h = size.height - 2f * padY
    fun y(p: Float) = padY + h * (1f - p)

    val liveTick = 0.02f * sin(live * 2f * PI).toFloat()
    val closes = ArrayList<Offset>(n)

    for (i in 0 until n) {
        val reveal = (build * n - i).coerceIn(0f, 1f)
        if (reveal <= 0f) break
        val cx = slot * (i + 0.5f)
        val leading = i == n - 1
        // A soft ripple so the whole chart "dances"; the last candle also gets a live price flicker.
        val bob = 0.014f * sin(live * 2f * PI + i * 0.8f).toFloat()
        val o = TREND_OPEN[i] + bob
        var c = TREND_CLOSE[i] + bob
        if (leading && build >= 1f) c += liveTick
        val col = (if (c >= o) up else down).copy(alpha = 0.94f * reveal)
        val hi = maxOf(o, c) + 0.05f
        val lo = minOf(o, c) - 0.05f

        // Grow the wick and body out from the OPEN price as the candle reveals.
        val wickTop = y(o + (hi - o) * reveal)
        val wickBot = y(o - (o - lo) * reveal)
        val bodyA = y(o + (maxOf(o, c) - o) * reveal)
        val bodyB = y(o - (o - minOf(o, c)) * reveal)
        drawLine(col, Offset(cx, wickTop), Offset(cx, wickBot), strokeWidth = 2.5f, cap = StrokeCap.Round)
        drawRect(
            col,
            topLeft = Offset(cx - bodyW / 2f, minOf(bodyA, bodyB)),
            size = androidx.compose.ui.geometry.Size(bodyW, kotlin.math.abs(bodyB - bodyA).coerceAtLeast(2f)),
        )
        if (reveal >= 1f) closes.add(Offset(cx, y(c)))
    }

    // Gold uptrend line through the fully-formed closes, with a glowing dot at the live point.
    for (k in 0 until closes.size - 1) {
        drawLine(Gold.copy(alpha = 0.55f), closes[k], closes[k + 1], strokeWidth = 2f, cap = StrokeCap.Round)
    }
    closes.lastOrNull()?.let { p ->
        drawCircle(Gold.copy(alpha = 0.22f), radius = 8f, center = p)
        drawCircle(Gold, radius = 3.5f, center = p)
    }
}
