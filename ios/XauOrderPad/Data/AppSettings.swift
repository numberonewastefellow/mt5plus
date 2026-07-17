import Foundation
import Combine

/// Persisted connection settings + a few UI preferences. Ported from Android's data/Secrets.kt
/// (the essentials; server-profile list, layout modes and candle timeframe are Phase 2).
///
/// ── Where things live ──
/// The base URL and non-secret flags are in UserDefaults; the API token is in the Keychain (the
/// bearer secret for the trading API). Unlike the Android build there are NO baked-in default URL/
/// token — nothing secret is compiled in, so this source stays publishable; the user types the
/// server + token on the Connect screen.
///
/// ── What the token guards ──
/// On the mTLS path the client certificate is the outer gate and this token is the inner one; on a
/// plain-HTTP LAN server the token is the ONLY gate — which is why Connect warns on cleartext.
/// The MT5 broker password is never persisted here (it is typed on Accounts and sent to the server
/// once; later logins are by profile id and the server reads its own vault).
final class AppSettings: ObservableObject {

    static let shared = AppSettings()

    private let d = UserDefaults.standard
    private static let tokenAccount = "api_token"
    private static let defaultPort = 8765

    // MARK: Published state (drives the UI)

    /// e.g. "http://100.101.102.103:8765". Normalized on write.
    @Published var baseURL: String {
        didSet { d.set(baseURL, forKey: K.baseURL) }
    }

    /// Sent as the `x-token` header, and as `?token=` on the /ws URL if ever needed. Kept in the
    /// Keychain; the @Published mirror is for the Connect field.
    @Published var token: String {
        didSet { Keychain.set(token.trimmed(), account: Self.tokenAccount) }
    }

    /// Has the user explicitly pressed CONNECT? Separate from "we have a URL" so a prefilled value
    /// never auto-dials. Gates the feed opening at all.
    @Published private(set) var connected: Bool

    /// Confirm before a bulk close? Defaults TRUE — a mis-tap on CLOSE ALL is irreversible.
    @Published var confirmCloses: Bool {
        didSet { d.set(confirmCloses, forKey: K.confirmCloses) }
    }

    /// Which side the ENTER button trades: "buy" or "sell". Persisted.
    @Published var armedSide: String {
        didSet {
            let s = (armedSide == "sell") ? "sell" : "buy"   // never store anything else
            if s != armedSide { armedSide = s; return }
            d.set(s, forKey: K.armedSide)
        }
    }

    private init() {
        baseURL = d.string(forKey: K.baseURL) ?? ""
        token = Keychain.get(account: Self.tokenAccount)
        connected = d.bool(forKey: K.connected)
        confirmCloses = d.object(forKey: K.confirmCloses) as? Bool ?? true
        armedSide = (d.string(forKey: K.armedSide).flatMap { $0 == "sell" ? "sell" : "buy" }) ?? "buy"
    }

    // MARK: Derived

    /// Gates the feed: requires BOTH a URL and an explicit CONNECT.
    var isConfigured: Bool { !baseURL.trimmed().isEmpty && connected }

    /// True when the active transport is plain HTTP (token travels in the clear). Drives the
    /// Connect-screen warning.
    var isCleartext: Bool { baseURL.trimmed().lowercased().hasPrefix("http://") }

    // MARK: Actions

    /// The user pressed CONNECT. Normalizes + persists URL/token, then marks connected.
    func connect(url: String, token: String) {
        baseURL = Self.normalizeBaseURL(url)
        self.token = token.trimmed()
        connected = true
        d.set(true, forKey: K.connected)
    }

    /// Forget the token + connected state so the next launch lands on Connect. The base URL is
    /// KEPT (not a secret; retyping it every logout is pointless friction).
    func disconnect() {
        token = ""
        connected = false
        d.set(false, forKey: K.connected)
    }

    // MARK: URL normalization (ported verbatim from Secrets.normalizeBaseUrl)

    /// Tolerate what a human types: a bare IP, a trailing slash, a missing scheme. The port is only
    /// appended when the AUTHORITY has no port — checking the whole string would turn
    /// "http://host/api" into "http://host/api:8765".
    static func normalizeBaseURL(_ raw: String) -> String {
        var s = raw.trimmed()
        while s.hasSuffix("/") { s.removeLast() }
        if s.isEmpty { return "" }
        if !s.hasPrefix("http://") && !s.hasPrefix("https://") { s = "http://" + s }

        guard let schemeRange = s.range(of: "://") else { return s }
        let schemeEnd = schemeRange.upperBound
        let pathStart = s.range(of: "/", range: schemeEnd..<s.endIndex)?.lowerBound ?? s.endIndex
        let authority = String(s[schemeEnd..<pathStart])

        if !authority.contains(":") {
            s = String(s[s.startIndex..<pathStart]) + ":\(defaultPort)" + String(s[pathStart..<s.endIndex])
        }
        return s
    }

    private enum K {
        static let baseURL = "base_url"
        static let connected = "connected"
        static let confirmCloses = "confirm_closes"
        static let armedSide = "armed_side"
    }
}

extension String {
    func trimmed() -> String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
