import SwiftUI
import UniformTypeIdentifiers

/// Upload / clear the mutual-TLS material for the EC2 path: our private CA (`ca.crt`) and the phone's
/// client identity (`client.p12` + password). Mirrors Android's Certs screen. Validated before it is
/// persisted, so a wrong p12 password fails here, not later as an opaque handshake error.
struct CertsView: View {
    @ObservedObject private var store = CertStore.shared

    @State private var caData: Data?
    @State private var p12Data: Data?
    @State private var password: String = ""
    @State private var importing: ImportKind?
    @State private var error: String?

    private enum ImportKind: Identifiable {
        case ca, p12
        var id: Int { self == .ca ? 0 : 1 }
    }

    var body: some View {
        Form {
            if let info = store.info() {
                Section("Installed") {
                    LabeledContent("Client CN", value: info.clientCN)
                    LabeledContent("CA CN", value: info.caCN)
                    if let notAfter = info.notAfter {
                        LabeledContent("Expires", value: notAfter.formatted(date: .abbreviated, time: .omitted))
                    }
                }
            }

            Section("Import") {
                Button(caData == nil ? "Choose ca.crt…" : "ca.crt ✓") { importing = .ca }
                Button(p12Data == nil ? "Choose client.p12…" : "client.p12 ✓") { importing = .p12 }
                SecureField("p12 password", text: $password)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }

            if let error {
                Section { Text(error).foregroundStyle(.red).font(.footnote) }
            }

            Section {
                Button("Save certificate") { save() }
                    .disabled(caData == nil || p12Data == nil || password.isEmpty)
                if store.hasCerts() {
                    Button("Remove certificate", role: .destructive) { store.clear() }
                }
            }
        }
        .navigationTitle("Client certificate")
        .fileImporter(
            isPresented: Binding(get: { importing != nil }, set: { if !$0 { importing = nil } }),
            allowedContentTypes: [.data, .x509Certificate, .pkcs12],
            allowsMultipleSelection: false
        ) { result in
            handleImport(result)
        }
    }

    private func handleImport(_ result: Result<[URL], Error>) {
        let kind = importing
        importing = nil
        do {
            guard let url = try result.get().first else { return }
            // Security-scoped access is required for files picked outside the app sandbox.
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            switch kind {
            case .ca: caData = data
            case .p12: p12Data = data
            case .none: break
            }
        } catch {
            self.error = "Could not read file: \(error.localizedDescription)"
        }
    }

    private func save() {
        guard let ca = caData, let p12 = p12Data else { return }
        do {
            try store.save(caData: ca, p12Data: p12, p12Password: password)
            error = nil
            password = ""
            caData = nil
            p12Data = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}
