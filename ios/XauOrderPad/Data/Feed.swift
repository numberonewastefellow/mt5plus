import Foundation
import Combine
import Network

/// App-scoped owner of the live feed + the command API — the Swift counterpart of Android's
/// data/Feed.kt. It survives view navigation (a singleton), mirrors the socket's state into
/// `@Published` properties for SwiftUI, and collapses the reconnect backoff when the network path
/// comes back (Android's ConnectivityManager callback).
@MainActor
final class Feed: ObservableObject {

    static let shared = Feed()

    /// Latest /ws snapshot, or nil for "no data yet".
    @Published private(set) var snapshot: Snapshot?
    /// Connection state, drives the status banner.
    @Published private(set) var link: Link = .connecting

    let api: Api
    private let settings: AppSettings
    private let client: TradingClient
    private var cancellables = Set<AnyCancellable>()

    private let pathMonitor = NWPathMonitor()
    private let monitorQueue = DispatchQueue(label: "xau.network.monitor")
    private var started = false

    private init(settings: AppSettings = .shared) {
        self.settings = settings
        self.api = Api(settings: settings)

        // authProbe: only a server that is REACHABLE and answers 401 makes the feed terminally
        // Unauthorized. A server that is merely down fails this and stays a normal retry.
        let probeApi = self.api
        self.client = TradingClient(settings: settings) {
            if case .unauthorized = await probeApi.state() { return true }
            return false
        }

        // Mirror the socket subjects onto the main actor for SwiftUI.
        client.snapshotSubject
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.snapshot = $0 }
            .store(in: &cancellables)
        client.linkSubject
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.link = $0 }
            .store(in: &cancellables)

        pathMonitor.pathUpdateHandler = { [weak self] path in
            guard path.status == .satisfied else { return }
            Task { @MainActor in self?.client.onNetworkAvailable() }
        }
        pathMonitor.start(queue: monitorQueue)
    }

    // MARK: Lifecycle

    /// Open the socket, but only once the user has actually pressed CONNECT (a prefilled URL must
    /// never auto-dial). Idempotent.
    func start() {
        guard settings.isConfigured else { return }
        guard !started else { return }
        started = true
        client.start()
    }

    func stop() {
        started = false
        client.stop()
    }

    /// After the user supplies a new token / re-connects.
    func retry() {
        started = true
        client.retryNow()
    }

    /// After an account switch — drop the stale book so foreign tickets aren't shown as live.
    func clearSnapshot() { client.clearSnapshot() }

    /// Full teardown for LOG OUT: forget the token/connected state and close the socket.
    func disconnect() {
        settings.disconnect()
        stop()
    }
}
