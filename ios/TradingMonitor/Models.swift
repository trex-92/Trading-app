import Foundation

struct Position: Codable, Identifiable {
    var id: String { symbol }
    let symbol: String
    let qty: Int
    let avg_price: Double
    let last_price: Double
    let pnl: Double
}

struct Status: Codable {
    let equity: Double
    let cash: Double
    let day_pnl: Double
    let halted: Bool
    let positions: [Position]
    let broker: String
    let env: String
}

struct TradeOrder: Codable, Identifiable {
    var id: String { "\(ts)-\(symbol)-\(side)" }
    let symbol: String
    let side: String
    let qty: Int
    let price: Double?
    let status: String
    let reason: String
    let ts: String
}

struct BotEvent: Codable, Identifiable {
    var id: String { ts + msg }
    let ts: String
    let level: String
    let msg: String
}
