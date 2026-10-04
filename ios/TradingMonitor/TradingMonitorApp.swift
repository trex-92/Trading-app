import SwiftUI

@main
struct TradingMonitorApp: App {
    @StateObject private var vm = MonitorViewModel()
    var body: some Scene {
        WindowGroup { ContentView().environmentObject(vm) }
    }
}
