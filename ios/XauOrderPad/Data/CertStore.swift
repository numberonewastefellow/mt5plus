import Foundation
import Security

/// Runtime-uploaded TLS material for the mTLS path: our private CA (`ca.crt`) and the phone's
/// client identity (`client.p12` + its password). Ported from Android's data/CertStore.kt.
///
/// ── Why upload at runtime instead of baking into the app ──
/// A rotated certificate must be a file you replace, not a new app you build and reinstall. Certs
/// are short-lived here (LEAF_DAYS=90, no CRL), so rotation is routine. The Certs screen imports
/// them via the Files picker; this object persists and serves them.
///
/// ── Storage ──
/// The files live in the app sandbox (Application Support/certs); the p12 password sits in the
/// Keychain. Baking secrets into the app is avoided so the source repo stays publishable.
///
/// ── Dual-server note ──
/// Having certs loaded does NOT break the plain-HTTP LAN server: the URLSession delegate only
/// presents the client cert when the server actually issues a client-certificate challenge (i.e.
/// over TLS). So one build reaches http://192.168.x:8765 (no TLS) and https://<host>:8443 (mTLS)
/// with the same client. See TradingURLSessionDelegate.
final class CertStore: ObservableObject {

    static let shared = CertStore()

    /// Bumped whenever certs are saved/cleared so the UI and the networking layer can react
    /// (rebuild the URLSession). Mirrors Android's Feed.reloadTls trigger.
    @Published private(set) var revision: Int = 0

    private let dir: URL
    private let caURL: URL
    private let p12URL: URL
    private static let pwAccount = "p12_password"

    struct CertError: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    struct Info: Equatable {
        let clientCN: String
        let caCN: String
        let notAfter: Date?
    }

    private init() {
        let base = (try? FileManager.default.url(for: .applicationSupportDirectory,
                                                 in: .userDomainMask,
                                                 appropriateFor: nil, create: true))
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        dir = base.appendingPathComponent("certs", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        caURL = dir.appendingPathComponent("ca.crt")
        p12URL = dir.appendingPathComponent("client.p12")
    }

    // MARK: State

    func password() -> String { Keychain.get(account: Self.pwAccount) }

    /// True only when BOTH files and a password are present -- i.e. mTLS can actually be attempted.
    func hasCerts() -> Bool {
        FileManager.default.fileExists(atPath: caURL.path)
            && FileManager.default.fileExists(atPath: p12URL.path)
            && !password().isEmpty
    }

    // MARK: Mutation

    /// Validate BEFORE persisting: a wrong p12 password or a non-PEM/DER CA must fail here, where
    /// the user can fix it, not later as an opaque handshake error mid-connect. Throws on bad input.
    func save(caData: Data, p12Data: Data, p12Password: String) throws {
        guard Self.parseCA(caData) != nil else {
            throw CertError(message: "ca.crt is not a valid X.509 certificate (PEM or DER).")
        }
        guard Self.importIdentity(p12: p12Data, password: p12Password) != nil else {
            throw CertError(message: "client.p12 could not be opened — wrong password or not a PKCS#12 file.")
        }
        try caData.write(to: caURL, options: .atomic)
        try p12Data.write(to: p12URL, options: .atomic)
        Keychain.set(p12Password, account: Self.pwAccount)
        bump()
    }

    func clear() {
        try? FileManager.default.removeItem(at: caURL)
        try? FileManager.default.removeItem(at: p12URL)
        Keychain.remove(account: Self.pwAccount)
        bump()
    }

    private func bump() {
        DispatchQueue.main.async { self.revision += 1 }
    }

    // MARK: Material for the TLS delegate

    /// The pinned CA certificate, or nil when nothing is loaded (the plain-HTTP LAN case).
    func caCertificate() -> SecCertificate? {
        guard FileManager.default.fileExists(atPath: caURL.path),
              let data = try? Data(contentsOf: caURL) else { return nil }
        return Self.parseCA(data)
    }

    /// The client identity for the mTLS handshake, or nil when nothing is loaded.
    func clientIdentity() -> SecIdentity? {
        guard hasCerts(), let data = try? Data(contentsOf: p12URL) else { return nil }
        return Self.importIdentity(p12: data, password: password())
    }

    // MARK: UI info (client cert CN + expiry)

    func info() -> Info? {
        guard let identity = clientIdentity() else { return nil }
        var cert: SecCertificate?
        guard SecIdentityCopyCertificate(identity, &cert) == errSecSuccess, let clientCert = cert else {
            return nil
        }
        let clientCN = Self.commonName(of: clientCert) ?? "(unknown)"
        let caCN = caCertificate().flatMap { Self.commonName(of: $0) } ?? "(unknown)"
        return Info(clientCN: clientCN, caCN: caCN, notAfter: Self.notAfter(of: clientCert))
    }

    // MARK: Security-framework helpers

    /// Accepts PEM (BEGIN/END CERTIFICATE) or raw DER; returns a SecCertificate or nil.
    static func parseCA(_ data: Data) -> SecCertificate? {
        let der = derFromPEMIfNeeded(data)
        return SecCertificateCreateWithData(nil, der as CFData)
    }

    /// PEM -> DER. If the bytes aren't PEM text, they're assumed to already be DER and returned as-is.
    static func derFromPEMIfNeeded(_ data: Data) -> Data {
        guard let text = String(data: data, encoding: .utf8),
              text.contains("BEGIN CERTIFICATE") else { return data }
        let base64 = text
            .components(separatedBy: .newlines)
            .filter { !$0.contains("BEGIN CERTIFICATE") && !$0.contains("END CERTIFICATE") }
            .joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return Data(base64Encoded: base64) ?? data
    }

    /// Import a PKCS#12 and pull out its SecIdentity. Wrong password / bad file -> nil.
    static func importIdentity(p12: Data, password: String) -> SecIdentity? {
        let options: [String: Any] = [kSecImportExportPassphrase as String: password]
        var items: CFArray?
        let status = SecPKCS12Import(p12 as CFData, options as CFDictionary, &items)
        guard status == errSecSuccess,
              let array = items as? [[String: Any]],
              let first = array.first,
              let identityRef = first[kSecImportItemIdentity as String] else { return nil }
        // CFTypeRef -> SecIdentity. force-bridge is safe: the key guarantees the type.
        return (identityRef as! SecIdentity)
    }

    static func commonName(of cert: SecCertificate) -> String? {
        var cn: CFString?
        guard SecCertificateCopyCommonName(cert, &cn) == errSecSuccess else { return nil }
        return cn as String?
    }

    /// Best-effort notAfter via SecCertificateCopyValues. The value is seconds since the reference
    /// date (2001-01-01). Returns nil if unavailable.
    static func notAfter(of cert: SecCertificate) -> Date? {
        let keys = [kSecOIDX509V1ValidityNotAfter] as CFArray
        guard let values = SecCertificateCopyValues(cert, keys, nil) as? [CFString: Any],
              let entry = values[kSecOIDX509V1ValidityNotAfter] as? [CFString: Any],
              let raw = entry[kSecPropertyKeyValue] else { return nil }
        let seconds: Double?
        if let n = raw as? NSNumber { seconds = n.doubleValue }
        else if let d = raw as? Double { seconds = d }
        else { seconds = nil }
        return seconds.map { Date(timeIntervalSinceReferenceDate: $0) }
    }
}
