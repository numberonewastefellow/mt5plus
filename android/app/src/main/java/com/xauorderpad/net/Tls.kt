package com.xauorderpad.net

import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * Builds the mutual-TLS material for OkHttp from an uploaded CA + client PKCS12.
 *
 * Trust is pinned to OUR CA and nothing else. The EC2 server cert is signed by our private CA, so
 * the system trust store would reject it -- which is the point: no public CA can mint a cert this
 * app will accept for the box. The plan calls this out explicitly: do NOT reach for a trust-all
 * X509TrustManager to make an opaque handshake failure "work". There is no such bypass in this app
 * and there must never be -- it would throw away the entire mTLS guarantee.
 */
object Tls {

    class Material(val factory: SSLSocketFactory, val trustManager: X509TrustManager)

    /**
     * @throws Exception if the CA is not a valid certificate, or the p12/password is wrong. Callers
     *   (CertStore.save) rely on this to reject bad input at upload time.
     */
    fun build(caPem: ByteArray, p12: ByteArray, p12Password: String): Material {
        // --- Trust: our CA only ---
        val ca = caPem.inputStream().use {
            CertificateFactory.getInstance("X.509").generateCertificate(it) as X509Certificate
        }
        val trustStore = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null, null)
            setCertificateEntry("xau-ca", ca)
        }
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            .apply { init(trustStore) }
        val trustManager = tmf.trustManagers.filterIsInstance<X509TrustManager>().firstOrNull()
            ?: error("no X509TrustManager produced from the CA")

        // --- Client identity from the PKCS12 (wrong password throws here) ---
        val pw = p12Password.toCharArray()
        val keyStore = KeyStore.getInstance("PKCS12").apply { p12.inputStream().use { load(it, pw) } }
        val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
            .apply { init(keyStore, pw) }

        val ctx = SSLContext.getInstance("TLS").apply {
            init(kmf.keyManagers, arrayOf<javax.net.ssl.TrustManager>(trustManager), null)
        }
        return Material(ctx.socketFactory, trustManager)
    }
}
