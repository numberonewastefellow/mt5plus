package com.xauorderpad.ui

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.sin

/**
 * A tiny, dependency-free "singing bowl / Om" chime, synthesized at runtime.
 *
 * ── Why generate it instead of bundling a .ogg ──
 *
 * The welcome screen is meant to be weightless. Shipping an audio asset would add a binary to
 * every APK for a one-second sound; instead the tone is generated as PCM and streamed once
 * through an AudioTrack. Nothing to package, nothing to license.
 *
 * The sound is a warm low swell (D3 fundamental + a few slightly-detuned partials, soft attack,
 * long decay) that suits the name. It is entirely a nicety: the whole thing runs on a daemon
 * thread inside a catch-all, so any audio-stack failure is swallowed and NEVER touches the
 * launch. It plays through USAGE_MEDIA, so it follows the phone's media volume and is silent on
 * a muted phone.
 */
object Chime {

    /** Guards against a double-play if the welcome screen recomposes. */
    @Volatile private var played = false

    fun playOnce() {
        if (played) return
        played = true
        Thread({
            try { render() } catch (_: Throwable) { /* audio is a nicety, never fatal */ }
        }, "om-chime").apply { isDaemon = true }.start()
    }

    private fun render() {
        val sampleRate = 44_100
        val seconds = 1.8
        val n = (sampleRate * seconds).toInt()
        val buf = ShortArray(n)

        val f0 = 146.83                                       // D3 — low, warm, "Om"-like
        val partials = doubleArrayOf(1.0, 2.01, 3.03, 4.0)    // slight detune → a living shimmer
        val gains = doubleArrayOf(1.0, 0.45, 0.28, 0.14)
        val gainSum = gains.sum()

        for (i in 0 until n) {
            val t = i.toDouble() / sampleRate
            // Soft ~80 ms attack, then a gentle exponential decay.
            val env = (t / 0.08).coerceAtMost(1.0) * exp(-t * 2.2)
            var s = 0.0
            for (p in partials.indices) s += gains[p] * sin(2 * PI * f0 * partials[p] * t)
            s /= gainSum
            // A slow amplitude vibrato gives the tone a breathing quality.
            val vib = 1.0 + 0.05 * sin(2 * PI * 5.0 * t)
            val v = (s * env * vib * 0.35 * Short.MAX_VALUE)
            buf[i] = v.toInt().coerceIn(-32_768, 32_767).toShort()
        }

        val attrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
            .build()
        val format = AudioFormat.Builder()
            .setSampleRate(sampleRate)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()

        val bytes = buf.size * 2
        val minBuf = AudioTrack.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val track = AudioTrack(
            attrs, format, maxOf(minBuf, bytes),
            AudioTrack.MODE_STATIC, AudioManager.AUDIO_SESSION_ID_GENERATE
        )
        track.write(buf, 0, buf.size)
        track.play()

        // We are already on a throwaway daemon thread, so simply wait out the tone (no Looper is
        // available here for a position-marker callback) and then release the native resources.
        try { Thread.sleep(((seconds + 0.2) * 1000).toLong()) } catch (_: InterruptedException) {}
        try { track.stop() } catch (_: Throwable) {}
        try { track.release() } catch (_: Throwable) {}
    }
}
