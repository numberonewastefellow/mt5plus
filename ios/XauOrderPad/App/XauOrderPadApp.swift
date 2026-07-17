import SwiftUI

@main
struct XauOrderPadApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

/// Routes between Connect and Trade. The Connect screen is the deliberate gate: a prefilled URL must
/// never auto-dial, and a rejected token (terminal Unauthorized) sends the user back here.
struct RootView: View {
    @ObservedObject private var settings = AppSettings.shared
    @ObservedObject private var feed = Feed.shared

    var body: some View {
        Group {
            if !settings.isConfigured {
                ConnectView()
            } else if feed.link == .unauthorized {
                ConnectView(notice: "The server rejected the token. Re-enter it to reconnect.")
            } else {
                TradeView()
            }
        }
        // Keep the screen awake while trading, like the Android app (KEEP_SCREEN_ON).
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
    }
}
