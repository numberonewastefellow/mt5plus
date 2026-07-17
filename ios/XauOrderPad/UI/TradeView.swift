import SwiftUI

/// The MVP trade screen: status banner, live quote, account line, order form, armed BUY/SELL with a
/// big ENTER button, a CLOSE button that names the exact target ticket, a bulk-close bar, and the
/// positions list. A single clean layout (the Android app's 4 layout modes are Phase 2).
///
/// SAFETY: every order/close here is fired only by a human tap. Nothing on this screen auto-trades.
struct TradeView: View {
    @StateObject private var vm = TradeViewModel()
    @ObservedObject private var feed = Feed.shared
    @State private var confirmFilter: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    StatusBanner(link: vm.link, live: vm.live)
                    QuoteRow(vm: vm)
                    AccountRow(vm: vm)
                    OrderForm(vm: vm)
                    ArmedControls(vm: vm)
                    BulkCloseBar(vm: vm, confirmFilter: $confirmFilter)
                    PositionsList(vm: vm)
                }
                .padding()
            }
            .navigationTitle("XAUUSD")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Log out") { feed.disconnect() }
                }
            }
            .overlay(alignment: .bottom) { bannerView }
            .confirmationDialog(
                "Close \(confirmFilter?.uppercased() ?? "") positions?",
                isPresented: Binding(get: { confirmFilter != nil }, set: { if !$0 { confirmFilter = nil } }),
                titleVisibility: .visible
            ) {
                if let filter = confirmFilter {
                    Button("Close \(filter)", role: .destructive) { vm.closeWhere(filter); confirmFilter = nil }
                }
                Button("Cancel", role: .cancel) { confirmFilter = nil }
            }
        }
        .onAppear { feed.start() }
    }

    @ViewBuilder private var bannerView: some View {
        if let banner = vm.banner {
            Text(banner.text)
                .font(.footnote)
                .padding(10)
                .frame(maxWidth: .infinity)
                .background(banner.isError ? Color.red.opacity(0.9) : Color.green.opacity(0.9))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .padding()
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .onTapGesture { vm.banner = nil }
                .task(id: banner.id) {
                    try? await Task.sleep(nanoseconds: 4_000_000_000)
                    if vm.banner?.id == banner.id { vm.banner = nil }
                }
        }
    }
}

private struct StatusBanner: View {
    let link: Link
    let live: Bool

    var body: some View {
        HStack {
            Circle().fill(color).frame(width: 10, height: 10)
            Text(text).font(.caption).foregroundStyle(.secondary)
            Spacer()
        }
    }

    private var color: Color {
        switch link {
        case .up: return live ? .green : .orange
        case .connecting: return .yellow
        case .down: return .orange
        case .unauthorized: return .red
        }
    }
    private var text: String {
        switch link {
        case .up: return live ? "Live" : "Connected — feed stale"
        case .connecting: return "Connecting…"
        case .down(let reason, let s): return "Reconnecting in \(s)s — \(reason)"
        case .unauthorized: return "Token rejected"
        }
    }
}

private struct QuoteRow: View {
    @ObservedObject var vm: TradeViewModel
    var body: some View {
        HStack {
            priceCell("BID", vm.bid, .red)
            Spacer()
            VStack {
                Text("SPREAD").font(.caption2).foregroundStyle(.secondary)
                Text(vm.spreadPoints.map { String(format: "%.0f", $0) } ?? "—")
                    .font(.headline).monospacedDigit()
            }
            Spacer()
            priceCell("ASK", vm.ask, .green)
        }
        .padding()
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
    }
    private func priceCell(_ label: String, _ value: Double?, _ tint: Color) -> some View {
        VStack {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(fmt(value, vm.digits)).font(.title2.bold()).monospacedDigit().foregroundStyle(tint)
        }
    }
}

private struct AccountRow: View {
    @ObservedObject var vm: TradeViewModel
    var body: some View {
        HStack {
            stat("Equity", fmt(vm.equity, 2))
            Spacer()
            stat("Floating P&L", fmtMoney(vm.floatingPl), tint: (vm.floatingPl ?? 0) >= 0 ? .green : .red)
            Spacer()
            stat("Open", "\(vm.openCount)")
            if let isDemo = vm.isDemo, !isDemo {
                Text("REAL").font(.caption2.bold()).padding(4)
                    .background(.red, in: Capsule()).foregroundStyle(.white)
            }
        }
    }
    private func stat(_ label: String, _ value: String, tint: Color = .primary) -> some View {
        VStack(alignment: .leading) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.subheadline.bold()).monospacedDigit().foregroundStyle(tint)
        }
    }
}

private struct OrderForm: View {
    @ObservedObject var vm: TradeViewModel
    var body: some View {
        HStack {
            field("LOT", $vm.lot)
            field("SL (pts)", $vm.slPoints)
            field("TP (pts)", $vm.tpPoints)
        }
    }
    private func field(_ label: String, _ text: Binding<String>) -> some View {
        VStack(alignment: .leading) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            TextField(label, text: text)
                .keyboardType(.decimalPad)
                .textFieldStyle(.roundedBorder)
                .monospacedDigit()
        }
    }
}

private struct ArmedControls: View {
    @ObservedObject var vm: TradeViewModel
    var body: some View {
        VStack(spacing: 10) {
            Picker("Side", selection: Binding(get: { vm.armedSide }, set: { vm.setArmedSide($0) })) {
                Text("BUY").tag("buy")
                Text("SELL").tag("sell")
            }
            .pickerStyle(.segmented)

            Button {
                vm.placeArmed()
            } label: {
                Text("ENTER \(vm.armedSide.uppercased())").frame(maxWidth: .infinity).padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .tint(vm.armedSide == "buy" ? .green : .red)
            .disabled(!vm.live)

            Button {
                vm.closeArmed()
            } label: {
                Text(closeLabel).frame(maxWidth: .infinity).padding(.vertical, 6)
            }
            .buttonStyle(.bordered)
            .disabled(vm.armedTarget == nil)
        }
    }
    private var closeLabel: String {
        guard let t = vm.armedTarget else { return "No \(vm.armedSide.uppercased()) position" }
        return "CLOSE #\(t.ticket) @ \(fmt(t.priceOpen, vm.digits))"
    }
}

private struct BulkCloseBar: View {
    @ObservedObject var vm: TradeViewModel
    @Binding var confirmFilter: String?

    var body: some View {
        HStack {
            bulk("CLOSE ALL", "all")
            bulk("LOSING", "losing")
            bulk("PROFIT", "profit")
        }
    }
    private func bulk(_ label: String, _ filter: String) -> some View {
        Button(label) {
            if vm.confirmCloses { confirmFilter = filter } else { vm.closeWhere(filter) }
        }
        .font(.caption.bold())
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(.red.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
        .disabled(vm.openCount == 0)
    }
}

private struct PositionsList: View {
    @ObservedObject var vm: TradeViewModel
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Positions").font(.headline)
            if vm.positions.isEmpty {
                Text("No open positions").font(.footnote).foregroundStyle(.secondary)
            } else {
                ForEach(vm.positions) { p in
                    HStack {
                        Text(p.isBuy ? "BUY" : "SELL")
                            .font(.caption.bold())
                            .foregroundStyle(p.isBuy ? .green : .red)
                            .frame(width: 44, alignment: .leading)
                        Text("\(p.volume.map { String(format: "%.2f", $0) } ?? "—")")
                            .font(.caption).monospacedDigit()
                        Text("@ \(fmt(p.priceOpen, vm.digits))")
                            .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                        Spacer()
                        Text(fmtMoney(p.profit))
                            .font(.caption.bold()).monospacedDigit()
                            .foregroundStyle((p.profit ?? 0) >= 0 ? .green : .red)
                    }
                    .padding(.vertical, 4)
                    Divider()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
