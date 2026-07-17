import Foundation
import Combine

/// Drives the Trade screen. Derives narrow, view-ready values from the live feed and owns the order
/// form + command handlers. Ported from the essential parts of Android's ui/TradingViewModel.kt
/// (slices, the `live` staleness guard, LIFO armed target, and the command handlers used by the MVP).
@MainActor
final class TradeViewModel: ObservableObject {

    struct Banner: Identifiable, Equatable {
        let id = UUID()
        let text: String
        let isError: Bool
    }

    private let feed: Feed
    private let settings: AppSettings
    private var cancellables = Set<AnyCancellable>()

    // MARK: Feed-derived state
    @Published private(set) var snapshot: Snapshot?
    @Published private(set) var link: Link = .connecting
    /// Entry is allowed only when the socket is Up AND a frame arrived recently — a frozen-but-
    /// connected feed must disable ENTER. Recomputed on each frame and once a second.
    @Published private(set) var live: Bool = false

    // MARK: Order form
    @Published var lot: String = "0.01"
    @Published var slPoints: String = "0"
    @Published var tpPoints: String = "0"
    @Published var armedSide: String        // "buy" | "sell"

    // MARK: Transient UI
    @Published var banner: Banner?

    /// Ten missed 1 s heartbeats. The server force-resends at least every 1 s even when unchanged,
    /// so this is a genuine "feed is frozen" threshold, not just "prices are quiet".
    private static let staleAfter: TimeInterval = 10
    private var lastFrameAt: DispatchTime?
    /// Tickets with a close already on the wire, so rapid CLOSE taps target N distinct positions
    /// instead of all hitting the same snapshot-lagged newest ticket.
    private var pendingCloses: Set<Int64> = []

    init(feed: Feed = .shared, settings: AppSettings = .shared) {
        self.feed = feed
        self.settings = settings
        self.armedSide = settings.armedSide

        feed.$snapshot
            .sink { [weak self] snap in
                guard let self else { return }
                self.snapshot = snap
                if snap != nil { self.lastFrameAt = .now() }
                self.recomputeLive()
            }
            .store(in: &cancellables)

        feed.$link
            .sink { [weak self] in self?.link = $0; self?.recomputeLive() }
            .store(in: &cancellables)

        // A frozen feed emits nothing, so drive staleness off a 1 s clock too.
        Timer.publish(every: 1, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in self?.recomputeLive() }
            .store(in: &cancellables)
    }

    private func recomputeLive() {
        guard case .up = link, let at = lastFrameAt else { live = false; return }
        let elapsed = Double(DispatchTime.now().uptimeNanoseconds - at.uptimeNanoseconds) / 1_000_000_000
        live = elapsed < Self.staleAfter
    }

    // MARK: Derived view values

    var bid: Double? { snapshot?.bid }
    var ask: Double? { snapshot?.ask }
    var spreadPoints: Double? { snapshot?.spreadPoints }
    var digits: Int { snapshot?.digits ?? 2 }
    var equity: Double? { snapshot?.account?.equity }
    var floatingPl: Double? { snapshot?.floatingPl }
    var currency: String { snapshot?.account?.currency ?? "" }
    var isDemo: Bool? { snapshot?.account?.isDemo }
    var login: Int64? { snapshot?.account?.login }
    var positions: [Position] { snapshot?.openPositions ?? [] }
    var openCount: Int { positions.count }

    private var armedWant: String { armedSide == "sell" ? "SELL" : "BUY" }
    var armedPositions: [Position] { positions.filter { $0.side == armedWant } }

    /// The ticket CLOSE would actually close: the NEWEST position on the armed side (LIFO) that does
    /// not already have a close on the wire. `time` can tie when several fills land in one second, so
    /// ticket breaks the tie (MT5 tickets increase with time) — deterministic, not list-order dependent.
    var armedTarget: Position? {
        armedPositions
            .filter { !pendingCloses.contains($0.ticket) }
            .max { ($0.time ?? 0, $0.ticket) < ($1.time ?? 0, $1.ticket) }
    }

    // MARK: Commands (all human-initiated — never auto-fired)

    func setArmedSide(_ side: String) {
        let s = side == "sell" ? "sell" : "buy"
        settings.armedSide = s
        armedSide = s
    }

    /// ENTER: trade the armed side. Gated on `live` — never send an order against a frozen feed.
    func placeArmed() {
        guard live else { show("Feed is stale — not sending", error: true); return }
        guard let volume = Double(lot), volume > 0 else { show("Enter a valid lot size", error: true); return }
        let sl = Double(slPoints) ?? 0
        let tp = Double(tpPoints) ?? 0
        let side = armedSide
        Task {
            let result = await feed.api.order(side: side, volume: volume, slPoints: sl, tpPoints: tp)
            switch result {
            case .ok(let r):
                show("\(side.uppercased()) \(volume) → #\(r.ticket.map(String.init) ?? "?") @ \(fmt(r.price, digits))")
            case .failed(let m, let rc, let c):
                show(errorText(m, rc, c), error: true)
            case .unauthorized:
                show("Token rejected — reconnect", error: true)
            case .timedOut:
                // NOT a failure: the order may still have filled. Do not invite a second tap.
                show("Timed out — the order MAY have filled. Check positions before retrying.", error: true)
            }
        }
    }

    /// EXIT: close the newest position on the armed side. Deliberately NOT gated on `live` — a dead
    /// socket is not a dead server (the WS can drop while HTTP still works), and closing by unique
    /// ticket is safe even against a slightly stale book.
    func closeArmed() {
        guard let ticket = armedTarget?.ticket else {
            show("No \(armedSide.uppercased()) position to close", error: true)
            return
        }
        pendingCloses.insert(ticket)
        objectWillChange.send()          // armedTarget shifts to the next-newest immediately
        Task {
            let result = await feed.api.close(ticket: ticket)
            pendingCloses.remove(ticket) // free on return: failed -> retry, success -> gone next snapshot
            objectWillChange.send()
            switch result {
            case .ok: show("Closed #\(ticket)")
            case .failed(let m, let rc, let c): show(errorText(m, rc, c), error: true)
            case .unauthorized: show("Token rejected — reconnect", error: true)
            case .timedOut: show("Timed out closing #\(ticket) — verify on the book.", error: true)
            }
        }
    }

    /// Bulk close by live P&L sign, evaluated SERVER-side. filter = "all" | "losing" | "profit".
    /// A failed bulk close returns HTTP 200; Api.closeWhere already maps ok:false -> .failed.
    func closeWhere(_ filter: String) {
        Task {
            let result = await feed.api.closeWhere(filter: filter)
            switch result {
            case .ok(let r): show("Closed \(r.closed) position(s)")
            case .failed(let m, _, _): show(m, error: true)
            case .unauthorized: show("Token rejected — reconnect", error: true)
            case .timedOut: show("Timed out — some positions MAY still be open. Verify the book.", error: true)
            }
        }
    }

    var confirmCloses: Bool { settings.confirmCloses }

    // MARK: Helpers

    private func show(_ text: String, error: Bool = false) { banner = Banner(text: text, isError: error) }

    private func errorText(_ message: String, _ retcode: Int?, _ comment: String?) -> String {
        var s = message
        if let comment, !comment.isEmpty, comment != message { s += " — \(comment)" }
        if let retcode { s += " [\(retcode)]" }
        return s
    }
}

/// Format a price to `digits` decimals; "—" when nil.
func fmt(_ value: Double?, _ digits: Int) -> String {
    guard let value else { return "—" }
    return String(format: "%.\(max(0, digits))f", value)
}

/// Format money with sign + 2 decimals; "—" when nil.
func fmtMoney(_ value: Double?) -> String {
    guard let value else { return "—" }
    return String(format: "%+.2f", value)
}
