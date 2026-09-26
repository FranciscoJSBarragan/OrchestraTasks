import SwiftUI
import UserNotifications

@main
struct OrchestraTasksApp: App {
    @StateObject private var store = TaskStore()

    init() {
        let open = UNNotificationAction(
            identifier: "OPEN_ORCHESTRA_TASKS",
            title: String(localized: "Open Orchestra Tasks"),
            options: [.foreground]
        )
        let category = UNNotificationCategory(
            identifier: "ORCHESTRA_ATTENTION",
            actions: [open],
            intentIdentifiers: []
        )
        let center = UNUserNotificationCenter.current()
        center.setNotificationCategories([category])
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    var body: some Scene {
        Window("Orchestra Tasks", id: "tasks") {
            MainWindowView(store: store).frame(minWidth: 940, minHeight: 600)
        }
        .defaultSize(width: 1160, height: 720)

        MenuBarExtra {
            MenuBarView(store: store)
        } label: {
            Label(menuTitle, systemImage: menuSymbol)
        }
        .menuBarExtraStyle(.window)
    }

    private var menuTitle: String {
        let active = store.tasks.filter { $0.isActive }
        guard !active.isEmpty else { return "" }
        if active.count == 1 { return "\(active[0].repositoryName) 1" }
        return "\(active.count)"
    }
    private var menuSymbol: String {
        store.errorMessage == nil ? "point.3.connected.trianglepath.dotted" : "exclamationmark.triangle"
    }
}
