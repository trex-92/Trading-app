import SwiftUI

@MainActor
final class MonitorViewModel: ObservableObject {
    @Published var status: Status?
    @Published var orders: [TradeOrder] = []
    @Published var events: [BotEvent] = []
    @Published var error: String?
    @AppStorage("baseURL") var baseURL = "http://localhost:8000"
    @Published var token = Keychain.load("apiToken") {
        didSet { Keychain.save(token, for: "apiToken") }
    }

    private var client: APIClient? {
        URL(string: baseURL).map { APIClient(baseURL: $0, token: token) }
    }

    func refresh() async {
        guard let c = client else { error = "Bad URL"; return }
        do {
            async let s = c.status(), o = c.orders(), e = c.events()
            (status, orders, events) = try await (s, o, e)
            error = nil
        } catch { self.error = error.localizedDescription }
    }

    func toggleHalt() async {
        guard let c = client, let s = status else { return }
        do { try await c.setHalted(!s.halted); await refresh() } catch { self.error = error.localizedDescription }
    }

    /// Poll while the app is in the foreground. Background alerts need APNs (see README roadmap).
    func poll() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(nanoseconds: 5_000_000_000)
        }
    }
}
