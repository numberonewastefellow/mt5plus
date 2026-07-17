import Foundation
import Combine

/// How the app is currently getting on with the server. Drives the status banner. Ported from
/// Android's net/TradingClient.Link.
enum Link: Equatable {
    case connecting
    /// Socket open AND at least one frame received. See the note in the receive loop.
    case up
    /// Transient: server down, box auto-stopped, phone roaming. We keep retrying.
    case down(reason: String, retryInSec: Int)
    /// The server rejected our token. TERMINAL until the user supplies a new one — retrying is an
    /// infinite loop against a server that will never let us in.
    case unauthorized
}

/// The live /ws feed, over `URLSessionWebSocketTask`. Ported from Android's TradingClient.
///
/// Frames are decoded off the main thread in the receive loop; the latest snapshot is published on
/// main. Like Android's conflating StateFlow, only the latest frame matters — SwiftUI coalesces
/// re-renders and the view model de-duplicates per slice, so a 5 Hz tick never floods the UI.
///
/// iOS note on auth detection: `URLSessionWebSocketTask` cannot reliably report the server's custom
/// 4401 close code, so a FRAMELESS disconnect is confirmed against an authenticated REST probe — a
/// 401 there is the terminal Unauthorized condition. This replaces Android's reliance on the raw
/// close code while preserving the "don't retry a rejected token forever" guarantee.
final class TradingClient {

    /// Latest snapshot (conflated: always the newest). nil = "no data yet", which the UI renders safely.
    let snapshotSubject = CurrentValueSubject<Snapshot?, Never>(nil)
    let linkSubject = CurrentValueSubject<Link, Never>(.connecting)

    private let settings: AppSettings
    private let session: URLSession
    private let hz: Int
    /// Returns true iff the server is reachable and answered 401 (token rejected).
    private let authProbe: () async -> Bool

    private var loopTask: Task<Void, Never>?
    /// Consecutive failed attempts. Drives the backoff; reset to 0 when a frame arrives.
    private var attempt = 0

    private static let unauthorizedCode = 4401
    private static let baseBackoffMs: UInt64 = 1_000
    private static let maxBackoffMs: UInt64 = 60_000
    private static let maxShift = 6            // 1s -> 64s, capped at maxBackoffMs
    private static let pingIntervalSec: UInt64 = 5

    init(settings: AppSettings = .shared,
         session: URLSession = SessionProvider.shared,
         hz: Int = 5,                          // the phone asks for 5; the server's own poll is 15
         authProbe: @escaping () async -> Bool) {
        self.settings = settings
        self.session = session
        self.hz = hz
        self.authProbe = authProbe
    }

    // MARK: Control

    func start() {
        guard loopTask == nil else { return }
        loopTask = Task { [weak self] in await self?.runLoop() }
    }

    func stop() {
        loopTask?.cancel()
        loopTask = nil
    }

    /// Drop the last frame WITHOUT tearing the socket down. Used after an account switch: the held
    /// frame describes the previous account's book, and the screen would otherwise render it (close
    /// buttons live against foreign tickets) until the next frame arrives.
    func clearSnapshot() { snapshotSubject.send(nil) }

    /// Break out of the terminal Unauthorized state after the user supplies a new token. Clearing
    /// the link is the whole point — the loop bails on its first line while it is still Unauthorized.
    func retryNow() {
        attempt = 0
        linkSubject.send(.connecting)
        stop()
        start()
    }

    /// Called when a network path appears. Collapses the backoff so a phone that just regained
    /// signal reconnects immediately instead of sitting out the rest of a 60 s sleep.
    func onNetworkAvailable() {
        if case .down = linkSubject.value { retryNow() }
    }

    // MARK: Loop

    private func runLoop() async {
        while !Task.isCancelled {
            // Unauthorized is TERMINAL. Retrying a rejected token would hammer the box and drain the
            // battery while the UI just said "disconnected". Wait for the user.
            if linkSubject.value == .unauthorized { return }

            if case .down = linkSubject.value {} else { linkSubject.send(.connecting) }

            let outcome = await connectAndWait()
            if Task.isCancelled { return }
            if outcome == .unauthorized {
                linkSubject.send(.unauthorized)
                return
            }

            attempt += 1

            // Exponential backoff with jitter, capped at 60 s. "Server gone" is NORMAL here (the box
            // auto-stops after 360 min); a phone that slept for hours must not machine-gun a host that
            // is deliberately off. Jitter stops phone + desktop retrying in lockstep.
            let shift = min(attempt, Self.maxShift)
            let ceiling = min(Self.maxBackoffMs, Self.baseBackoffMs << shift)
            let wait = ceiling / 2 + UInt64.random(in: 0...(ceiling / 2))

            let reason: String
            if case .closed(let r) = outcome { reason = r } else { reason = "disconnected" }
            linkSubject.send(.down(reason: reason, retryInSec: Int(wait / 1000)))
            try? await Task.sleep(nanoseconds: wait * 1_000_000)
        }
    }

    private enum Outcome: Equatable {
        case unauthorized
        case closed(reason: String)
    }

    /// Opens the socket and suspends until it closes. Returns why it ended.
    private func connectAndWait() async -> Outcome {
        guard let url = webSocketURL() else { return .closed(reason: "bad server URL") }

        var request = URLRequest(url: url)
        request.timeoutInterval = 0            // long-lived socket; liveness is the ping, not a timeout
        // Token in the x-token HEADER, not the query string — a header never lands in an access log
        // the way a URL query does, and native clients (unlike browsers) can set handshake headers.
        let tok = settings.token.trimmed()
        if !tok.isEmpty { request.setValue(tok, forHTTPHeaderField: "x-token") }

        let task = session.webSocketTask(with: request)
        task.resume()

        // Manual keep-alive: URLSessionWebSocketTask has no periodic ping, so send one every ~5 s.
        // This is Android's 5 s ping interval — the detection latency for a silently-reaped socket on
        // carrier NAT, so a frozen quote is caught within ~5 s.
        let pinger = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.pingIntervalSec * 1_000_000_000)
                if Task.isCancelled { break }
                task.sendPing { _ in }         // failure surfaces on the receive side; ignore here
            }
        }
        defer {
            pinger.cancel()
            task.cancel(with: .goingAway, reason: nil)
        }

        var gotFrame = false
        while true {
            do {
                let message = try await task.receive()
                let text: String?
                switch message {
                case .string(let s): text = s
                case .data(let d): text = String(data: d, encoding: .utf8)
                @unknown default: text = nil
                }
                guard let text, let data = text.data(using: .utf8) else { continue }

                // A frame arrived — THIS proves the connection is genuinely good (not merely "socket
                // opened"): the server can accept and THEN close on a bad token or an internal error,
                // so only a delivered frame resets the backoff and flips the banner to Up.
                if let snap = try? JSONDecoder().decode(Snapshot.self, from: data) {
                    snapshotSubject.send(snap)
                    gotFrame = true
                    attempt = 0
                    if linkSubject.value != .up { linkSubject.send(.up) }
                }
                // A malformed frame must never kill the socket — keep the last good snapshot and read
                // the next message. (The classic cause was magic overflowing Int32; it is Int64 now.)
            } catch {
                if Task.isCancelled { return .closed(reason: "cancelled") }

                // Opportunistic: some iOS versions do surface the raw code.
                if task.closeCode.rawValue == Self.unauthorizedCode { return .unauthorized }

                // Reliable path: a FRAMELESS close might be a rejected token. Confirm with an
                // authenticated REST probe — only a real 401 makes this terminal.
                if !gotFrame, await authProbe() { return .unauthorized }

                let code = task.closeCode.rawValue
                let reason = (error as NSError).localizedDescription
                return .closed(reason: reason.isEmpty ? "server closed the connection (\(code))" : reason)
            }
        }
    }

    /// http(s)://host → ws(s)://host/ws?hz=N. Only the leading scheme is rewritten.
    private func webSocketURL() -> URL? {
        var base = settings.baseURL
        if base.hasPrefix("https") { base = "wss" + base.dropFirst("https".count) }
        else if base.hasPrefix("http") { base = "ws" + base.dropFirst("http".count) }
        return URL(string: base + "/ws?hz=\(hz)")
    }
}
