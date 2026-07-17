import SwiftUI

/// Enter the server URL + API token and connect. Mirrors the Android Connect screen: it warns on a
/// cleartext transport (the token is the only gate on a plain-HTTP LAN server) and is the deliberate
/// gate before the socket opens at all.
struct ConnectView: View {
    @ObservedObject var settings = AppSettings.shared
    @ObservedObject var feed = Feed.shared

    @State private var url: String
    @State private var token: String

    /// A reason to show at the top (e.g. after a token rejection kicked us back here).
    let notice: String?

    init(notice: String? = nil) {
        self.notice = notice
        _url = State(initialValue: AppSettings.shared.baseURL)
        _token = State(initialValue: AppSettings.shared.token)
    }

    private var normalizedIsCleartext: Bool {
        AppSettings.normalizeBaseURL(url).lowercased().hasPrefix("http://")
    }

    var body: some View {
        NavigationStack {
            Form {
                if let notice {
                    Section {
                        Label(notice, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }

                Section("Server") {
                    TextField("host or http://host:8765", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    SecureField("API token", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                if normalizedIsCleartext && !url.trimmed().isEmpty {
                    Section {
                        Label(
                            "Plain HTTP — the token travels in the clear. Use only on a trusted LAN or VPN.",
                            systemImage: "lock.open.fill"
                        )
                        .font(.footnote)
                        .foregroundStyle(.orange)
                    }
                }

                Section {
                    Button {
                        connect()
                    } label: {
                        Text("Connect").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(url.trimmed().isEmpty)
                }

                Section("Mutual TLS (EC2)") {
                    NavigationLink("Client certificate…") { CertsView() }
                }
            }
            .navigationTitle("Connect")
        }
    }

    private func connect() {
        settings.connect(url: url, token: token)
        // Reflect the normalized value back into the field.
        url = settings.baseURL
        feed.retry()
    }
}
