import Foundation
import Security

/// The mutual-TLS handler for BOTH the WebSocket task and every REST call — the Swift equivalent
/// of Android's Tls.kt (KeyManagerFactory for the client identity + a CA-pinned X509TrustManager).
///
/// One delegate is shared by the whole app's URLSession, so mTLS is configured once. Connection-
/// level challenges (server trust + client certificate) are delivered to this session-level
/// method for both data tasks and WebSocket tasks.
///
/// ── Trust is pinned to OUR CA and nothing else. ──
/// The server cert is signed by our private CA, so the system trust store would reject it — which
/// is the point: no public CA can mint a cert this app accepts for the box. There is deliberately
/// NO trust-all bypass here, and there must never be — it would throw away the entire mTLS guarantee.
///
/// ── Dual transport. ──
/// The plain-HTTP LAN server issues no TLS challenges, so this delegate is simply never consulted
/// for it. Only the https/wss mTLS server triggers the two branches below.
final class TradingURLSessionDelegate: NSObject, URLSessionDelegate {

    private let certs: CertStore

    init(certs: CertStore = .shared) {
        self.certs = certs
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        switch challenge.protectionSpace.authenticationMethod {

        case NSURLAuthenticationMethodClientCertificate:
            // Present our client identity (Android: KeyManagerFactory from client.p12).
            guard let identity = certs.clientIdentity() else {
                // No cert loaded. We can't authenticate; let the default machinery fail the
                // handshake rather than proceeding cert-less against an mTLS server.
                completionHandler(.performDefaultHandling, nil)
                return
            }
            let credential = URLCredential(identity: identity,
                                           certificates: nil,
                                           persistence: .forSession)
            completionHandler(.useCredential, credential)

        case NSURLAuthenticationMethodServerTrust:
            // Pin server trust to our CA only (Android: custom X509TrustManager over our CA).
            guard let serverTrust = challenge.protectionSpace.serverTrust else {
                completionHandler(.performDefaultHandling, nil)
                return
            }
            guard let ca = certs.caCertificate() else {
                // No pinned CA loaded → this is the plain/public path; use system defaults.
                completionHandler(.performDefaultHandling, nil)
                return
            }
            SecTrustSetAnchorCertificates(serverTrust, [ca] as CFArray)
            // Anchors-only: our CA is the ONLY acceptable root for this evaluation.
            SecTrustSetAnchorCertificatesOnly(serverTrust, true)

            var error: CFError?
            if SecTrustEvaluateWithError(serverTrust, &error) {
                completionHandler(.useCredential, URLCredential(trust: serverTrust))
            } else {
                // Does not chain to our CA → reject. Never fall back to system trust here.
                completionHandler(.cancelAuthenticationChallenge, nil)
            }

        default:
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
