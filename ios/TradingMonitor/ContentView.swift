import SwiftUI

struct ContentView: View {
    @EnvironmentObject var vm: MonitorViewModel

    var body: some View {
        TabView {
            DashboardView().tabItem { Label("Dashboard", systemImage: "chart.line.uptrend.xyaxis") }
            OrdersView().tabItem { Label("Orders", systemImage: "list.bullet") }
            LogView().tabItem { Label("Log", systemImage: "doc.text") }
            SettingsView().tabItem { Label("Settings", systemImage: "gear") }
        }
        .task { await vm.poll() }
    }
}

struct DashboardView: View {
    @EnvironmentObject var vm: MonitorViewModel
    @State private var confirmHalt = false

    var body: some View {
        NavigationStack {
            List {
                if let e = vm.error { Section { Text(e).foregroundStyle(.red) } }
                if let s = vm.status {
                    Section("Account (\(s.broker) / \(s.env))") {
                        row("Equity", s.equity)
                        row("Cash", s.cash)
                        row("Day P&L", s.day_pnl, colored: true)
                    }
                    Section("Positions") {
                        ForEach(s.positions) { p in
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(p.symbol).bold()
                                    Text("\(p.qty) @ \(p.avg_price, specifier: "%.2f")").font(.caption)
                                }
                                Spacer()
                                Text(p.pnl, format: .currency(code: "USD"))
                                    .foregroundStyle(p.pnl >= 0 ? .green : .red)
                            }
                        }
                    }
                    Section {
                        Button(s.halted ? "Resume trading" : "HALT trading", role: s.halted ? nil : .destructive) {
                            if s.halted { Task { await vm.toggleHalt() } } else { confirmHalt = true }
                        }
                    }
                } else if vm.error == nil { ProgressView() }
            }
            .navigationTitle("Bot")
            .refreshable { await vm.refresh() }
            .confirmationDialog("Halt all new orders?", isPresented: $confirmHalt) {
                Button("Halt", role: .destructive) { Task { await vm.toggleHalt() } }
            }
        }
    }

    private func row(_ name: String, _ v: Double, colored: Bool = false) -> some View {
        HStack {
            Text(name); Spacer()
            Text(v, format: .currency(code: "USD"))
                .foregroundStyle(colored ? (v >= 0 ? .green : .red) : .primary)
        }
    }
}

struct OrdersView: View {
    @EnvironmentObject var vm: MonitorViewModel
    var body: some View {
        NavigationStack {
            List(vm.orders) { o in
                VStack(alignment: .leading) {
                    HStack {
                        Text("\(o.side) \(o.qty) \(o.symbol)").bold()
                        Spacer()
                        Text(o.status).font(.caption)
                            .foregroundStyle(o.status == "BLOCKED" || o.status == "REJECTED" ? .orange : .secondary)
                    }
                    Text(o.reason).font(.caption).foregroundStyle(.secondary)
                    Text(o.ts).font(.caption2).foregroundStyle(.tertiary)
                }
            }
            .navigationTitle("Orders")
            .refreshable { await vm.refresh() }
        }
    }
}

struct LogView: View {
    @EnvironmentObject var vm: MonitorViewModel
    var body: some View {
        NavigationStack {
            List(vm.events) { e in
                VStack(alignment: .leading) {
                    Text(e.msg).foregroundStyle(e.level == "ERROR" ? .red : e.level == "WARN" ? .orange : .primary)
                    Text(e.ts).font(.caption2).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Log")
            .refreshable { await vm.refresh() }
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject var vm: MonitorViewModel
    var body: some View {
        NavigationStack {
            Form {
                TextField("Server URL", text: $vm.baseURL).textInputAutocapitalization(.never).keyboardType(.URL)
                SecureField("API token", text: $vm.token)
            }
            .navigationTitle("Settings")
        }
    }
}
