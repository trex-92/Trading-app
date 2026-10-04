import Foundation

/// Talks to the bot's FastAPI service. Token is kept in the Keychain-backed
/// AppStorage replacement below; base URL should be https (e.g. via Tailscale/Cloudflare tunnel).
struct APIClient {
    let baseURL: URL
    let token: String

    private func request(_ path: String, method: String = "GET") -> URLRequest {
        var r = URLRequest(url: baseURL.appendingPathComponent(path))
        r.httpMethod = method
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return r
    }

    private func send<T: Decodable>(_ r: URLRequest) async throws -> T {
        let (data, resp) = try await URLSession.shared.data(for: r)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func status() async throws -> Status { try await send(request("status")) }
    func orders() async throws -> [TradeOrder] { try await send(request("orders")) }
    func events() async throws -> [BotEvent] { try await send(request("events")) }
    func setHalted(_ halt: Bool) async throws {
        let _: [String: Bool] = try await send(request(halt ? "halt" : "resume", method: "POST"))
    }
}
