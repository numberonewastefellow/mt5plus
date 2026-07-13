package com.xauorderpad.data

import android.content.Context
import android.content.SharedPreferences
import com.xauorderpad.net.Tls
import java.io.File
import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

/**
 * Runtime-uploaded TLS material for the EC2 mTLS path: our private CA (`ca.crt`) and the phone's
 * client identity (`client.p12` + its password).
 *
 * ── Why upload at runtime instead of baking into the APK ──
 * A rotated certificate must be a file you replace, not a new app you build and reinstall. Certs
 * are short-lived here (LEAF_DAYS=90, no CRL), so rotation is routine. The screen in
 * SettingsScreens imports them via the system file picker; this object persists and serves them.
 *
 * ── Storage ──
 * The files live in the app-private `filesDir` (other apps cannot read it without root); the p12
 * password sits in a private SharedPreferences. This matches Secrets' deliberate choice NOT to use
 * EncryptedSharedPreferences (see Secrets' header for the reasoning). The client.p12 IS a trading
 * credential -- a later hardening step imports the key into AndroidKeyStore so it becomes
 * non-exportable; this baseline keeps it in the sandbox.
 *
 * ── Dual-server note ──
 * Having certs loaded does NOT break the plain-HTTP LAN server: OkHttp only invokes the
 * SSLSocketFactory for https:// URLs. So one build reaches http://192.168.x:8765 (no TLS) and
 * https://<eip>:8443 (mTLS) with the same client.
 */
object CertStore {

    private const val PREFS = "xau_certs"
    private const val KEY_P12_PASSWORD = "p12_password"
    private const val CA_FILE = "ca.crt"
    private const val P12_FILE = "client.p12"

    private lateinit var dir: File
    private lateinit var prefs: SharedPreferences

    @Synchronized
    fun init(context: Context) {
        if (::dir.isInitialized) return
        dir = File(context.applicationContext.filesDir, "certs").apply { mkdirs() }
        prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    }

    private val caFile get() = File(dir, CA_FILE)
    private val p12File get() = File(dir, P12_FILE)

    fun password(): String = prefs.getString(KEY_P12_PASSWORD, "").orEmpty()

    /** True only when BOTH files and a password are present -- i.e. mTLS can actually be attempted. */
    fun hasCerts(): Boolean =
        ::dir.isInitialized && caFile.exists() && p12File.exists() && password().isNotEmpty()

    fun save(caPem: ByteArray, p12: ByteArray, p12Password: String) {
        // Validate BEFORE persisting: a wrong p12 password or a non-PEM CA must fail here, at the
        // point the user can fix it, not later as an opaque handshake error mid-connect.
        Tls.build(caPem, p12, p12Password)          // throws if either is unusable
        caFile.writeBytes(caPem)
        p12File.writeBytes(p12)
        prefs.edit().putString(KEY_P12_PASSWORD, p12Password).apply()
    }

    fun clear() {
        if (!::dir.isInitialized) return
        caFile.delete()
        p12File.delete()
        prefs.edit().remove(KEY_P12_PASSWORD).apply()
    }

    /**
     * "Launch and trade" demo build: if no cert is loaded yet and the APK bundles one in
     * assets/certs/, import it with [password]. Returns true if it just loaded a cert (so the
     * caller can rebuild the HTTP client). No-op when a cert is already present (a user upload
     * always wins), when the password is blank (non-demo build), or when the assets are absent.
     *
     * The bundled files are gitignored and only present in a build made from a machine that has
     * the certs -- so a normal build simply has no assets here and this does nothing.
     */
    fun seedFromAssetsIfEmpty(context: Context, password: String): Boolean {
        if (password.isBlank() || hasCerts()) return false
        return try {
            val am = context.applicationContext.assets
            val ca = am.open("certs/ca.crt").use { it.readBytes() }
            val p12 = am.open("certs/client.p12").use { it.readBytes() }
            save(ca, p12, password)      // validates before persisting; throws on a bad pair
            true
        } catch (_: Exception) {
            false                        // no bundled assets, or they don't validate -> manual flow
        }
    }

    /** The TLS material for OkHttp, or null when nothing is loaded (the plain-HTTP LAN case). */
    fun tls(): Tls.Material? {
        if (!hasCerts()) return null
        return try {
            Tls.build(caFile.readBytes(), p12File.readBytes(), password())
        } catch (_: Exception) {
            // Corrupt/incomplete on disk: behave as "no certs" rather than crash the client build.
            null
        }
    }

    /** For the UI: the client cert's subject CN and NotAfter. Null if absent/unreadable. */
    fun info(): Info? {
        if (!hasCerts()) return null
        return try {
            val ks = KeyStore.getInstance("PKCS12")
            p12File.inputStream().use { ks.load(it, password().toCharArray()) }
            val alias = ks.aliases().toList().firstOrNull { ks.isKeyEntry(it) }
                ?: ks.aliases().toList().firstOrNull() ?: return null
            val cert = ks.getCertificate(alias) as? X509Certificate ?: return null
            val cn = cert.subjectX500Principal.name
                .split(",").firstOrNull { it.trim().startsWith("CN=") }
                ?.trim()?.removePrefix("CN=") ?: "(unknown)"
            val caCn = (CertificateFactory.getInstance("X.509")
                .generateCertificate(caFile.inputStream()) as X509Certificate)
                .subjectX500Principal.name
                .split(",").firstOrNull { it.trim().startsWith("CN=") }
                ?.trim()?.removePrefix("CN=") ?: "(unknown)"
            Info(clientCn = cn, caCn = caCn, notAfter = cert.notAfter.time)
        } catch (_: Exception) {
            null
        }
    }

    data class Info(val clientCn: String, val caCn: String, val notAfter: Long)
}
